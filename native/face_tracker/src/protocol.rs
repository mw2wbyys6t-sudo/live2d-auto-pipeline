//! JSON line protocol for face_tracker ↔ Go backend communication.
//!
//! stdin  → [`Command`]  (one JSON object per line)
//! stdout ← [`Output`]   (one JSON object per line)

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// 入站命令（从 stdin 读取，每行一个 JSON）
#[derive(Debug, Deserialize)]
#[serde(tag = "cmd")]
pub enum Command {
    /// 开始面捕
    #[serde(rename = "start")]
    Start {
        #[serde(default = "default_camera_id")]
        camera_id: u32,
        #[serde(default = "default_width")]
        width: u32,
        #[serde(default = "default_height")]
        height: u32,
        #[serde(default = "default_fps")]
        fps: u32,
    },
    /// 停止面捕
    #[serde(rename = "stop")]
    Stop,
    /// 查询状态
    #[serde(rename = "status")]
    Status,
    /// 设置平滑因子 (0.0 ~ 1.0, 越大越平滑)
    #[serde(rename = "set_smoothing")]
    SetSmoothing { alpha: f32 },
    /// 设置死区阈值
    #[serde(rename = "set_deadzone")]
    SetDeadzone { threshold: f32 },
    /// 退出
    #[serde(rename = "quit")]
    Quit,
}

/// 出站消息（写入 stdout，每行一个 JSON）
///
/// 注意：变体名与 type 值刻意不同（PascalCase 变体名 ↔ 小写 type 值），
/// 与 Go 端 native_bridge.go readLoop 的 switch 保持一致。
#[derive(Debug, Serialize)]
#[serde(tag = "type")]
pub enum Output {
    /// 就绪信号
    #[serde(rename = "ready")]
    Ready {
        version: &'static str,
        has_camera: bool,
        has_model: bool,
    },
    /// 面捕已启动
    #[serde(rename = "started")]
    Started {
        camera_id: u32,
        width: u32,
        height: u32,
    },
    /// 一帧面捕数据
    #[serde(rename = "frame")]
    Frame {
        #[serde(skip_serializing_if = "Option::is_none")]
        blendshapes: Option<HashMap<String, f32>>,
        #[serde(skip_serializing_if = "Option::is_none")]
        head_rotation: Option<HeadRotation>,
        #[serde(skip_serializing_if = "Option::is_none")]
        live2d_params: Option<HashMap<String, f32>>,
        fps: f32,
        timestamp_ms: u64,
    },
    /// 面捕已停止
    #[serde(rename = "stopped")]
    Stopped,
    /// 状态信息
    #[serde(rename = "status")]
    StatusInfo {
        running: bool,
        camera_id: u32,
        fps: f32,
        smoothing: f32,
        deadzone: f32,
    },
    /// 错误
    #[serde(rename = "error")]
    Error { message: String },
}

#[derive(Debug, Serialize)]
pub struct HeadRotation {
    pub pitch: f32,
    pub yaw: f32,
    pub roll: f32,
}

// Defaults
fn default_camera_id() -> u32 { 0 }
fn default_width() -> u32 { 640 }
fn default_height() -> u32 { 480 }
fn default_fps() -> u32 { 30 }
