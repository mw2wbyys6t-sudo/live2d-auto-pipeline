//! ARKit 52-BlendShape computation from facial landmarks.
//!
//! Ported from drivers/face_tracker/mediapipe_tracker.py — same geometric
//! heuristics, same reference scales, same clamp/normalize helpers.
//!
//! Works with any landmark model that outputs normalized (x, y, z) points.
//! The caller provides landmark indices appropriate for their model.

use std::collections::HashMap;

/// ARKit BlendShape 名称（52 个系数）
pub const ARKIT_BLENDSHAPE_NAMES: &[&str] = &[
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
];

/// 3D 关键点
pub type Point = (f32, f32, f32);

#[inline]
fn clamp01(v: f32) -> f32 { v.max(0.0).min(1.0) }

#[inline]
fn dist2d(a: Point, b: Point) -> f32 {
    let dx = a.0 - b.0;
    let dy = a.1 - b.1;
    (dx * dx + dy * dy).sqrt()
}

#[inline]
fn normalize(val: f32, reference: f32) -> f32 {
    if reference < 1e-9 { 0.0 } else { clamp01(val / reference) }
}

/// 从 MediaPipe 468 点（+ 10 虹膜点 = 478）计算 52 个 ARKit BlendShape。
///
/// 这是 Python `mediapipe_tracker.py::_compute_blendshapes` 的直接移植。
/// 所有关键点索引与 Python 版完全一致。
pub fn compute_blendshapes_mediapipe468(points: &[Point]) -> HashMap<String, f32> {
    let mut bs: HashMap<String, f32> = ARKIT_BLENDSHAPE_NAMES
        .iter()
        .map(|&n| (n.to_string(), 0.0))
        .collect();

    if points.len() < 468 {
        return bs;
    }

    let pt = |i: usize| -> Point { points[i] };

    // 参考尺度：外眼角间距 (landmark 33 ↔ 263)
    let eye_outer_l = pt(33);
    let eye_outer_r = pt(263);
    let ref_scale = dist2d(eye_outer_l, eye_outer_r);
    if ref_scale < 1e-6 {
        return bs;
    }

    // --- 眨眼 (159/145 左, 386/374 右) ---
    let left_eye_top = pt(159);
    let left_eye_bot = pt(145);
    let right_eye_top = pt(386);
    let right_eye_bot = pt(374);
    let left_open = dist2d(left_eye_top, left_eye_bot);
    let right_open = dist2d(right_eye_top, right_eye_bot);
    let eye_open_ref = ref_scale * 0.18;
    bs.insert("eyeBlinkLeft".into(), clamp01(1.0 - normalize(left_open, eye_open_ref)));
    bs.insert("eyeBlinkRight".into(), clamp01(1.0 - normalize(right_open, eye_open_ref)));

    // --- 眼球注视方向（虹膜 landmark 468-477）---
    if points.len() > 477 {
        let left_iris = pt(468);
        let right_iris = pt(473);
        let left_eye_center = pt(168);
        let right_eye_center = pt(362);
        let lx = (left_iris.0 - left_eye_center.0) / ref_scale;
        let rx = (right_iris.0 - right_eye_center.0) / ref_scale;
        let look_x = (lx + rx) * 5.0;
        if look_x > 0.0 {
            bs.insert("eyeLookOutLeft".into(), clamp01(look_x));
            bs.insert("eyeLookInRight".into(), clamp01(look_x));
        } else {
            bs.insert("eyeLookInLeft".into(), clamp01(-look_x));
            bs.insert("eyeLookOutRight".into(), clamp01(-look_x));
        }
        let ly = (left_iris.1 - left_eye_center.1) / ref_scale;
        if ly > 0.0 {
            bs.insert("eyeLookDownLeft".into(), clamp01(ly * 5.0));
            bs.insert("eyeLookDownRight".into(), clamp01(ly * 5.0));
        } else {
            bs.insert("eyeLookUpLeft".into(), clamp01(-ly * 5.0));
            bs.insert("eyeLookUpRight".into(), clamp01(-ly * 5.0));
        }
    }

    // --- 张嘴 (13 上唇, 14 下唇) ---
    let mouth_open = dist2d(pt(13), pt(14));
    let jaw_ref = ref_scale * 0.35;
    bs.insert("jawOpen".into(), normalize(mouth_open, jaw_ref));

    // --- 微笑 (61 左嘴角, 291 右嘴角) ---
    let mouth_center = pt(13);
    let left_corner = pt(61);
    let right_corner = pt(291);
    let left_smile = clamp01((mouth_center.1 - left_corner.1) / (ref_scale * 0.15));
    let right_smile = clamp01((mouth_center.1 - right_corner.1) / (ref_scale * 0.15));
    bs.insert("mouthSmileLeft".into(), left_smile);
    bs.insert("mouthSmileRight".into(), right_smile);

    // --- 嘴宽拉伸 ---
    let mouth_width = dist2d(left_corner, right_corner);
    let width_ref = ref_scale * 0.55;
    let stretch = clamp01((mouth_width - width_ref * 0.8) / (width_ref * 0.5));
    bs.insert("mouthStretchLeft".into(), stretch);
    bs.insert("mouthStretchRight".into(), stretch);

    // --- 嘴部缩拢/噘嘴 ---
    if mouth_open > ref_scale * 0.05 {
        if mouth_width < width_ref * 0.9 {
            bs.insert("mouthFunnel".into(), clamp01(1.0 - mouth_width / width_ref));
        } else {
            bs.insert("mouthPucker".into(), clamp01(1.0 - mouth_width / width_ref) * 0.5);
        }
    }

    // --- 眉毛 (105/334 内眉, 70/300 外眉) ---
    let left_brow_inner = pt(105);
    let right_brow_inner = pt(334);
    let left_brow_outer = pt(70);
    let right_brow_outer = pt(300);
    let left_eye_top_pt = pt(159);
    let right_eye_top_pt = pt(386);
    let brow_ref = ref_scale * 0.2;
    bs.insert("browInnerUp".into(), normalize(
        dist2d(left_brow_inner, left_eye_top_pt) + dist2d(right_brow_inner, right_eye_top_pt),
        brow_ref * 2.0,
    ));
    bs.insert("browOuterUpLeft".into(), normalize(dist2d(left_brow_outer, left_eye_top_pt), brow_ref));
    bs.insert("browOuterUpRight".into(), normalize(dist2d(right_brow_outer, right_eye_top_pt), brow_ref));

    let baseline_brow = ref_scale * 0.18;
    let left_brow_down = clamp01(1.0 - dist2d(left_brow_inner, left_eye_top_pt) / baseline_brow);
    let right_brow_down = clamp01(1.0 - dist2d(right_brow_inner, right_eye_top_pt) / baseline_brow);
    bs.insert("browDownLeft".into(), left_brow_down * 0.5);
    bs.insert("browDownRight".into(), right_brow_down * 0.5);

    // --- 下巴左右 (152 ↔ 1) ---
    let chin = pt(152);
    let nose_tip = pt(1);
    let jaw_offset = (chin.0 - nose_tip.0) / ref_scale;
    if jaw_offset > 0.0 {
        bs.insert("jawRight".into(), clamp01(jaw_offset * 3.0));
    } else {
        bs.insert("jawLeft".into(), clamp01(-jaw_offset * 3.0));
    }

    // --- 脸颊鼓气 (205/425) ---
    let left_cheek = pt(205);
    let right_cheek = pt(425);
    let cheek_width = dist2d(left_cheek, right_cheek);
    let cheek_ref = ref_scale * 1.3;
    bs.insert("cheekPuff".into(), normalize(cheek_width, cheek_ref));

    // --- 眯眼 (159↔233) ---
    let left_squint = dist2d(pt(159), pt(233));
    let right_squint = dist2d(pt(386), pt(463));
    let squint_ref = ref_scale * 0.25;
    bs.insert("eyeSquintLeft".into(), clamp01(1.0 - normalize(left_squint, squint_ref)) * 0.7);
    bs.insert("eyeSquintRight".into(), clamp01(1.0 - normalize(right_squint, squint_ref)) * 0.7);

    // --- 睁大眼睛 ---
    bs.insert("eyeWideLeft".into(), clamp01((left_open / eye_open_ref - 1.0) * 2.0));
    bs.insert("eyeWideRight".into(), clamp01((right_open / eye_open_ref - 1.0) * 2.0));

    // --- 鼻子皱缩 (97/326) ---
    let left_nose = pt(97);
    let right_nose = pt(326);
    let nose_ref = ref_scale * 0.15;
    bs.insert("noseSneerLeft".into(), normalize(dist2d(left_nose, pt(165)), nose_ref) * 0.5);
    bs.insert("noseSneerRight".into(), normalize(dist2d(right_nose, pt(391)), nose_ref) * 0.5);

    // --- 嘴角下垂 / 酒窝 ---
    let left_frown = clamp01((left_corner.1 - mouth_center.1) / (ref_scale * 0.1));
    let right_frown = clamp01((right_corner.1 - mouth_center.1) / (ref_scale * 0.1));
    bs.insert("mouthFrownLeft".into(), left_frown);
    bs.insert("mouthFrownRight".into(), right_frown);
    bs.insert("mouthDimpleLeft".into(), left_frown * 0.5);
    bs.insert("mouthDimpleRight".into(), right_frown * 0.5);

    // --- 抿嘴 ---
    let lip_dist = dist2d(pt(13), pt(14));
    if lip_dist < ref_scale * 0.02 {
        bs.insert("mouthPressLeft".into(), 0.5);
        bs.insert("mouthPressRight".into(), 0.5);
        bs.insert("mouthClose".into(), 0.5);
    }

    // --- 嘴部左右偏移 ---
    let mouth_mid_x = (left_corner.0 + right_corner.0) * 0.5;
    let asymmetry = (mouth_mid_x - nose_tip.0) / ref_scale;
    if asymmetry > 0.0 {
        bs.insert("mouthRight".into(), clamp01(asymmetry * 5.0));
    } else {
        bs.insert("mouthLeft".into(), clamp01(-asymmetry * 5.0));
    }

    // --- 上唇上提 / 下唇下降 ---
    let upper_up = (nose_tip.1 - pt(13).1) / ref_scale;
    let lower_down = (pt(14).1 - chin.1) / ref_scale;
    let upper_val = clamp01(upper_up * 8.0 - 0.5);
    let lower_val = clamp01(-lower_down * 8.0 + 0.5);
    bs.insert("mouthUpperUpLeft".into(), upper_val);
    bs.insert("mouthUpperUpRight".into(), upper_val);
    bs.insert("mouthLowerDownLeft".into(), lower_val);
    bs.insert("mouthLowerDownRight".into(), lower_val);

    // --- 下巴前伸 ---
    let jaw_fwd = chin.2 - nose_tip.2;
    bs.insert("jawForward".into(), clamp01(jaw_fwd * 10.0 + 0.5));

    bs
}

/// 从 5 关键点（YuNet 风格：双眼、鼻尖、双嘴角）计算基础 BlendShape。
///
/// 精度不如 468 点，但不需要完整关键点模型，适合轻量级部署。
pub fn compute_blendshapes_5pt(
    left_eye: Point,
    right_eye: Point,
    nose_tip: Point,
    left_mouth: Point,
    right_mouth: Point,
) -> HashMap<String, f32> {
    let mut bs: HashMap<String, f32> = ARKIT_BLENDSHAPE_NAMES
        .iter()
        .map(|&n| (n.to_string(), 0.0))
        .collect();

    let eye_dist = dist2d(left_eye, right_eye);
    if eye_dist < 1e-6 {
        return bs;
    }

    // 张嘴检测：嘴角到鼻尖距离 vs 嘴角间距
    let mouth_width = dist2d(left_mouth, right_mouth);
    let mouth_center = ((left_mouth.0 + right_mouth.0) * 0.5, (left_mouth.1 + right_mouth.1) * 0.5, 0.0);
    let nose_to_mouth = dist2d(nose_tip, mouth_center);
    let jaw_open = clamp01((nose_to_mouth / eye_dist - 0.5) * 4.0);
    bs.insert("jawOpen".into(), jaw_open);

    // 微笑：嘴角 y 是否高于中点
    let mouth_mid_y = (left_mouth.1 + right_mouth.1) * 0.5;
    let smile_left = clamp01((mouth_mid_y - left_mouth.1) / (eye_dist * 0.08));
    let smile_right = clamp01((mouth_mid_y - right_mouth.1) / (eye_dist * 0.08));
    bs.insert("mouthSmileLeft".into(), smile_left);
    bs.insert("mouthSmileRight".into(), smile_right);

    // 眨眼估算（近似：眼角到鼻尖距离的变化不能直接检测眨眼，
    // 这里用 5 点只能做粗估——后续加完整关键点模型后替换）
    bs.insert("eyeBlinkLeft".into(), 0.0);
    bs.insert("eyeBlinkRight".into(), 0.0);

    bs
}
