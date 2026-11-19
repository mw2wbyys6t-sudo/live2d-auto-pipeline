#pragma once
// ONNX Runtime 动态加载封装（纯 C API）。
//
// 边界：运行时只需要 exe 同目录的 onnxruntime.dll（1.30.x），
// 编译期不需要 import lib —— 通过 LoadLibrary + OrtGetApiBase 取 API 表。
// 头文件来自官方 NuGet 包 Microsoft.ML.OnnxRuntime 1.30.0（third_party/）。

#include <cstdint>
#include <string>
#include <vector>

#include "onnxruntime_c_api.h"

namespace ort {

// 加载 dll + 创建全局 Env。重复调用幂等；失败返回 false 并填 err。
bool init(std::string& err);
const OrtApi* api();

// 推理会话：方形 NCHW 单输入，float32。
class Session {
public:
    Session() = default;
    ~Session();
    Session(const Session&) = delete;
    Session& operator=(const Session&) = delete;

    bool load(const std::wstring& model_path, int intra_threads, std::string& err);
    bool ok() const { return sess_ != nullptr; }

    // 输入 H/W（从图读取；动态尺寸返回 0）
    int input_size() const { return input_size_; }
    const std::vector<std::string>& output_names() const { return out_names_; }
    const std::vector<std::vector<int64_t>>& output_shapes() const { return out_shapes_; }

    // 单 batch 推理。input 为 CHW float 数据（内部不拷贝，调用期间须保持有效）。
    // 成功后 out_ptrs[i] 指向第 i 个输出，有效期到下一次 run()。
    bool run(const float* input, size_t input_floats,
             std::vector<float*>& out_ptrs, std::string& err);

private:
    void release_run();

    OrtSession* sess_ = nullptr;
    OrtMemoryInfo* mem_info_ = nullptr;
    std::string in_name_;
    std::vector<std::string> out_names_;
    std::vector<std::vector<int64_t>> out_shapes_;
    int input_size_ = 0;
    OrtValue* in_value_ = nullptr;
    std::vector<OrtValue*> out_values_;
};

}  // namespace ort