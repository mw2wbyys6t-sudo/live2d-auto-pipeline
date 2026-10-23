# 🚀 部署指南

> v0.10.5 起，项目全面转向**桌面应用**为唯一分发形态，直接利用本地 GPU/CPU 进行 AI 推理。
> Docker 和独立 Web 部署模式已废弃移除。

---

## 桌面安装（推荐）

### Windows 安装包（Tauri NSIS）

从 [Release 页面](https://github.com/mw2wbyys6t-sudo/live2d-auto-pipeline/releases)
下载最新的 `.exe`，或从 GitHub Actions 的 `tauri-nsis-installer` 产物获取，双击安装。

安装包采用 ADR-007 的「自带运行时」布局（`{res}` = 安装后的 Tauri 资源目录）：

```
{install}\Live2D Master Agent.exe      Tauri 壳（Rust + WebView2）
{res}\live2d-api.exe                   Go 后端（内嵌 Web 工作台）
{res}\face_tracker.exe                 Rust 原生面捕
{res}\onnxruntime.dll                  面捕 load-dynamic 依赖
{res}\models\face_detection_yunet_2023mar.onnx
{res}\models\face_landmarker.task      MediaPipe Tasks 面捕模型
{res}\python\                          嵌入式 CPython 3.12 + 离线 site-packages
{res}\app\                             Python 工程源码树（core/ drivers/ ...）
```

用户数据**严格不写安装目录**：

```
%APPDATA%\Live2DMasterAgent\              config / logs / hf-cache / characters
%USERPROFILE%\Documents\Live2DMasterAgent\output\
```

> 首次启动时 Go 端 `config.ApplyDesktopLayout` 会凭 `<res>\app\core\workflow.py`
> 自动解析解释器、源码树与用户数据目录，**无需手写 `-config`**。

> 装完后的验收步骤见 [`tests/e2e/checklist.md`](../tests/e2e/checklist.md)：
> 落盘完整性、随包解释器导入、`/api/health` 与 `/api/diagnostics` 判据、
> 生图→分层→导出→预览→桌宠全链路、进程回收，以及失败排查对照表。

### 从源码构建安装包

**推荐：GitHub Actions（云端 Windows，本地零工具链）**

推送或手动触发 `.github/workflows/tauri-build.yml`，完成后下载
`tauri-nsis-installer` 产物。

**本地：Windows 一键脚本**

```bat
scripts\build_tauri_release.bat
```

前提：Rust + `tauri-cli` + Node.js + Go + Python 3.12（脚本会逐项检查）。
流程：前端静态导出 → 内嵌进 Go 二进制 → 编译原生面捕 →
`scripts\build_desktop_bundle.py` 装配 `desktop\bundle\` →
`cargo tauri build --bundles nsis`。

产物：`desktop\target\release\bundle\nsis\*.exe`

### 装配脚本（scripts/build_desktop_bundle.py）

把「安装包自带运行时」装进 `desktop/bundle/`，再由 `tauri.conf.json` 的
`resources: { "bundle/": "" }` **扁平**打进 `$RESOURCE`：

- 下载并 SHA256 校验嵌入式 Python 3.12.10 / YuNet / onnxruntime / face_landmarker
  （锁定表：`scripts/windows/hashes.json`）
- `pip download --only-binary=:all:` 拉 win_amd64 + cp312 轮子，
  `pip install --no-index --target` 离线装进 `python/Lib/site-packages`
- 修 `python312._pth`（放开 `import site`）并写 `sitecustomize.py` ——
  `._pth` 会进入 isolated 模式、**忽略 `PYTHONPATH`**，必须借此把 `<res>/app`
  注入 `sys.path`
- 拷贝源码树、Go/面捕 exe、`onnxruntime.dll` 与两个模型
- 用随包解释器做导入冒烟，生成 `BUILD-INFO.json`

依赖清单见 `requirements.desktop.txt`。主链路依赖全量内置；**torch 组
（torch / torchvision / transformers / segment-anything / huggingface-hub）
刻意不内置**，由应用内按需下载（`/api/capabilities` → Go 侧 `capabilityPlans`），
理由见 `docs/DESKTOP.md` 的「已知边界」。

> 装配必须在 **Windows** 上执行：pip 的 `--platform` 只约束 wheel 标签，
> 不改变 `platform_system` / `python_version` 等环境标记的评估。

### 已废弃的构建路径

- `scripts\build_desktop.bat` / `.sh`：单文件 Go exe（不含 Python 运行时）
- `deploy\installer\desktop.iss` + `scripts\build_portable.py`：Inno + 作者机 CUDA venv

自 ADR-007 起不再维护，保留源码仅作历史参考；桌面分发唯一通道为 Tauri NSIS。

---

## 开发模式

开发时前后端分离运行，方便热更新调试：

```bash
# 终端 1 — Python 后端 + Go API
python api_server.py          # Python 服务（端口 8080）

# 终端 2 — Next.js 开发服务器
cd web && npm run dev         # 前端开发服务器（端口 3000）
```

开发模式下，Next.js 通过 `rewrites` 代理将 `/api/*`、`/output/*`、`/ws/*` 转发到后端 8080 端口，无需 CORS 配置。

---

## 环境变量

见 `.env.example`。关键配置：

| 变量 | 说明 | 默认 |
|------|------|------|
| GO_API_PORT | API 端口 | 8080 |
| OUTPUT_DIR | 输出目录 | ./output |
| ARK_API_KEY | 火山引擎 Key | 空 |
| OPENAI_API_KEY | OpenAI Key | 空 |
| REDIS_URL | Redis 连接 | 空(内存) |
