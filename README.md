# Pygmalion Studio

> 一句中文 → 会呼吸的 Live2D 角色。生图、分层、建模、导出，全在本地一条流水线里完成。

![许可证](https://img.shields.io/badge/license-AGPL--3.0%20%2B%20commercial-blue)
![平台](https://img.shields.io/badge/platform-Windows-0078D4)
![版本](https://img.shields.io/badge/version-0.10.11-ff5c9d)

---

<!-- ============================================================
     作者补写区（约一半篇幅）
     下面四节由项目作者填写；建议每节 2–5 段/3–6 条，写你真实的想法即可。
     写完把 ✍️ 行删掉。
     ============================================================ -->

## 为什么做这个项目

> ✍️ **待作者补写**
>
> 建议覆盖：
> - 你最初遇到的痛点是什么（例如：有了立绘却卡在分层/绑定；商业工具门槛高）
> - 现有工具（Live2D Cubism、各类"一键生成"服务）缺了什么
> - 你想把哪一段变得"普通人也能跑通"

## 这个项目面向谁

> ✍️ **待补写**
>
> 建议给三组人各写一句：独立 VTuber / 同人创作者 / 想批量出草稿的小团队。
> 同时写清楚"可能不适合你"的情况，避免预期错位。

## 界面预览

> ✍️ **待补写**
>
> 建议放 4 张图（存到 `docs/assets/screenshots/` 后引用）：
> 1. 桌面启动页（图标 + Pygmalion Studio）
> 2. 工作台「生成」阶段（三栏：作品库 / 画布 / 操作台）
> 3. 模型库（作品命名、分类、一个作品多个模型）
> 4. 导出阶段（模型命名 + 导出结果）

## v0.10.11 发布说明

> ✍️ **待补写**
>
> 建议只讲三件事：本版最值得升级的点、升级方式（覆盖安装/先卸载）、已知问题。
> 技术细节可直接链接到 [CHANGELOG.md](CHANGELOG.md)。

<!-- ===================== 作者补写区结束 ===================== -->

---

## 现在能做什么（v0.10.11）

- **两条入口链路**，按你手里已有的东西选：
  - **从零开始**：一句中文 → 立绘 → 分层 → Live2D 模型 → 导出
  - **已有图片**：上传现成立绘，跳过生成，直接从分层开始
- **单页三栏工作台**：作品库（左） / 阶段画布（中） / 操作台（右），生成、分层、预览、导出都在一页完成
- **模型库**：作品可以命名、自定义分类、按阶段筛选；**一个作品可以保存多个模型**，每个模型单独命名、独立目录，互不覆盖
- **导出的确实是 Live2D 模型**：自研 moc3 编译器产出 `.moc3` + 贴图 + `model3.json`，可导入 Live2D Cubism 与 VTube Studio
- **本地优先**：桌面安装包自带运行时，默认不联网也能生成（走本地兜底）；配了云端 Key 才走云端高质量出图

---

## 快速开始

### Windows 安装包（推荐）

> 安装包自带完整运行时（嵌入式 Python 3.12 + 依赖 + 源码树 + 模型），**无需预装 Python**。

| 项目 | 说明 |
|------|------|
| 下载 | [Release v0.10.11](https://github.com/mw2wbyys6t-sudo/live2d-auto-pipeline/releases/tag/v0.10.11) |
| 文件 | `Pygmalion Studio_0.10.11_x64-setup.exe` |
| 体积 | 约 320 MiB（<!-- BUILD_SIZE -->以 Release 页为准） |
| 平台 | Windows 10 / 11 x64 |

安装步骤：

1. 从 Release 下载 `Pygmalion Studio_0.10.11_x64-setup.exe`
2. 若装过旧版，先运行旧安装目录的 `uninstall.exe`（旧版产品名是 `Live2D Master Agent`）
3. 双击安装，装完确认安装目录含 `live2d-api.exe`、`python\`、`app\`、`models\`
4. 从开始菜单启动；桌面启动页出现后会拉起本地后端并进入工作台

安装目录默认在 `%LOCALAPPDATA%\Pygmalion Studio`。

装完自检（可选）：

```powershell
# 安装目录应含这些；缺 live2d-api.exe 就是没装对
Get-ChildItem "$env:LOCALAPPDATA\Pygmalion Studio" | Select-Object Name

# 应用启动后，后端应能自报健康状态
Invoke-RestMethod http://127.0.0.1:8080/api/health | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8080/api/diagnostics | ConvertTo-Json -Depth 6
```

仓库还带一键验收脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\verify_desktop_install.ps1 -Launch
```

> 🔏 **安装包未签名**：首次运行会出现 SmartScreen「未知发布者」，点「更多信息 → 仍要运行」即可。
> 发布后请用 Release 页给出的 SHA256 校验文件完整性。

### 命令行快速体验（30 秒核心能力）

```bash
pip install -r requirements.txt

# 一句话生成角色（默认用免费的 Pollinations，不需要 API Key）
python -m core.cli generate "蓝发猫耳少女，白色背景，日系赛璐璐风格"

# 交互式菜单
python -m core.cli
```

产物落在 `output/`。

### 从源码构建桌面应用

前置：Node.js 20+、Go 1.25+、Rust stable、Python 3.12、tauri-cli（`cargo install tauri-cli`）。

```bash
# 1) 前端静态导出（含许可文件注入）
cd web && npm ci && NEXT_STATIC_EXPORT=1 npm run build && cd ..
node scripts/version-assets.mjs web/out

# 2) 嵌入 Go 后端
rm -rf api/webui/dist && mkdir -p api/webui/dist && cp -r web/out/. api/webui/dist/
cd api && go build -trimpath -tags nomsgpack -ldflags "-s -w -H windowsgui" -o live2d-api.exe . && cd ..

# 3) 编译原生面捕
cd native/face_tracker && cargo build --release && cd ../..

# 4) 装配随包运行时（嵌入式 Python / 离线 wheel / 模型，site-packages 约 0.9 GB）
python scripts/build_desktop_bundle.py --go-exe api/live2d-api.exe \
  --face-tracker-exe native/face_tracker/target/release/face_tracker.exe

# 5) 构建桌面启动页（Vue + Vite，产出单文件 index.html）
cd desktop/launch && npm ci && npm run build && cd ../..

# 6) 打 NSIS 安装包
cd desktop && cargo tauri build --bundles nsis
# 产物：desktop/target/release/bundle/nsis/Pygmalion Studio_<version>_x64-setup.exe
```

Windows 上也可以直接跑一键脚本：`scripts\build_tauri_release.bat`。

> 装配脚本会在收尾时清掉随包运行时里的 `__pycache__` / `.pyc`，保证安装包只含运行所需文件。

### 开发模式（前后端分离调试）

需要两个终端：

```bash
# 终端 1 — Go 后端（端口 8080）
cd api && go run .

# 终端 2 — 前端开发服务器
cd web && npm install && npm run dev
```

打开 **http://localhost:3000**，前端通过 rewrites 代理到后端。

> 想直接预览内嵌版（与安装包一致）：先按上面的步骤 1–2 生成 `api/webui/dist`，再打开 <http://localhost:8080/studio>。

---

## 核心流程

```
你的描述          AI 生图           自动分层          导出
  │                │                │               │
  ▼                ▼                ▼               ▼
"蓝发猫耳少女" → 立绘 PNG → 分层 PSD → .moc3 模型包
                                    ↓
                              可导入 Live2D Cubism
                              可导入 VTube Studio
```

### 工作台

单页三栏，四个阶段：

| 阶段 | 做什么 | 产物 |
|------|--------|------|
| 生成 | 中文提示词 → 立绘（可选分辨率、生图引擎） | `output/character_<ts>.png` |
| 分层 | K-means 快速分层 / 语义分层，可调图层顺序与显隐 | `output/layers_<ts>/`（PNG + `character.psd`） |
| 预览 | 用参数与表情驱动模型，检查绑定效果 | 运行时渲染 |
| 导出 | 命名模型并打包，或交给桌面桌宠 | `.moc3` + 贴图 + `model3.json` |

### 模型库

`/models` 是独立页面，用来管理做过的作品与模型：

- 搜索作品名 / 提示词 / 模型名
- 按自定义分类、阶段标签（已分层 / 已建模 / 已导出）筛选
- 作品改名、设置分类，实时同步到工作台左栏
- 一个作品下的多个模型并排展示，点「打开工作台」继续编辑
- 页面每 8 秒自动刷新，窗口重新聚焦时也会刷新

### AI 图像生成

按优先级自动降级，不配 Key 默认走免费的 Pollinations：

| 优先级 | 服务 | 需要付费？ |
|--------|------|-----------|
| 1 | Seedream（火山引擎） | 需 Key |
| 2 | SenseNova（商汤） | 需 Key |
| 3 | OpenAI 兼容端点 | 需 Key |
| 4 | **Pollinations.ai** | **免费** |
| 5 | 本地占位图 | 免费兜底 |

### 自动分层

- **K-means 颜色聚类**（默认，纯 Python）：快，不需要额外模型
- **SAM + ISNet 语义分割**：按语义识别头发、五官、衣物，精度更高，需要模型权重（`python scripts/download_models.py`）

### Live2D moc3 导出

不是调用外部工具，而是**自己写了 moc3 编译器**：从分层 PNG 直接编译出符合 Cubism 4 规范的 `.moc3`，包含网格、骨骼、变形器、表情参数与物理模拟，并通过官方 Cubism Native Core 的逐像素验收。

---

## 项目结构

```
├── core/                  Python 核心内核（生图 / 分层 / PSD / 质检 / 编排）
├── live2d_builder/        Live2D Cubism4 构建管线（网格 / 骨骼 / 表情 / 物理 / moc3 编译器）
├── drivers/               实时驱动层（面捕 / 桌宠 / 软件渲染）
├── llm_bridge/            AI 对话网关
├── api/                   Go REST API（Gin），内嵌前端静态资源
├── web/                   Next.js 工作台（单页三栏 + 模型库）
├── desktop/               Tauri v2 外壳（启动页 / 拉起后端 / NSIS 打包）
│   └── launch/              Vue 3 + Vite 桌面启动页（单文件产物）
├── native/face_tracker/   Rust 原生面捕（YuNet + onnxruntime）
├── scripts/               构建、装配与验收脚本
├── assets/icon/           项目图标
└── docs/                  文档
```

---

## 配置（可选）

默认零配置即可跑通核心链路。需要更高质量出图或对话能力时：

```bash
cp .env.example .env
```

按需填写：

```env
# 高质量生图（不填就走免费的 Pollinations）
ARK_API_KEY=你的火山引擎Key
SENSENOVA_API_KEY=你的商汤Key

# LLM 对话（不填则该能力不可用）
OPENAI_API_KEY=sk-你的Key
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

> `.env` 已被 `.gitignore` 排除；请不要把任何密钥写进源码或提交到仓库。

---

## 已知局限

1. **出图质量取决于服务商**：免费 Pollinations 中等质量；要商业级立绘请配 Seedream。
2. **语义分层需要模型权重**：不下载就退回 K-means，效果够用但部位边界更粗。
3. **moc3 导出是"可用但粗糙"的起点**：自动绑定替代不了技术师精调。
4. **面部捕捉未接摄像头实测**：程序、模型、运行库齐备，实际驱动效果仍待验证。
5. **安装包未签名**：首次运行会触发 SmartScreen 提示。
6. **安装包体积较大**（约 320 MiB）：因为内置了完整运行时与模型。
7. 项目仍在快速迭代，接口与数据结构可能随版本变化。

详见 [docs/LIMITATIONS.md](docs/LIMITATIONS.md)。

---

## 测试

```bash
# Python 测试
python -m pytest tests/ -v

# Go 测试
cd api && go test ./... -v

# 前端类型检查 + 静态构建
cd web && npx tsc --noEmit && NEXT_STATIC_EXPORT=1 npm run build
```

---

## 版本记录

- 完整变更与发布凭据：[CHANGELOG.md](CHANGELOG.md)
- 当前版本号单一真源：`core/version.py`
- 发布流程：`scripts/build_tauri_release.bat` → 校验产物 → 打 tag → 上传 Release

---

## 文档

| 文档 | 内容 |
|------|------|
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | 最快上手路径 |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | 工作台逐项说明 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 模块与数据流 |
| [docs/UI-REDESIGN.md](docs/ui-redesign.md) | 界面重设计说明 |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | 已知局限与规避方式 |
| [docs/LICENSE-POLICY.md](docs/license-policy.md) | 第三方依赖许可策略 |
| [docs/FAQ.md](docs/FAQ.md) | 常见问题 |

---

## 如何贡献

1. Fork 并创建分支（`feat/xxx`、`fix/xxx`）
2. 改动前先跑一遍测试；涉及 UI 的附截图
3. 提交前确认没有把 `.env`、`output/`、模型权重、本机配置带进提交
4. 发起 PR，说明动机、改动范围与验证方式

---

## 致谢

感谢 Live2D 生态中的开源项目与模型作者，以及所有提交 issue、帮忙验证安装包的人。

---

## 许可证

本项目自 v0.10.10 起采用 **AGPL-3.0 + 商业授权双轨**：

- 个人 / 开源使用：遵循 [AGPL-3.0](LICENSE)
- 商业闭源分发：需要单独获得商业授权（联系方式见仓库主页）

随安装包分发第三方组件均为宽松许可（MIT / Apache-2.0 / BSD），明细与判定依据见 [docs/license-policy.md](docs/license-policy.md)。
