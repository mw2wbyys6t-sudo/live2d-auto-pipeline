# 桌面版 v1 · 真实可用性 PRD

> 目标形态：一个装完即可用的 Windows 桌面应用。**可见的每个控件都必须有真实效果，或明确标注不可用；任何失败都必须显式失败，不得假成功。**

## 1. 背景与目标

**观察到的证据**

- 安装包已能稳定产出（Tauri NSIS，约 321 MB，内置 CPython 3.12 + 99 个依赖，装配冒烟 9 项必装模块全部可导入）。
- 但功能审计发现 13 处"看起来能用、实际没有"或"失败却报成功"的问题（见 §4.4 决策表）。典型：
  - 导出页 PSD 选项 **100% 失败**（前端读 `data.success`，后端只在外层 envelope 给 `success`）。
  - 角色保存/删除失败时用 `.catch()` 伪造成功，页面清除脏标记、卡片从列表消失。
  - 生成页的 Provider / 风格 / Steps / CFG，导出页的 Include physics/expressions/motions / Compression，Live2D 页的模型结构树 / Physics 面板 / Wireframe，**全部不影响实际行为**。
  - Go 的 See-through 接口从未真正抠图，却以 `200 + success:true` 返回。
  - 对话上游不可达时返回固定"萌系"文案并标记 `done`，前端无法区分真假回复。
  - Python 面捕在依赖缺失时仍返回 `running: true`。

**受影响用户与时刻**

第一次安装的普通用户，在"生成→分层→导出→预览"这条主链路上按下按钮之后。

**产品决策**

v1 不新增能力，只做**真实化**：能做的接通、做不了的标注或移除、失败就报错。宁可少一个按钮，不留一个假按钮。

**可观察结果**

- 主链路任一按钮按下后，要么产生可见产物，要么给出可读的错误原因；不存在"点了没反应"或"提示成功但产物不存在"。
- 页面上不存在"改了也不影响结果"的控件。
- 降级/占位产物带有机器可读标记，UI 明确展示。

## 2. 范围

**入口条件**：用户双击安装包完成安装，首次启动。

**P0 动作（完成即算可用）**

1. 启动后能看到工作台，且不是"Go API backend not found"错误页。
2. 生成一张立绘（云端或离线占位图，但必须标注来源）。
3. 对生成的图做分层，得到 `layer_*.png`。
4. 导出 Live2D，得到 `.moc3` / `model3.json` / 纹理；若官方内核验收未通过，如实标注。
5. 预览页能加载模型并看到参数形变。
6. 部署桌宠，关闭后状态回到 `stopped`。

**完成证据（用户可见）**

`%USERPROFILE%\Documents\Live2DMasterAgent\output\` 下出现对应产物；页面无报错；`/api/diagnostics` 全项 `ok=true`。

**恢复路径**

- 生成失败 → 显示具体原因（无网络 / 无 Key / 上游限流），并可重试。
- 分层失败 → 显示缺失依赖与安装方式，而不是给出必 404 的下载链接。
- 导出失败 → `runtime_ready=false` + `blocker` 文案，产物仍可下载。
- 后端未就绪 → 显示诊断页入口，而不是白屏。

**P1**

- Python 降级后端的接口补齐或能力探测（当前只有 Go 后端是完整形态）。
- SAM2 / rembg 等重型能力按需下载。

**明确排除（v1 不做）**

- 自动更新、代码签名、崩溃上报。
- 官方 Cubism 工程自动创建与编译。
- 网格键形、rotation 参数绑定（已知能力缺口，继续保持如实标注）。

## 3. 核心流程

```
安装 → 首次启动（自动建 %APPDATA% 与文档输出目录）
     → Generate（出图，标注来源）
     → Layers（分层，产出 layer_*.png）
     → Export（moc3 + model3.json + 纹理，标注 runtime_ready）
     → Preview（加载模型、拖参数）
     → Pet（部署桌宠、关闭回收）
```

任一环节失败都必须在**当前页面**给出可读原因与下一步动作，不得静默、不得假成功。

## 4. 功能需求

### 4.1 能力声明（CAP）

**目的**：让"能不能用"可被机器判断，而不是靠文案猜。

- 每个模块在渲染前必须获得该能力的真实状态：`available` / `unavailable` / `degraded`。
- `unavailable` 的控件必须**不可点击**，并显示原因与恢复方式。
- `degraded` 的控件可点击，但必须在结果处标注降级（例如"颜色分层（实验性）"）。
- 能力状态来源必须是**实际探测**（进程/文件/依赖导入/接口返回），不得硬编码 `true`。

### 4.2 失败必须显式失败（ERR）

- 任何写操作（保存角色、删除角色、导出、部署）失败时，UI 必须保留原状态并提示失败原因。
- 禁止用本地拼接对象、`.catch(() => undefined)` 等方式伪造成功。
- 接口在"功能不存在"时应返回 **501**（或明确的 `not_implemented`），不得返回 `200 + success:true`。
- 上游不可达时的兜底回复必须携带 `fallback: true`（或等价标记），UI 必须展示"离线兜底"标识。

### 4.3 静态/占位数据必须标注（LBL）

- 离线兜底图：响应与 UI 都必须显示"占位图（离线兜底）"。
- 离线兜底对话：显示"离线兜底回复"。
- 静态演示区（若保留）：标题旁标注"静态演示，不随模型变化"。
- 硬编码列表（模型/表情）：若无法按真实数据生成，必须标注"内置示例列表"并从"能力可用"语义中剔除。

### 4.4 删/留决策表（按审计证据）

| ID | 位置 | 现状 | v1 决策 |
|---|---|---|---|
| D-01 | 导出页 PSD 导出 | 前端读内层 `success`，后端只在外层给 → 必失败 | **修**：前端按 envelope 判定 |
| D-02 | 角色保存 | `catch` 返回本地伪对象，清脏标记 | **修**：失败保留表单并报错 |
| D-03 | 角色删除 | `catch` 吞错，卡片仍移除 | **修**：失败不移除并报错 |
| D-04 | 生成页 Provider / Style / Steps / CFG | 未进入请求体，后端无对应字段 | **移除或置灰标注**（不接通） |
| D-05 | 导出页 Include physics / expressions / motions / Compression | 未进入请求体，后端无字段 | **移除或置灰标注** |
| D-06 | Live2D 页模型结构树 / Physics 面板 / Wireframe | 静态写死，不影响渲染 | **移除或标注"静态演示"** |
| D-07 | Go `/api/see-through` | 从不抠图，恒 200+success | **改为 501** + 明确原因 |
| D-08 | Go LLM 兜底回复 | 固定文案、无标记、`done` | **加 `fallback:true`**，UI 展示 |
| D-09 | Python 面捕启动 | 未检查 `is_running()` 即 `running:true` | **修**：失败返回错误 |
| D-10 | 分层页 PSD 下载 | `psd_path` 是目录，链接必 404 | **修**：只给文件链接，目录降级为"打开文件夹"或不展示 |
| D-11 | 仪表盘 GPU / 延迟 | 写死 `false` / `0ms` | **改为"未探测"** 或真实探测 |
| D-12 | `/api/info` features | 固定宣称人脸追踪、缓存等 | **改为按实际探测生成** |
| D-13 | `/api/models`、`/api/expressions` | 硬编码且与模型无关 | **标注"内置示例"** 或按真实数据生成 |

### 4.5 安装与卸载（PKG）

- 安装后不写安装目录；用户数据只在 `%APPDATA%` 与「文档」。
- 卸载保留用户产物；卸载程序不得删除 `output/`、`characters/`。
- 首次启动自动创建数据目录；创建失败不阻断启动，但要在诊断页可见。

### 4.6 面捕（TRK）

- 原生路径：`face_tracker.exe` 不可用时，`/api/tracking/start` 返回 501 与可读原因。
- Python 路径：解释器/模型/依赖任一缺失时，启动必须失败，不得 `running:true`。
- 两条路径的帧数据必须走前端实际订阅的通道与消息名。

### 4.7 导出（EXP）

- 导出结果必须包含 `runtime_ready` 与（未通过时的）`blocker`。
- `runtime_ready=false` 时，UI 显示"未通过官方内核验收"，但产物可下载。
- 导出页不得出现任何"不改变产物"的开关。

## 5. 共享规则

**状态与错误语义**

| 场景 | 语义 | UI |
|---|---|---|
| 功能不存在 | `501 not_implemented` | 控件不可用 + 原因 |
| 能力缺失（可修复） | `4xx` + `missing` 列表 | 显示恢复步骤 |
| 超时 | `504` | 可重试 |
| 降级成功 | `200` + `degraded:true` + `degraded_reason` | 结果处标注 |
| 真实失败 | `5xx` + 错误原文 | 保留原状态 + 重试 |

**降级标记字段**（统一命名，便于 UI 判定）

`local_placeholder`、`fallback`、`degraded`、`runtime_ready`、`semantic_hsv_fallback`。

## 6. 决策记录（原待决问题已闭环）

| ID | 决策 |
|---|---|
| Q-01 | **删除** 生成页的 Provider / 风格 / Steps / CFG（已实施） |
| Q-02 | **删除** Live2D 页的静态模型树、Physics 面板、Wireframe 开关（已实施） |
| Q-03 | **仅 Go 后端为受支持形态**；Python 降级后端不承诺接口完整性（已实施：不再假报成功） |
| Q-04 | 保留 `/api/models`、`/api/expressions`，但明确标注为「内置示例列表」（已实施） |

## 7. 实施状态（本轮）

| ID | 状态 | 落点 |
|---|---|---|
| D-01 | 已修 | `web/lib/api-client.ts` 合并外层 envelope 的 `success` |
| D-02 | 已修 | `web/pages/characters/[id].tsx` 去掉伪造成功 |
| D-03 | 已修 | `web/pages/characters/index.tsx` 删除失败保留列表并报错 |
| D-04 | 已删 | `web/pages/generate.tsx` 移除 Provider/Style/Steps/CFG |
| D-05 | 已删 | `web/pages/export.tsx` 移除 Include*/Compression |
| D-06 | 已删 | `web/pages/live2d.tsx` 移除结构树/Physics/Wireframe |
| D-07 | 已修 | `api/handlers/handlers.go` See-through 改 501 |
| D-08 | 已修 | `api/services/chat_service.go` + `api/models/models.go` 增加 `fallback` 标记与可见文案 |
| D-09 | 已修 | `api_server.py` 启动后校验 `is_running()`，失败返回 503 |
| D-10 | 已修 | `core/psd/creator.py` 降级时 `psd_path` 为空并给出 `package_dir` |
| D-11 | 已修 | `web/lib/api-client.ts` 真实测延迟；GPU 改为「未探测」 |
| D-12 | 已修 | `api/handlers/handlers.go` `/api/info` features 按实际探测 + `notes` |
| D-13 | 已标 | `/api/models`、`/api/expressions` 响应文案标注「内置示例」 |

验证：`npx tsc --noEmit`、`NEXT_STATIC_EXPORT=1 next build`、`go build ./...`、
`go test ./...`、`python -m py_compile` 全部通过。
