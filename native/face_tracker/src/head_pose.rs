//! Head pose estimation from facial landmarks.
//!
//! Ported from mediapipe_tracker.py::get_head_rotation — uses geometric
//! relationships between key landmarks to estimate pitch/yaw/roll.
//!
//! Unlike the Python version which uses cv2.solvePnP, this pure-Rust
//! implementation uses a lightweight orthogonal-projection approximation
//! that doesn't need camera calibration matrices.

use crate::blendshapes::Point;
use crate::protocol::HeadRotation;

#[inline]
fn degrees(r: f32) -> f32 {
    r * 180.0 / std::f32::consts::PI
}

/// 从 MediaPipe 468 点估算头部旋转 (pitch/yaw/roll)。
///
/// 使用关键面部特征点的几何关系：
/// - yaw: 鼻尖偏离面部中线的水平偏移
/// - pitch: 鼻子到下巴的垂直比例
/// - roll: 双眼连线的倾斜角
pub fn estimate_head_rotation_468(points: &[Point]) -> Option<HeadRotation> {
    if points.len() < 468 {
        return None;
    }

    let pt = |i: usize| -> Point { points[i] };

    let nose_tip = pt(1);       // 鼻尖
    let chin = pt(152);         // 下巴
    let forehead = pt(10);      // 额头
    let left_eye = pt(33);      // 左外眼角
    let right_eye = pt(263);    // 右外眼角
    let left_face = pt(234);    // 左脸颊
    let right_face = pt(454);   // 右脸颊

    // 面部宽度（左脸颊到右脸颊）
    let face_width = (right_face.0 - left_face.0).abs();
    if face_width < 1e-6 {
        return None;
    }

    // --- Roll: 双眼连线的倾斜角 ---
    let eye_dy = right_eye.1 - left_eye.1;
    let eye_dx = right_eye.0 - left_eye.0;
    let roll = degrees(eye_dy.atan2(eye_dx));

    // --- Yaw: 鼻尖偏离面部中线的水平偏移 ---
    // 面部中线 = 左右脸颊的中点
    let face_center_x = (left_face.0 + right_face.0) * 0.5;
    let nose_offset = (nose_tip.0 - face_center_x) / face_width;
    // 经验映射：归一化偏移 → 角度，clamp 到 ±30°
    let yaw = (nose_offset * 60.0).max(-30.0).min(30.0);

    // --- Pitch: 鼻到下巴距离 vs 额头到鼻距离的比例 ---
    // 头部前倾时，鼻尖到下巴的距离相对于额头到鼻尖的距离会变化
    let nose_to_chin = ((chin.0 - nose_tip.0).powi(2) + (chin.1 - nose_tip.1).powi(2)).sqrt();
    let forehead_to_nose = ((nose_tip.0 - forehead.0).powi(2) + (nose_tip.1 - forehead.1).powi(2)).sqrt();
    let total_height = nose_to_chin + forehead_to_nose;
    if total_height < 1e-6 {
        return None;
    }
    // 正常比例约 1.2（下巴到鼻 > 额头到鼻），前倾时减小
    let ratio = nose_to_chin / forehead_to_nose;
    let pitch = ((ratio - 1.2) * 50.0).max(-30.0).min(30.0);

    Some(HeadRotation { pitch, yaw, roll })
}

/// 从 5 关键点估算头部旋转。
pub fn estimate_head_rotation_5pt(
    left_eye: Point,
    right_eye: Point,
    nose_tip: Point,
    left_mouth: Point,
    right_mouth: Point,
) -> HeadRotation {
    // Roll: 双眼连线倾斜
    let eye_dy = right_eye.1 - left_eye.1;
    let eye_dx = right_eye.0 - left_eye.0;
    let roll = degrees(eye_dy.atan2(eye_dx));

    // Yaw: 鼻尖偏离双眼中点的水平偏移
    let eye_mid_x = (left_eye.0 + right_eye.0) * 0.5;
    let eye_dist = (eye_dx * eye_dx + eye_dy * eye_dy).sqrt();
    let nose_offset = (nose_tip.0 - eye_mid_x) / eye_dist.max(1e-6);
    let yaw = (nose_offset * 60.0).max(-30.0).min(30.0);

    // Pitch: 鼻尖偏离双眼中点的垂直偏移（正值=低头）
    let eye_mid_y = (left_eye.1 + right_eye.1) * 0.5;
    let mouth_mid_y = (left_mouth.1 + right_mouth.1) * 0.5;
    let eye_to_mouth = (mouth_mid_y - eye_mid_y).abs();
    let nose_offset_y = (nose_tip.1 - eye_mid_y) / eye_to_mouth.max(1e-6);
    // 正常鼻子在眼到嘴的约 40% 处
    let pitch = ((nose_offset_y - 0.4) * 60.0).max(-30.0).min(30.0);

    HeadRotation { pitch, yaw, roll }
}
