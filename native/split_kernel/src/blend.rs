//! 图层混合：与 composer._alpha_over 的 float64 公式逐位一致。
//!
//! Python 参考（core/segment_engine/composer.py::_alpha_over）：
//! ```text
//! canvas = zeros((h, w, 4), float64)
//! for src in arrays:                      # uint8 RGBA
//!     alpha = src[..., 3:4] / 255.0
//!     canvas[..., :3] = src[..., :3] * alpha + canvas[..., :3] * (1 - alpha)
//!     canvas[..., 3]  = alpha * 255 + canvas[..., 3] * (1 - alpha)
//! return clip(canvas, 0, 255).astype(uint8)   # 向零截断
//! ```
//! 这里用 f64、同样的运算顺序，保证对拍逐字节相等（不是"近似"）。

/// 按绘制顺序叠加 RGBA 图层。`stack` 每项长度必须是 `px*4`。
pub fn alpha_over(stack: &[&[u8]], px: usize) -> Vec<u8> {
    let mut rgb = vec![0.0f64; px * 3];
    let mut a = vec![0.0f64; px];
    for src in stack {
        debug_assert_eq!(src.len(), px * 4);
        for i in 0..px {
            let s = &src[i * 4..i * 4 + 4];
            let alpha = s[3] as f64 / 255.0;
            let inv = 1.0 - alpha;
            let r = &mut rgb[i * 3..i * 3 + 3];
            r[0] = s[0] as f64 * alpha + r[0] * inv;
            r[1] = s[1] as f64 * alpha + r[1] * inv;
            r[2] = s[2] as f64 * alpha + r[2] * inv;
            a[i] = alpha * 255.0 + a[i] * inv;
        }
    }
    let mut out = vec![0u8; px * 4];
    for i in 0..px {
        out[i * 4] = clip_u8(rgb[i * 3]);
        out[i * 4 + 1] = clip_u8(rgb[i * 3 + 1]);
        out[i * 4 + 2] = clip_u8(rgb[i * 3 + 2]);
        out[i * 4 + 3] = clip_u8(a[i]);
    }
    out
}

/// clip(0,255) 后向零截断（等价 numpy astype(uint8) 对非负值的语义）。
#[inline]
fn clip_u8(v: f64) -> u8 {
    if v <= 0.0 {
        0
    } else if v >= 255.0 {
        255
    } else {
        v as u8
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn opaque_top_covers_bottom() {
        let bottom = vec![10u8, 20, 30, 255, 40, 50, 60, 255];
        let top = vec![200u8, 100, 50, 255, 0, 0, 0, 0];
        let out = alpha_over(&[&bottom, &top], 2);
        assert_eq!(&out[0..4], &[200, 100, 50, 255]);
        // 第二像素顶层全透明：保留底层
        assert_eq!(&out[4..8], &[40, 50, 60, 255]);
    }

    #[test]
    fn half_alpha_blends() {
        let bottom = vec![0u8, 0, 0, 255];
        let top = vec![255u8, 255, 255, 128];
        let out = alpha_over(&[&bottom, &top], 1);
        // alpha = 128/255 ≈ 0.50196；rgb = 255*alpha ≈ 128.0 -> 截断 128
        assert_eq!(out[0], 128);
        assert_eq!(out[3], 255);
    }

    #[test]
    fn empty_stack_is_callers_problem() {
        // FFI 层对空栈返回错误码；纯函数层面这里只保证不 panic。
        let out = alpha_over(&[], 1);
        assert_eq!(out, vec![0, 0, 0, 0]);
    }
}
