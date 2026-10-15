//! ONNX 人脸检测模块 — 基于 YuNet 深度学习模型。
//!
//! 当 models/ 目录存在 ONNX 模型文件时，自动启用深度学习检测，
//! 替代肤色检测法。YuNet 输出人脸边界框 + 5 个面部关键点：
//!   右眼、左眼、鼻尖、右嘴角、左嘴角
//!
//! 模型来源：OpenCV Zoo — face_detection_yunet_2023mar.onnx
//! 解码逻辑严格对齐 OpenCV `modules/objdetect/src/face_detect.cpp`：
//!   - 输入：BGR、原始 0..255（等价于 blobFromImage 默认参数）
//!   - 输出：cls_{8,16,32} / obj_{8,16,32} / bbox_{8,16,32} / kps_{8,16,32}
//!   - 置信度：sqrt(clamp(cls) * clamp(obj))
//!   - 边界框：cx=(c+dx)*s, cy=(r+dy)*s, w=exp(dw)*s, h=exp(dh)*s
//!   - 关键点：(kx+c)*s, (ky+r)*s
//!
//! 使用 load-dynamic 模式：运行时加载同目录的 onnxruntime.dll。

use std::path::Path;
use std::sync::Once;

use ndarray::Array4;
use ort::session::Session;
use ort::value::{Value, ValueType};

/// 输出张量形状仅打印一次（便于诊断，避免每帧刷屏）
static LOG_SHAPES: Once = Once::new();

const STRIDES: [usize; 3] = [8, 16, 32];

/// ONNX 检测结果
pub struct OnnxFace {
    pub x: f32,
    pub y: f32,
    pub w: f32,
    pub h: f32,
    pub confidence: f32,
    /// 5 个面部关键点（像素坐标），顺序与 YuNet 一致：
    /// [0]=右眼, [1]=左眼, [2]=鼻尖, [3]=右嘴角, [4]=左嘴角
    pub landmarks: Option<[(f32, f32); 5]>,
}

/// ONNX 人脸检测器
pub struct OnnxDetector {
    session: Session,
    input_size: usize,
}

impl OnnxDetector {
    /// 尝试从 models/ 目录加载 ONNX 模型。
    /// 返回 None 表示没有找到模型文件（调用方应回退到肤色检测）。
    pub fn try_load(model_dir: &str) -> Option<Self> {
        let candidates = [
            format!("{}/face_detection_yunet_2023mar.onnx", model_dir),
            format!("{}/yunet.onnx", model_dir),
            format!("{}/face_detector.onnx", model_dir),
        ];

        let model_path = candidates.iter().find(|p| Path::new(p).exists())?;

        eprintln!("[onnx] 加载模型: {}", model_path);

        // 初始化 ONNX Runtime（load-dynamic 模式）：从 exe 同目录或 PATH 查找 dll
        let env = match ort::environment::init()
            .with_name("face_tracker")
            .build()
        {
            Ok(e) => e,
            Err(e) => {
                eprintln!(
                    "[onnx] ONNX Runtime 初始化失败（请确认 onnxruntime.dll 在 exe 同目录、版本 >= 1.31）: {}",
                    e
                );
                return None;
            }
        };

        let mut builder = match Session::builder(&env) {
            Ok(b) => b,
            Err(e) => {
                eprintln!("[onnx] 创建会话构建器失败: {}", e);
                return None;
            }
        };
        let session = match builder.commit_from_file(model_path) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("[onnx] 模型加载失败（文件损坏或版本不兼容）: {}", e);
                return None;
            }
        };

        // 从模型读取输入尺寸（NCHW 的 H/W）。YuNet 2023mar 固定为 640x640，
        // 若模型为动态尺寸（-1）则回退到 320。
        let input_size = session
            .inputs()
            .first()
            .and_then(|o| match o.dtype() {
                ValueType::Tensor { shape, .. } => {
                    let dims: &[i64] = shape;
                    if dims.len() == 4 && dims[2] > 0 && dims[3] > 0 {
                        Some(dims[2].min(dims[3]) as usize)
                    } else {
                        None
                    }
                }
                _ => None,
            })
            .unwrap_or(320);

        eprintln!("[onnx] 模型加载成功 (input={}x{})", input_size, input_size);

        Some(Self {
            session,
            input_size,
        })
    }

    /// 对一帧 RGB 图像执行人脸检测。
    /// 返回所有人脸（按置信度降序，已 NMS）。
    pub fn detect(
        &mut self,
        rgb: &[u8],
        width: u32,
        height: u32,
        conf_threshold: f32,
    ) -> Vec<OnnxFace> {
        let s = self.input_size;

        // 1. 缩放到模型输入尺寸（不保持宽高比，与 OpenCV 示例一致）
        let resized = bilinear_resize(rgb, width, height, s, s);

        // 2. 构造 NCHW 张量：BGR + 原始 0..255
        //    （等价于 OpenCV blobFromImage 默认参数：scale=1, mean=0, swapRB=false）
        let mut input_vec = Vec::with_capacity(s * s * 3);
        for c in 0..3usize {
            // 目标通道顺序 B,G,R → 源 RGB 中的下标
            let src_c = 2 - c;
            for y in 0..s {
                for x in 0..s {
                    input_vec.push(resized[(y * s + x) * 3 + src_c] as f32);
                }
            }
        }

        let input = match Array4::from_shape_vec((1, 3, s, s), input_vec) {
            Ok(a) => a,
            Err(e) => {
                eprintln!("[onnx] 张量构造错误: {}", e);
                return vec![];
            }
        };

        let input_value = match Value::from_array(input) {
            Ok(v) => v,
            Err(e) => {
                eprintln!("[onnx] 张量转换错误: {}", e);
                return vec![];
            }
        };

        // 3. 推理
        let outputs = match self.session.run(ort::inputs![input_value]) {
            Ok(o) => o,
            Err(e) => {
                eprintln!("[onnx] 推理错误: {}", e);
                return vec![];
            }
        };

        // 4. 按名称收集输出张量（值借用切片，避免每帧 ~525KB 的 to_vec 拷贝）。
        //    注意：不能用 outputs.iter() —— 它产出的 ValueRef 是迭代临时值，
        //    借用的切片无法跨迭代存活；改用 outputs.get() 直接从 outputs 借用。
        let tensor = |name: &str| -> Option<&[f32]> {
            outputs
                .get(name)
                .and_then(|v| v.try_extract_tensor::<f32>().ok())
                .map(|(_, data)| data)
        };

        // 输出张量形状仅首帧打印一次（便于诊断）
        if !LOG_SHAPES.is_completed() {
            let names: Vec<&str> = outputs.iter().map(|(name, _)| name).collect();
            let log: Vec<String> = names
                .iter()
                .map(|&n| match outputs.get(n).map(|v| v.try_extract_tensor::<f32>()) {
                    Some(Ok((shape, _))) => {
                        let dims: &[i64] = shape;
                        format!("{}={:?}", n, dims)
                    }
                    _ => format!("{}=<non-f32>", n),
                })
                .collect();
            LOG_SHAPES.call_once(|| {
                eprintln!("[onnx] 输出张量: {}", log.join(", "));
            });
        }

        // 5. 逐 stride 解码
        let scale_x = width as f32 / s as f32;
        let scale_y = height as f32 / s as f32;
        let mut faces = Vec::new();

        for &stride in &STRIDES {
            let cols = s / stride;
            let rows = s / stride;

            let cls = match tensor(&format!("cls_{}", stride)) {
                Some(v) => v,
                None => continue,
            };
            let obj = match tensor(&format!("obj_{}", stride)) {
                Some(v) => v,
                None => continue,
            };
            let bbox = match tensor(&format!("bbox_{}", stride)) {
                Some(v) => v,
                None => continue,
            };
            let kps = tensor(&format!("kps_{}", stride));

            let sf = stride as f32;

            for r in 0..rows {
                for c in 0..cols {
                    let idx = r * cols + c;

                    let cls_score = cls.get(idx).copied().unwrap_or(0.0).clamp(0.0, 1.0);
                    let obj_score = obj.get(idx).copied().unwrap_or(0.0).clamp(0.0, 1.0);
                    let score = (cls_score * obj_score).sqrt();
                    if score < conf_threshold {
                        continue;
                    }

                    let base = idx * 4;
                    if base + 3 >= bbox.len() {
                        continue;
                    }

                    // 边界框（输入图坐标系）
                    let cx = (c as f32 + bbox[base]) * sf;
                    let cy = (r as f32 + bbox[base + 1]) * sf;
                    let w = bbox[base + 2].exp() * sf;
                    let h = bbox[base + 3].exp() * sf;
                    let x1 = (cx - w / 2.0) * scale_x;
                    let y1 = (cy - h / 2.0) * scale_y;
                    let w_s = w * scale_x;
                    let h_s = h * scale_y;

                    if w_s < 20.0 || h_s < 20.0 {
                        continue;
                    }

                    // 5 个关键点
                    let landmarks = kps.and_then(|kv| {
                        let kb = idx * 10;
                        if kb + 9 >= kv.len() {
                            return None;
                        }
                        let mut pts = [(0.0f32, 0.0f32); 5];
                        for n in 0..5 {
                            let kx = (kv[kb + 2 * n] + c as f32) * sf * scale_x;
                            let ky = (kv[kb + 2 * n + 1] + r as f32) * sf * scale_y;
                            pts[n] = (kx, ky);
                        }
                        Some(pts)
                    });

                    faces.push(OnnxFace {
                        x: x1,
                        y: y1,
                        w: w_s,
                        h: h_s,
                        confidence: score,
                        landmarks,
                    });
                }
            }
        }

        // 6. NMS 去重
        nms(&mut faces, 0.3);

        faces
    }
}

// ──────────────────── 内部辅助 ────────────────────

/// 双线性插值缩放图像（RGB）
fn bilinear_resize(src: &[u8], sw: u32, sh: u32, dw: usize, dh: usize) -> Vec<u8> {
    let mut dst = vec![0u8; dw * dh * 3];
    let x_ratio = sw as f32 / dw as f32;
    let y_ratio = sh as f32 / dh as f32;

    for dy in 0..dh {
        for dx in 0..dw {
            let sx = dx as f32 * x_ratio;
            let sy = dy as f32 * y_ratio;
            let x0 = sx.floor() as u32;
            let y0 = sy.floor() as u32;
            let x1 = (x0 + 1).min(sw - 1);
            let y1 = (y0 + 1).min(sh - 1);
            let fx = sx - x0 as f32;
            let fy = sy - y0 as f32;

            for c in 0..3usize {
                let p00 = src[((y0 * sw + x0) * 3 + c as u32) as usize] as f32;
                let p01 = src[((y0 * sw + x1) * 3 + c as u32) as usize] as f32;
                let p10 = src[((y1 * sw + x0) * 3 + c as u32) as usize] as f32;
                let p11 = src[((y1 * sw + x1) * 3 + c as u32) as usize] as f32;

                let val = p00 * (1.0 - fx) * (1.0 - fy)
                    + p01 * fx * (1.0 - fy)
                    + p10 * (1.0 - fx) * fy
                    + p11 * fx * fy;

                dst[(dy * dw + dx) * 3 + c] = val.round().clamp(0.0, 255.0) as u8;
            }
        }
    }

    dst
}

/// IoU（交并比）
fn iou(a: &OnnxFace, b: &OnnxFace) -> f32 {
    let ax2 = a.x + a.w;
    let ay2 = a.y + a.h;
    let bx2 = b.x + b.w;
    let by2 = b.y + b.h;

    let ix1 = a.x.max(b.x);
    let iy1 = a.y.max(b.y);
    let ix2 = ax2.min(bx2);
    let iy2 = ay2.min(by2);

    let inter = ((ix2 - ix1).max(0.0) * (iy2 - iy1).max(0.0)).max(0.0);
    let union = a.w * a.h + b.w * b.h - inter;

    if union > 0.0 {
        inter / union
    } else {
        0.0
    }
}

/// 非极大值抑制（NMS）
fn nms(faces: &mut Vec<OnnxFace>, threshold: f32) {
    faces.sort_by(|a, b| {
        b.confidence
            .partial_cmp(&a.confidence)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let mut keep = Vec::new();
    while !faces.is_empty() {
        let best = faces.remove(0);
        faces.retain(|f| iou(&best, f) < threshold);
        keep.push(best);
    }
    *faces = keep;
}
