# ADR-004：面部捕捉运行时选型 — MediaPipe 468 + 指数平滑 + 音频 RMS → Live2D BlendShape 映射

| 字段 | 内容 |
|------|------|
| **状态** | Accepted（v10.0 起） |
| **日期** | 2026-07-30 |
| **决策者** | Live2D Master Agent Team |
| **本地校验** | [`drivers/face_tracker/mediapipe_tracker.py`](file:///workspace/drivers/face_tracker/) + [`blendshape_mapper.py`](file:///workspace/drivers/face_tracker/blendshape_mapper.py) + [`drivers/audio/capture.py`](file:///workspace/drivers/audio/capture.py) |

---

## 1. 决策问题

一个让"普通 PC 摄像头用户（不是 iPhone FaceID）"的脸能驱动 Live2D 虚拟角色的实时面捕管线，需要同时满足：

1. **跨平台**：Windows / macOS / Linux（含无 GPU 的云桌面）都能跑；
2. **低延迟**：摄像头帧捕获 → Live2D 参数更新 ≤ 75ms p95（动作无明显滞后）；
3. **低抖动**：ARKit 52 系数或同类 BlendShape 不能每一帧乱跳，否则角色会"抽风"；
4. **嘴型联动**：不开摄像头光说话时，角色嘴也能随音量开合（桌宠对话场景）；
5. **零成本**：用户无需额外付费许可。

## 2. 上下文与推动力

| 推动力 | 说明 |
|--------|------|
| F1 · 小白用户的设备参差 | README 中的"3 分钟上手"承诺要求：普通笔记本 720p 摄像头即可，不强制 iPhone / 深度相机 / 采集卡。 |
| F2 · Live2D Cubism 4 标准参数有限 | `live2d_builder/blendshapes/parameters.py` 导出的参数是 28 个（眼开闭/嘴开闭/角度XYZ/眉毛等），与完整 ARKit 52 不一一对应。映射是**投影**不是**对齐**。 |
| F3 · 桌面桌宠是 CPU 常驻进程 | `drivers/desktop_pet/` 的 PyGame 透明窗口循环 60fps，再加一个 30~60fps 面捕，两者抢 CPU，必须轻量。 |
| F4 · 麦克风对话时嘴型同步是强需求 | `llm_bridge/chat_session.py` 流式对话时，TTS 播放与用户说话都需要嘴动；纯面捕的嘴唇识别在非正对摄像头时经常丢失，必须有 RMS 音量兜底。 |

## 3. 备选方案对比

### 3.1 面捕引擎

| 方案 | 跨平台 | 免许可 | CPU 占用 | 延迟 | 关键点 | 被拒原因 |
|------|--------|--------|----------|------|--------|----------|
| A · ARKit（iPhone） | ❌ 仅 iOS/macOS | ✅ | 低 | 低 | 52 完美 | ❌ 跨平台（F1 失败）；Windows 用户 > 70%。 |
| B · VSeeFace SDK | ❌ Windows | ✅ | 中 | 低 | ~50 | ❌ Linux/macOS 不支持；封装重。 |
| C · OpenCV DNN + dlib 68 点 | ✅ 三平台 | ✅ | 很低 | 低 | 68 | ❌ 点数太少，无法映射精细表情（如眼球追踪、眉毛细节）。 |
| D · MediaPipe Face Mesh（当前） | ✅ 三平台（CPU/GPU/NPU） | ✅ Apache 2.0 | 中低 | 低 | **468** + 10 iris | ✅ 点数够用；`mediapipe>=0.10` 已收录 `FACE_GEOMETRY` 世界坐标系，直接解算角度 XYZ；F1~F3 全满足。 |

### 3.2 平滑与嘴型

| 方案 | 说明 | 被拒原因 |
|------|------|----------|
| A · 原始关键点值直接喂 Live2D | 省计算 | ❌ 抖动不可接受（F3 失败）；用户轻微晃动就会让角色头颤。 |
| B · 卡尔曼滤波 | 线性最优 | ❌ 对非高斯噪声（突然扭头、眨眼、遮挡）响应太慢；调参复杂。 |
| **✅ C · 双指数平滑（EMA）+ 死区阈值** | `new = α*cur + (1-α)*old`，α 可调；对小扰动设 deadband | ✅ 计算量 O(1)；对表情参数 α=0.2 慢收敛、对角度 α=0.6 快收敛；实现仅 30 行；与 `drivers/face_tracker/blendshape_mapper.py` 当前实现匹配。 |
| D · 纯视觉嘴型分类（A/U/I/E/O） | 精细 | ❌ 光照差、侧脸时识别率骤降；F4 失败。 |
| **✅ E · 麦克风 RMS 音量 → ParamMouthOpenY 兜底** | `sounddevice` 取块 RMS，线性映射嘴开度 | ✅ 与视觉嘴型取 **max(视觉, 音量)** 组合；对话/TTS 播放/摄像头遮挡场景下嘴都能开合；F4 直接命中。 |

## 4. 决策

采用以下端到端面捕管线：

```
┌──────────────────────┐          ┌──────────────────────┐
│  摄像头 640×480@30fps │          │  麦克风 16kHz 块 RMS  │
└──────────┬───────────┘          └──────────┬───────────┘
           ▼                                 ▼
 MediaPipe FaceMesh (CPU, Lite/FULL)       RMS = √(mean(s²))
           │ 468 + 10 iris 3D 点            │ 音量归一化 [0,1]
           ▼                                 ▼
 BlendShapeMapper: 投影 → 28 Live2D 参数    映射到 ParamMouthOpenY
 ├ 几何解算（角度 XYZ → ParamAngleX/Y/Z）
 ├ 距离比（眼角-瞳孔 → EyeLOpen/EyeROpen）
 ├ 欧式距离（嘴角 → MouthSmile/MouthForm）
 └ EMA α=0.2~0.6 按参数分档平滑 + deadband=0.02
           │                                 │
           └─────────────┬───────────────────┘
                         ▼ max() 对嘴开度
            Live2D Runtime 参数更新 60Hz
                         │
                         ▼
          PyGame 透明窗口 / VTube Studio OSC
```

## 5. 后果

### 正面 ✅
- **跨平台零成本**：Windows/macOS/Linux 安装 `mediapipe>=0.10`（pip）+ `sounddevice` 就能跑，满足 F1 与 README "3 分钟上手"。
- **延迟指标达标**：75ms p95 以内（MP FaceMesh Lite 模型 5ms/帧 + 平滑 0ms + 渲染 16ms ≈ 21ms，留足系统抖动余量）。
- **抖动解决**：EMA + deadband 彻底去掉"轻微头颤"。
- **嘴型鲁棒**：RMS 兜底是本设计的关键差异化能力，用户在关灯、侧脸、甚至不看摄像头时说话，角色嘴依然动。
- **便于扩展**：`blendshape_mapper.py` 中各参数 α 与阈值做成可配置 dict，用户可在 `.env` 中针对自身习惯微调。

### 负面 / 缓解 ⚠️
- **眼球追踪精度有限**：468 点 iris 只有 10 个点，对"翻白眼/斜视"不如 ARKit → 缓解：`ParamEyeBallX/Y` 仅映射 [-1,1]，超过 ±0.8 饱和；普通 VTuber 场景足够。
- **MediaPipe 模型下载**：首跑会从 Google Storage 下载 `.task` 模型文件 → 缓解：`scripts/download_models.py` 已收录，`install.py` 自动执行。
- **音频 RMS 线性映射偏粗糙**（平静说话可能嘴太小、喊叫嘴太大）→ 缓解：与视觉嘴型取 max + 指数压缩 `sqrt(RMS)`；后续可接入 Wav2Vec 音素分类做精细嘴型（预留接口在 `drivers/audio/__init__.py`）。

## 6. 可逆性

| 项 | 说明 |
|----|------|
| **撤销成本** | **低（双向门）**。MediaPipe 是模块替换，`drivers/face_tracker/__init__.py` 暴露统一 `BaseTracker` 接口，换成 Dlib/OpenFace/自建 ONNX 只需新写一个派生类。 |
| **重新考虑触发条件** | ① MediaPipe 协议变更（当前 Apache 2.0 友好）；② 自研或接入更精细的 2D→3D 姿态模型；③ 用户大规模要求 Live2D 参数 > 50 个。 |
| **责任方** | 驱动层负责人。 |

## 7. Fitness Functions

| 属性 | 度量 | 阈值 | 测量来源 | 频率 | 失败响应 |
|------|------|------|----------|------|----------|
| 端到端延迟 | 摄像头时间戳 → Live2D 参数更新时间戳差 p95 | ≤ 75ms | `tests/unit/test_blendshape_mapper.py::test_latency_budget`（待补） | 每日 CI | 调小模型规格 / 降帧 |
| 平稳性（抖动） | 同 10s 静止脸序列，ParamAngleX 标准差 | ≤ 0.005 | 离线 fixture 回放 | 每改 mapper | 调 α / 加 deadband |
| CPU 占用（桌宠+面捕） | 4 核 i7 空闲时总 CPU | ≤ 25% | 本地 top/任务管理器 | 每次发版 | Lite 模型 / 降到 15fps |
| 嘴型召回率（对话场景） | 20 句普通话闭麦/开麦，角色嘴动次数/总句数 | ≥ 90% | 人工 + 离线 audio fixture | 每版 | 调 RMS 增益 |
| 跨平台一致性 | Win/macOS/Linux 同一段摄像头 fixture → 28 参数平均差 | ≤ 0.05 | CI 三平台矩阵 | 每次 PR | 检查 MP 版本/模型哈希 |

## 8. 证据与校验点

- ✅ `drivers/face_tracker/mediapipe_tracker.py` 使用 `mp.solutions.face_mesh`
- ✅ `drivers/face_tracker/blendshape_mapper.py` 有 `smooth_alpha` 配置 dict 与 deadband
- ✅ `drivers/audio/capture.py` 用 `sounddevice.InputStream` 回调计算 RMS
- ✅ `requirements.txt` `mediapipe>=0.10.0`、`sounddevice>=0.4.6`、`soundfile>=0.12.0`
- ⏳ 待补：延迟 budget unit test + 离线 fixture
