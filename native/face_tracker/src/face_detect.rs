//! 轻量级人脸检测 — 基于 YCbCr 肤色建模。
//!
//! 不依赖任何 ONNX 模型文件，纯像素级计算：
//! 1. RGB → YCbCr 颜色空间转换
//! 2. 肤色阈值分割（Cb/Cr 区间判定）
//! 3. 投影直方图确定面部边界框
//! 4. 从边界框估算五官大致位置（用于 BlendShape 计算）
//!
//! 精度远不如 MediaPipe 468 点模型，但零依赖、零模型文件，
//! 适合作为初版面捕管道。后续可替换为 ONNX 模型推理。

use crate::blendshapes::Point;

/// 检测到的人脸
pub struct FaceDetection {
    pub x: u32,
    pub y: u32,
    pub width: u32,
    pub height: u32,
    pub confidence: f32,
}

/// 从 RGB 帧中检测人脸（肤色法）
pub fn detect_face(rgb: &[u8], width: u32, height: u32) -> Option<FaceDetection> {
    if rgb.len() < (width * height * 3) as usize {
        return None;
    }

    // 肤色 mask
    let mut mask = vec![false; (width * height) as usize];
    let mut skin_count = 0usize;

    // 中央区域优先（略过边缘 10% 以减少噪声）
    let x_start = width / 10;
    let x_end = width - width / 10;
    let y_start = height / 10;
    let y_end = height - height / 10;

    let mut min_x = width;
    let mut max_x = 0u32;
    let mut min_y = height;
    let mut max_y = 0u32;

    for y in y_start..y_end {
        for x in x_start..x_end {
            let i = ((y * width + x) * 3) as usize;
            let r = rgb[i] as f32;
            let g = rgb[i + 1] as f32;
            let b = rgb[i + 2] as f32;

            // RGB → YCbCr (ITU-R BT.601)
            let cb = -0.168736 * r - 0.331264 * g + 0.5 * b + 128.0;
            let cr = 0.5 * r - 0.418688 * g - 0.081312 * b + 128.0;

            // 肤色阈值：多种光照条件下的经验值
            let is_skin = cb >= 77.0 && cb <= 130.0 && cr >= 135.0 && cr <= 175.0
                && r > 60.0 && g > 30.0; // 排除暗部噪声

            if is_skin {
                let idx = (y * width + x) as usize;
                mask[idx] = true;
                skin_count += 1;
                if x < min_x { min_x = x; }
                if x > max_x { max_x = x; }
                if y < min_y { min_y = y; }
                if y > max_y { max_y = y; }
            }
        }
    }

    // 肤色像素太少 → 无人脸
    let total_pixels = ((x_end - x_start) * (y_end - y_start)) as usize;
    let skin_ratio = skin_count as f32 / total_pixels as f32;
    if skin_count < 200 || skin_ratio < 0.02 || skin_ratio > 0.8 {
        return None;
    }

    let fw = max_x - min_x;
    let fh = max_y - min_y;
    if fw < 40 || fh < 40 {
        return None;
    }

    // 投影直方图精修边界（去除离散噪声）
    let (min_x, max_x, min_y, max_y) = refine_bbox(
        &mask, width, height, min_x, max_x, min_y, max_y,
    );

    let fw = max_x - min_x;
    let fh = max_y - min_y;
    if fw < 30 || fh < 30 {
        return None;
    }

    let confidence = (skin_ratio * 5.0).min(1.0);

    Some(FaceDetection {
        x: min_x,
        y: min_y,
        width: fw,
        height: fh,
        confidence,
    })
}

/// 用水平/垂直投影直方图精修边界框
fn refine_bbox(
    mask: &[bool],
    width: u32,
    height: u32,
    mut min_x: u32,
    mut max_x: u32,
    mut min_y: u32,
    mut max_y: u32,
) -> (u32, u32, u32, u32) {
    let threshold = 3u32; // 行/列至少 3 个肤色像素才算有效

    // 左边界
    while min_x < max_x {
        let col_count = (min_y..=max_y)
            .filter(|&y| mask[(y * width + min_x) as usize])
            .count() as u32;
        if col_count >= threshold { break; }
        min_x += 1;
    }
    // 右边界
    while max_x > min_x {
        let col_count = (min_y..=max_y)
            .filter(|&y| mask[(y * width + max_x) as usize])
            .count() as u32;
        if col_count >= threshold { break; }
        max_x -= 1;
    }
    // 上边界
    while min_y < max_y {
        let row_count = (min_x..=max_x)
            .filter(|&x| mask[(min_y * width + x) as usize])
            .count() as u32;
        if row_count >= threshold { break; }
        min_y += 1;
    }
    // 下边界
    while max_y > min_y {
        let row_count = (min_x..=max_x)
            .filter(|&x| mask[(max_y * width + x) as usize])
            .count() as u32;
        if row_count >= threshold { break; }
        max_y -= 1;
    }

    (min_x, max_x, min_y, max_y)
}

/// 从人脸边界框估算五官位置（像素坐标）。
///
/// 返回 5 个关键点（像素坐标，调用方需自行除以帧宽高做归一化），
/// 顺序与 YuNet 5-point 一致：
/// (左眼中心, 右眼中心, 鼻尖, 左嘴角, 右嘴角)
pub fn estimate_5pt_from_bbox(face: &FaceDetection) -> [Point; 5] {
    let fx = face.x as f32;
    let fy = face.y as f32;
    let fw = face.width.max(1) as f32;
    let fh = face.height.max(1) as f32;

    // 经验比例（面部五官在边界框中的典型位置）：
    // - 双眼 y ≈ 38% 面高
    // - 鼻尖 y ≈ 55%
    // - 嘴角 y ≈ 75%
    // - 左眼 x ≈ 32% 面宽, 右眼 x ≈ 68%
    // - 左嘴角 x ≈ 38%, 右嘴角 x ≈ 62%

    let eye_y = fy + fh * 0.38;
    let nose_y = fy + fh * 0.55;
    let mouth_y = fy + fh * 0.75;

    [
        (fx + fw * 0.32, eye_y, 0.0),    // 左眼
        (fx + fw * 0.68, eye_y, 0.0),    // 右眼
        (fx + fw * 0.50, nose_y, 0.0),   // 鼻尖
        (fx + fw * 0.38, mouth_y, 0.0),  // 左嘴角
        (fx + fw * 0.62, mouth_y, 0.0),  // 右嘴角
    ]
}
