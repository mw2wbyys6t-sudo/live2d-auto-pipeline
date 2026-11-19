#pragma once
// BlendShape / 头部姿态 / Live2D 参数映射。
//
// 逐条移植自 Rust 版 native/face_tracker/src/{blendshapes,head_pose,live2d_mapping}.rs
// （它们本身又是 drivers/face_tracker/mediapipe_tracker.py 的移植），
// 保证 C++ 版与现有链路输出语义一致。

#include <map>
#include <string>
#include <vector>

struct Point3 {
    float x = 0, y = 0, z = 0;
};

struct HeadRotation {
    float pitch = 0, yaw = 0, roll = 0;
};

using Params = std::map<std::string, float>;

// ARKit 52 个 BlendShape 名称（全零初始化）。
Params zero_blendshapes();

// MediaPipe 468/478 点（归一化坐标）→ 52 BlendShape。
// points.size() >= 468 才计算；> 477 时启用虹膜注视。
Params compute_blendshapes_468(const std::vector<Point3>& points);

// 5 关键点（归一化）→ 基础 BlendShape（轻量回退）。
Params compute_blendshapes_5pt(Point3 left_eye, Point3 right_eye, Point3 nose_tip,
                               Point3 left_mouth, Point3 right_mouth);

// 468 点 → 头姿；点数不足返回 false。
bool estimate_head_rotation_468(const std::vector<Point3>& points, HeadRotation& out);

// 5 关键点 → 头姿。
HeadRotation estimate_head_rotation_5pt(Point3 left_eye, Point3 right_eye, Point3 nose_tip,
                                        Point3 left_mouth, Point3 right_mouth);

// BlendShape + 头姿 → Live2D Cubism 参数。
Params blendshapes_to_live2d(const Params& bs, const HeadRotation* head);

// EMA 平滑：alpha=1 全用旧值，alpha=0 全用新值。
Params smooth(const Params& current, const Params& previous, float alpha);

// 死区：接近默认值的小变化归零。
void apply_deadzone(Params& params, float threshold);