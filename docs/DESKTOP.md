# 桌面版（Desktop）

> 把 Live2D Master Agent 变成**电脑里的软件**：一个双击即用的 `Live2DMasterAgent.exe`，
> 内嵌完整 Web 工作台 + API 服务，不再需要三个终端、Node 开发服务器或端口代理。

## 形态与架构

```
┌────────────────────────── dist/Live2DMasterAgent.exe ──────────────────────────┐
│  Go 二进制（单文件）                                                            │
│  ├─ 内嵌 Web 工作台（go:embed ← Next.js 静态导出，同源服务）                     │
│  ├─ API 服务（生成/分层/导入/导出/桌宠/面捕 WebSocket）                          │
│  └─ Python 桥接（调用机器上的 Python 执行 core/* 重活，argv 安全传参）           │
└────────────────────────────────────────────────────────────────────────────────┘
        │ 同源 http://127.0.0.1:8080/
        ▼
    浏览器打开即为工作台（Generate → Layers → Export → Preview → Pet）
```

选型说明：这是 ComfyUI / Stable Diffusion WebUI / Ollama 同款的"本地服务 + 浏览器"形态。
相比 Electron/Tauri 原生窗口方案，它**零新增依赖**（无 CGO/Node 打包链），单文件可分发；
后续若要原生窗口壳，可平滑升级到 Wails（见文末路线图）。

## 构建

```bat
scripts\build_desktop.bat
```

脚本做四件事：`NEXT_STATIC_EXPORT=1 next build`（静态导出）→ 复制 `web/out` 到
`api/webui/dist`（go:embed 源）→ `go build -H windowsgui`（无控制台单文件）→
同时产出带控制台的调试版。产物在 `dist/`：

- `Live2DMasterAgent.exe` — 双击运行（无控制台窗口）
- `Live2DMasterAgent-console.exe` — 带控制台日志，排查 Python 桥接问题时用

## 运行要求与首次启动

1. **项目根目录**：exe 启动时会从自身位置向上查找 `core/workflow.py` 标记来定位
   Python 工程与输出目录。推荐把 exe 放在**项目根目录**（或根目录的 dist/ 内）；
   任意位置运行时用 `-config` 指定配置（`scripts_dir` 指向项目根）。
2. **Python 环境**：机器上需有 `python`（Windows）/`python3`（Unix）及依赖
   （`pip install -r requirements.txt`）。API 已就绪时工作台首页会显示各服务状态；
   缺依赖时生成/分层会返回**带确切安装命令的报错**，不会假成功。
3. 端口默认 8080，`-port` 可改；`-config`、`-host` 同主程序文档。

## 与开发模式的关系

| | 开发模式 | 桌面版 |
|---|---|---|
| 前端 | `npm run dev`（:3000，rewrites 代理） | 静态导出内嵌进 exe（同源） |
| 后端 | `go run ./api`（:8080） | 同一个 exe |
| 静态导出开关 | `NEXT_STATIC_EXPORT` 未设置 | `NEXT_STATIC_EXPORT=1` |

`NEXT_STATIC_EXPORT=1` 时 next.config 走 `output: 'export'` 且**省略 rewrites**
（导出模式不支持；同源下也不需要）。两条构建路径互不影响。

## 已知边界

- exe 内嵌的是构建时刻的 UI；改前端后需重跑 `build_desktop.bat`。
- Python 解释器与依赖不打进 exe（见下方路线图）；无 Python 时 API 与 UI 可用，
  但生成/分层/导出会明确报错。
- 桌面版跨平台：Windows 构建脚本目前为 `.bat`；macOS/Linux 用
  `NEXT_STATIC_EXPORT=1 npm run build && cp -r web/out api/webui/dist && go build`。

## 路线图（完全离线的"绿色版"）

1. **嵌入 Python 运行时**：用 python.org 的 Windows embeddable 包 +
   `pip install --target` 离线依赖目录，随 exe 分发（`PYTHONPATH` 指向包内目录），
   实现零 Python 环境要求——需要一次性联网拉取发行物。
2. **原生窗口壳**：Wails（Go + WebView2）包一层，获得独立窗口/托盘/协议跳转；
   需要 CGO 工具链（当前环境无 gcc，暂缓）。
3. **自动更新**：单文件 + 版本接口（`/api/info`）已具备，差一个更新器。
