// face_tracker_cpp — C++ 原生面捕主程序
//
// JSON Lines 协议与 Rust 版 native/face_tracker/src/protocol.rs 完全一致，
// Go 后端 NativeBridge 无需任何改动即可切换（配置 BinaryPath 指向本 exe）。
//
// 数据流（完整模式）：
//   MF 摄像头帧 → YuNet 检测 → ROI 裁剪 → 478 点网格 → BlendShape/头姿
//   → Live2D 参数 → EMA 平滑 → 死区 → JSON frame
// 回退链：网格模型缺失/低置信 → YuNet 5 点；ONNX 缺失 → ready(has_model=false)。
//
// 退出策略与 Rust 版一致：quit / stdin 关闭时直接 ExitProcess，
// 由 OS 回收摄像头与 ORT 资源，避免析构竞态。

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <shellapi.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <deque>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "camera.h"
#include "json.hpp"
#include "mapping.h"
#include "models.h"
#include "ort.h"

// 自测模式（--image）的图像解码；仅编译进 JPEG/PNG/BMP 支持。
#define STB_IMAGE_IMPLEMENTATION
#define STBI_ONLY_JPEG
#define STBI_ONLY_PNG
#define STBI_ONLY_BMP
#include "stb_image.h"

using json = nlohmann::json;
using namespace std::chrono;

static const char* VERSION = "2.0.0-cpp";

static void emit(const json& j) {
    std::cout << j.dump() << "\n";
    std::cout.flush();
}

static void emit_error(const std::string& msg) {
    emit(json{{"type", "error"}, {"message", msg}});
}

// ─────────────────── 模型查找 ───────────────────

static std::wstring exe_dir() {
    wchar_t buf[MAX_PATH];
    DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    std::wstring path(buf, n);
    size_t slash = path.find_last_of(L"\\/");
    return slash == std::wstring::npos ? L"" : path.substr(0, slash + 1);
}

static std::wstring utf8_to_wide(const std::string& s) {
    if (s.empty()) return L"";
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), w.data(), n);
    return w;
}

static std::vector<std::wstring> model_dirs() {
    std::vector<std::wstring> dirs;
    char env[1024];
    DWORD n = GetEnvironmentVariableA("FACE_TRACKER_MODEL_DIR", env, sizeof(env));
    if (n > 0 && n < sizeof(env)) dirs.push_back(utf8_to_wide(env) + L"\\");
    std::wstring ed = exe_dir();
    // 部署布局：models/ 与 exe 同目录
    dirs.push_back(ed + L"models\\");
    // 开发布局：native/face_tracker_cpp/build/ → 项目根（上三级）
    dirs.push_back(ed + L"..\\..\\..\\models\\face\\");
    dirs.push_back(ed + L"..\\..\\..\\models\\");
    dirs.push_back(ed + L"..\\..\\..\\native\\face_tracker\\models\\");
    // 相对工作目录
    dirs.push_back(L"models\\face\\");
    dirs.push_back(L"models\\");
    dirs.push_back(L"native\\face_tracker\\models\\");
    return dirs;
}

static std::wstring find_model(const std::vector<std::wstring>& names) {
    for (const auto& dir : model_dirs()) {
        for (const auto& name : names) {
            std::wstring p = dir + name;
            DWORD attr = GetFileAttributesW(p.c_str());
            if (attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY))
                return p;
        }
    }
    return L"";
}

// ─────────────────── 应用状态 ───────────────────

struct App {
    Camera cam;
    YuNet yunet;
    FaceMesh mesh;
    bool running = false;
    float smoothing = 0.5f;
    float deadzone = 0.02f;
    Params prev;
    int camera_id = 0;
    float fps = 0;
    uint32_t frame_count = 0;
    steady_clock::time_point last_fps_time = steady_clock::now();
    bool mesh_warned = false;

    // 返回 true = quit
    bool handle(const json& cmd) {
        std::string c = cmd.value("cmd", "");
        if (c == "start") {
            int id = cmd.value("camera_id", 0);
            int w = cmd.value("width", 640);
            int h = cmd.value("height", 480);
            if (running) {
                if (id != camera_id)
                    emit_error("面捕已在运行（摄像头 " + std::to_string(camera_id) + "），请先停止再切换");
                return false;  // 同摄像头重复 start：幂等忽略
            }
            if (!yunet.ok()) {
                emit_error("人脸检测模型未加载（需要 onnxruntime.dll + YuNet 模型，见 download_deps.ps1）");
                return false;
            }
            camera_id = id;
            int aw = 0, ah = 0;
            std::string err;
            if (cam.open(id, w, h, aw, ah, err)) {
                running = true;
                prev.clear();
                frame_count = 0;
                last_fps_time = steady_clock::now();
                emit(json{{"type", "started"}, {"camera_id", id}, {"width", aw}, {"height", ah}});
            } else {
                fprintf(stderr, "[face_tracker] 摄像头错误: %s\n", err.c_str());
                emit_error(err);
            }
        } else if (c == "stop") {
            cam.close();
            running = false;
            emit(json{{"type", "stopped"}});
        } else if (c == "status") {
            emit(json{{"type", "status"}, {"running", running}, {"camera_id", camera_id},
                      {"fps", fps}, {"smoothing", smoothing}, {"deadzone", deadzone}});
        } else if (c == "set_smoothing") {
            float a = cmd.value("alpha", 0.5f);
            smoothing = std::max(0.0f, std::min(1.0f, a));
        } else if (c == "set_deadzone") {
            float t = cmd.value("threshold", 0.02f);
            deadzone = std::max(0.0f, std::min(0.5f, t));
        } else if (c == "quit") {
            return true;
        }
        return false;
    }

    void process_frame() {
        RgbFrame frame;
        std::string err;
        if (!cam.capture(frame, err)) {
            if (!err.empty()) {
                fprintf(stderr, "[face_tracker] 采集失败: %s\n", err.c_str());
                std::this_thread::sleep_for(milliseconds(100));
            }
            return;  // 无样本（tick）：下轮重试
        }
        const float wf = (float)frame.width, hf = (float)frame.height;

        Params bs;
        HeadRotation head;
        bool has_face = false;

        auto faces = yunet.detect(frame.rgb.data(), frame.width, frame.height, 0.5f);
        if (!faces.empty()) {
            const Face& f = faces[0];
            bool mesh_ok = false;
            if (mesh.ok()) {
                std::vector<Point3> pts;
                float score = 0;
                if (mesh.infer(frame.rgb.data(), frame.width, frame.height, f, pts, score, err)) {
                    bs = compute_blendshapes_468(pts);
                    estimate_head_rotation_468(pts, head);
                    has_face = true;
                    mesh_ok = true;
                } else if (!err.empty() && !mesh_warned) {
                    fprintf(stderr, "[mesh] 推理失败（回退 5 点）: %s\n", err.c_str());
                    mesh_warned = true;
                }
            }
            if (!mesh_ok && f.has_kps) {
                // YuNet 顺序 [右眼,左眼,鼻,右嘴角,左嘴角] → [左眼,右眼,鼻,左嘴角,右嘴角]
                Point3 le{f.kps[1][0] / wf, f.kps[1][1] / hf, 0};
                Point3 re{f.kps[0][0] / wf, f.kps[0][1] / hf, 0};
                Point3 nose{f.kps[2][0] / wf, f.kps[2][1] / hf, 0};
                Point3 lm{f.kps[4][0] / wf, f.kps[4][1] / hf, 0};
                Point3 rm{f.kps[3][0] / wf, f.kps[3][1] / hf, 0};
                bs = compute_blendshapes_5pt(le, re, nose, lm, rm);
                head = estimate_head_rotation_5pt(le, re, nose, lm, rm);
                has_face = true;
            }
        }

        Params empty;
        Params params = blendshapes_to_live2d(has_face ? bs : empty, has_face ? &head : nullptr);
        if (!prev.empty()) params = smooth(params, prev, smoothing);
        apply_deadzone(params, deadzone);
        prev = params;

        // FPS 统计
        ++frame_count;
        auto now = steady_clock::now();
        float elapsed = duration<float>(now - last_fps_time).count();
        if (elapsed >= 1.0f) {
            fps = frame_count / elapsed;
            frame_count = 0;
            last_fps_time = now;
        }
        uint64_t ts = (uint64_t)duration_cast<milliseconds>(
            system_clock::now().time_since_epoch()).count();

        json j = json{{"type", "frame"}, {"live2d_params", params},
                      {"fps", fps}, {"timestamp_ms", ts}};
        if (has_face) {
            j["blendshapes"] = bs;
            j["head_rotation"] = json{{"pitch", head.pitch}, {"yaw", head.yaw}, {"roll", head.roll}};
        }
        emit(j);
    }
};

// 加载 ONNX Runtime + YuNet + 网格模型（协议模式与自测模式共用）。
static void load_models(App& app) {
    std::string err;
    bool ort_ok = ort::init(err);
    if (!ort_ok) {
        fprintf(stderr, "[face_tracker] ONNX Runtime 不可用: %s\n", err.c_str());
        return;
    }
    std::wstring yunet_path = find_model({
        L"face_detection_yunet_2023mar.onnx", L"yunet.onnx", L"face_detector.onnx"});
    if (!yunet_path.empty() && app.yunet.load(yunet_path, err)) {
        fprintf(stderr, "[face_tracker] YuNet 已加载: %ls\n", yunet_path.c_str());
        std::wstring mesh_path = find_model({
            L"face_landmark_478.onnx", L"face_landmark_468.onnx", L"face_mesh.onnx"});
        if (!mesh_path.empty()) {
            if (app.mesh.load(mesh_path, err))
                fprintf(stderr, "[face_tracker] 网格模型已加载 (%d 点): %ls\n",
                        app.mesh.num_landmarks(), mesh_path.c_str());
            else
                fprintf(stderr, "[face_tracker] 网格模型加载失败（回退 5 点）: %s\n", err.c_str());
        }
    } else {
        fprintf(stderr, "[face_tracker] YuNet 模型未找到或加载失败: %s\n", err.c_str());
    }
}

// 自测模式：face_tracker.exe --image <path>
// 对静态图跑与实时帧完全相同的 YuNet→网格→BlendShape 路径，
// 输出归一化关键点 JSON，供与 Python 参考流水线数值对拍。
static int selftest(const wchar_t* path) {
    App app;
    load_models(app);
    if (!app.yunet.ok() || !app.mesh.ok()) {
        emit_error("自测需要 YuNet + 网格模型（见 download_deps.ps1）");
        return 1;
    }
    FILE* f = _wfopen(path, L"rb");
    if (!f) { emit_error("无法打开图像文件"); return 1; }
    int w = 0, h = 0, n = 0;
    uint8_t* rgb = stbi_load_from_file(f, &w, &h, &n, 3);
    fclose(f);
    if (!rgb) { emit_error("图像解码失败"); return 1; }

    auto faces = app.yunet.detect(rgb, w, h, 0.5f);
    json j = json{{"type", "selftest"}, {"image_width", w}, {"image_height", h},
                  {"faces", (int)faces.size()}};
    if (!faces.empty()) {
        const Face& f0 = faces[0];
        j["box"] = json::array({f0.x, f0.y, f0.w, f0.h});
        if (f0.has_kps) {
            json kp = json::array();
            for (int i = 0; i < 5; ++i)
                kp.push_back(json::array({f0.kps[i][0], f0.kps[i][1]}));
            j["kps"] = kp;
        }
        std::vector<Point3> pts;
        float score = 0;
        std::string err;
        if (app.mesh.infer(rgb, w, h, f0, pts, score, err)) {
            j["score"] = score;
            json lm = json::array();
            for (const auto& p : pts) lm.push_back(json::array({p.x, p.y, p.z}));
            j["landmarks"] = lm;
            j["blendshapes"] = compute_blendshapes_468(pts);
            HeadRotation hr;
            estimate_head_rotation_468(pts, hr);
            j["head_rotation"] = json{{"pitch", hr.pitch}, {"yaw", hr.yaw}, {"roll", hr.roll}};
        } else {
            j["score"] = score;
            j["mesh_error"] = err.empty() ? "low score" : err;
        }
    }
    stbi_image_free(rgb);
    emit(j);
    return 0;
}

int main() {
    std::ios::sync_with_stdio(false);

    // 宽字符命令行（支持中文路径）
    int wargc = 0;
    wchar_t** wargv = CommandLineToArgvW(GetCommandLineW(), &wargc);
    if (wargv) {
        if (wargc >= 3 && wcscmp(wargv[1], L"--image") == 0) {
            int rc = selftest(wargv[2]);
            LocalFree(wargv);
            return rc;
        }
        LocalFree(wargv);
    }

    App app;
    std::string err;
    if (!Camera::startup(err))
        fprintf(stderr, "[face_tracker] %s\n", err.c_str());
    load_models(app);

    emit(json{{"type", "ready"}, {"version", VERSION},
              {"has_camera", Camera::count_devices() > 0},
              {"has_model", app.yunet.ok()}});

    // stdin 命令线程
    std::mutex qmu;
    std::deque<json> queue;
    std::thread([&]() {
        std::string line;
        while (std::getline(std::cin, line)) {
            if (line.empty()) continue;
            try {
                json j = json::parse(line);
                std::lock_guard<std::mutex> lk(qmu);
                queue.push_back(std::move(j));
            } catch (...) {
                // 非法 JSON 行忽略（与 Rust 版一致）
            }
        }
        ExitProcess(0);  // stdin 关闭 = 父进程退出
    }).detach();

    // 主循环
    for (;;) {
        for (;;) {
            json cmd;
            {
                std::lock_guard<std::mutex> lk(qmu);
                if (queue.empty()) break;
                cmd = std::move(queue.front());
                queue.pop_front();
            }
            if (app.handle(cmd)) ExitProcess(0);
        }
        if (app.running) {
            auto t0 = steady_clock::now();
            app.process_frame();
            auto elapsed = steady_clock::now() - t0;
            if (elapsed < milliseconds(33))
                std::this_thread::sleep_for(milliseconds(33) - elapsed);
        } else {
            std::this_thread::sleep_for(milliseconds(50));
        }
    }
}