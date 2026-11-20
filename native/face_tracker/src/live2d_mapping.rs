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

    // 头部旋转（Live2D 约定：X=左右转 yaw、Y=上下抬 -pitch、Z=歪头 roll，
    // 与 api_server.py 主链路一致；此前 X/Y 互换会让转头变成点头）
    if let Some(h) = head {
        p.insert("ParamAngleX".into(), clamp(h.yaw, -30.0, 30.0));
        p.insert("ParamAngleY".into(), clamp(-h.pitch, -30.0, 30.0));
        p.insert("ParamAngleZ".into(), clamp(h.roll, -30.0, 30.0));
    } else {
        p.insert("ParamAngleX".into(), 0.0);
        p.insert("ParamAngleY".into(), 0.0);
        p.insert("ParamAngleZ".into(), 0.0);
    }

    // 身体角度（跟随头部）
    p.insert("ParamBodyAngleX".into(), *p.get("ParamAngleX").unwrap_or(&0.0) * 0.3);
    p.insert("ParamBodyAngleY".into(), *p.get("ParamAngleY").unwrap_or(&0.0) * 0.2);

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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::HeadRotation;

    /// Live2D 约定：AngleX=左右转(yaw)、AngleY=上下抬(-pitch)。
    /// 早期 X/Y 互换会让转头变成点头，这条断言锁住语义。
    #[test]
    fn head_angles_map_to_correct_live2d_axes() {
        let bs = HashMap::new();
        let head = HeadRotation { pitch: 10.0, yaw: 20.0, roll: 5.0 };
        let p = blendshapes_to_live2d(&bs, Some(&head));
        assert!((p["ParamAngleX"] - 20.0).abs() < 1e-6, "AngleX 应取 yaw");
        assert!((p["ParamAngleY"] + 10.0).abs() < 1e-6, "AngleY 应取 -pitch");
        assert!((p["ParamAngleZ"] - 5.0).abs() < 1e-6, "AngleZ 应取 roll");
        assert!((p["ParamBodyAngleX"] - 6.0).abs() < 1e-6);
        assert!((p["ParamBodyAngleY"] + 2.0).abs() < 1e-6);
    }

    #[test]
    fn missing_head_falls_back_to_neutral() {
        let p = blendshapes_to_live2d(&HashMap::new(), None);
        assert_eq!(p["ParamAngleX"], 0.0);
        assert_eq!(p["ParamAngleY"], 0.0);
        assert_eq!(p["ParamAngleZ"], 0.0);
    }

    #[test]
    fn precomputed_pitch_ratio_is_not_saturated() {
        // 真实正面人脸实测 ratio≈0.56~0.68，旧公式写死 1.2 会饱和到 -30°
        let pitch = crate::head_pose::pitch_from_ratio(0.62, crate::head_pose::PITCH_RATIO_NEUTRAL);
        assert!(pitch.abs() < 1.0, "中性比值应接近 0°，实际 {pitch}");
        let up = crate::head_pose::pitch_from_ratio(0.75, crate::head_pose::PITCH_RATIO_NEUTRAL);
        assert!(up > 5.0 && up < 30.0, "抬头应给出正且未饱和的角度，实际 {up}");
    }
}
