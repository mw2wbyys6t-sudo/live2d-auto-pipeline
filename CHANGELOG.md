# 更新日志

本项目遵循「语义化版本 + 诚实降级」原则：每个版本都明确标注**解决了什么致命问题**、**已知限制**、**如何回退**。

---

## [v0.10.8] — 2026-10-17 — 代号「Honest Desktop」

> **本版目标**：让安装包"装完真的能用"，并把"看起来能用、实际没有"的东西清掉。

### 🔴 修复（会直接坑到用户）

- **安装包不再缺运行时**：Tauri NSIS 改为自带嵌入式 CPython 3.12 + 全量离线依赖（**92 个包**，site-packages 约 **1.4 GB**）+ `app/` 源码树 + `live2d-api.exe` / `face_tracker.exe` / `onnxruntime.dll` / YuNet / `face_landmarker.task`。装配由 `scripts/build_desktop_bundle.py` 完成，下载物 SHA256 锁定，构建期冒烟 9/9 必装模块。
- **「后端未找到」根因修复**：`resources` 从数组 `["bin/*"]`（会落进 `$RESOURCE\bin\`）改为 map `{"bundle/": ""}`（扁平），并扩展 `find_go_binary` 的候选路径。
- **导出页 PSD 100% 失败**：前端改为合并外层 envelope 的 `success`（内层 `data` 本就没有该字段）。
- **角色保存/删除假成功**：去掉 `.catch()` 伪造对象与静默吞错，失败保留原状态并报错。
- **See-through 假成功**：接口从不抠图却返回 `200 + success:true`，改为 **501** + 可读原因。
- **LLM 兜底假回复**：新增 `fallback:true` 标记，并在文案前显示「离线兜底回复」。
- **Python 面捕假运行**：启动后校验 `is_running()`，失败返回 **503**。
- **PSD 降级下载 404**：`psd_path` 不再返回目录，改给 `package_dir` + `fallback_reason`。

### 🟡 移除（无效控件，改了也不影响结果）

- 生成页：Provider / 风格 / Steps / CFG。
- 导出页：Include physics / expressions / motions、Compression。
- Live2D 页：静态模型结构树、Physics 面板、Wireframe 开关。

### 🟢 如实标注

- `/api/info` 的 `features` 改为按实际探测生成，并新增 `notes`。
- `/api/models`、`/api/expressions` 明确标注「内置示例列表」。
- 仪表盘延迟改为真实测量；GPU 显示「未探测」。

### 📦 安装包变化

- 体积从 ~8 MB 变为 **306.17 MiB（321,047,178 字节）**（自包含运行时，正常）。
- SHA256：`ff7d8aba4a96e38f93a4f2d5bf9c4314dd32817a43050c321f1551b30387d248`
- **旧 v0.10.7 的 8 MB 包不可用**（缺 Python 运行时 / 源码树 / 模型），该资产已从 Release 撤下并在发布说明中加了警示；请先卸载旧版再装本版。

### 🧪 安装后自检（本版新增）

- `scripts/windows/verify_desktop_install.ps1`：检查 17 项关键文件、随包解释器与核心依赖、`/api/health` 与 `/api/diagnostics`、残留进程，输出 `PASS / PASS with warnings / FAIL`。
- 新增 `/api/diagnostics` 端点，逐项返回运行时状态（解释器路径、依赖可用性、模型、输出目录）。

### ⚠️ 尚未验证（如实标注）

- **真机安装验收未完成**：CI 已通过装配冒烟与产物检查，但"双击安装 → 启动 → 走完 5 个页面"尚未在真实 Windows 机器上录得结果。
- **原生面捕 / 桌宠**未实机验证（仅确认二进制、dll 与模型已打入安装包）。
- **安装包未签名**，首次运行会触发 SmartScreen 告警。签名管线已就绪（`scripts/windows/sign_artifacts.ps1`），默认 `L2D_SIGN_MODE=off` 不生效。

### ↩️ 回退

- 卸载后重装本版即可；用户产物在 `%APPDATA%\Live2DMasterAgent` 与「文档\Live2DMasterAgent」，卸载不清空。

### 🧾 发布证据（release receipt）

| 项 | 值 |
|---|---|
| 版本 / 标签 | `v0.10.8`（annotated tag `02592f3` → 提交 `69b27d1`） |
| 源码修订 | `69b27d1` — `chore(release): v0.10.8 - Honest Desktop`，发布线 `main`（trunk） |
| 构建运行 | [CI/CD Pipeline #39033114624](https://github.com/mw2wbyys6t-sudo/live2d-auto-pipeline/actions/runs/39033114624)（tag 触发；`windows-installer` 仅 tag 构建） |
| 构建环境 | `windows-latest`；Node 20 / Go 1.25 / Rust stable / Python 3.12.10 |
| 必须通过的检查 | Python Tests 3.10 / 3.11 / 3.12（3.12 实测 **504 passed / 96 skipped**）、Go build + test、Next.js build + TS check、装配冒烟（**必装 9 项全部可导入**） |
| 依赖解析 | `requirements.desktop.txt`（29 个直接依赖）→ win_amd64 / cp312，实装 **92 个包**，site-packages **1.4 GB** |
| 装配产物体积 | python 1.4 GB + app 1.6 MB + models 3.8 MB = **1.5 GB**（未压缩） |
| 钉住且强制校验 | `python-embed` `4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3`<br>`yunet-onnx` `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`<br>`face-landmarker-task` `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`<br>（来源 `scripts/windows/hashes.json`，不匹配即构建失败） |
| 已实测但未强制 | `onnxruntime-dll` `0c42dc4e278f0968fdf9ebf1b1e54a74c657a9942e70803e453c91993451cf4c`（17,253,176 字节）——`hashes.json` 中该项仍为空，回填后转为强制校验 |
| 产物 | `Live2D.Master.Agent_0.10.8_x64-setup.exe` — **306.17 MiB / 321,047,178 字节** |
| 产物校验 | `sha256:ff7d8aba4a96e38f93a4f2d5bf9c4314dd32817a43050c321f1551b30387d248` |
| 下载可达性 | Release 直链匿名请求 `HTTP 200`，`content-length: 321047178`（已实测） |
| 替代关系 | 取代 `v0.10.7`；其 8 MB 坏资产已删除，发布说明顶部加了警示 |
| 回滚目标 | 卸载后重装**同一产物**（v0.10.8）；用户数据在 `%APPDATA%` 与「文档」保留，卸载不清空 |
| 例外 | 未签名（SmartScreen 告警）；无自动更新；无 MSIX / Store 分发 |
| 复核方式 | `(Get-FileHash "<安装包>" -Algorithm SHA256).Hash` 与上方 `sha256` 比对 |

---

## [v0.10.7] — 2026-10-16 — 代号「Dependency Governance」

> **本版目标**：仓库侧依赖治理 + 发布流程修复。抬高 Python/npm 依赖下限以消除已知 CVE 面、降低 FOSSA 安全/质量告警；修复「Release 有 tag 无资产」的发布回归；并清零全栈版本号残留漂移。

### 🔵 修复与维护

#### 依赖治理（安全 / 质量）
- `requirements.txt` 依赖下限抬高（对齐 dependabot 已通过 CI 的目标）：Pillow 10.0.0→12.3.0、numpy 1.24.0→2.5.4、urllib3 2.2.2→2.8.0、aiohttp 3.10.11→3.14.5、edge-tts 6.1.0→7.2.8。
- 全仓确认无 numpy 2.x 已移除 API 依赖（`np.float_`/`np.NaN`/`np.object_` 等零命中）。
- 修正 `requirements.txt` 中关于 live2d-py 的过时说明：PyPI 已提供 cp311-abi3 预编译 wheel，基础包钉 `==0.8.1`（1.0.0 移除 `live2d.v3`，与验收链不兼容）。
- `web/package.json`：lucide-react 1.53.0→1.55.0、@types/node 26.6.4→26.6.5。

#### 发布流程修复
- **问题**：v0.10.6 的 GitHub Release 资产为空（`assets: []`）——`release` job 只发布 `dist/*`，而该 workflow 从不生成 `dist/*`。
- **修复**：新增 tag 触发的 Windows NSIS 构建 job，并把安装包上传到对应 Release。

#### 版本号统一
- 残留漂移清零：`web/components/Sidebar.tsx`（v0.10.5）、`web/pages/index.tsx`（v0.10.5）、`web/lib/api-client.ts`（fallback v0.10.5）、`install.py`（v0.10.0）、`deploy/installer/desktop.iss`（0.10.2）、`requirements.txt` 头部横幅（v0.10.2）全部对齐到 **0.10.7**。

### 已知限制
- FOSSA 的三项检查（Dependency Quality / License Compliance / Security Analysis）为第三方 SaaS 状态，需在 FOSSA 后台按策略/忽略规则收敛；本版仅做仓库侧治理以降低报道数。
- 桌面安装包当前仅含 Go 后端 + 内嵌 Web UI + Rust 面捕二进制；Python 运行时随包分发属 P0 打包计划（见 `docs/project_understanding.md`）。
- README 曾宣称的单文件 `Live2DMasterAgent.exe` / `-portable.zip` 实际并无脚本产出，本版已按真实产物修正说明。

---

## [v0.10.6] — 2026-10-15 — 代号「Desktop First」

> **本版目标**：解决 CI 构建失败并统一全栈版本号。dependabot 提出的 pixi.js v7→v8 大版本升级因 API 不兼容（v8 移除 PlaneGeometry、重构 Geometry Buffer/Texture API）导致 Next.js TypeScript 检查失败，本版关闭该 PR 并在 dependabot 配置中忽略 pixi.js major 升级，确保发布管线畅通。

### 🔵 修复与维护

#### CI 构建失败修复
- **问题**：dependabot PR #18 将 `pixi.js` 从 7.4.3 升级到 8.22.0，`web/lib/live2d-player.ts` 使用的 v7 API（`PlaneGeometry`、`geometry.getBuffer('aVertexPosition')`、`tex.baseTexture.resource` 等）在 v8 中全部变更，TypeScript 检查失败。
- **修复**：关闭 PR #18，在 `.github/dependabot.yml` 中为 `pixi.js` 增加 `version-update:semver-major` 忽略规则，避免反复提出破坏性大版本 PR。pixi.js v8 迁移留待后续专门迭代。

#### 版本号统一
- 此前各端版本号不一致（Python 0.10.3 / Go 0.10.5 / Web 0.10.5 / Desktop 0.10.3），本版全部对齐到 **0.10.6**：
  - `core/version.py`：`0.10.3` → `0.10.6`
  - `api/handlers/handlers.go`：`v0.10.5-go` → `v0.10.6-go`
  - `api_server.py`：`v0.10.3-py` → `v0.10.6-py`
  - `web/package.json` / `web/package-lock.json`：`0.10.5` → `0.10.6`
  - `desktop/Cargo.toml` / `desktop/tauri.conf.json`：`0.10.3` → `0.10.6`

### 已知限制

- pixi.js 仍为 v7，v8 迁移（含 WebGPU 支持）需单独排期
- macOS / Linux 桌面打包脚本仍以 Windows 为主

---

## [v0.10.5] — 2026-10-11 — 代号「Desktop First」

> **本版目标**：全面转向桌面应用为唯一分发形态。移除 Docker 和独立 Web 部署模式，保留 Next.js 前端用于桌面内嵌和开发调试。核心动机：AI 推理需要直接利用本地 GPU/CPU，容器化部署无法高效访问硬件加速。

### 🔴 架构变更（BREAKING）

#### 移除 Docker 部署
- **删除** `Dockerfile`（三阶段构建：Go API + Next.js Web + Python 运行时）
- **删除** `docker-compose.yml`（All-in-one 服务编排，含独立前端服务 :3000）
- **删除** `.dockerignore`
- **删除** `deploy/docker/entrypoint.sh`（含独立启动 Next.js `next start -p 3000` 逻辑）
- **CI 移除** `docker-build` job 及 `release` 的 `needs` 依赖

#### 移除 Web 托管配置
- **删除** `web/public/_headers`（Cloudflare Pages / Netlify 专用安全头；桌面版由 Go 同源服务统一注入安全头）

### 🔵 保留与调整

#### 保留 Next.js 前端源码
- `web/` 目录完整保留——桌面版仍需 Next.js 静态导出（`output: 'export'`）嵌入 Go 二进制
- `next.config.js` 保留双模式：`NEXT_STATIC_EXPORT=1`（桌面构建）vs 默认 standalone + rewrites（开发调试）

#### 文档更新
- `README.md`：桌面应用提为推荐方式，Docker 章节移除，独立 Web 启动降级为「开发模式」
- `docs/DEPLOY.md`：重写为桌面安装指南为主，补充源码构建桌面应用的完整流程
- `docs/QUICKSTART.md`：安装章节改为桌面应用优先
- `docs/ARCHITECTURE.md`：技术栈表中部署方案更新为「桌面应用 (Go embed)」

### 已知限制

- 开发模式仍需手动启动两个终端（Python 后端 + Next.js 开发服务器）
- macOS / Linux 桌面打包脚本尚未完善（当前主要支持 Windows Inno Setup 安装包）

---

## [v0.10.4] — 2026-10-11 — 代号「Hardened Shell」

> **本版目标**：系统性安全加固——堵住 5 类审计发现的安全缺陷，覆盖前端面捕、文件导出、静态产物服务、SSRF 防护和批量导入内存安全。

### 🔴 安全修复（SECURITY）

#### S1. 桌面端面捕恢复 — CSP 放行 MediaPipe + Permissions-Policy 允许 camera（审计 A）
- **问题**：CSP `connect-src` 缺少 MediaPipe CDN 回退源（`cdn.jsdelivr.net` / `storage.googleapis.com`），导致桌面端摄像头面捕在 CDN 回退路径下被浏览器阻断；Permissions-Policy 设为 `camera=()` 直接全局禁用摄像头 API。
- **修复**：
  - [api/main.go](api/main.go) 提取命名安全头中间件 `securityHeadersMiddleware()`，CSP `connect-src` 放行 `cdn.jsdelivr.net` + `storage.googleapis.com`，`media-src` 增加 `blob:`
  - Permissions-Policy 从 `camera=()` 修正为 `camera=(self)`
  - [web/public/_headers](web/public/_headers) 同步更新，覆盖非嵌入式部署场景
- **测试**：[api/p4_csp_permissions_test.go](api/p4_csp_permissions_test.go) 验证 CSP 包含所需源、Permissions-Policy 允许 `self` 摄像头

#### S2. zip 导出不再跟随符号链接 — 修复 symlink 泄露 + fd 累积（审计 C）
- **问题**：`export_zip.go` 使用 `os.Open` 跟随符号链接，攻击者可在打包目录中放置指向敏感文件的 symlink 导致越权读取；`defer f.Close()` 在 `filepath.Walk` 回调中累积文件描述符，大批量打包时可能耗尽 fd。
- **修复**：
  - 增加 `!info.Mode().IsRegular()` 检查，跳过 symlink / 设备 / 管道等非普通文件
  - 将 `defer f.Close()` 替换为 `io.Copy` 后显式关闭，消除 fd 累积
  - 增加 `io.Copy` 字节数与 `info.Size()` 一致性校验
- **测试**：[api/handlers/p4_zip_symlink_test.go](api/handlers/p4_zip_symlink_test.go) 构造指向外部秘密文件的 symlink，验证产物不含 symlink 及泄露内容

#### S3. /output/ 目录列举禁用（审计 D）
- **问题**：`ServeOutput` 直接调用 `http.ServeFile`，当请求路径指向目录时返回 HTML 目录列举，泄露所有生成产物文件名。
- **修复**：[api/handlers/handlers.go](api/handlers/handlers.go) ServeOutput 增加 `info.IsDir()` 检查，目录请求统一返回 404 JSON。
- **测试**：[api/handlers/p4_output_dir_test.go](api/handlers/p4_output_dir_test.go) 验证目录请求返回非 200、普通文件仍正常下载

#### S4. SSRF DNS Rebinding TOCTOU 收口（审计 B）
- **问题**：图片下载链路虽然做了 URL 校验（协议、IP 黑白名单），但校验与实际 HTTP 请求之间存在 TOCTOU（Time-of-Check-Time-of-Use）窗口：攻击者可在校验通过后、请求发起前通过 DNS rebinding 将域名解析切换到内网 IP。
- **修复**：
  - [core/image_gen/base.py](core/image_gen/base.py) 引入 threading-local DNS 钉扎机制
  - `CheckedURL` 校验时缓存 `addr_infos`，`checked_image_download` 下载时通过 `DNSPin` 上下文管理器锁定解析结果，确保下载阶段使用的 IP 与校验阶段完全一致
- **测试**：[tests/unit/test_checked_download.py](tests/unit/test_checked_download.py) 新增 5 个 DNS 钉扎回归用例（21/21 通过）

#### S5. PNG 批量导入内存放大修复 — 两遍扫描（审计 E）
- **问题**：批量导入时一次性对所有上传文件执行 `image.Decode`（完整像素解码），200 张 4K PNG 会同时占用约 10GB 内存。
- **修复**：
  - [api/services/imaging.go](api/services/imaging.go) 提取 `checkImageConfig` 函数，封装 header-only 尺寸校验逻辑
  - [api/services/upload_service.go](api/services/upload_service.go) 改为两遍策略：
    - **Pass 1**：逐文件 `checkImageConfig`（仅读 header）→ 收集最大画布尺寸 + 元数据
    - **Pass 2**：逐文件 `Decode` → `NormalizeToCanvas` → 写入 → 立即释放
  - 峰值内存从「N 张同时解码」降至「1 张解码 + N 个 header」
- **测试**：[api/services/p4_png_twopass_test.go](api/services/p4_png_twopass_test.go) 导入 3 张不同尺寸 PNG，验证全部归一化到最大画布

### 🔵 工程改进

- **安全头集中管理**：Go 后端安全头从匿名中间件提取为命名函数 `securityHeadersMiddleware()`，便于测试和维护
- **TDD 覆盖**：全部 5 项修复均通过红绿流程交付，回归测试覆盖 symlink 泄露、DNS rebinding、目录列举、CSP 头、内存安全等场景

### 验证状态

| 检查项 | 结果 |
|--------|------|
| `go build ./...` | ✅ 通过 |
| `go test -race -count=1 ./...` | ✅ 全包通过 |
| `go vet ./...` | ✅ 无告警 |
| `pytest test_checked_download.py` | ✅ 21/21 |
| `npx tsc --noEmit` | ✅ 仅预存 preview.tsx 噪音 |

### 已知限制

- CSP 仍保留 `'unsafe-inline'` 和 `'unsafe-eval'`——前者用于内联样式，后者用于前端动态加载。后续版本可通过 nonce 收紧。
- DNS 钉扎仅覆盖 `checked_image_download` 路径；若新增其他出站 HTTP 调用，需同样接入 `DNSPin`。

---

## [v0.10.3] — 2026-10-08 — 代号「Sandbox-Ready」

> **本版目标**：解决阻碍「下载双击就能在本地沙箱跑通完整生成流程」的最致命问题，为本地开发与首次运行做好万全准备。

### 🔴 致命问题修复（FATAL）

#### F1. 离线兜底生图正式接入主流程
- **问题**：此前「完全离线」根本跑不通——主工作流走的是 ProviderRouter，最终兜底的 Pollinations 需要联网。项目里有一个真正的离线生成器 `local_image_generator.py`，但它只被 Go 旧路径调用，主流程根本不走它。
- **修复**：新增 `LocalPlaceholderProvider`（[core/image_gen/local_placeholder.py](core/image_gen/local_placeholder.py)），注册为路由优先级最末位。所有云端 provider 不可用时（无 Key / 断网），自动产出确定性占位立绘（渐变背景 + 角色剪影 + 提示词水印），并如实标注 `local_placeholder=True`。
- **影响**：「输入文本 → 生成 → 分层 → 导出 → 预览」主链路在**完全离线**条件下也能走通。

#### F2. PSD 缺少 psd-tools 时不再让整个流程前功尽弃
- **问题**：图片已生成、分层已完成，但因为可选依赖 `psd-tools` 没装上，整个 workflow 抛 `RuntimeError`，所有产物白做。
- **修复**：PSD fallback 从「致命错误」降级为「带警告的降级」（[core/workflow.py:533](core/workflow.py#L533)）。缺少 psd-tools 时产出 PNG 包 + 警告，后续 Live2D 导出继续走通。

#### F3. 双击 exe 时项目根定位更健壮
- **问题**：候选目录列表只有容器约定路径（`/workspace /app /repo /project`），在普通 Windows 用户电脑上不存在。
- **修复**：追加 `exe 同级 runtime/ 子目录`作为候选（便携版常见布局）；全部找不到时如实记录警告，不静默继续（[api/config/config.go:178](api/config/config.go#L178)）。

### 🟠 严重问题修复（CRITICAL）

#### C4. /api/health 不再永远返回 200
- **问题**：健康检查永远 OK，前端误判后端可用，点「生成」立刻崩——「先成功后失败」比直接报错更令人困惑。
- **修复**：HealthCheck 启动后跑一次 Python 解释器探针，不可用时返回 503 + 中文说明（[api/handlers/handlers.go:60](api/handlers/handlers.go#L60)）。

#### C5. 端口冲突不再直接 log.Fatalf 崩溃
- **问题**：小白双击两次 exe，第二次撞端口，原实现直接 `log.Fatalf` 退出且无任何可读提示。
- **修复**：绑定前用 `net.Listen` 探测端口，被占时给出中文友好提示（关闭已运行实例 / 换端口）（[api/main.go:143](api/main.go#L143)）。

#### C6. Python 后端默认端口对齐到 8080
- **问题**：Python 后端默认监听 8000，但前端 Next.js 代理目标是 8080。降级模式启动后所有请求 404。
- **修复**：`api_server.py` 默认端口改为 8080，与 Go 后端及前端代理目标一致。

### 🔵 工程改进

- **版本号集中化**：Go 后端新增 `Version` 常量（`v0.10.3-go`），消除散落多处的硬编码版本字符串。
- **未接入配置诚实标注**：SDWebUI / TTS / Redis / MediaPipe 四个仅预留的配置项添加注释，避免用户误以为修改它们会生效。

### 已知限制

- 离线占位图是**确定性剪影**，不是真实 AI 生成结果——它会如实标注 `LOCAL-PLACEHOLDER`。配置 API Key 或联网后可获得真实立绘。
- HSV 兜底分层质量近似 K-means，产出部位可能嘴眼歪斜。建议安装 SAM 模型以获得最佳效果。

---

## [v0.10.2] — 2026-09-29 — 代号「Offline & Desktop Pack」

- 离线兜底生成器 + PSD 图层名识别 + 桌面打包工具链 + 相关项目调研

## [v0.10.1] — 自研 moc3 导出管线

- 官方 Cubism 参数驱动 + 官方内核逐像素验收

## [v0.10.0] — 角色一致性 + 语义分割 + Live2D 导出

- Character consistency system, semantic segmentation, Live2D export.
