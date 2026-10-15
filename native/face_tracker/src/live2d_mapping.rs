//! Map ARKit BlendShapes + head rotation to Live2D Cubism parameters.
//!
//! Ported from drivers/face_tracker/blendshape_mapper.py — same mapping
//! table, same smoothing (EMA), same deadzone filter.

use crate::protocol::HeadRotation;
use std::collections::HashMap;

/// Live2D 参数范围: (min, max, default)
const PARAM_RANGES: &[(&str, f32, f32, f32)] = &[
    ("ParamAngleX",      -30.0, 30.0,  0.0),
    ("ParamAngleY",      -30.0, 30.0,  0.0),
    ("ParamAngleZ",      -30.0, 30.0,  0.0),
    ("ParamBodyAngleX",  -10.0, 10.0,  0.0),
    ("ParamBodyAngleY",  -10.0, 10.0,  0.0),
    ("ParamEyeLOpen",      0.0,  1.0,  1.0),
    ("ParamEyeROpen",      0.0,  1.0,  1.0),
    ("ParamEyeBallX",     -1.0,  1.0,  0.0),
    ("ParamEyeBallY",     -1.0,  1.0,  0.0),
    ("ParamMouthOpenY",    0.0,  1.0,  0.0),
    ("ParamMouthForm",    -1.0,  1.0,  0.0),
    ("ParamBrowLY",       -1.0,  1.0,  0.0),
    ("ParamBrowRY",       -1.0,  1.0,  0.0),
    ("ParamBreath",        0.0,  1.0,  0.5),
    ("ParamCheek",         0.0,  1.0,  0.0),
];

#[inline]
fn clamp(value: f32, lo: f32, hi: f32) -> f32 {
    value.max(lo).min(hi)
}

/// 将 ARKit BlendShapes + 头部旋转映射为 Live2D 参数。
pub fn blendshapes_to_live2d(
    bs: &HashMap<String, f32>,
    head: Option<&HeadRotation>,
) -> HashMap<String, f32> {
    let get = |k: &str| -> f32 { *bs.get(k).unwrap_or(&0.0) };
    let mut p = HashMap::new();

    // 眼睛开合 (blink → open: invert)
    p.insert("ParamEyeLOpen".into(), clamp(1.0 - get("eyeBlinkLeft"), 0.0, 1.0));
    p.insert("ParamEyeROpen".into(), clamp(1.0 - get("eyeBlinkRight"), 0.0, 1.0));

    // 嘴部
    p.insert("ParamMouthOpenY".into(), clamp(get("jawOpen"), 0.0, 1.0));
    let smile = (get("mouthSmileLeft") + get("mouthSmileRight")) * 0.5;
    let frown = (get("mouthFrownLeft") + get("mouthFrownRight")) * 0.5;
    p.insert("ParamMouthForm".into(), clamp(0.5 + (smile - frown) * 0.5, -1.0, 1.0));

    // 眉毛
    let brow_up_l = get("browInnerUp") * 0.5 + get("browOuterUpLeft") * 0.5;
    let brow_up_r = get("browInnerUp") * 0.5 + get("browOuterUpRight") * 0.5;
    let brow_dn_l = get("browDownLeft");
    let brow_dn_r = get("browDownRight");
    p.insert("ParamBrowLY".into(), clamp(0.5 + (brow_up_l - brow_dn_l) * 0.5, -1.0, 1.0));
    p.insert("ParamBrowRY".into(), clamp(0.5 + (brow_up_r - brow_dn_r) * 0.5, -1.0, 1.0));

    // 脸颊
    p.insert("ParamCheek".into(), clamp(get("cheekPuff"), 0.0, 1.0));

    // 眼球注视
    let eye_x = (get("eyeLookOutLeft") - get("eyeLookInLeft")
               + get("eyeLookInRight") - get("eyeLookOutRight")) * 0.5;
    let eye_y = (get("eyeLookUpLeft") + get("eyeLookUpRight")
              - get("eyeLookDownLeft") - get("eyeLookDownRight")) * 0.5;
    p.insert("ParamEyeBallX".into(), clamp(eye_x, -1.0, 1.0));
    p.insert("ParamEyeBallY".into(), clamp(eye_y, -1.0, 1.0));

    // 头部旋转
    if let Some(h) = head {
        p.insert("ParamAngleX".into(), clamp(h.pitch, -30.0, 30.0));
        p.insert("ParamAngleY".into(), clamp(h.yaw, -30.0, 30.0));
        p.insert("ParamAngleZ".into(), clamp(h.roll, -30.0, 30.0));
    } else {
        p.insert("ParamAngleX".into(), 0.0);
        p.insert("ParamAngleY".into(), 0.0);
        p.insert("ParamAngleZ".into(), 0.0);
    }

    // 身体角度（跟随头部）
    p.insert("ParamBodyAngleX".into(), *p.get("ParamAngleY").unwrap_or(&0.0) * 0.3);
    p.insert("ParamBodyAngleY".into(), *p.get("ParamAngleX").unwrap_or(&0.0) * 0.2);

    // 呼吸默认值
    p.insert("ParamBreath".into(), 0.5);

    p
}

/// 指数平滑（EMA）: alpha=1 → 完全用旧值, alpha=0 → 完全用新值
pub fn smooth(current: &HashMap<String, f32>, previous: &HashMap<String, f32>, alpha: f32) -> HashMap<String, f32> {
    let alpha = alpha.max(0.0).min(1.0);
    let keys: std::collections::HashSet<_> = current.keys().chain(previous.keys()).collect();
    let mut result = HashMap::with_capacity(keys.len());
    for key in keys {
        let cur = current.get(key);
        let prev = previous.get(key);
        let val = match (cur, prev) {
            (Some(c), Some(p)) => p * alpha + c * (1.0 - alpha),
            (Some(c), None) => *c,
            (None, Some(p)) => *p,
            (None, None) => 0.0,
        };
        result.insert(key.clone(), val);
    }
    result
}

/// 死区过滤：接近默认值的小变化归零，防止微抖
pub fn apply_deadzone(params: &mut HashMap<String, f32>, threshold: f32) {
    for &(name, lo, hi, default) in PARAM_RANGES {
        if let Some(val) = params.get(name) {
            let span = if hi != lo { hi - lo } else { 1.0 };
            let norm = (val - default) / span * 2.0;
            if norm.abs() < threshold {
                params.insert(name.to_string(), default);
            }
        }
    }
}
