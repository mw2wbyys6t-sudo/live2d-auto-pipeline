#include "mapping.h"

#include <algorithm>
#include <cmath>

// ─────────────────── 基础工具 ───────────────────

static const char* ARKIT_NAMES[] = {
    "browDownLeft", "browDownRight", "browInnerUp",
    "browOuterUpLeft", "browOuterUpRight",
    "cheekPuff", "cheekSquintLeft", "cheekSquintRight",
    "eyeBlinkLeft", "eyeBlinkRight", "eyeLookDownLeft", "eyeLookDownRight",
    "eyeLookInLeft", "eyeLookInRight", "eyeLookOutLeft", "eyeLookOutRight",
    "eyeLookUpLeft", "eyeLookUpRight",
    "eyeSquintLeft", "eyeSquintRight", "eyeWideLeft", "eyeWideRight",
    "jawForward", "jawLeft", "jawRight", "jawOpen",
    "mouthClose", "mouthDimpleLeft", "mouthDimpleRight",
    "mouthFrownLeft", "mouthFrownRight", "mouthFunnel",
    "mouthLeft", "mouthRight",
    "mouthLowerDownLeft", "mouthLowerDownRight",
    "mouthPressLeft", "mouthPressRight",
    "mouthPucker", "mouthRollLower", "mouthRollUpper",
    "mouthShrugLower", "mouthShrugUpper",
    "mouthSmileLeft", "mouthSmileRight",
    "mouthStretchLeft", "mouthStretchRight",
    "mouthUpperUpLeft", "mouthUpperUpRight",
    "noseSneerLeft", "noseSneerRight",
    "tongueOut",
};

static inline float clampf(float v, float lo, float hi) {
    return std::max(lo, std::min(hi, v));
}
static inline float clamp01(float v) { return clampf(v, 0.0f, 1.0f); }

static inline float dist2d(Point3 a, Point3 b) {
    float dx = a.x - b.x, dy = a.y - b.y;
    return std::sqrt(dx * dx + dy * dy);
}

static inline float normalize(float val, float ref) {
    return ref < 1e-9f ? 0.0f : clamp01(val / ref);
}

static inline float degrees(float r) { return r * 180.0f / 3.14159265358979323846f; }

Params zero_blendshapes() {
    Params bs;
    for (const char* n : ARKIT_NAMES) bs[n] = 0.0f;
    return bs;
}

static inline float get(const Params& bs, const char* k) {
    auto it = bs.find(k);
    return it == bs.end() ? 0.0f : it->second;
}

// ─────────────────── 468/478 点 BlendShape ───────────────────

Params compute_blendshapes_468(const std::vector<Point3>& points) {
    Params bs = zero_blendshapes();
    if (points.size() < 468) return bs;
    auto pt = [&](size_t i) { return points[i]; };

    // 参考尺度：外眼角间距 (33 ↔ 263)
    float ref_scale = dist2d(pt(33), pt(263));
    if (ref_scale < 1e-6f) return bs;

    // 眨眼 (159/145 左, 386/374 右)
    float left_open = dist2d(pt(159), pt(145));
    float right_open = dist2d(pt(386), pt(374));
    float eye_open_ref = ref_scale * 0.18f;
    bs["eyeBlinkLeft"] = clamp01(1.0f - normalize(left_open, eye_open_ref));
    bs["eyeBlinkRight"] = clamp01(1.0f - normalize(right_open, eye_open_ref));

    // 眼球注视（虹膜 468-477）
    if (points.size() > 477) {
        Point3 left_iris = pt(468), right_iris = pt(473);
        Point3 left_eye_center = pt(168), right_eye_center = pt(362);
        float lx = (left_iris.x - left_eye_center.x) / ref_scale;
        float rx = (right_iris.x - right_eye_center.x) / ref_scale;
        float look_x = (lx + rx) * 5.0f;
        if (look_x > 0.0f) {
            bs["eyeLookOutLeft"] = clamp01(look_x);
            bs["eyeLookInRight"] = clamp01(look_x);
        } else {
            bs["eyeLookInLeft"] = clamp01(-look_x);
            bs["eyeLookOutRight"] = clamp01(-look_x);
        }
        float ly = (left_iris.y - left_eye_center.y) / ref_scale;
        if (ly > 0.0f) {
            bs["eyeLookDownLeft"] = clamp01(ly * 5.0f);
            bs["eyeLookDownRight"] = clamp01(ly * 5.0f);
        } else {
            bs["eyeLookUpLeft"] = clamp01(-ly * 5.0f);
            bs["eyeLookUpRight"] = clamp01(-ly * 5.0f);
        }
    }

    // 张嘴 (13 上唇, 14 下唇)
    float mouth_open = dist2d(pt(13), pt(14));
    bs["jawOpen"] = normalize(mouth_open, ref_scale * 0.35f);

    // 微笑 (61 左嘴角, 291 右嘴角)
    Point3 mouth_center = pt(13);
    Point3 left_corner = pt(61), right_corner = pt(291);
    bs["mouthSmileLeft"] = clamp01((mouth_center.y - left_corner.y) / (ref_scale * 0.15f));
    bs["mouthSmileRight"] = clamp01((mouth_center.y - right_corner.y) / (ref_scale * 0.15f));

    // 嘴宽拉伸
    float mouth_width = dist2d(left_corner, right_corner);
    float width_ref = ref_scale * 0.55f;
    float stretch = clamp01((mouth_width - width_ref * 0.8f) / (width_ref * 0.5f));
    bs["mouthStretchLeft"] = stretch;
    bs["mouthStretchRight"] = stretch;

    // 缩拢/噘嘴
    if (mouth_open > ref_scale * 0.05f) {
        if (mouth_width < width_ref * 0.9f)
            bs["mouthFunnel"] = clamp01(1.0f - mouth_width / width_ref);
        else
            bs["mouthPucker"] = clamp01(1.0f - mouth_width / width_ref) * 0.5f;
    }

    // 眉毛 (105/334 内眉, 70/300 外眉)
    Point3 lb_in = pt(105), rb_in = pt(334), lb_out = pt(70), rb_out = pt(300);
    Point3 le_top = pt(159), re_top = pt(386);
    float brow_ref = ref_scale * 0.2f;
    bs["browInnerUp"] = normalize(dist2d(lb_in, le_top) + dist2d(rb_in, re_top), brow_ref * 2.0f);
    bs["browOuterUpLeft"] = normalize(dist2d(lb_out, le_top), brow_ref);
    bs["browOuterUpRight"] = normalize(dist2d(rb_out, re_top), brow_ref);
    float baseline_brow = ref_scale * 0.18f;
    bs["browDownLeft"] = clamp01(1.0f - dist2d(lb_in, le_top) / baseline_brow) * 0.5f;
    bs["browDownRight"] = clamp01(1.0f - dist2d(rb_in, re_top) / baseline_brow) * 0.5f;

    // 下巴左右 (152 ↔ 1)
    Point3 chin = pt(152), nose_tip = pt(1);
    float jaw_offset = (chin.x - nose_tip.x) / ref_scale;
    if (jaw_offset > 0.0f)
        bs["jawRight"] = clamp01(jaw_offset * 3.0f);
    else
        bs["jawLeft"] = clamp01(-jaw_offset * 3.0f);

    // 脸颊鼓气 (205/425)
    bs["cheekPuff"] = normalize(dist2d(pt(205), pt(425)), ref_scale * 1.3f);

    // 眯眼 (159↔233, 386↔463)
    float squint_ref = ref_scale * 0.25f;
    bs["eyeSquintLeft"] = clamp01(1.0f - normalize(dist2d(pt(159), pt(233)), squint_ref)) * 0.7f;
    bs["eyeSquintRight"] = clamp01(1.0f - normalize(dist2d(pt(386), pt(463)), squint_ref)) * 0.7f;

    // 睁大眼睛
    bs["eyeWideLeft"] = clamp01((left_open / eye_open_ref - 1.0f) * 2.0f);
    bs["eyeWideRight"] = clamp01((right_open / eye_open_ref - 1.0f) * 2.0f);

    // 鼻子皱缩 (97/326)
    float nose_ref = ref_scale * 0.15f;
    bs["noseSneerLeft"] = normalize(dist2d(pt(97), pt(165)), nose_ref) * 0.5f;
    bs["noseSneerRight"] = normalize(dist2d(pt(326), pt(391)), nose_ref) * 0.5f;

    // 嘴角下垂 / 酒窝
    float left_frown = clamp01((left_corner.y - mouth_center.y) / (ref_scale * 0.1f));
    float right_frown = clamp01((right_corner.y - mouth_center.y) / (ref_scale * 0.1f));
    bs["mouthFrownLeft"] = left_frown;
    bs["mouthFrownRight"] = right_frown;
    bs["mouthDimpleLeft"] = left_frown * 0.5f;
    bs["mouthDimpleRight"] = right_frown * 0.5f;

    // 抿嘴
    if (dist2d(pt(13), pt(14)) < ref_scale * 0.02f) {
        bs["mouthPressLeft"] = 0.5f;
        bs["mouthPressRight"] = 0.5f;
        bs["mouthClose"] = 0.5f;
    }

    // 嘴部左右偏移
    float mouth_mid_x = (left_corner.x + right_corner.x) * 0.5f;
    float asymmetry = (mouth_mid_x - nose_tip.x) / ref_scale;
    if (asymmetry > 0.0f)
        bs["mouthRight"] = clamp01(asymmetry * 5.0f);
    else
        bs["mouthLeft"] = clamp01(-asymmetry * 5.0f);

    // 上唇上提 / 下唇下降
    float upper_up = (nose_tip.y - pt(13).y) / ref_scale;
    float lower_down = (pt(14).y - chin.y) / ref_scale;
    float upper_val = clamp01(upper_up * 8.0f - 0.5f);
    float lower_val = clamp01(-lower_down * 8.0f + 0.5f);
    bs["mouthUpperUpLeft"] = upper_val;
    bs["mouthUpperUpRight"] = upper_val;
    bs["mouthLowerDownLeft"] = lower_val;
    bs["mouthLowerDownRight"] = lower_val;

    // 下巴前伸
    bs["jawForward"] = clamp01((chin.z - nose_tip.z) * 10.0f + 0.5f);

    return bs;
}

// ─────────────────── 5 点 BlendShape（回退） ───────────────────

Params compute_blendshapes_5pt(Point3 left_eye, Point3 right_eye, Point3 nose_tip,
                               Point3 left_mouth, Point3 right_mouth) {
    Params bs = zero_blendshapes();
    float eye_dist = dist2d(left_eye, right_eye);
    if (eye_dist < 1e-6f) return bs;

    float mouth_width = dist2d(left_mouth, right_mouth);
    Point3 mouth_center{(left_mouth.x + right_mouth.x) * 0.5f,
                        (left_mouth.y + right_mouth.y) * 0.5f, 0.0f};
    float nose_to_mouth = dist2d(nose_tip, mouth_center);
    bs["jawOpen"] = clamp01((nose_to_mouth / eye_dist - 0.5f) * 4.0f);

    float mouth_mid_y = (left_mouth.y + right_mouth.y) * 0.5f;
    bs["mouthSmileLeft"] = clamp01((mouth_mid_y - left_mouth.y) / (eye_dist * 0.08f));
    bs["mouthSmileRight"] = clamp01((mouth_mid_y - right_mouth.y) / (eye_dist * 0.08f));

    // 5 点无法检测眨眼，显式置 0（与 Rust 版一致）
    bs["eyeBlinkLeft"] = 0.0f;
    bs["eyeBlinkRight"] = 0.0f;
    (void)mouth_width;
    return bs;
}

// ─────────────────── 头部姿态 ───────────────────

bool estimate_head_rotation_468(const std::vector<Point3>& points, HeadRotation& out) {
    if (points.size() < 468) return false;
    auto pt = [&](size_t i) { return points[i]; };

    Point3 nose_tip = pt(1), chin = pt(152), forehead = pt(10);
    Point3 left_eye = pt(33), right_eye = pt(263);
    Point3 left_face = pt(234), right_face = pt(454);

    float face_width = std::abs(right_face.x - left_face.x);
    if (face_width < 1e-6f) return false;

    // Roll: 双眼连线倾斜
    out.roll = degrees(std::atan2(right_eye.y - left_eye.y, right_eye.x - left_eye.x));

    // Yaw: 鼻尖偏离面部中线
    float face_center_x = (left_face.x + right_face.x) * 0.5f;
    float nose_offset = (nose_tip.x - face_center_x) / face_width;
    out.yaw = clampf(nose_offset * 60.0f, -30.0f, 30.0f);

    // Pitch: 鼻-下巴 vs 额头-鼻 距离比例
    float dcx = chin.x - nose_tip.x, dcy = chin.y - nose_tip.y;
    float dfx = nose_tip.x - forehead.x, dfy = nose_tip.y - forehead.y;
    float nose_to_chin = std::sqrt(dcx * dcx + dcy * dcy);
    float forehead_to_nose = std::sqrt(dfx * dfx + dfy * dfy);
    float total_height = nose_to_chin + forehead_to_nose;
    if (total_height < 1e-6f) return false;
    float ratio = nose_to_chin / forehead_to_nose;
    out.pitch = clampf((ratio - 1.2f) * 50.0f, -30.0f, 30.0f);
    return true;
}

HeadRotation estimate_head_rotation_5pt(Point3 left_eye, Point3 right_eye, Point3 nose_tip,
                                        Point3 left_mouth, Point3 right_mouth) {
    HeadRotation out;
    float eye_dy = right_eye.y - left_eye.y;
    float eye_dx = right_eye.x - left_eye.x;
    out.roll = degrees(std::atan2(eye_dy, eye_dx));

    float eye_mid_x = (left_eye.x + right_eye.x) * 0.5f;
    float eye_dist = std::sqrt(eye_dx * eye_dx + eye_dy * eye_dy);
    float nose_offset = (nose_tip.x - eye_mid_x) / std::max(eye_dist, 1e-6f);
    out.yaw = clampf(nose_offset * 60.0f, -30.0f, 30.0f);

    float eye_mid_y = (left_eye.y + right_eye.y) * 0.5f;
    float mouth_mid_y = (left_mouth.y + right_mouth.y) * 0.5f;
    float eye_to_mouth = std::abs(mouth_mid_y - eye_mid_y);
    float nose_offset_y = (nose_tip.y - eye_mid_y) / std::max(eye_to_mouth, 1e-6f);
    out.pitch = clampf((nose_offset_y - 0.4f) * 60.0f, -30.0f, 30.0f);
    return out;
}

// ─────────────────── Live2D 映射 ───────────────────

// (min, max, default) —— 与 live2d_mapping.rs PARAM_RANGES 一致
struct ParamRange { const char* name; float lo, hi, def; };
static const ParamRange PARAM_RANGES[] = {
    {"ParamAngleX",      -30.0f, 30.0f, 0.0f},
    {"ParamAngleY",      -30.0f, 30.0f, 0.0f},
    {"ParamAngleZ",      -30.0f, 30.0f, 0.0f},
    {"ParamBodyAngleX",  -10.0f, 10.0f, 0.0f},
    {"ParamBodyAngleY",  -10.0f, 10.0f, 0.0f},
    {"ParamEyeLOpen",      0.0f,  1.0f, 1.0f},
    {"ParamEyeROpen",      0.0f,  1.0f, 1.0f},
    {"ParamEyeBallX",     -1.0f,  1.0f, 0.0f},
    {"ParamEyeBallY",     -1.0f,  1.0f, 0.0f},
    {"ParamMouthOpenY",    0.0f,  1.0f, 0.0f},
    {"ParamMouthForm",    -1.0f,  1.0f, 0.0f},
    {"ParamBrowLY",       -1.0f,  1.0f, 0.0f},
    {"ParamBrowRY",       -1.0f,  1.0f, 0.0f},
    {"ParamBreath",        0.0f,  1.0f, 0.5f},
    {"ParamCheek",         0.0f,  1.0f, 0.0f},
};

Params blendshapes_to_live2d(const Params& bs, const HeadRotation* head) {
    Params p;
    auto g = [&](const char* k) { return get(bs, k); };

    p["ParamEyeLOpen"] = clamp01(1.0f - g("eyeBlinkLeft"));
    p["ParamEyeROpen"] = clamp01(1.0f - g("eyeBlinkRight"));

    p["ParamMouthOpenY"] = clamp01(g("jawOpen"));
    float smile = (g("mouthSmileLeft") + g("mouthSmileRight")) * 0.5f;
    float frown = (g("mouthFrownLeft") + g("mouthFrownRight")) * 0.5f;
    p["ParamMouthForm"] = clampf(0.5f + (smile - frown) * 0.5f, -1.0f, 1.0f);

    float brow_up_l = g("browInnerUp") * 0.5f + g("browOuterUpLeft") * 0.5f;
    float brow_up_r = g("browInnerUp") * 0.5f + g("browOuterUpRight") * 0.5f;
    p["ParamBrowLY"] = clampf(0.5f + (brow_up_l - g("browDownLeft")) * 0.5f, -1.0f, 1.0f);
    p["ParamBrowRY"] = clampf(0.5f + (brow_up_r - g("browDownRight")) * 0.5f, -1.0f, 1.0f);

    p["ParamCheek"] = clamp01(g("cheekPuff"));

    float eye_x = (g("eyeLookOutLeft") - g("eyeLookInLeft")
                 + g("eyeLookInRight") - g("eyeLookOutRight")) * 0.5f;
    float eye_y = (g("eyeLookUpLeft") + g("eyeLookUpRight")
                 - g("eyeLookDownLeft") - g("eyeLookDownRight")) * 0.5f;
    p["ParamEyeBallX"] = clampf(eye_x, -1.0f, 1.0f);
    p["ParamEyeBallY"] = clampf(eye_y, -1.0f, 1.0f);

    if (head) {
        p["ParamAngleX"] = clampf(head->pitch, -30.0f, 30.0f);
        p["ParamAngleY"] = clampf(head->yaw, -30.0f, 30.0f);
        p["ParamAngleZ"] = clampf(head->roll, -30.0f, 30.0f);
    } else {
        p["ParamAngleX"] = 0.0f;
        p["ParamAngleY"] = 0.0f;
        p["ParamAngleZ"] = 0.0f;
    }

    p["ParamBodyAngleX"] = p["ParamAngleY"] * 0.3f;
    p["ParamBodyAngleY"] = p["ParamAngleX"] * 0.2f;
    p["ParamBreath"] = 0.5f;
    return p;
}

Params smooth(const Params& current, const Params& previous, float alpha) {
    alpha = clamp01(alpha);
    Params result;
    for (const auto& kv : current) {
        auto it = previous.find(kv.first);
        result[kv.first] = it == previous.end()
            ? kv.second
            : it->second * alpha + kv.second * (1.0f - alpha);
    }
    for (const auto& kv : previous) {
        if (current.find(kv.first) == current.end())
            result[kv.first] = kv.second;
    }
    return result;
}

void apply_deadzone(Params& params, float threshold) {
    for (const auto& r : PARAM_RANGES) {
        auto it = params.find(r.name);
        if (it == params.end()) continue;
        float span = r.hi != r.lo ? r.hi - r.lo : 1.0f;
        float norm = (it->second - r.def) / span * 2.0f;
        if (std::abs(norm) < threshold) it->second = r.def;
    }
}