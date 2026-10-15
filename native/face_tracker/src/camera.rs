//! Camera capture via nokhwa (Windows Media Foundation).
//!
//! 在 Windows 上通过 Media Foundation 后端打开摄像头，输出 RGB 像素数据。
//!
//! 重要：nokhwa 0.10 的 `MediaFoundationDevice::drop` 会在释放底层 COM 对象
//! 之前就调用 `MFShutdown()` + `CoUninitialize()`，随后释放 `source_reader`
//! 会触发 0xC0000005 访问冲突。因此本模块**永不析构** Camera 设备对象：
//!   - `close()` 仅停止视频流，保留设备以便复用；
//!   - 需要替换设备时对旧对象调用 `std::mem::forget`；
//!   - `Drop for CameraSource` 同样只做 forget。
//! 设备资源交由进程终止回收（Go 桥或以 process::exit 结束时）。

use nokhwa::pixel_format::RgbAFormat;
use nokhwa::utils::{CameraIndex, RequestedFormat, RequestedFormatType, Resolution};
use nokhwa::Camera;

/// RGB 帧数据
pub struct Frame {
    /// R, G, B 交替存储（每像素 3 字节）
    pub rgb: Vec<u8>,
    pub width: u32,
    pub height: u32,
}

/// 摄像头采集源
pub struct CameraSource {
    camera: Option<Camera>,
    camera_id: u32,
    streaming: bool,
}

impl CameraSource {
    pub fn new() -> Self {
        Self {
            camera: None,
            camera_id: 0,
            streaming: false,
        }
    }

    /// 枚举可用摄像头数量（简化实现：假设至少 1 个）
    pub fn count_devices() -> usize {
        1
    }

    /// 打开（或复用）摄像头并启动视频流。
    /// 返回实际分辨率。
    pub fn open(&mut self, camera_id: u32, width: u32, height: u32) -> Result<(u32, u32), String> {
        // 防御：流已在打开状态时直接返回当前分辨率，
        // 避免对同一设备重复 open_stream()（nokhwa 未定义行为）。
        if self.streaming && self.camera_id == camera_id {
            if let Some(cam) = self.camera.as_ref() {
                let res = cam.resolution();
                return Ok((res.width(), res.height()));
            }
        }

        // 若已有不同 id 的设备，换成新设备（旧设备不析构，避免 nokhwa 崩溃）
        if self.camera.is_none() || self.camera_id != camera_id {
            if let Some(old) = self.camera.take() {
                std::mem::forget(old);
            }
            let format = RequestedFormat::new::<RgbAFormat>(
                RequestedFormatType::AbsoluteHighestResolution,
            );
            let cam = Camera::new(CameraIndex::Index(camera_id), format)
                .map_err(|e| format!("打开摄像头失败: {}", e))?;
            self.camera = Some(cam);
            self.camera_id = camera_id;
        }

        let cam = self.camera.as_mut().ok_or("摄像头不可用")?;

        // 先设置目标分辨率再开流（开流后切换分辨率在多数设备上不生效，
        // 会一直停留在 AbsoluteHighestResolution，白白多处理数倍像素）。
        // 设置失败则沿用设备当前分辨率。
        let _ = cam.set_resolution(Resolution::new(width, height));

        cam.open_stream()
            .map_err(|e| format!("启动视频流失败: {}", e))?;

        let actual_res = cam.resolution();
        self.streaming = true;
        Ok((actual_res.width(), actual_res.height()))
    }

    /// 采集一帧并转换为 RGB
    pub fn capture_rgb(&mut self) -> Result<Frame, String> {
        if !self.streaming {
            return Err("摄像头未启动".to_string());
        }
        let camera = self.camera.as_mut().ok_or("摄像头未打开")?;
        let buffer = camera.frame().map_err(|e| format!("采集帧失败: {}", e))?;

        let res = buffer.resolution();
        let w = res.width();
        let h = res.height();

        // 解码为 RGBA，然后剥离 Alpha 通道得到 RGB
        let rgba = buffer
            .decode_image::<RgbAFormat>()
            .map_err(|e| format!("像素解码失败: {}", e))?;

        let mut rgb = Vec::with_capacity((w * h * 3) as usize);
        for chunk in rgba.chunks_exact(4) {
            rgb.push(chunk[0]);
            rgb.push(chunk[1]);
            rgb.push(chunk[2]);
        }

        Ok(Frame {
            rgb,
            width: w,
            height: h,
        })
    }

    /// 停止视频流（保留设备对象以便再次 open 复用，不触发 nokhwa 崩溃析构）
    pub fn close(&mut self) {
        if let Some(cam) = self.camera.as_mut() {
            let _ = cam.stop_stream();
        }
        self.streaming = false;
    }

    pub fn is_open(&self) -> bool {
        self.streaming
    }
}

impl Drop for CameraSource {
    fn drop(&mut self) {
        // 故意不析构底层设备（见模块头注释）
        if let Some(cam) = self.camera.take() {
            std::mem::forget(cam);
        }
    }
}
