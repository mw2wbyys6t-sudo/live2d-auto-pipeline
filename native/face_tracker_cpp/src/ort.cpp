#include "ort.h"

#include <windows.h>

#include <algorithm>
#include <stdexcept>

namespace ort {

static const OrtApi* g_api = nullptr;
static OrtEnv* g_env = nullptr;
static HMODULE g_dll = nullptr;

const OrtApi* api() { return g_api; }

// 把 OrtStatus* 转成异常；nullptr 表示成功。
static void check(OrtStatus* st) {
    if (!st) return;
    std::string msg = g_api->GetErrorMessage(st);
    g_api->ReleaseStatus(st);
    throw std::runtime_error(msg);
}

bool init(std::string& err) {
    if (g_api) return true;

    // 优先 exe 同目录，其次系统搜索路径（PATH）。
    wchar_t exe_path[MAX_PATH];
    DWORD n = GetModuleFileNameW(nullptr, exe_path, MAX_PATH);
    std::wstring dll_path = L"onnxruntime.dll";
    if (n > 0 && n < MAX_PATH) {
        std::wstring dir(exe_path, n);
        size_t slash = dir.find_last_of(L"\\/");
        if (slash != std::wstring::npos)
            dll_path = dir.substr(0, slash + 1) + L"onnxruntime.dll";
    }
    g_dll = LoadLibraryW(dll_path.c_str());
    if (!g_dll) g_dll = LoadLibraryW(L"onnxruntime.dll");
    if (!g_dll) {
        err = "无法加载 onnxruntime.dll（请放到 exe 同目录）";
        return false;
    }

    auto get_base = reinterpret_cast<const OrtApiBase* (*)()>(
        GetProcAddress(g_dll, "OrtGetApiBase"));
    if (!get_base) {
        err = "onnxruntime.dll 缺少 OrtGetApiBase 导出";
        return false;
    }
    const OrtApiBase* base = get_base();
    g_api = base->GetApi(ORT_API_VERSION);
    if (!g_api) {
        err = "onnxruntime.dll 版本与头文件不匹配（需要 >= 1.30）";
        return false;
    }

    try {
        check(g_api->CreateEnv(ORT_LOGGING_LEVEL_ERROR, "face_tracker_cpp", &g_env));
    } catch (const std::exception& e) {
        err = e.what();
        return false;
    }
    return true;
}

Session::~Session() {
    release_run();
    if (mem_info_) g_api->ReleaseMemoryInfo(mem_info_);
    if (sess_) g_api->ReleaseSession(sess_);
}

void Session::release_run() {
    if (in_value_) { g_api->ReleaseValue(in_value_); in_value_ = nullptr; }
    for (auto* v : out_values_) if (v) g_api->ReleaseValue(v);
    std::fill(out_values_.begin(), out_values_.end(), nullptr);
}

bool Session::load(const std::wstring& model_path, int intra_threads, std::string& err) {
    try {
        OrtSessionOptions* opts = nullptr;
        check(g_api->CreateSessionOptions(&opts));
        check(g_api->SetIntraOpNumThreads(opts, intra_threads));
        check(g_api->SetSessionGraphOptimizationLevel(opts, ORT_ENABLE_ALL));
        check(g_api->CreateSession(g_env, model_path.c_str(), opts, &sess_));
        g_api->ReleaseSessionOptions(opts);

        OrtAllocator* alloc = nullptr;
        check(g_api->GetAllocatorWithDefaultOptions(&alloc));

        // 输入名 + 形状（取 H/W）。
        char* name = nullptr;
        check(g_api->SessionGetInputName(sess_, 0, alloc, &name));
        in_name_ = name;
        check(g_api->AllocatorFree(alloc, name));

        OrtTypeInfo* ti = nullptr;
        check(g_api->SessionGetInputTypeInfo(sess_, 0, &ti));
        const OrtTensorTypeAndShapeInfo* shape = nullptr;
        check(g_api->CastTypeInfoToTensorInfo(ti, &shape));
        size_t nd = 0;
        check(g_api->GetDimensionsCount(shape, &nd));
        if (nd == 4) {
            int64_t dims[4];
            check(g_api->GetDimensions(shape, dims, 4));
            if (dims[2] > 0 && dims[3] > 0)
                input_size_ = static_cast<int>(dims[2] < dims[3] ? dims[2] : dims[3]);
        }
        g_api->ReleaseTypeInfo(ti);

        // 输出名 + 形状。
        size_t n_out = 0;
        check(g_api->SessionGetOutputCount(sess_, &n_out));
        for (size_t i = 0; i < n_out; ++i) {
            check(g_api->SessionGetOutputName(sess_, i, alloc, &name));
            out_names_.emplace_back(name);
            check(g_api->AllocatorFree(alloc, name));

            check(g_api->SessionGetOutputTypeInfo(sess_, i, &ti));
            check(g_api->CastTypeInfoToTensorInfo(ti, &shape));
            check(g_api->GetDimensionsCount(shape, &nd));
            std::vector<int64_t> dims(nd);
            if (nd) check(g_api->GetDimensions(shape, dims.data(), nd));
            out_shapes_.push_back(std::move(dims));
            g_api->ReleaseTypeInfo(ti);
        }
        out_values_.assign(n_out, nullptr);

        check(g_api->CreateCpuMemoryInfo(OrtArenaAllocator, OrtMemTypeDefault, &mem_info_));
        return true;
    } catch (const std::exception& e) {
        err = e.what();
        return false;
    }
}

bool Session::run(const float* input, size_t input_floats,
                  std::vector<float*>& out_ptrs, std::string& err) {
    try {
        release_run();
        const int64_t s = input_size_;
        int64_t in_dims[4] = {1, 3, s, s};
        check(g_api->CreateTensorWithDataAsOrtValue(
            mem_info_, const_cast<float*>(input), input_floats * sizeof(float),
            in_dims, 4, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &in_value_));

        std::vector<const char*> in_names{in_name_.c_str()};
        std::vector<const char*> out_names;
        for (const auto& n : out_names_) out_names.push_back(n.c_str());

        check(g_api->Run(sess_, nullptr, in_names.data(), &in_value_, 1,
                         out_names.data(), out_names.size(), out_values_.data()));

        out_ptrs.resize(out_values_.size());
        for (size_t i = 0; i < out_values_.size(); ++i)
            check(g_api->GetTensorMutableData(out_values_[i],
                                              reinterpret_cast<void**>(&out_ptrs[i])));
        return true;
    } catch (const std::exception& e) {
        err = e.what();
        return false;
    }
}

}  // namespace ort