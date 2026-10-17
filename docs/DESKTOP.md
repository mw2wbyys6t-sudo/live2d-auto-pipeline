# 桌面版（Desktop）

> 把 Live2D Master Agent 变成**电脑里的软件**：双击安装包，桌面窗口里就是完整工作台。
> 不需要 Node 开发服务器、不需要自装 Python、不需要端口代理。

## 形态与架构

```
┌──────────────────── Live2D Master Agent.exe（Tauri 壳）────────────────────┐
│  Rust + WebView2：拉起 Go 后端、等待健康检查、退出时回收子进程               │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │ spawn，等待 127.0.0.1:8080 就绪
                                ▼
┌──────────────────── $RESOURCE/live2d-api.exe（Go 后端）────────────────────┐
│  ├─ 内嵌 Web 工作台（go:embed ← Next.js 静态导出，同源服务）                │
│  ├─ API（生成 / 分层 / 导入 / 导出 / 桌宠 / 面捕 WebSocket）                │
│  ├─ Python 桥 → $RESOURCE/python/pythonw.exe 执行 $RESOURCE/app/core/*      │
│  └─ 原生面捕桥 → $RESOURCE/face_tracker.exe（onnxruntime.dll + YuNet）      │
└────────────────────────────────────────────────────────────────────────────┘
```

安装后的资源目录（`{res}` = Tauri `resource_dir`）：

```
{install}\Live2D Master Agent.exe
{res}\live2d-api.exe
{res}\face_tracker.exe
{res}\onnxruntime.dll
{res}\models\face_detection_yunet_2023mar.onnx
{res}\models\face_landmarker.task
{res}\python\               嵌入式 CPython 3.12 + 离线 site-packages
{res}\app\                  core/ drivers/ live2d_builder/ llm_bridge/ prompts/ templates/
```

布局标记是 `{res}\app\core\workflow.py`：Go 端 `config.ApplyDesktopLayout` 命中后
自动把解释器、源码树、用户数据目录解析到位（详见 ADR-007）。

用户数据**严格不写安装目录**：

```
%APPDATA%\Live2DMasterAgent\              config / logs / hf-cache / characters
%USERPROFILE%\Documents\Live2DMasterAgent\output\
```

## 构建

```bat
scripts\build_tauri_release.bat
```

或在 GitHub Actions 手动触发 `tauri-build.yml`（windows-latest，产物
`tauri-nsis-installer`）。脚本流程：

1. `NEXT_STATIC_EXPORT=1 next build` → 复制到 `api/webui/dist`（`go:embed` 源）
2. `go build -H windowsgui -o live2d-api.exe`（内嵌 UI）
3. `cargo build --release`（原生面捕）
4. `python scripts\build_desktop_bundle.py` 装配 `desktop\bundle\`
5. `cargo tauri build --bundles nsis`

安装包产物：`desktop\target\release\bundle\nsis\*.exe`

### 装配脚本做了什么

`scripts/build_desktop_bundle.py` 负责「安装包自带运行时」：

- 下载并 SHA256 校验嵌入式 Python 3.12.10 / YuNet / onnxruntime / face_landmarker
  （锁定表：`scripts/windows/hashes.json`）
- `pip download --only-binary=:all:` 拉 win_amd64 + cp312 轮子，再
  `pip install --no-index --target` 离线装进 `python/Lib/site-packages`
  （清单：`requirements.desktop.txt`，含 CPU 版 torch）
- 修 `python312._pth`（放开 `import site`）并写 `sitecustomize.py`
- 拷源码树与原生二进制，生成 `BUILD-INFO.json`，最后用随包解释器做导入冒烟

> 必须在 **Windows** 上装配：pip 的 `--platform` 只约束 wheel 标签，不改变
> `platform_system` / `python_version` 等环境标记的评估，跨平台装配会选错版本。

## 运行要求与首次启动

1. **无需预装 Python**：安装包自带解释器与全部依赖。
2. 端口默认 8080；`-port` / `-host` 可改（桌面壳默认显式绑回环）。
3. 首次启动自动创建 `%APPDATA%` 与文档输出目录；创建失败不阻断启动。

> 安装完成后的完整验收步骤见 [`tests/e2e/checklist.md`](../tests/e2e/checklist.md)
> （含可直接复制的 PowerShell 自检命令、`/api/diagnostics` 判据与失败排查表）。

## 与开发模式的关系

| | 开发模式 | 桌面安装版 |
|---|---|---|
| 前端 | `npm run dev`（:3000，rewrites 代理） | 静态导出内嵌进 Go 二进制（同源） |
| 后端 | `go run ./api`（:8080） | 随包 `live2d-api.exe` |
| Python | 机器上的 `python` + `pip install -r requirements.txt` | 随包嵌入式解释器 + 离线依赖 |

## 面捕的两条路径

- **原生面捕（桌面默认）**：Go 拉起 `face_tracker.exe`，YuNet ONNX +
  `onnxruntime.dll`，逐帧经 WebSocket 推送，可直接驱动桌宠。
- **Python 面捕**：`drivers/face_tracker/mediapipe_tracker.py` 服务 Python 桌宠。
  mediapipe **0.10.30 起官方删除了经典 `mp.solutions`**，因此该模块做了双路适配：
  有 `solutions` 时走经典 `FaceMesh`，否则切 **Tasks `FaceLandmarker`**
  （模型 `{res}\models\face_landmarker.task`）。任一路不可用时按原逻辑优雅降级。

## 已知边界

- 内嵌 UI 是**构建时刻**的快照；改前端后必须重新构建安装包。
- 安装包体积较大（内置 CPU torch 等全量依赖）；CUDA 版 torch 不内置。
- Python 依赖走 `numpy 2.x` 线：`mediapipe` 必须 `>=0.10.30,<0.10.32` ——
  更低版本强制 `numpy<2`，与 scipy / scikit-learn / rembg 的 numpy 2 ABI 轮子冲突；
  `0.10.32+` 又会引入 `opencv-contrib-python` 与 `opencv-python-headless` 争抢 `cv2`。
- 安装包当前不签代码证书（SmartScreen 告警属预期）。

## 已废弃的构建路径

- `scripts\build_desktop.bat` / `.sh`：单文件 Go exe（不含 Python 运行时）
- `deploy\installer\desktop.iss` + `scripts\build_portable.py`：Inno + 作者机 CUDA venv

自 ADR-007 起不再维护，保留源码仅作历史参考。

## HF_HOME 决策（已采纳方案 B）

打包后的权重缓存位于 `%APPDATA%\Live2DMasterAgent\hf-cache`，而 `transformers`
默认读用户目录。决策：Go 侧 `python_bridge` 在 `exec.CommandContext` 时注入：

```
HF_HOME={hf_cache}
HUGGINGFACE_HUB_CACHE={hf_cache}
TRANSFORMERS_CACHE={hf_cache}/hub
```

只影响本程序的 Python 子进程，不污染用户全局环境；开发模式（`HfCache` 为空）不注入。

## 路线图

1. ~~嵌入 Python 运行时~~ —— 已随安装包落地（ADR-007）。
2. **自动更新**：`/api/info` 已提供版本，差一个更新器。
3. **重型能力按需下载**：SAM2 / 面捕增强等继续走应用内下载器。
