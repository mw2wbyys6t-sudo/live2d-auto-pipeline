#include "camera.h"

#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <mferror.h>

#include <climits>
#include <cstdio>
#include <cstdlib>

template <typename T>
static void safe_release(T*& p) {
    if (p) { p->Release(); p = nullptr; }
}

static std::string hr_msg(const char* what, HRESULT hr) {
    char buf[160];
    snprintf(buf, sizeof(buf), "%s (HRESULT=0x%08lX)", what, static_cast<unsigned long>(hr));
    return buf;
}

// 常见视频子类型的可读名（诊断用）。
static const char* subtype_name(const GUID& g) {
    if (g == MFVideoFormat_RGB32) return "RGB32";
    if (g == MFVideoFormat_RGB24) return "RGB24";
    if (g == MFVideoFormat_NV12) return "NV12";
    if (g == MFVideoFormat_YUY2) return "YUY2";
    if (g == MFVideoFormat_UYVY) return "UYVY";
    if (g == MFVideoFormat_I420) return "I420";
    if (g == MFVideoFormat_MJPG) return "MJPG";
    if (g == MFVideoFormat_H264) return "H264";
    return "other";
}

// 子类型优先级：无需/易于软件转换的排前面，MJPG 需解码器排后。
static int subtype_rank(const GUID& g) {
    if (g == MFVideoFormat_RGB32) return 0;
    if (g == MFVideoFormat_RGB24) return 1;
    if (g == MFVideoFormat_NV12) return 2;
    if (g == MFVideoFormat_YUY2) return 3;
    if (g == MFVideoFormat_UYVY) return 4;
    if (g == MFVideoFormat_I420) return 5;
    if (g == MFVideoFormat_MJPG) return 6;
    return 9;
}

bool Camera::startup(std::string& err) {
    static bool done = false;
    static bool ok = false;
    if (done) return ok;
    done = true;
    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(hr) && hr != RPC_E_CHANGED_MODE) {
        err = hr_msg("COM 初始化失败", hr);
        return false;
    }
    hr = MFStartup(MF_VERSION);
    if (FAILED(hr)) {
        err = hr_msg("Media Foundation 初始化失败", hr);
        return false;
    }
    ok = true;
    return true;
}

// 枚举视频采集设备；返回 activate 数组（调用方负责释放）。
static HRESULT enum_devices(IMFAttributes*& attrs, IMFActivate**& devices, UINT32& count) {
    devices = nullptr;
    count = 0;
    HRESULT hr = MFCreateAttributes(&attrs, 1);
    if (FAILED(hr)) return hr;
    hr = attrs->SetGUID(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE,
                        MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID);
    if (FAILED(hr)) return hr;
    return MFEnumDeviceSources(attrs, &devices, &count);
}

int Camera::count_devices() {
    std::string err;
    if (!startup(err)) return 0;
    IMFAttributes* attrs = nullptr;
    IMFActivate** devices = nullptr;
    UINT32 count = 0;
    if (FAILED(enum_devices(attrs, devices, count))) count = 0;
    for (UINT32 i = 0; i < count; ++i) devices[i]->Release();
    CoTaskMemFree(devices);
    safe_release(attrs);
    return static_cast<int>(count);
}

Camera::~Camera() { close(); }

bool Camera::query_format() {
    IMFMediaType* type = nullptr;
    HRESULT hr = reader_->GetCurrentMediaType(
        static_cast<DWORD>(MF_SOURCE_READER_FIRST_VIDEO_STREAM), &type);
    if (FAILED(hr)) return false;
    UINT32 w = 0, h = 0;
    MFGetAttributeSize(type, MF_MT_FRAME_SIZE, &w, &h);
    w_ = static_cast<int>(w);
    h_ = static_cast<int>(h);
    // capture() 按 RGB32（4 字节/像素）解读，输出格式必须是 RGB32。
    GUID sub = {};
    type->GetGUID(MF_MT_SUBTYPE, &sub);
    if (sub != MFVideoFormat_RGB32) {
        fprintf(stderr, "[camera] 输出格式非 RGB32: %s\n", subtype_name(sub));
        safe_release(type);
        return false;
    }
    // DEFAULT_STRIDE 可能不存在（按 w*4 处理）；负值表示自底向上。
    UINT32 stride = 0;
    if (SUCCEEDED(type->GetUINT32(MF_MT_DEFAULT_STRIDE, &stride)))
        stride_ = static_cast<int32_t>(stride);
    else
        stride_ = w_ * 4;
    safe_release(type);
    return w_ > 0 && h_ > 0;
}

bool Camera::open(int id, int req_w, int req_h, int& actual_w, int& actual_h, std::string& err) {
    if (is_open()) close();
    if (!startup(err)) return false;

    IMFAttributes* attrs = nullptr;
    IMFActivate** devices = nullptr;
    UINT32 count = 0;
    HRESULT hr = enum_devices(attrs, devices, count);
    if (FAILED(hr)) {
        safe_release(attrs);
        err = hr_msg("枚举摄像头失败", hr);
        return false;
    }
    if (id < 0 || static_cast<UINT32>(id) >= count) {
        for (UINT32 i = 0; i < count; ++i) devices[i]->Release();
        CoTaskMemFree(devices);
        safe_release(attrs);
        err = "摄像头 " + std::to_string(id) + " 不存在（共 " + std::to_string(count) + " 个）";
        return false;
    }

    hr = devices[id]->ActivateObject(IID_PPV_ARGS(&source_));
    for (UINT32 i = 0; i < count; ++i) devices[i]->Release();
    CoTaskMemFree(devices);
    safe_release(attrs);
    if (FAILED(hr)) {
        err = hr_msg("打开摄像头失败（可能被其他程序占用）", hr);
        return false;
    }

    // 启用高级视频处理（格式转换 + MJPG 解码），覆盖只输出 MJPG/YUV 的摄像头。
    // 注意：不能与 MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING 同时设置（互斥）。
    IMFAttributes* rattrs = nullptr;
    MFCreateAttributes(&rattrs, 1);
    if (rattrs)
        rattrs->SetUINT32(MF_SOURCE_READER_ENABLE_ADVANCED_VIDEO_PROCESSING, TRUE);
    hr = MFCreateSourceReaderFromMediaSource(source_, rattrs, &reader_);
    safe_release(rattrs);
    if (FAILED(hr)) {
        safe_release(source_);
        err = hr_msg("创建 SourceReader 失败", hr);
        return false;
    }
    const DWORD stream = static_cast<DWORD>(MF_SOURCE_READER_FIRST_VIDEO_STREAM);
    reader_->SetStreamSelection(static_cast<DWORD>(MF_SOURCE_READER_ALL_STREAMS), FALSE);
    reader_->SetStreamSelection(stream, TRUE);

    // 枚举原生格式，选「子类型优先级 + 分辨率接近度」最优的一档。
    IMFMediaType* best = nullptr;
    int best_score = INT_MAX;
    for (DWORD i = 0;; ++i) {
        IMFMediaType* t = nullptr;
        HRESULT e = reader_->GetNativeMediaType(stream, i, &t);
        if (e == MF_E_NO_MORE_TYPES || FAILED(e)) break;
        GUID sub = {};
        t->GetGUID(MF_MT_SUBTYPE, &sub);
        UINT32 tw = 0, th = 0;
        MFGetAttributeSize(t, MF_MT_FRAME_SIZE, &tw, &th);
        fprintf(stderr, "[camera] 原生格式[%lu]: %s %lux%lu\n",
                (unsigned long)i, subtype_name(sub), (unsigned long)tw, (unsigned long)th);
        int dist = abs((int)tw - req_w) + abs((int)th - req_h);
        int score = subtype_rank(sub) * 100000 + dist;
        if (score < best_score) {
            safe_release(best);
            best = t;
            best_score = score;
        } else {
            safe_release(t);
        }
    }
    if (best) {
        HRESULT e = reader_->SetCurrentMediaType(stream, nullptr, best);
        if (FAILED(e))
            fprintf(stderr, "[camera] 设置原生格式失败: 0x%08lX\n", (unsigned long)e);
        safe_release(best);
    }

    // 请求 RGB32 输出（不指定分辨率，沿用上面选定的尺寸）。
    IMFMediaType* type = nullptr;
    hr = MFCreateMediaType(&type);
    if (SUCCEEDED(hr)) {
        type->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
        type->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_RGB32);
        hr = reader_->SetCurrentMediaType(stream, nullptr, type);
        safe_release(type);
    }
    if (FAILED(hr) || !query_format()) {
        close();
        err = hr_msg("设置视频格式失败", hr);
        return false;
    }

    actual_w = w_;
    actual_h = h_;
    fprintf(stderr, "[camera] 已打开设备 %d: %dx%d stride=%d\n", id, w_, h_, stride_);
    return true;
}

void Camera::close() {
    safe_release(reader_);
    if (source_) {
        source_->Shutdown();  // 显式释放硬件，保证下次 open 干净
        safe_release(source_);
    }
    w_ = h_ = stride_ = 0;
}

bool Camera::capture(RgbFrame& out, std::string& err) {
    if (!reader_) { err = "摄像头未打开"; return false; }

    DWORD flags = 0;
    IMFSample* sample = nullptr;
    HRESULT hr = reader_->ReadSample(
        static_cast<DWORD>(MF_SOURCE_READER_FIRST_VIDEO_STREAM), 0,
        nullptr, &flags, nullptr, &sample);
    if (FAILED(hr)) {
        err = hr_msg("采集失败", hr);
        return false;
    }
    if (flags & MF_SOURCE_READERF_CURRENTMEDIATYPECHANGED) query_format();
    if (!sample) return false;  // tick / 流间隙：调用方重试

    IMFMediaBuffer* buf = nullptr;
    hr = sample->ConvertToContiguousBuffer(&buf);
    if (FAILED(hr)) {
        safe_release(sample);
        err = hr_msg("取帧缓冲失败", hr);
        return false;
    }

    BYTE* data = nullptr;
    DWORD max_len = 0, cur_len = 0;
    hr = buf->Lock(&data, &max_len, &cur_len);
    if (SUCCEEDED(hr)) {
        out.width = w_;
        out.height = h_;
        out.rgb.resize(static_cast<size_t>(w_) * h_ * 3);
        const int abs_stride = stride_ < 0 ? -stride_ : stride_;
        const bool bottom_up = stride_ < 0;
        for (int y = 0; y < h_; ++y) {
            const int sy = bottom_up ? (h_ - 1 - y) : y;
            const BYTE* src = data + static_cast<size_t>(sy) * abs_stride;
            uint8_t* dst = out.rgb.data() + static_cast<size_t>(y) * w_ * 3;
            for (int x = 0; x < w_; ++x) {
                // RGB32 内存字节序: B, G, R, X
                dst[x * 3 + 0] = src[x * 4 + 2];
                dst[x * 3 + 1] = src[x * 4 + 1];
                dst[x * 3 + 2] = src[x * 4 + 0];
            }
        }
        buf->Unlock();
    } else {
        err = hr_msg("锁定帧缓冲失败", hr);
    }
    safe_release(buf);
    safe_release(sample);
    return SUCCEEDED(hr);
}