# ADR-007：桌面安装布局 — Tauri resources + 外置 embeddable Python（非 sidecar）

> 状态：Accepted（2026-10-16，P0）。取代 deploy/installer/desktop.iss 的 Inno+CUDA venv 路线（正式标注废弃，见 ADR 末节）。
> 关联：ADR-002 三栈架构；P-1 理解报告 §4.5、§8。

## 背景

v0.10.x 的 Tauri NSIS 只 bundle `desktop/bin/*`（live2d-api.exe、face_tracker.exe），
不含 Python 解释器、app 源码、YuNet 模型与 onnxruntime.dll；Go 按 exe 向上探测
`core/workflow.py` 必然失败，用户装完无法使用。仓库同时存在三套互不相通的打包
（Tauri NSIS / Inno+CUDA venv / 单文件 Go exe），文档互相矛盾。

## 决策

### 布局（NSIS currentUser 安装目录，下称 `{res}` = Tauri resource_dir）

```
{install}\Live2D Master Agent.exe          ← Tauri 壳（Rust）
{res}\live2d-api.exe                      ← Go 后端（内嵌 Next 静态站）
{res}\face_tracker.exe                    ← Rust 面捕
{res}\onnxruntime.dll                     ← ort load-dynamic（≥1.31，MIT）
{res}\models\face_detection_yunet_2023mar.onnx   ← YuNet（~340KB, Apache-2.0）
{res}\python\python.exe                   ← CPython 3.12.10 embeddable amd64
{res}\python\python312._pth                ← 已放开 import site + Lib\site-packages
{res}\python\Lib\site-packages\*           ← 离线装配的 CPU wheel（--no-index）
{res}\app\                                 ← Python 工程源码树（见下）
{res}\app\core\  live2d_builder\  drivers\  llm_bridge\  prompts\  templates\
{res}\app\local_image_generator.py
{res}\app\LICENSE                          ← Apache-2.0 随附（ADR-001 R4-7 补）
```

`{res}\app` 内容：`core/`、`live2d_builder/`、`drivers/`、`llm_bridge/`、`prompts/`、
`templates/`、`local_image_generator.py`、`requirements.desktop.txt`、`LICENSE`。
**不含** tests/、tools/、docs/、api_server.py（降级桩服务不进基础包）。

### 用户数据（严格不写安装目录，成功标准 #9）

```
%APPDATA%\Live2DMasterAgent\
  config.json        ← 首启由 Tauri 壳生成（绝对路径指向 {res} 与本目录）
  logs\live2d.log   ← RotatingFileHandler
  cache\            ← 请求缓存 / 模型下载临时区
%USERPROFILE%\Documents\Live2DMasterAgent\
  output\           ← 立绘 / 分层 PNG / 导出模型 / 角色库
```

- `LIVE2D_DATA_DIR` 环境变量整体覆盖（CI 冒烟测试用，指向临时目录）。
- 优先级：`-config` 显式路径 > `LIVE2D_DATA_DIR` > 默认 `%APPDATA%` / Documents。
- Go `config.LoadConfig` 在 unmarshal 后应用 data-dir 重定向；Python 经
  `OUTPUT_DIR` / `LIVE2D_LOG_DIR` / `LIVE2D_DATA_DIR` 环境变量对齐。

### 为什么用 resources + `-config`，而不升 externalBin sidecar

| 维度 | resources + `-config`（采纳） | externalBin sidecar（备选，未采纳） |
|---|---|---|
| 进程管理链 | 现有 `find_go_binary` 只加候选目录；Rust 直接 spawn exe | Tauri 管理 sidecar 生命周期，需改全部 spawn/kill 调用 |
| 路径解析 | Go/Rust 各自按 resource_dir/exe_dir 候选搜索，可控 | sidecar 要求文件名带目标三元组后缀（`live2d-api-x86_64-pc-windows-msvc.exe`），会断掉现有 `desktop/bin` 拷贝链与开发态候选 |
| 配置注入 | `-config <appdata>\config.json` 一次传入全部绝对路径 | sidecar 仍需另走 env/args 传配置，收益为零 |
| 迁移成本 | 新增打包脚本 + 接线，不动既有进程模型 | 重写进程托管，回归风险高 |

sidecar 留作未来自动更新/单实例加固的升级路径，本次不做。

### Python 运行时方案

- 官方 **Windows embeddable CPython 3.12.10 amd64**（python.org FTP 实测 200；
  3.12.11+ 已从 FTP 下架）。选 3.12 而非 3.13：abi3 wheel 覆盖，且避开部分可选
  包的 3.13 wheel 缺口。
- 装配：CI `pip download`（`--platform win_amd64 --only-binary=:all: --python-version 3.12`）
  拉全量 CPU wheel → `--no-index --find-links` 离线装进 embeddable 的
  `Lib\site-packages`；`python312._pth` 放开 `import site` 并加 site-packages 路径。
  **不随包引导 pip**（减小体积、消除运行时联网装包面）。
- 全部下载物（embed zip、wheel、YuNet onnx、onnxruntime.dll）锁版本 + SHA256，
  写入 BUILD-INFO.json；校验失败中文报错中止，不静默使用损坏文件。

### live2d-py 钉 0.8.1（关键纠正，见 P-1 §4.1）

1.0.0 移除了 `live2d.v3` 模块并重构统一 Model API，与本仓库验收链
（`import live2d.v3` + `LAppModel` + `glInit/clearBuffer/glRelease`）不兼容。
基础包钉 `live2d-py==0.8.1`（cp311-abi3-win_amd64，含 `_v3cpp.pyd`+`_common.dll`，
MIT，依赖 pyopengl/pillow/numpy）。1.0.0 迁移另立任务，不在本次降级风险内引入。

## 不做（明确边界）

- torch/torchvision/transformers/SAM2、rembg、mediapipe、sounddevice/soundfile、
  edge-tts、faster-whisper/funasr **不进基础包**，走 P2 应用内按需下载器；
- 不内置 CUDA；全部 CPU wheel；
- 不签代码证书（SmartScreen 告警在自检页/发布说明如实说明）；
- 不做自动更新。

## 废弃声明

`deploy/installer/desktop.iss`（Inno，写死 0.10.2，依赖作者机 CUDA torch + 手工
权重 + iscc）与 `scripts/build_portable.py`（作者机 venv 镜像）**自 v0.11.0 起
正式废弃**，保留源码仅作历史参考，不再维护、不进 CI。桌面分发唯一通道为
Tauri NSIS + 本 ADR 布局。
