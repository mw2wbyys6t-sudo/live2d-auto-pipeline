#pragma once
// Media Foundation 摄像头采集（IMFSourceReader，同步 ReadSample）。
//
// 输出统一为 RGB24 紧密排列。设备枚举/打开/关闭都在调用线程（主线程）完成，
// COM 以 MTA 初始化一次。stop 时显式 Shutdown() 媒体源释放硬件，
// 避免 Rust 版 nokhwa 的「stop 后再 open 失败」问题。

#include <cstdint>
#include <string>
#include <vector>

struct RgbFrame {
    int width = 0;
    int height = 0;
    std::vector<uint8_t> rgb;  // h*w*3
};

class Camera {
public:
    // 进程生命周期内初始化一次 COM + MF。
    static bool startup(std::string& err);
    static int count_devices();

    ~Camera();
    // 打开第 id 个设备，请求 w×h（驱动可能给最接近的分辨率）。
    // 成功返回 true，actual_w/h 为实际分辨率。
    bool open(int id, int req_w, int req_h, int& actual_w, int& actual_h, std::string& err);
    void close();
    bool is_open() const { return reader_ != nullptr; }

    // 阻塞取一帧并转 RGB。无样本（tick/格式变更）返回 false，调用方重试。
    bool capture(RgbFrame& out, std::string& err);

private:
    bool query_format();

    struct IMFSourceReader* reader_ = nullptr;
    struct IMFMediaSource* source_ = nullptr;
    int w_ = 0, h_ = 0;
    int stride_ = 0;  // 有符号；负值 = 自底向上
};