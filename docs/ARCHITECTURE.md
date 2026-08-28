# 🏗️ 技术架构

> **👉 高级架构师/贡献者必读**：本文件面向**普通开发者**，展示系统总览与数据流。
> 关于**为什么这么设计（架构决策、权衡、备选方案、可逆性、风险登记册、架构不变量）**，请移步
> **[🏛️ 架构决策中心](../docs/architecture/index.md)** — Staff Engineer Mode 出品，含 6 条完整 ADR。

## 系统总览

```
用户输入 (Prompt/图片/语音)
    │
    ▼
┌──────────────────────────────────────────┐
│           core/workflow.py               │
│        WorkflowEngine (状态机)            │
├──────────┬──────────┬────────────────────┤
│ Generate │ Segment  │ Build + Export     │
│ (AI图生) │ (语义分层)│ (Live2D绑定)       │
└────┬─────┴────┬─────┴────────┬───────────┘
     │          │              │
     ▼          ▼              ▼
 core/image_gen  core/segment   live2d_builder/
 (3 Provider)    _engine        (mesh/bones/
                (SAM/ISNet)     physics/blendshapes)
     │          │              │
     ▼          ▼              ▼
┌──────────────────────────────────────────┐
│           drivers/ (实时驱动)              │
│  face_tracker │ audio │ live2d_runtime   │
│  (MediaPipe)  │ (mic) │ (PNG参数渲染)     │
└──────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────┐
│           llm_bridge/ (AI对话)            │
│  providers │ tts │ asr │ emotion         │
│  (GPT/Claude│(Edge│(Whisper)│(7情绪映射) │
│  /Ollama)   │ TTS)│        │             │
└──────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────┐
│     api/ (Go Gin HTTP + WebSocket)       │
│  handlers/ │ services/ │ models/         │
└──────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────┐
│           web/ (Next.js Workbench)        │
│  8 pages │ 24 components │ PixiJS canvas │
└──────────────────────────────────────────┘
```

## 数据流

### 生成流程
1. 用户输入 Prompt → `core/image_gen/router.py` 选择 Provider
2. AI 返回图片 → `core/qa/engine.py` 质量检测
3. `core/segment_engine/semantic.py` 语义分割为 18 层
4. `core/segment_engine/amodal.py` 补全遮挡区域
5. `live2d_builder/pipeline.py` 生成网格/骨骼/物理/表情
6. `live2d_builder/exporter/` 输出 model3.json + 纹理图集
7. `drivers/desktop_pet/animator.py` 打包桌宠

### 实时面捕流程
1. `drivers/face_tracker/mediapipe_tracker.py` 提取 468 关键点
2. `drivers/face_tracker/blendshape_mapper.py` 映射到 Live2D 参数
3. `drivers/audio/capture.py` 采集麦克风音量
4. `drivers/live2d_runtime/renderer.py` 参数驱动 PNG 合成
5. `drivers/desktop_pet/window.py` 透明窗口 60fps 渲染

### 对话流程
1. 用户语音 → `llm_bridge/asr/` 转文字
2. `llm_bridge/providers/router.py` 选择 LLM
3. 流式回复 → `llm_bridge/emotion/analyzer.py` 情绪分析
4. 情绪 → 表情 + 物理参数 → Live2D 渲染
5. 回复 → `llm_bridge/tts/edge_tts.py` 语音合成

## 技术选型

| 模块 | 技术 | 原因 |
|------|------|------|
| 图像生成 | Pollinations/Seedream/SenseNova | 免费+高质量双保险 |
| 语义分割 | ISNet + SAM + CV2 inpaint | 二次元专用+通用+补全 |
| 网格生成 | OpenCV 轮廓 + SciPy Delaunay | 自动化三角剖分 |
| 骨骼/变形器 | 程序化标准层级 | 无需手动绑定 |
| 面部捕捉 | MediaPipe Face Mesh | 轻量、跨平台、468点 |
| 语音TTS | Edge TTS | 免费、高质量、多语言 |
| LLM | OpenAI/Claude/Ollama | 云端+本地双模式 |
| 后端 | Go + Gin | 高性能、并发安全 |
| 前端 | Next.js + PixiJS | React生态+WebGL渲染 |
| 实时通信 | WebSocket + SSE | 双向+流式 |
| 部署 | Docker + GitHub Actions | 一键部署+CI/CD |
