//! split_kernel — Live2D 拆分/合成加速内核
//!
//! 边界（与 Python 流水线的分工，见 docs/staging 设计记录）：
//! * 本 crate 只实现**确定性纯函数**：输入数组 -> 输出数组，无 IO、无模型推理、
//!   无全局状态。Python 侧保留全部编排、启发式与 SAM2/ISNet 推理。
//! * 每个导出函数都必须与 Python 参考实现**逐字节对拍**
//!   （tests/unit/test_native_kernel.py），对拍不过就不接线。
//! * Python 通过 ctypes 加载本 DLL；加载失败/环境变量关闭时自动回退原实现。
//!
//! 首批热点（2026-11-18 cProfile 实测，1024 立绘）：
//! * alpha_over：composer._alpha_over，compose() 7.1s 中占 2.0s；
//!   4K 画布像素数 ×16，收益随分辨率放大。
//!
//! ABI 约定：
//! * 输入 RGBA 均为 u8、行优先、紧密排列（h*w*4 字节）；
//! * 输出由内核分配，调用方必须用 sk_free 释放；
//! * 返回 0 = 成功，非 0 = 错误码（参数非法/空输入）。

pub mod blend;

use std::slice;

/// 版本探针：Python 加载器用它确认 DLL 与期望的 ABI 匹配。
pub const ABI_VERSION: u32 = 1;

#[no_mangle]
pub extern "C" fn sk_abi_version() -> u32 {
    ABI_VERSION
}

/// 释放内核分配的输出缓冲。只允许传回本 DLL 分配的指针。
///
/// # Safety
/// `ptr` 必须是本 DLL 某次成功调用返回的 out 指针，`len` 必须与当时一致。
#[no_mangle]
pub unsafe extern "C" fn sk_free(ptr: *mut u8, len: usize) {
    if ptr.is_null() || len == 0 {
        return;
    }
    drop(Vec::from_raw_parts(ptr, len, len));
}

/// 把若干 RGBA 图层按绘制顺序叠成一张（source-over，与
/// composer._alpha_over 的 float64 公式逐位一致）。
///
/// # Safety
/// `layers` 指向 `n` 个指针，每个指向 `h*w*4` 字节的 RGBA 数据；
/// 允许个别指针为空（跳过该层，与 Python 的 None 项一致）。
/// 成功时 `*out` 指向新分配的 `h*w*4` 字节，调用方负责 sk_free。
#[no_mangle]
pub unsafe extern "C" fn sk_alpha_over(
    layers: *const *const u8,
    n: usize,
    h: usize,
    w: usize,
    out: *mut *mut u8,
) -> i32 {
    if layers.is_null() || out.is_null() || h == 0 || w == 0 {
        return 1;
    }
    let px = match h.checked_mul(w) {
        Some(v) => v,
        None => return 1,
    };
    let bytes = match px.checked_mul(4) {
        Some(v) => v,
        None => return 1,
    };
    let ptrs = slice::from_raw_parts(layers, n);
    let mut stack: Vec<&[u8]> = Vec::with_capacity(n);
    for &p in ptrs {
        if !p.is_null() {
            stack.push(slice::from_raw_parts(p, bytes));
        }
    }
    if stack.is_empty() {
        return 2; // 与 Python 返回 None 对应：调用方据此走原路径
    }
    let result = blend::alpha_over(&stack, px);
    let mut boxed = result.into_boxed_slice();
    *out = boxed.as_mut_ptr();
    std::mem::forget(boxed);
    0
}
