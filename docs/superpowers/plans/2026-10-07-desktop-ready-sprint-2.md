# Desktop-Ready Sprint 2 — 桌面应用包装实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Live2D Master Agent 从"双击 start.py 启动器"升级到"下载 .dmg/.msi/.deb 双击安装的真正桌面应用"，同时补全 Sprint 1 标记的 A 类业务缺口（rotation 变形器参数绑定 + F-06 多参数带轴序编译器接入）与 6 处中低优先级 FF-16 红线。

**Architecture:** Tauri 2.0 作为桌面应用外壳，前端复用现有 Next.js（导出为静态站点），后端 Go API 与 Python 内核通过 Tauri sidecar 模式作为子进程托管。安装包同时打包：
- Tauri 壳（Rust + WebView，~5 MB）
- Go API 静态编译二进制（~15 MB）
- Python 内核 + 依赖（用 PyInstaller 打包成单文件夹，~80 MB）
- SAM2 / ISNet 模型权重（按需下载，首启时拉取）

**Tech Stack:** Tauri 2.0 (Rust) / Next.js SSG / Go sidecar / Python PyInstaller bundle / NSIS (Windows) / .dmg (macOS) / .deb (Linux)

**Out of scope（Sprint 3 留给）:**
- 自定义画风训练（v0.11 roadmap）
- VRM 3D 模型支持（v0.12 roadmap）
- 应用商店上架（Microsoft Store / Mac App Store）

---

## File Structure

**新建：**
- `desktop/` — Tauri 工程根目录
  - `desktop/Cargo.toml` — Rust 依赖
  - `desktop/tauri.conf.json` — Tauri 配置（窗口、图标、sidecar、bundle）
  - `desktop/src/main.rs` — Rust 主入口（启动 sidecar、创建系统托盘、窗口管理）
  - `desktop/src/sidecar.rs` — Go API + Python 子进程生命周期管理
  - `desktop/src/tray.rs` — 系统托盘（启动/停止/打开数据目录/关于）
  - `desktop/icons/` — 应用图标（.ico/.icns/.png）
  - `desktop/build.rs` — Tauri 构建脚本
  - `desktop/README.md` — 桌面应用构建说明
- `desktop/scripts/` — 打包脚本
  - `desktop/scripts/build-windows.ps1` — Windows .msi 构建
  - `desktop/scripts/build-macos.sh` — macOS .dmg 构建（含签名）
  - `desktop/scripts/build-linux.sh` — Linux .deb/.AppImage 构建
  - `desktop/scripts/bundle-python.sh` — PyInstaller 打包 Python 内核
- `tests/e2e/test_desktop_app.py` — 桌面应用启动侧载测试

**修改：**
- `web/next.config.js` — 加 `output: 'export'` 让 Next.js 导出静态站点
- `web/package.json` — 加 `"build:static": "next build"`（已有 build 不动）
- `package.json`（项目根，如果不存在则新建）— 协调 desktop/web/api 三方构建
- `.github/workflows/release.yml`（新建）— 标签触发的多平台 release 流水线
- `live2d_builder/exporter/moc3_pipeline.py` — 接入 F-06 多参数带轴序规则
- `live2d_builder/exporter/deformer_compiler.py`（或同类文件）— rotation 变形器参数绑定
- `api/services/python_bridge.go` — 关闭剩余 6 个 FF-16 中低优先红线（去掉 t.Skip）

---

## 三栈并存的迁移路径（关键决策）

### 当前状态
```
用户 → 双击 start.py
     ↓
     Python 启动器
     ↓
     并行启动 Go API (8080) + Next.js Dev (3000)
     ↓
     浏览器打开 http://localhost:3000
```

### Sprint 2 后状态
```
用户 → 双击 Live2DMasterAgent.exe (.app/.AppImage)
     ↓
     Tauri 壳启动（Rust main.rs）
     ↓
     启动 sidecar：Go API 二进制 + Python 内核 bundle
     ↓
     Tauri WebView 加载 Next.js 静态导出
     ↓
     单一应用窗口（不再需要浏览器）
```

**关键迁移点：**
1. **Next.js 必须改成静态导出**（`output: 'export'`）→ 不再需要 npm run dev，Tauri 加载本地 HTML
2. **API 请求路径**：前端原本请求 `http://localhost:8080/api/...`，桌面应用里改成相对路径 `/api/...`，Tauri 通过 sidecar 转发（或在 Tauri 配置里把 8080 端口绑定到窗口）
3. **Python 模型路径**：打包后路径会变，必须用 `tauri::api::path::app_data_dir()` 注入到 Python 子进程的环境变量
4. **许可与归属**：Tauri 是 MIT/Apache 双许可，与我们 Apache-2.0 兼容，无需法律变更

---

## Task 1: Tauri 工程脚手架

**Files:**
- Create: `desktop/Cargo.toml`、`desktop/tauri.conf.json`、`desktop/src/main.rs`、`desktop/build.rs`

**步骤：**
- [ ] Step 1: `npm create tauri-app@latest desktop -- --template vanilla-ts`（脚手架）
- [ ] Step 2: 改 `desktop/tauri.conf.json`，指向 `../web/out` 作为前端 dist 目录
- [ ] Step 3: 写最小化 `main.rs`（启动一个加载本地 HTML 的窗口）
- [ ] Step 4: `cargo tauri dev` 能跑起一个空白窗口
- [ ] Step 5: 加 sidecar 配置（Go API + Python）
- [ ] Step 6: Commit

**验证：** 桌面窗口能打开并显示 "Hello World"。

---

## Task 2: Next.js 静态导出适配

**Files:**
- Modify: `web/next.config.js`、`web/package.json`

**步骤：**
- [ ] Step 1: 加 `output: 'export'` 到 next.config.js
- [ ] Step 2: 处理所有 `next/image`（静态导出不支持默认 loader，改成 `unoptimized: true`）
- [ ] Step 3: 把 API 请求 base URL 抽成环境变量 `NEXT_PUBLIC_API_BASE`
- [ ] Step 4: 跑 `next build` 确认生成 `web/out/` 目录
- [ ] Step 5: 验证关键页面（/, /generate, /characters, /export）能纯静态访问
- [ ] Step 6: Commit

**风险：** `/characters/[id]` 是动态路由，静态导出需要 `getStaticPaths`，否则只能用 query string。

---

## Task 3: Go sidecar 集成

**Files:**
- Modify: `desktop/src/main.rs`、`desktop/tauri.conf.json`
- Create: `desktop/src/sidecar.rs`

**步骤：**
- [ ] Step 1: `cd api && go build -o ../desktop/sidecars/live2d-api-x86_64-pc-windows-msvc.exe .`（按 Tauri sidecar 命名规则）
- [ ] Step 2: 在 tauri.conf.json 注册三个目标平台的 sidecar
- [ ] Step 3: 写 `sidecar.rs` 管理 Go 进程生命周期（启动、健康检查、停止）
- [ ] Step 4: Tauri 启动时拉起 Go sidecar，退出时优雅停止
- [ ] Step 5: 前端通过 Tauri 命令拿到 Go 实际监听端口（避免硬编码 8080）
- [ ] Step 6: Commit

---

## Task 4: Python 内核 PyInstaller 打包

**Files:**
- Create: `desktop/scripts/bundle-python.sh`、`desktop/python-bundle/live2d-master-agent.spec`

**步骤：**
- [ ] Step 1: 写 PyInstaller spec，把 `api_server.py`、`core/`、`live2d_builder/`、`drivers/`、`llm_bridge/` 全部打成单文件夹
- [ ] Step 2: 处理 SAM2 / ISNet 模型权重 —— 不打进包，首启时从云端拉到 `app_data_dir`
- [ ] Step 3: 测试 `PyInstaller live2d-master-agent.spec` 在三个平台都能产出可执行文件
- [ ] Step 4: 包大小目标：Windows < 100 MB、macOS < 120 MB、Linux < 90 MB
- [ ] Step 5: Commit

**风险：** psd-tools、onnxruntime 等依赖有原生扩展，PyInstaller 需要用 `--hidden-import` 显式声明。

---

## Task 5: 系统托盘与生命周期 UI

**Files:**
- Create: `desktop/src/tray.rs`
- Modify: `desktop/src/main.rs`

**托盘菜单：**
```
─────────────────
▶ 打开主窗口
─────────────────
↻ 重启后端
■ 停止后端
─────────────────
📁 打开生成目录
📁 打开模型目录
─────────────────
⚙ 设置...
ℹ 关于
─────────────────
⏻ 退出
```

**步骤：**
- [ ] Step 1: 写 tray.rs 实现菜单
- [ ] Step 2: 主窗口关闭时不退出应用，只最小化到托盘
- [ ] Step 3: 加开机自启动选项（用 Tauri 的 `autostart` 插件）
- [ ] Step 4: 后端崩溃时托盘图标变红 + 系统通知
- [ ] Step 5: Commit

---

## Task 6: 多平台构建脚本

**Files:**
- Create: `desktop/scripts/build-windows.ps1`、`desktop/scripts/build-macos.sh`、`desktop/scripts/build-linux.sh`

**步骤：**
- [ ] Step 1: Windows 脚本：cargo tauri build → 产出 .msi 与 .exe（NSIS）
- [ ] Step 2: macOS 脚本：cargo tauri build → 产出 .dmg（含签名与公证，需要 Apple Developer ID）
- [ ] Step 3: Linux 脚本：cargo tauri build → 产出 .deb、.AppImage、.rpm
- [ ] Step 4: 每个脚本结束后打印产物大小与 SHA256
- [ ] Step 5: Commit

**风险：** macOS 签名需要付费 Apple Developer 账号；首版可以先不签名，发布到 GitHub Releases 让用户手动信任。

---

## Task 7: GitHub Actions Release 流水线

**Files:**
- Create: `.github/workflows/release.yml`

**触发条件：** 标签 `v*` 推送时

**矩阵：**
- windows-latest (x86_64)
- macos-latest (x86_64 + arm64)
- ubuntu-latest (x86_64)

**步骤：**
- [ ] Step 1: 检出代码、装 Rust/Node/Go/Python
- [ ] Step 2: 跑 web build、PyInstaller、cargo tauri build
- [ ] Step 3: 上传产物到 GitHub Release（用 softprops/action-gh-release）
- [ ] Step 4: 自动生成 changelog（从 git log）
- [ ] Step 5: Commit

---

## Task 8: rotation 变形器参数绑定（A 类缺口）

**Files:**
- Modify: `live2d_builder/exporter/deformer_compiler.py`（或实际所在文件）

**业务目标：** 让 Live2D 模型的眉毛/眼睛/嘴等参数能被真实驱动，而不是只剩静态姿态。

**步骤：**
- [ ] Step 1: 读 `docs/reviews/2026-09-20-moc3-export-risk-review.md` 第 7 节看 rotation 变形器的当前状态
- [ ] Step 2: 读 `live2d_builder/exporter/deformer_compiler.py` 看 rotation 变形器是怎么生成的
- [ ] Step 3: 设计参数→变形器绑定规则（哪个角色参数映射到哪个 rotation 角度）
- [ ] Step 4: 实现绑定逻辑 + 写最小测试
- [ ] Step 5: 跑官方内核验收确认 rotation 真的能驱动
- [ ] Step 6: Commit

**注意：** 这一任务需要美术/动画判断，最好让用户确认绑定规则后再编码。

---

## Task 9: F-06 多参数带轴序编译器接入（A 类缺口）

**Files:**
- Modify: `live2d_builder/exporter/moc3_pipeline.py`

**业务目标：** 把评审里已实测的"多参数带轴序"规则（`index = Σ i_j · Π k_m`）接入编译器，让跨参数网格不靠猜。

**步骤：**
- [ ] Step 1: 读 `docs/reviews/2026-09-21-kotlin-cpp-boundary.md` 找 F-06 规则
- [ ] Step 2: 实现 `compute_param_index(param_axes: List[int], axis_cardinalities: List[int]) -> int`
- [ ] Step 3: 接入 moc3_pipeline 的参数表生成
- [ ] Step 4: 写测试（单参数、双参数、四参数网格）
- [ ] Step 5: 跑官方内核验收
- [ ] Step 6: Commit

---

## Task 10: 关闭剩余 6 个 FF-16 中低优先红线

**Files:**
- Modify: `api/services/python_bridge.go`、`api/services/python_bridge_inject_test.go`

**步骤：**
- [ ] Step 1: 设计绝对路径白名单策略（接受 `app_data_dir` 与 `output/` 子树，拒绝其他）
- [ ] Step 2: 实现白名单校验，去掉 `absolute_unix`/`absolute_win` 的 t.Skip
- [ ] Step 3: 黑名单加 `'` 和 `"`，去掉 `shell_meta_q`/`shell_meta_dq` 的 t.Skip
- [ ] Step 4: leading dash 改成逐段检查，去掉 t.Skip
- [ ] Step 5: only_dot 单独拒绝，去掉 t.Skip
- [ ] Step 6: 跑 `go test ./services` 确认所有 58 个用例都 PASS
- [ ] Step 7: Commit

---

## Verification

```bash
# 桌面应用构建验证（每个平台）
cd desktop && cargo tauri build --target x86_64-pc-windows-msvc  # 产出 .msi
cd desktop && cargo tauri build --target aarch64-apple-darwin    # 产出 .dmg

# 业务测试
python3 -m pytest tests/unit tests/integration
cd api && go test ./...
cd web && npm run build

# 桌面应用 E2E 测试
python3 tests/e2e/test_desktop_app.py
```

---

## Self-Review

**Spec coverage:**
- 用户"变成电脑软件形式" → Task 1-7（Tauri 全套） ✅
- 用户"各个没有实现的功能都要实现" → Task 8（rotation）+ Task 9（F-06）+ Task 10（FF-16 全绿）✅
- 用户"清除一些没有的东西" → Sprint 1 已完成，Sprint 2 不再追加清理 ✅
- 用户"先不上传到 GitHub 直到真正能使用" → Task 7 是 release 流水线，但只在打 tag 时触发；用户掌控发布节奏 ✅

**关键风险：**
1. **Tauri 在 Linux 上依赖 WebKitGTK** —— 用户需要预装，但 Tauri 2.0 已支持 AppImage 自带 webview
2. **Python bundle 体积** —— 80 MB 是底线，可能突破到 120 MB；这是 Python 桌面应用的固有代价
3. **macOS 签名** —— 没 Apple Developer 账号时用户需手动信任；首版可接受
4. **模型权重分发** —— 不打进安装包，首启下载；需要 CDN 或 GitHub Releases 托管

**资源预估（不在本计划承诺范围内）：**
- 桌面应用构建首次跑通：需要装 Rust + Tauri CLI + 各平台 SDK，环境搭建本身就要花时间
- 建议 Sprint 2 拆成两个子冲刺：2a 桌面包装（Task 1-7）、2b 业务补全（Task 8-10）

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-10-07-desktop-ready-sprint-2.md`.**

建议执行顺序：
1. **先做 Task 8-10**（业务补全 + FF-16 全绿）—— 不依赖桌面包装，独立可验证
2. **再做 Task 1-3**（Tauri 脚手架 + 静态导出 + Go sidecar）—— 最小可行桌面应用
3. **最后做 Task 4-7**（Python bundle + 托盘 + 多平台构建 + Release）—— 完整桌面应用流水线
