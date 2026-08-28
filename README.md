# 🎭 Live2D Master Agent v10.0

> **一句话**：输入一句话，AI 生成你的专属虚拟主播——支持实时面部捕捉、语音对话、表情联动、桌宠运行。

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.9+-blue?style=for-the-badge)](https://python.org)
[![Go](https://img.shields.io/badge/Go-1.21+-00ADD8?style=for-the-badge)](https://go.dev)
[![Next.js](https://img.shields.io/badge/Next.js-16-black?style=for-the-badge)](https://nextjs.org)
[![License](https://img.shields.io/badge/License-Apache--2.0-green?style=for-the-badge)](LICENSE)
[![Version](https://img.shields.io/badge/Version-10.0-ff69b4?style=for-the-badge)]()

</div>

---

## 💡 项目简介

Live2D Master Agent 是一款**面向人人的 AI 虚拟主播生产工具**。哪怕你没有任何绘画、建模、编程基础，只要一句话描述，就能在 3 分钟内从 0 到 1 产出一套**合规可二创的 Live2D Cubism4 虚拟角色**，并开箱即用地运行在桌面桌宠、VTuber 推流、AI 对话、VTube Studio 等个人与学习场景。

项目从 **AI 图像生成 → 语义分层 → PSD 质检 → Live2D 自动绑定 → 实时面部捕捉 → LLM 对话 → 桌宠/工作台运行**，打通了一整条工业化流水线。核心技术栈为 **Python 内核 + Go API + Next.js 工作台**，全栈开源、模块化、可扩展。

## 🎬 效果预览

| 功能 | 效果说明 |
|------|----------|
| 🎨 **AI 生成角色** | 一句话 Prompt → 4096×4096 透明日系赛璐璐立绘 |
| ✂️ **自动分层** | SAM+ISNet 语义分割，一键拆出 18 层 PSD（头发/五官/衣物…） |
| 🦴 **自动绑定** | 输出完整 Cubism4 模型包，直接导入 VTube Studio / Live2D Viewer |
| 🎯 **面部捕捉** | MediaPipe 468 关键点驱动，75ms 低延迟，支持麦克风嘴型联动 |
| 💬 **AI 对话** | LLM + TTS + ASR 三合一，情绪分析联动表情与动作 |
| 🖥️ **桌宠运行** | 跨平台透明悬浮窗，Windows/macOS/Linux 通吃 |

> ⏳ 视频演示与示例模型包即将上线，敬请期待。

## 🎯 适用场景

- **个人 VTuber** — 零成本快速出道，无需画师与建模师
- **独立创作者** — 为漫画、小说、游戏角色生成可互动 Live2D 形象
- **AI 陪伴/对话** — 结合 LLM 打造有声音、有表情的 AI 桌宠
- **二次元社区** — 社团活动、粉丝二创、虚拟偶像企划
- **教学演示** — 高校/培训机构的虚拟讲师、数字人课堂
- **MCN/公会** — 批量生产虚拟主播形象，快速搭建虚拟艺人矩阵

---

## ✨ v10.0 重大升级

| 功能 | v9.0 | **v10.0** |
|------|------|-----------|
| 图像分层 | K-means 颜色聚类 | **SAM+ISNet 语义分割 + Amodal 补全** |
| Live2D 导出 | 空脚手架 model3.json | **完整 Cubism4 模型包（28 表情 + 物理 + 骨骼）** |
| 桌宠驱动 | 预设循环动画 | **MediaPipe 面部捕捉 + 麦克风音频驱动** |
| 角色一致性 | ❌ 每次随机变脸 | **角色卡 + 参考图锚定 + Embedding 锁定** |
| AI 对话 | ❌ 无 | **LLM 流式对话 + TTS 语音 + 情绪联动** |
| 前端工作台 | PSD 质检单页 | **8 页面一站式工作台** |
| 代码架构 | 双目录冗余 | **模块化 6 层架构** |
| 部署 | 手动 | **Docker + 一键安装 + CI/CD** |

---

## 🚀 快速上手

### 环境要求

| 依赖 | 最低版本 | 检查命令 |
|------|---------|---------|
| Python | 3.9+ | `python3 --version` |
| Node.js | 18+ | `node --version` |
| npm | 9+ | `npm --version` |
| Go | 1.21+ | `go version` |

### 方式一：一键安装（推荐小白）

```bash
# Windows: 双击 install.bat
# macOS/Linux:
bash install.sh

# 或直接用 Python:
python install.py
```

安装程序会自动：
1. 检测 Python/Node/Go 环境
2. 安装所有 Python 依赖
3. 安装 npm 依赖（Web 前端）
4. 编译 Go API 服务器
5. 创建 .env 配置文件
6. 验证安装

### 方式二：手动安装（开发者推荐）

#### 第 1 步：安装 Python 依赖

```bash
cd Live2D-Master-Agent
pip install -r requirements.txt
```

#### 第 2 步：安装前端依赖

```bash
cd web
npm install
```

#### 第 3 步：编译 Go API

```bash
cd api
go mod tidy
go build -o live2d-api .
```

#### 第 4 步：创建配置文件

```bash
cp .env.example .env
```

> 默认使用免费的 Pollinations 图像生成 + Edge TTS 语音，无需填写任何 API Key 即可体验。

#### 第 5 步：启动服务（需要两个终端）

**终端 1 — 启动 Go API 后端：**

```bash
cd api
export PYTHONPATH=$(pwd)/..
export LIVE2D_PROJECT_ROOT=$(pwd)/..
./live2d-api
# API 运行在 http://localhost:8080
```

**终端 2 — 启动 Next.js 前端工作台：**

```bash
cd web
npm run dev
# 前端运行在 http://localhost:3000
```

打开浏览器访问 **http://localhost:3000** 即可使用！

> 前端通过 Next.js rewrites 代理所有 `/api/*` 请求到 Go 后端，无需担心跨域问题。

### 方式三：Docker 一键部署

```bash
docker compose up -d
# Web: http://localhost:3000  API: http://localhost:8080
```

### 方式四：命令行快速体验（无需启动 Web）

```bash
# 交互式菜单
python -m core.cli

# 一句话生成角色（免费 Pollinations，无需 API Key）
python -m core.cli generate "蓝发猫耳少女，白色背景，日系赛璐璐风格" --deploy-desktop

# 运行桌宠
python -m core.cli pet

# 与角色对话（需要配置 LLM API Key）
python -m core.cli chat
```

### 验证安装成功

```bash
# 检查 Go API 是否正常
curl http://localhost:8080/api/health
# 应返回: {"success":true,"message":"Live2D API 服务正常运行"}

# 检查前端是否能代理到后端
curl http://localhost:3000/api/health
# 应返回同样的结果
```

---

## 🏗️ 项目架构

```
Live2D-Master-Agent/
├── core/                    # 🐍 Python 核心内核
│   ├── segment_engine/      #   语义分割（SAM+ISNet+Amodal）
│   ├── image_gen/           #   AI 图像生成（Pollinations/Seedream/SenseNova）
│   ├── character/           #   角色一致性系统（卡片+Embedding）
│   ├── psd/                 #   PSD 读写与校验
│   ├── qa/                  #   质量检测引擎
│   ├── utils/               #   图像/文件工具集
│   ├── config.py            #   安全配置管理
│   ├── workflow.py          #   全流程编排引擎
│   └── cli.py               #   命令行入口
├── live2d_builder/          # 🦴 Live2D Cubism4 构建管线
│   ├── mesh/                #   Delaunay 网格生成 + UV 展开
│   ├── bones/               #   36 骨骼层级 + 变形器
│   ├── blendshapes/         #   28 标准表情参数
│   ├── physics/             #   头发/裙摆/呼吸物理
│   ├── exporter/            #   model3.json + physics3.json + 纹理图集
│   └── validator/           #   模型合法性校验
├── drivers/                 # 🎯 实时驱动层
│   ├── face_tracker/        #   MediaPipe 468 关键点 → BlendShape 映射
│   ├── audio/               #   麦克风采集 + 音量/音调分析
│   ├── desktop_pet/         #   跨平台透明窗口桌宠
│   └── live2d_runtime/      #   软件 Live2D 渲染器（参数驱动）
├── llm_bridge/              # 💬 AI 对话网关
│   ├── providers/           #   OpenAI/Anthropic/Ollama 多模型
│   ├── tts/                 #   Edge TTS（免费）/ OpenAI TTS
│   ├── asr/                 #   Whisper/FunASR 语音识别
│   ├── emotion/             #   情绪分析（7 类→表情+动作）
│   └── chat_session.py      #   对话管理 + 语音指令
├── api/                     # 🔷 Go REST API（Gin 高性能）
│   ├── handlers/            #   路由处理（角色/生成/聊天/WebSocket）
│   ├── services/            #   业务逻辑（Python桥接/缓存/WS Hub）
│   ├── models/              #   数据模型
│   └── config/              #   配置管理
├── web/                     # ⚛️ Next.js 工作台
│   ├── pages/               #   8 页面（仪表盘/角色/生成/分层/Live2D/预览/聊天/导出）
│   ├── components/          #   24+ React 组件
│   ├── lib/                 #   API 客户端/WS/Live2D 播放器
│   └── types/               #   TypeScript 类型定义
├── assets/                  # 📦 资产存储
│   ├── characters/          #   角色卡片 JSON
│   ├── models/              #   AI 模型权重
│   └── output/              #   生成产物
├── scripts/                 # 🔧 工具脚本（模型下载等）
├── deploy/                  # 🚀 部署配置（Docker）
├── tests/                   # 🧪 测试套件（unit/integration/e2e）
├── docs/                    # 📚 文档
├── install.py / .sh / .bat  # 🔨 一键安装程序
├── Dockerfile               # 🐳 容器化
└── docker-compose.yml       #   多服务编排
```

---

## 🎯 核心功能

### 1. AI 角色生成
- **多 Provider**：Pollinations（免费）/ Seedream（火山引擎）/ SenseNova（商汤）
- **生产级 Prompt**：4096×4096 透明背景、正面朝向、五官对称、赛璐璐风格
- **自动 QA**：边缘清晰度、颜色分离度、背景检测

### 2. 语义分层引擎
- **ISNet Anime-Segmentation**：二次元主体精准抠图
- **SAM**：实例语义分层（头发/五官/衣物/配饰）
- **Amodal Completion**：遮挡区域像素补全（被头发遮挡的脸部等）
- **18 层标准顺序**：头皮→后发→中发→前发→眉毛→眼睛→口鼻→脸→颈→上衣→内衣→手臂→手→裙摆→腿→配饰→兽耳/尾→特效

### 3. Live2D Cubism4 自动绑定
- **Delaunay 三角网格**自动生成（边界细分+内部网格）
- **36 骨骼**标准层级自动排布
- **28 BlendShape**：眨眼、微笑、生气、惊讶、哭泣、嘴型 A/I/U/E/O 等
- **物理引擎**：头发摆动、裙摆飘动、呼吸、兽耳/尾巴弹性
- **导出**：model3.json + physics3.json + 28 个 exp3.json + 纹理图集 + Cubism 导入指南

### 4. 实时面部捕捉
- **MediaPipe Face Mesh** 468 个人脸关键点
- **ARKit BlendShape** → Live2D 参数映射（52 系数）
- **指数平滑** + 死区滤波，低延迟 ≤75ms
- **麦克风**：RMS 音量 → 嘴型开合，基频 → 语调情绪
- **跨平台**：Windows/macOS/Linux 透明悬浮窗口

### 5. 角色一致性锁定
- **角色卡**：JSON 存档脸型/五官/配色/体型/服装/人设
- **参考图锚定**：正面/侧面/背面三视图约束生成
- **Embedding 锁定**：CLIP/颜色直方图特征注入 Prompt
- **换装系统**：同角色多套穿搭，主体形象不偏移

### 6. LLM 对话 + 语音 + 情绪联动
- **多模型**：OpenAI GPT-4o / Claude 3 / Ollama 本地 Qwen
- **流式输出**：逐字显示，实时情绪分析
- **免费 TTS**：微软 Edge TTS（中文晓晓/日语 Nanami/英文 Aria）
- **ASR**：Whisper/FunASR 本地语音识别
- **7 类情绪** → 表情 + 肢体参数联动
- **语音指令**：「换衣服」「晃头发」「收起桌宠」

### 7. Web 一站式工作台
| 页面 | 功能 |
|------|------|
| Dashboard | 总览、快捷入口、系统状态 |
| Characters | 角色卡 CRUD、参考图上传、历史存档 |
| Generate | Prompt 编辑、Provider 选择、实时进度 WS |
| Layers | 分层可视化、拖拽排序、蒙版预览、PSD 导出 |
| Live2D | 骨骼树、参数滑块、物理调试、模型导出 |
| Preview | PixiJS 实时预览、Webcam 捕捉开关 |
| Chat | 聊天界面、语音输入、表情联动 |
| Export | PSD/PNG/模型包/桌宠包/角色卡导出 |

---

## 🔧 配置 API Key

默认使用 Pollinations 免费生成，无需配置。如需高质量生成或 LLM 对话，编辑 `.env`：

```env
# 图像生成（可选）
ARK_API_KEY=your-volcengine-key
SENSENOVA_API_KEY=your-sensenova-key

# LLM 对话（可选，不设则无法聊天）
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini

# 或使用本地 Ollama（免费）
# 无需 Key，只需 ollama serve + ollama pull qwen2.5:3b
```

---

## 🧪 测试

```bash
# 运行全部测试
python -m pytest tests/ -v

# 带覆盖率
python -m pytest tests/ -v --cov=core --cov=drivers --cov=llm_bridge --cov=live2d_builder --cov-report=term

# Go 测试
cd api && go test ./... -v

# 前端构建检查
cd web && npm run build
```

---

## 📖 文档

| 文档 | 内容 |
|------|------|
| [快速入门](docs/QUICKSTART.md) | 5 分钟跑通全流程 |
| [用户指南](docs/USER_GUIDE.md) | 完整功能使用说明 |
| [常见问题](docs/FAQ.md) | 遇到问题先看这里 |
| [已知局限](docs/LIMITATIONS.md) | 功能边界说明 |
| [架构设计](docs/ARCHITECTURE.md) | 技术架构详解（流程图/数据流/选型） |
| 🏛️ **[架构决策中心](docs/architecture/index.md)** | **Staff Engineer 出品：6 条核心 ADR + 系统图 + 10 上下文映射 + 16 条架构不变量 + 12 项风险登记册** |
| [部署指南](docs/DEPLOY.md) | 云端/本地部署教程 |
| [开发规范](docs/CODE_STANDARD.md) | 代码贡献指南 |

---

## 🤝 相关开源生态

- [MediaPipe](https://mediapipe.dev/) — 实时面部/手势捕捉
- [Segment Anything](https://github.com/facebookresearch/segment-anything) — 通用语义分割
- [anime-segmentation](https://github.com/SkyTNT/anime-segmentation) — 二次元专用分割
- [pixi-live2d-display](https://github.com/nicxfer/pixi-live2d-display) — Web Live2D 渲染
- [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) — AI VTuber 框架
- [VTube Studio](https://github.com/DenchiSoft/VTubeStudio) — 专业 VTuber 软件

---

## 🗺️ 版本 Roadmap

| 阶段 | 版本 | 核心方向 | 状态 |
|------|------|----------|------|
| 现在 | **v10.0** | 全流程打通（AI生成→分层→Live2D→驱动→对话→工作台） | ✅ 已发布 |
| 近期 | v10.5 | 自定义画风 / 多角色换装编辑器 / VTube Studio 插件直连 | 🚧 开发中 |
| 中期 | v11.0 | ComfyUI 工作流集成 / SDXL 本地推理 / 中文 ASR 优化 | 📋 规划中 |
| 远期 | v12.0 | 3D VTuber 支持（VRM 导出）/ 实时动作捕捉（全身）/ 多模态输入 | 🔮 构思中 |

> 💡 欢迎在 **Issues** 里提需求，每一条 Star 和 Issue 都是我们迭代的方向。

## 🤝 如何贡献

本项目采用 **Apache-2.0 License**（v9.0 原版许可，已于 2026-07-30 恢复），**欢迎所有形式的贡献（包括商业用途衍生）**；提交 PR 即视为您同意将代码以 Apache-2.0 协议并入本项目，无任何额外限制。

### 贡献方式
1. **提交 Issue** — 反馈 Bug、功能建议、体验问题
2. **Pull Request** — 修复 Bug、新增功能、优化文档、完善测试
3. **分享作品** — 用本项目生成的角色、模型、二创（保留署名即可）
4. **文档翻译** — 英文/日文文档翻译校对
5. **教程创作** — 视频教程、图文教程、使用心得

### 贡献流程
1. Fork 本仓库 → 创建分支 `git checkout -b feat/your-feature`
2. 完成开发 → 确保测试通过 `pytest tests/` 与 `npm run build`
3. 提交 PR → 附上改动说明与测试截图
4. Code Review 通过 → 合并进主分支

详细规范见 [docs/CODE_STANDARD.md](docs/CODE_STANDARD.md)。

## 💬 社区与反馈

| 渠道 | 说明 |
|------|------|
| **GitHub Issues** | Bug 反馈、功能建议、技术讨论 |
| **Discussions** | 使用心得、作品分享、需求投票 |
| **Wiki** | 常见问题、进阶教程、FAQ |

> 遇到任何使用障碍，先看 [docs/FAQ.md](docs/FAQ.md) 与 [docs/LIMITATIONS.md](docs/LIMITATIONS.md)，大多数问题都有现成解答。

## 🙏 致谢

感谢以下开源项目与社区为本项目提供了重要技术基础设施：

- **MediaPipe** — 面部捕捉底层能力
- **Segment Anything (Meta)** — 通用语义分割模型
- **anime-segmentation** — 二次元专用抠图
- **pixi-live2d-display** — Web 端 Live2D 渲染
- **Open-LLM-VTuber** — AI VTuber 工程实践参考
- **VTube Studio** — Live2D VTuber 行业标杆
- **Pollinations** — 免费 AI 图像生成服务

以及每一位 Star、Issue、PR 贡献者 ❤️。

---

## ⭐ 支持我们

如果这个项目对你有用，欢迎给我们一个 **Star** ⭐，这是我们持续迭代的最大动力。

同时欢迎：
- 将本项目推荐给身边的 VTuber / 画师 / 二次元创作者
- 在社交媒体分享你生成的角色（记得 tag 我们）
- 提交 Issue 告诉我们你想要的功能

---

## 📄 许可证

本项目采用 **Apache-2.0 License**，详见 [LICENSE](LICENSE) 文件。



---

<div align="center">

**Made with ❤️ by Live2D Master Agent Team**

</div>
