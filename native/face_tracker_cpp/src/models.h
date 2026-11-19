#pragma once
// 人脸检测（YuNet）+ 稠密关键点（MediaPipe 478 点 Face Landmarker）。
//
// YuNet 解码逐条移植自 native/face_tracker/src/onnx_detector.rs
// （对齐 OpenCV face_detect.cpp）。
// FaceMesh 的 ROI/预处理/逆映射对齐 yakhyo/mediapipe-face-mesh-onnx
// models/onnx_model.py（MediaPipe detection_to_roi 规则）。

#include <cstdint>
#include <string>
#include <vector>

#include "mapping.h"
#include "ort.h"

struct Face {
    float x = 0, y = 0, w = 0, h = 0;  // 原图像素坐标
    float confidence = 0;
    bool has_kps = false;
    // YuNet 顺序: [0]=右眼, [1]=左眼, [2]=鼻尖, [3]=右嘴角, [4]=左嘴角
    float kps[5][2] = {};
};

class YuNet {
public:
    bool load(const std::wstring& path, std::string& err);
    bool ok() const { return sess_.ok(); }
    // 返回按置信度降序、已 NMS 的人脸列表。
    std::vector<Face> detect(const uint8_t* rgb, int w, int h, float conf_threshold);

private:
    ort::Session sess_;
    int size_ = 0;
};

class FaceMesh {
public:
    bool load(const std::wstring& path, std::string& err);
    bool ok() const { return sess_.ok(); }
    int num_landmarks() const { return num_landmarks_; }

    // 对检测框做 ROI 裁剪 + 推理，输出归一化 (x/w, y/h, z/w) 关键点。
    // score < 0.5 或推理失败返回 false。
    bool infer(const uint8_t* rgb, int w, int h, const Face& det,
               std::vector<Point3>& points, float& score, std::string& err);

private:
    ort::Session sess_;
    int size_ = 0;             // 模型输入边长（256）
    int num_landmarks_ = 0;    // 478 / 468
    int lm_out_ = -1;          // landmarks 输出下标
    int score_out_ = -1;       // score 输出下标
};

// 双线性缩放（RGB，与 Rust 版 bilinear_resize 一致：round + clamp）。
void bilinear_resize_rgb(const uint8_t* src, int sw, int sh,
                         uint8_t* dst, int dw, int dh);