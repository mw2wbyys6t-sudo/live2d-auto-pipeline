# P-1 项目理解报告（只读勘察）

> 勘察日期：2026-10-16。方法：全仓库通读 + grep/ast/只读命令佐证；未改动任何产品代码。
> 真相源优先级：代码 > 测试 > 文档。文档与代码冲突处单列于 §6。
> 本报告是 P0~P3 的设计依据；所有结论附文件:行号证据。

## 1. 真实架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│ Tauri 2 壳 (desktop/, Rust)                                          │
│  - lib.rs: spawn live2d-api.exe (-host 127.0.0.1) → 轮询 TCP 8080    │
│  - webview 直接 External http://localhost:8080（UI 由 Go 内嵌服务）   │
│  - fallback: desktop/dist/index.html（后端失败提示页，非 Next 产物）  │
└──────────────┬──────────────────────────────────────────────────────┘
               │ HTTP/WebSocket（回环，无鉴权，CORS/CSRF/Host 护栏）
┌──────────────▼──────────────────────────────────────────────────────┐
│ Go API (api/, gin, 单进程)                                           │
│  - webui/embed.go: go:embed 内嵌 Next 静态导出 (web/out → dist/)     │
│  - python_bridge.go: exec.Python 拉起 core/workflow.py / 内联脚本    │
│  - native_bridge.go: spawn Rust face_tracker.exe（stdin JSON 协议）  │
│  - image_generator.go: workflow 链 → 失败回退 local_image_generator   │
└──────┬───────────────────────────┬─────────────────────┬───────────┘
       │ PYTHONPATH=scripts_dir     │ stdin/stdout JSONL   │ /output/*
┌──────▼──────────────┐   ┌─────────▼──────────┐   ┌─────▼──────────┐
│ Python 核心          │   │ Rust face_tracker    │   │ 输出目录        │
│ core/workflow.py     │   │ (native/face_tracker) │   │ (当前写安装目录) │
│  image_gen/router    │   │  YuNet ONNX + ort    │   └────────────────┘
│  segment_engine     │   │  load-dynamic        │
│ live2d_builder/      │   └──────────────────────┘
│  moc3_pipeline(自研) │
│ drivers/live2d_runtime│ ← live2d-py v3 子进程隔离验收（一致性/像素）
│ drivers/desktop_pet  │
│ llm_bridge/（聊天/TTS，非主链路）
└─────────────────────┘
```

**两套后端并存（不打包进基础包的那套）**：`api_server.py` 是 FastAPI 降级实现，多数 POST 为 202 桩（LIMITATIONS.md §6），端口同为 8080 不能与 Go 同跑；主链路只走 Go。

## 2. 用户链路逐跳映射（成功标准 #3 拆解）

| 跳 | 用户动作 | 前端 | Go 端点 | Python/内核 | 真实状态 | 证据 |
|---|---|---|---|---|---|---|
| 1 | 输入一句话 | `/generate` generateStream | `POST /api/generate/character` (handlers.go:206) | `core/workflow.py --json --live2d-export`（image_generator.go:94-254）；ProviderRouter 优先级 seedream→sensenova→openai→pollinations→local_placeholder（router.py:57） | **已实现+多级降级**；无 Key 联网走 pollinations（pollinations.py:37 免费无 Key）；断网落 local_placeholder 确定性剪影+LOCAL-PLACEHOLDER 水印（local_image_generator.py:85-90；local_placeholder.py:84） | 已实现 |
| 2 | 立绘出现在页面 | getLatestGeneration | `GET /api/generations/latest` | latest_generation.json（latest_generation.go:20） | 已实现 | 已实现 |
| 3 | 一键分层 | `/layers` segmentImage | `POST /api/segment` (handlers.go:459) | SemanticSegmenter：ISNet/rembg 学不到 → **HSV 颜色启发式**（semantic.py:193,807-846，纯 numpy 可无 cv2 回落 `_rgb_to_hsv`）；sam2_gd 显式指定时缺 torch 直接 ModelUnavailable 不偷换（semantic.py:163-169） | **诚实降级可用**（颜色分层）；学习式分割按需下载 | 降级可用 |
| 4 | 一键导出 moc3+model3.json+图集 | `/export` exportModelZip | `POST /api/export/live2d` download=true（handlers.go:940-975） | `live2d_builder.pipeline.Live2DBuilder.build()`（pipeline.py:93-248）→ compile_export_moc3 自编译 moc3 → live2d-py 子进程一致性/motion/physics 探针（moc3_pipeline.py:929-957） | **已实现**；moc3 自研编译，warp 变形器过内核；rotation 静态姿态；网格键形全局关闭（moc3_pipeline.py:280 MESH_KEYFORMS_ENABLED=False） | 已实现（带诚实标注） |
| 5 | 预览拖滑块形变 | `/preview` | `GET /api/generations/models` | **Web 侧自研 pixi v7 平面网格假人**：不加载 moc3，不使用 Cubism SDK；读 model3.json 的 PreviewLayers/Textures/Parameters，手写顶点位移（live2d-player.ts:161-234,489-595）；眨眼/张嘴/转头/呼吸为顶点近似 | **已实现（近似渲染，非官方内核画面）**；官方内核像素验收在服务端 verify.py | 已实现（近似，如实标注） |
| 6 | 官方内核验收 | 部署闸门 | `drivers/live2d_runtime/verify.py`（desktop_verification.go:50-65） | live2d.v3 + pygame GL + glReadPixels 逐像素（verify.py:42-66） | **代码就绪，依赖未随包**；见 §4 关键纠正 | 代码就绪 |
| 7 | 摄像头面捕 | `/preview` native-tracker | `POST /api/tracking/start` → native_bridge.go spawn Rust exe | YuNet ONNX 检测 + 5 点几何启发式 BlendShape（非官方面部模型） | **二进制/模型/DLL 未进包**（CI 缺失） | 未打包 |
| 8 | 桌宠 | deployDesktop | `POST /api/deploy/desktop` | native_window.py（live2d-py+GL，Windows 色键透明）或 pygame PNG 层桌宠 | 代码就绪；依赖 live2d-py；兼容性实测后决定启用/禁用 | 条件就绪 |

## 3. 能力真实状态表

| 能力 | 状态 | 说明 / 证据 |
|---|---|---|
| 免费云生图（pollinations，无 Key） | 已实现 | pollinations.py:37-80，90s 超时×3，SSRF 护栏在 base.py |
| 离线占位图（水印明示） | 已实现 | local_image_generator.py:41-91；metadata `local_placeholder=True` |
| HSV 颜色分层（无模型） | 已实现 | semantic.py:807+，纯 numpy 可跑；cv2 可选 |
| KMeans 兜底分层 | 已实现（sklearn 可选） | kmeans.py:26-37，缺 sklearn 退化为颜色量化 |
| sam2_gd 学习式分割 | 按需下载（不进基础包） | 缺 torch/权重抛 ModelUnavailable，不降级（sam2_gd.py:292-399） |
| 自研 moc3 编译（warp） | 已实现 | moc3_pipeline.py；warp 过官方内核+像素验收（LIMITATIONS §3） |
| rotation 变形器 | 静态姿态 | 结构过内核，无参数绑定，记入 deformers_static |
| 网格键形 | 全局关闭 | ≥2 真实网格概率性触发内核段错误（moc3_pipeline.py:272-280） |
| Web 预览（pixi 近似形变） | 已实现 | 非 Cubism 运行时，滑块驱动顶点位移 |
| 官方内核逐像素验收 | 代码就绪/依赖待打包 | verify.py；需 live2d-py v3 + PyOpenGL + pygame |
| Rust 面捕（YuNet） | 代码就绪/资产待打包 | exe 由 CI 编译；onnx 模型+onnxruntime.dll 未下载进包 |
| pygame PNG 桌宠 | 已实现（可选） | drivers/desktop_pet/animator.py |
| live2d-py GL 原生桌宠 | 条件就绪 | native_window.py；需实测 0.8.1 内核兼容 |
| LLM 聊天（SSE） | 已实现（可选 Key） | llm_bridge/providers；无 Key 走 local/Ollama 探活 |
| Edge TTS | 已实现（在线可选） | llm_bridge/tts/edge_tts.py；未接入 HTTP 端点 |
| ASR（whisper/funasr） | 按需下载 | 重依赖，懒加载 |
| rembg/ISNet 去背 | 按需下载 | semantic.py:43-46 可选导入 |
| api_server.py（FastAPI） | 桩为主 | 不进基础包；仅测试需要其依赖 |
| LoRA 训练 / ControlNet | 桩/不可用 | LIMITATIONS §7，不宣传 |

## 4. 关键技术纠正（相对任务书「事实基线」的核实结果）

### 4.1 live2d-py 版本：必须钉 0.8.1，不能用 1.0.0（重要）

任务书事实 A 称 1.0.0 提供 cp311-abi3-win_amd64 wheel（已核实：存在，0.45MB，内置 Cubism 5-r.5）。**但 1.0.0 移除了 `live2d.v3` 模块并重构为统一 `Model` API**（PyPI wheel 实测：125 条条目里 `live2d/v3/` 为 0；`_live2d.pyi` 为 v2/v3 统一 Model）。而本仓库验收链全部 `import live2d.v3`：

- `drivers/live2d_runtime/verify.py:15` `import live2d.v3 as sdk`，调用 `sdk.init()/glInit()/clearBuffer()/glRelease()/dispose()`、`LAppModel`、`HasMocConsistencyFromFile`、`GetParamIds`、`SetParameterValue`、`SetAutoBlinkEnable`；
- `drivers/live2d_runtime/moc3_consistency.py:20`、`moc3_load.py`、`moc3_motion_probe.py`、`moc3_physics_probe.py` 同；
- 集成测试 `find_spec("live2d.v3")` 守卫（test_moc3_official_acceptance.py:30 等）。

**核实**：`live2d_py-0.8.1-cp311-abi3-win_amd64.whl`（0.64MB，MIT，Requires-Dist: pyopengl/pillow/numpy）含完整 `live2d/v3/`（`_v3cpp.pyd` + `_common.dll` + FrameworkShaders），API 面与 verify.py 调用逐一吻合。**决策：基础包钉 `live2d-py==0.8.1`（abi3，3.11/3.12/3.13 通吃）；1.0.0 迁移另立 ADR，不在本次降级风险内引入。** embeddable Python 选 3.12.x。

### 4.2 硬依赖核实（ast 静态扫描 + 导入守卫）

- **cv2（opencv）是硬依赖**：`live2d_builder/mesh/generator.py:16` 顶层 `import cv2` 无守卫，MeshGenerator 在 Live2DBuilder.__init__ 必构造（pipeline.py:68）→ 基础包必须含 **opencv-python-headless**（不用 opencv-python，避免 GUI 库）。
- **scipy 硬依赖**：mesh/generator.py:19 `from scipy.spatial import Delaunay` → 必装。
- **scikit-learn 可选**：kmeans.py:26 有 ImportError 守卫，缺它退化为颜色量化；为分层质量仍放入基础包。
- **onnxruntime（Python 包）不进基础包**：Rust 面捕用自己的 onnxruntime.dll（ort load-dynamic），Python onnxruntime 只服务 ISNet/rembg 可选路径。
- **py-opengl + pygame 进基础包**：verify.py GL 像素验收（verify.py:14-16）与桌宠需要。

### 4.3 进程树清理缺口（成功标准 #6）

- `desktop/src/lib.rs:121-128` `kill_backend` 仅 `Child::kill()`：无 Windows Job Object、Unix 无 killpg；Go 的 Python 子进程、face_tracker、桌宠在关窗后孤儿化。
- Go 侧：python_bridge 有进程组灭杀（python_process_windows.go:12 `taskkill /T /F`），但 `image_generator.go:168-180` workflow 链仅 `Process.Kill()` 直接子进程且用 `context.Background()`，客户端断连不取消 —— 两条链语义不一致，需统一。
- Go 信号处理器（main.go:196-201）只 ShutdownNativeBridge，不回收桌宠进程、不调 server.Shutdown。

### 4.4 数据目录违规（成功标准 #9）

当前所有产物写进 `ScriptsDir/output`（config.go:252 `filepath.Join(baseDir,"output")`；image_generator.go:101；upload_service.go:62；character_service.go:24 写 baseDir/assets/characters）。安装目录（NSIS currentUser 下 `%LOCALAPPDATA%\Programs\...`）不可写/卸载应清空 → **必须实现 LIVE2D_DATA_DIR，拆分 `%APPDATA%\PygmalionStudio`（config/logs/缓存）与「文档\PygmalionStudio」（output/角色库）**。Go 目前不读 LIVE2D_DATA_DIR（grep 零命中），Python `core/config.py:280-286` 用 OUTPUT_DIR/LIVE2D_PROJECT_ROOT。

### 4.5 打包现状（根因确认）

> **已修复（ADR-007）**：Tauri 现在先由 `scripts/build_desktop_bundle.py` 装配
> `desktop/bundle/`（嵌入式 Python 3.12 + 全量离线依赖 + app 源码树 +
> Go/面捕 exe + onnxruntime.dll + YuNet / face_landmarker 模型），再由
> `tauri.conf.json` 的 `resources: {"bundle/": ""}` **扁平**打进 `$RESOURCE`；
> `find_go_binary` 已扩展候选路径。以下为修复前的审计记录，保留以说明根因。

tauri-build.yml 仅 bundle `desktop/bin/*`（live2d-api.exe + face_tracker.exe）：无 Python、无 app 源码、无 onnx 模型/DLL、无 webviewInstallMode 配置（tauri.conf.json:15-37）。三套打包互不相通：Tauri NSIS（A）、Inno+CUDA venv（B，desktop.iss 写死 0.10.2、依赖作者机 torch/权重/iscc）、单文件 Go exe（C，build_desktop.bat）。DEPLOY.md:14-19 把 B 的内容安到 A/C 头上，属文档虚假。

## 5. 路由表（Go，main.go:208-268）

| Method | Path | Handler | 用途 |
|---|---|---|---|
| GET | /api/health | HealthCheck | Python 探针（python --version + PIL/numpy），失败 503 |
| GET | /api/status,/api/info,/api/models,/api/expressions | — | 状态/元信息（models 为硬编码列表） |
| POST | /api/generate | GenerateImage | 旧版生图（本地生成器） |
| POST | /api/generate/character | GenerateCharacter | **主链路**：workflow + WS 进度 |
| POST | /api/segment | SegmentImage | 分层 |
| POST | /api/upload | UploadImage | 25MB 图片上传 |
| POST | /api/psd-plan,/api/export/psd | — | KMeans PSD 规划 |
| POST | /api/export/live2d | ExportLive2D | **导出 moc3 包（zip 下载/JSON）** |
| POST | /api/deploy/desktop | DeployDesktop | 桌宠（先过 verify.py 闸门） |
| GET | /api/deploy/desktop/status | — | 桌宠状态 |
| GET/POST/PUT/DELETE | /api/characters[/:id] | character_service | 角色 CRUD/参考图/embedding |
| POST | /api/chat,/api/chat/stream | Chat/ChatStream | LLM（SSE） |
| GET | /api/ws | WSHandle | 进度+面捕帧广播 |
| POST | /api/tracking/start\|stop, GET /api/tracking/status | native_bridge | Rust 面捕控制 |
| GET | /api/generations/latest,/api/generations/models | — | 跨页接力/模型列表 |
| POST | /api/import/psd,/api/import/pngs | — | 素材导入（500MB 上限） |
| GET | /api/providers | ListImageProviders | 上游可用性 |
| GET | /api/gpu | GetGPUStatus | 本机 GPU + 推理运行时探测（判定在 core/gpu_probe） |
| GET | /api/scripts,/api/cache/stats, POST /api/cache/clear | — | 工具 |
| GET | /output/*filepath | ServeOutput | 产物静态服务（防目录列举/穿越） |
| — | SPA NoRoute | main.go:301-363 | 内嵌静态站回退 |

安全中间件（已核实，不回退）：回环默认绑定（config.go:223）、Host 白名单防 DNS rebinding（main.go:104,481-494）、CSRF 跨站护栏（main.go:110,598-619）、CORS 严格白名单（main.go:128-146）、按路由 body 上限（10/25/500MB）、CSP/Permissions-Policy（main.go:504-518）、不信任代理（SetTrustedProxies(nil)）、限流回环豁免、validatePath 注入/穿越校验（python_bridge.go:45-88）、zip 跳过 symlink、SSRF DNS 钉扎在 Python 侧（core/image_gen/base.py:91-156）。

## 6. 文档与代码冲突清单

| # | 文档声称 | 代码事实 | 处置 |
|---|---|---|---|
| D1 | DEPLOY.md:14-19 安装包含便携 Python/SAM 权重/OpenCV/FFmpeg | tauri-build.yml 只打两个 exe | P0 重写 DEPLOY |
| D2 | DESKTOP.md:21 路线图选 Wails、零新依赖 | 仓库已落地 Tauri 壳（desktop/） | P3 对齐文档 |
| D3 | api/README.md:145-148 的 LIVE2D_API_PORT/LIVE2D_SD_WEBUI_URL/LIVE2D_PYTHON_PATH/LIVE2D_OUTPUT_DIR | Go 代码零读取（仅 LIVE2D_API_HOST/GO_API_HOST/LIVE2D_ALLOWED_HOSTS） | 修文档或补实现（补 LIVE2D_DATA_DIR/PORT） |
| D4 | requirements.txt:78 注释「live2d-py 无预编译 wheel，需 cmake」 | PyPI 0.8.1/1.0.0 均有 abi3 win wheel | 更新注释，钉 0.8.1 |
| D5 | desktop.iss:26 版本 0.10.2；install.py 横幅 v0.10.0；requirements 横幅 v0.10.2 | 真源 0.10.6 → 本次升 0.11.0 | P3 全量统一 |
| D6 | README/Release 表宣称 ~22MB 单文件 exe / 24MB portable.zip | 无脚本生产这两个产物 | 删除虚假条目 |
| D7 | chat.tsx:126 离线提示旧端口 :12009 | 实际 8080 | P2 修正 |
| D8 | /live2d 页 Model tree/Physics 面板 | 硬编码静态展示（LIMITATIONS §5 已承认） | 保持标注，不伪装 |
| D9 | tauri.conf.json csp:null | Go 内嵌站自带 CSP 头；webview 直连 localhost | P0 评估补 Tauri CSP |
| D10 | moc3-acceptance「CI 装 live2d-py 跑像素」 | 默认 job skip；本次改为随包内置 + Windows 闸门实跑 | P1 |

## 7. 依赖/许可证/安全面初扫（三组待办的基线）

- **Go**：直接依赖仅 gin v1.12.0 + gzip v1.2.6；indirect 残留 mongo-driver v2.5.0、quic-go v0.59.0 零引用 → `go mod tidy` 缩供应链面。无已知高危版本。
- **Python**：requirements 全部 `>=` 无上界（可被新版 break）；CVE 下限已注释（requests/urllib3/aiohttp/cryptography/python-multipart）。本次桌面清单改**精确钉版本 + hash**。
- **npm**：仅 4 运行时依赖（next 16.4.0 / react 18.3.1 / pixi 7.4.3 / lucide-react），无 postinstall 脚本；pixi v8 被 dependabot 忽略（API 不兼容）。
- **许可证**：项目 AGPL-3.0 + 商业授权双轨（ADR-001 Rev.5，2026-10-29）；待补：安装包随附 LICENSE、model3.zip 内嵌 LICENSE。live2d-py MIT、onnxruntime MIT、YuNet(OpenCV Zoo Apache-2.0)、ort BSD-3、nokhwa MIT、scipy/sklearn BSD、Pillow HPND、psd-tools MIT、py-moc3 MIT —— 第三方依赖均为宽松许可，可与 AGPL 组合（衍生整体按 AGPL 发布）；SAM2 权重（按需下载）遵循 Apache-2.0 模型许可。
- **89 安全项**：以现有 p1-p4 测试契约为基线，本次不回退任何加固；新增面（打包后）：WebView2 缺失引导、杀软拦截中文提示、中文/空格/长路径/非 ASCII 用户名。

## 8. 风险登记表（交付视角）

| ID | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R1 | live2d-py 0.8.1 内核加载自研 moc3 兼容性 | 中（已降级） | **本地 x86_64 Core 6.0 一致性已实测通过**（`HasMocConsistencyFromFile=true`，smoke+联网两条链）；Windows Core 二进制由 P1 `--verify-core` 闸门复核；逐像素 verify.py 需活动 GL，交 moc3-acceptance |
| R2 | embeddable Python + pip 离线装配（._pth 放开 site）踩坑 | 中 | P0 PowerShell 脚本确定性处理，CI 离线复现 |
| R3 | 安装包体积（CPU wheel：opencv-headless ~40MB、scipy ~40MB、sklearn ~30MB、numpy ~18MB、live2d-py <1MB） | 中 | 实测构成报告；目标 ≤150MB；torch 系全部排除在基础包外 |
| R4 | 关窗进程树残留（Python/face_tracker/桌宠） | 高 | P0 Job Object / taskkill /T；P1 端口释放+进程断言 |
| R5 | 输出目录写安装目录（非 ASCII/长路径/卸载残留） | 高 | LIVE2D_DATA_DIR 拆分 %APPDATA% 与 Documents |
| R6 | WebView2 在 Win10 缺失 → 白屏 | 中 | tauri.conf webviewInstallMode=bootstrapper + 离线中文指引 + 自检页 |
| R7 | pollinations 免费服务不稳定/限流 | 中 | 3 次重试 + 占位图兜底，UI 明示来源 |
| R8 | HSV 分层质量粗（非干净 AI 分割） | 中 | UI 明示「颜色分层（实验性）」，SAM2 按需下载器作为升级路径 |
| R9 | 离线装配 hash 损坏文件 | 中 | 全部下载物锁版本+SHA256，失败中文兜底，不静默使用 |
| R10 | 端口 8080 被占 | 中 | 现有中文提示（main.go:179-190）；P2 自检页探测 |
| R11 | Tauri resources 路径解析（resource_dir vs exe 同级） | 中 | lib.rs find_go_binary 扩展资源搜索；ADR 对比 sidecar |
| R12 | CI Windows 上 GL 像素验收（verify.py）无会话桌面 | 中 | windows-latest 有交互会话；失败则退一致性+加载判据并如实标注 |

## 8b. 本地冒烟证据（2026-10-16，沙箱 Python 3.10）

在仅装系统 cv2/scipy 的环境下直接跑 `core/workflow.py --json --no-semantic --width 512 --height 512 --seed 42`：

- **生图真实走通**：provider=`pollinations`（联网免费云，非占位图），产出 `character_*.png`（512×512 RGB）；
- **分层产出 11 个 layer_000..010.png** + `LAYER_GUIDE.txt` + PSD 降级包（无 psd_tools → PNG package，符合 fallback 设计）；
- **moc3 真实落盘**：`generated_character.moc3` 150,336 字节，头 `MOC3 02 00 00 00`；同目录 model3.json、4 页纹理图集、28 个 exp3.json、physics3.json、meshes.json、CUBISM_IMPORT_GUIDE.md；
- **内核闸门如实失败**：build_meta.json `runtime_ready=false`，blocker=「官方内核拒绝: 检查环境不可用: 未安装 live2d-py」——没有假装通过，符合诚实文化；
- 全程 2.9s（CPU，512px），证明主链路在无 torch/无 Key 环境下可跑完，唯一缺口是 live2d-py 验收依赖未随包。

## 8c. 下载物可达性核实（2026-10-16）

- `live2d_py-0.8.1-cp311-abi3-win_amd64.whl`（0.64MB，MIT）PyPI 200，含完整 live2d/v3（`_v3cpp.pyd`+`_common.dll`+shaders），API 面与 verify.py 吻合；
- `live2d_py-1.0.0-cp311-abi3-win_amd64.whl`（0.45MB）存在但**无 live2d.v3 模块**，不兼容本仓库验收链（§4.1）；
- python.org FTP：`python-3.12.10-embed-amd64.zip` 200（11.1MB）；3.12.11+ 已从 FTP 下架（404），**基础包钉 3.12.10**；
- YuNet `face_detection_yunet_2023mar.onnx`（opencv_zoo raw）与 onnxruntime-win-x64-1.31.0.zip（ort rc.14 要求 ≥1.31）URL 见 download_models.bat，P0 脚本加 SHA256。

## 9. P0 设计决策预告（详见 P0 ADR）

1. 安装布局：Tauri `resources` 下 `bin/`（live2d-api.exe、face_tracker.exe、onnxruntime.dll、models/yunet.onnx）、`python/`（embeddable 3.12 + 离线 site-packages）、`app/`（Python 源码树 core/drivers/live2d_builder/llm_bridge/prompts/templates + local_image_generator.py）；首启由 lib.rs 在 `%APPDATA%\PygmalionStudio` 生成 config.json（python_path/scripts_dir/data_dir 全部指向资源与用户目录），spawn 追加 `-config`。
2. 保留 resources + `-config` 最小改动路线（不升 sidecar），另立 ADR 记录 sidecar 对比。
3. requirements.desktop.txt 精确钉版本 + hash；torch/rembg/mediapipe/sounddevice 全部按需下载。
4. tauri-build.yml 先跑 PowerShell 打包脚本（下载校验 embeddable Python → 修 ._pth → 离线装配 → 拷 app → 下载校验 YuNet/DLL → 生成 BUILD-INFO）再 cargo tauri build。
