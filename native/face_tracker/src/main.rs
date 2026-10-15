//! face_tracker — 原生面捕主程序
//!
//! 通过 stdin 接收 JSON 命令，通过 stdout 输出面捕数据（JSON Lines）。
//! Go 后端的 NativeBridge 服务以子进程方式启动本程序。
//!
//! 协议见 src/protocol.rs。
//! 数据流（ONNX 模式）：
//!   摄像头帧 → YuNet ONNX 人脸检测 → 5 关键点 → BlendShape → Live2D 参数 → JSON
//! 数据流（回退模式）：
//!   摄像头帧 → 肤色人脸检测 → bbox → 5pt 估算 → BlendShape → Live2D 参数 → JSON

mod blendshapes;
mod camera;
mod face_detect;
mod head_pose;
mod live2d_mapping;
mod onnx_detector;
mod protocol;

use std::collections::HashMap;
use std::io::{self, BufRead, Write};
use std::sync::mpsc::{channel, TryRecvError};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use protocol::{Command, Output};

const VERSION: &str = "1.0.0";
const TARGET_FRAME_DURATION: Duration = Duration::from_millis(33); // ~30 FPS

fn main() {
    let mut app = App::new();
    app.run();
}

struct App {
    cam: camera::CameraSource,
    onnx: Option<onnx_detector::OnnxDetector>,
    running: bool,
    smoothing: f32,
    deadzone: f32,
    prev_params: HashMap<String, f32>,
    camera_id: u32,
    fps: f32,
    frame_count: u32,
    last_fps_time: Instant,
}

impl App {
    fn new() -> Self {
        // 尝试加载 ONNX 人脸检测模型。
        // 同时覆盖「相对工作目录」与「相对可执行文件」两种布局，
        // 因为 Go 桥以子进程方式启动时并不保证工作目录。
        let mut model_dirs: Vec<String> = vec![
            "models".to_string(),
            "native/face_tracker/models".to_string(),
            "../models".to_string(),
        ];
        if let Ok(exe) = std::env::current_exe() {
            if let Some(exe_dir) = exe.parent() {
                // 开发布局：target/release/face_tracker.exe → native/face_tracker/models
                model_dirs.push(exe_dir.join("../../models").to_string_lossy().into_owned());
                // 部署布局：models/ 与 exe 同目录
                model_dirs.push(exe_dir.join("models").to_string_lossy().into_owned());
            }
        }

        let onnx = model_dirs
            .iter()
            .find_map(|dir| onnx_detector::OnnxDetector::try_load(dir));

        if onnx.is_some() {
            eprintln!("[face_tracker] ONNX 模式：深度学习人脸检测");
        } else {
            eprintln!("[face_tracker] 回退模式：肤色人脸检测（无 ONNX 模型）");
        }

        Self {
            cam: camera::CameraSource::new(),
            onnx,
            running: false,
            smoothing: 0.5,
            deadzone: 0.02,
            prev_params: HashMap::new(),
            camera_id: 0,
            fps: 0.0,
            frame_count: 0,
            last_fps_time: Instant::now(),
        }
    }

    fn run(&mut self) {
        // 发送就绪信号
        let has_camera = camera::CameraSource::count_devices() > 0;
        self.emit(Output::Ready {
            version: VERSION,
            has_camera,
            has_model: self.onnx.is_some(),
        });

        // stdin 读取线程
        let (cmd_tx, cmd_rx) = channel::<Command>();
        thread::spawn(move || {
            let stdin = io::stdin();
            for line in stdin.lock().lines().flatten() {
                if let Ok(cmd) = serde_json::from_str::<Command>(&line) {
                    if cmd_tx.send(cmd).is_err() {
                        break;
                    }
                }
            }
        });

        // 主循环
        loop {
            // 处理所有待处理命令
            loop {
                match cmd_rx.try_recv() {
                    Ok(cmd) => {
                        if self.handle_command(cmd) {
                            // Quit：刻意不关闭摄像头（见 Quit 分支注释），
                            // 直接进程退出，跳过析构，避免 Media Foundation /
                            // ONNX Runtime 后台线程退出竞态导致 0xC0000005 访问冲突。
                            std::process::exit(0);
                        }
                    }
                    Err(TryRecvError::Empty) => break,
                    Err(TryRecvError::Disconnected) => {
                        // stdin 关闭（父进程退出）：直接终止进程，跳过析构
                        std::process::exit(0);
                    }
                }
            }

            // 采集 + 处理
            if self.running {
                let frame_start = Instant::now();
                self.process_frame();
                // 维持目标帧率
                let elapsed = frame_start.elapsed();
                if elapsed < TARGET_FRAME_DURATION {
                    thread::sleep(TARGET_FRAME_DURATION - elapsed);
                }
            } else {
                thread::sleep(Duration::from_millis(50));
            }
        }
    }

    fn handle_command(&mut self, cmd: Command) -> bool {
        match cmd {
            Command::Start {
                camera_id,
                width,
                height,
                fps: _,
            } => {
                // 幂等防护：已运行时拒绝重复 start，
                // 避免对已开流的摄像头再次 open_stream()（nokhwa 未定义行为）。
                if self.running {
                    if camera_id == self.camera_id {
                        // 同一摄像头重复 start：静默忽略（幂等成功）
                    } else {
                        self.emit(Output::Error {
                            message: format!(
                                "面捕已在运行（摄像头 {}），请先停止再切换",
                                self.camera_id
                            ),
                        });
                    }
                    return false;
                }

                self.camera_id = camera_id;
                match self.cam.open(camera_id, width, height) {
                    Ok((w, h)) => {
                        self.running = true;
                        self.prev_params.clear();
                        self.frame_count = 0;
                        self.last_fps_time = Instant::now();
                        self.emit(Output::Started {
                            camera_id,
                            width: w,
                            height: h,
                        });
                    }
                    Err(e) => {
                        eprintln!("[face_tracker] 摄像头错误: {}", e);
                        self.emit(Output::Error { message: e });
                    }
                }
            }
            Command::Stop => {
                self.cam.close();
                self.running = false;
                self.emit(Output::Stopped);
            }
            Command::Status => {
                self.emit(Output::StatusInfo {
                    running: self.running,
                    camera_id: self.camera_id,
                    fps: self.fps,
                    smoothing: self.smoothing,
                    deadzone: self.deadzone,
                });
            }
            Command::SetSmoothing { alpha } => {
                self.smoothing = alpha.clamp(0.0, 1.0);
            }
            Command::SetDeadzone { threshold } => {
                self.deadzone = threshold.clamp(0.0, 0.5);
            }
            Command::Quit => {
                // 注意：这里刻意不调用 self.cam.close()。
                // nokhwa(0.10) 的 MediaFoundationDevice::drop 会在释放底层
                // COM 对象之前就执行 MFShutdown()+CoUninitialize()，随后再释放
                // source_reader 会触发 0xC0000005 访问冲突。
                // 因此退出时直接终止进程（见 run()），跳过该析构路径。
                return true;
            }
        }
        false
    }

    fn process_frame(&mut self) {
        let frame = match self.cam.capture_rgb() {
            Ok(f) => f,
            Err(e) => {
                eprintln!("[face_tracker] 采集失败: {}", e);
                thread::sleep(Duration::from_millis(100));
                return;
            }
        };

        let w_f = frame.width as f32;
        let h_f = frame.height as f32;

        // 人脸检测：优先 ONNX 深度学习，回退肤色检测
        let (blendshapes, head_rotation) = if let Some(ref mut onnx) = self.onnx {
            // ── ONNX 模式：YuNet 直接输出 5 个面部关键点 ──
            let faces = onnx.detect(&frame.rgb, frame.width, frame.height, 0.5);
            if let Some(face) = faces.first() {
                let pts = if let Some(ref lm) = face.landmarks {
                    // YuNet 输出顺序：右眼、左眼、鼻尖、右嘴角、左嘴角
                    // BlendShape 期望：左眼、右眼、鼻尖、左嘴角、右嘴角
                    [
                        (lm[1].0, lm[1].1), // 左眼
                        (lm[0].0, lm[0].1), // 右眼
                        (lm[2].0, lm[2].1), // 鼻尖
                        (lm[4].0, lm[4].1), // 左嘴角
                        (lm[3].0, lm[3].1), // 右嘴角
                    ]
                } else {
                    // 模型未输出关键点 → 从 bbox 估算
                    let det = face_detect::FaceDetection {
                        x: face.x as u32,
                        y: face.y as u32,
                        width: face.w as u32,
                        height: face.h as u32,
                        confidence: face.confidence,
                    };
                    face_detect::estimate_5pt_from_bbox(&det)
                        .map(|p| (p.0, p.1))
                };

                // 归一化到 0..1
                let norm: [(f32, f32, f32); 5] = [
                    (pts[0].0 / w_f, pts[0].1 / h_f, 0.0),
                    (pts[1].0 / w_f, pts[1].1 / h_f, 0.0),
                    (pts[2].0 / w_f, pts[2].1 / h_f, 0.0),
                    (pts[3].0 / w_f, pts[3].1 / h_f, 0.0),
                    (pts[4].0 / w_f, pts[4].1 / h_f, 0.0),
                ];
                let bs = blendshapes::compute_blendshapes_5pt(
                    norm[0], norm[1], norm[2], norm[3], norm[4],
                );
                let head = head_pose::estimate_head_rotation_5pt(
                    norm[0], norm[1], norm[2], norm[3], norm[4],
                );
                (Some(bs), Some(head))
            } else {
                (None, None)
            }
        } else {
            // ── 回退模式：肤色检测 + bbox 估算 ──
            let detection = face_detect::detect_face(&frame.rgb, frame.width, frame.height);
            if let Some(ref det) = detection {
                let pts = face_detect::estimate_5pt_from_bbox(det);
                let norm: [(f32, f32, f32); 5] = [
                    (pts[0].0 / w_f, pts[0].1 / h_f, 0.0),
                    (pts[1].0 / w_f, pts[1].1 / h_f, 0.0),
                    (pts[2].0 / w_f, pts[2].1 / h_f, 0.0),
                    (pts[3].0 / w_f, pts[3].1 / h_f, 0.0),
                    (pts[4].0 / w_f, pts[4].1 / h_f, 0.0),
                ];
                let bs = blendshapes::compute_blendshapes_5pt(
                    norm[0], norm[1], norm[2], norm[3], norm[4],
                );
                let head = head_pose::estimate_head_rotation_5pt(
                    norm[0], norm[1], norm[2], norm[3], norm[4],
                );
                (Some(bs), Some(head))
            } else {
                (None, None)
            }
        };

        // 映射到 Live2D 参数
        let empty = HashMap::new();
        let bs_ref = blendshapes.as_ref().unwrap_or(&empty);
        let mut params = live2d_mapping::blendshapes_to_live2d(bs_ref, head_rotation.as_ref());

        // 平滑
        if !self.prev_params.is_empty() {
            params = live2d_mapping::smooth(&params, &self.prev_params, self.smoothing);
        }

        // 死区
        live2d_mapping::apply_deadzone(&mut params, self.deadzone);

        self.prev_params = params.clone();

        // FPS 统计
        self.frame_count += 1;
        let now = Instant::now();
        let elapsed = now.duration_since(self.last_fps_time);
        if elapsed >= Duration::from_secs(1) {
            self.fps = self.frame_count as f32 / elapsed.as_secs_f32();
            self.frame_count = 0;
            self.last_fps_time = now;
        }

        // 时间戳
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64;

        self.emit(Output::Frame {
            blendshapes,
            head_rotation,
            live2d_params: Some(params),
            fps: self.fps,
            timestamp_ms: timestamp,
        });
    }

    fn emit(&self, output: Output) {
        let json = serde_json::to_string(&output).unwrap_or_else(|_| r#"{"type":"error"}"#.into());
        let stdout = io::stdout();
        let mut lock = stdout.lock();
        let _ = writeln!(lock, "{}", json);
        let _ = lock.flush();
    }
}
