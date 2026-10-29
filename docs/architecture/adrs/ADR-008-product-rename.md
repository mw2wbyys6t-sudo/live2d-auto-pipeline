# ADR-008：产品改名 — Live2D Master Agent → Pygmalion Studio

> 状态：Accepted（2026-10-28，随 v0.10.10 生效，安装包尚未构建）。
> 关联：ADR-001 许可（Rev.5 起 AGPL-3.0 + 商业授权双轨对外分发）；ADR-007 桌面安装布局；ADR-002 三栈架构。

## 背景

1. 旧名里的 **Live2D 是第三方商标**，把它放进产品名等于把商标当成产品标识来分发
   （ADR-001 Rev.5 起本项目以 AGPL-3.0 + 商业授权双轨对外分发），且会让读者误以为它是官方工具。
2. 项目的实际能力不止 Live2D：一句话/一张图 → 立绘 → 分层 PSD → 自动绑定 moc3 →
   预览/面捕/桌宠/LLM 对话。旧名只描述了其中一段输出格式。
3. 仓库的作者与标识符早已是 Pygmalion（`com.pygmalion.*`、Cargo 作者字段），
   「Pygmalion Studio」把品牌与既有命名对齐；语义上也贴：雕塑家做出的像活了过来。

## 决策

改名必须覆盖**有影响的身份**，而不是只换标题；同时**不得让老用户的数据消失**。

| 位置 | 旧 | 新 |
|---|---|---|
| Tauri `productName` / 窗口标题 | Live2D Master Agent | Pygmalion Studio |
| Tauri `identifier` | `com.pygmalion.live2d-master-agent` | `com.pygmalion.studio` |
| Cargo 包名 → 二进制名（决定 CI 产物路径） | `live2d-master-agent(.exe)` | `pygmalion-studio(.exe)` |
| Rust lib 名 | `live2d_master_agent_lib` | `pygmalion_studio_lib` |
| NSIS `MyAppName` / `MyAppExeName` / `OutputBaseFilename` | Live2DMasterAgent | PygmalionStudio |
| 数据根（Windows） | `%APPDATA%\Live2DMasterAgent` | `%APPDATA%\PygmalionStudio` |
| 数据根（Unix） | `~/.live2d-master-agent` | `~/.pygmalion-studio` |
| 产物根 | `Documents\Live2DMasterAgent\output` | `Documents\PygmalionStudio\output` |
| 密钥库 | `%APPDATA%\live2d-master-agent\vault.key` | `%APPDATA%\PygmalionStudio\vault.key` |
| `.env` 技能目录 | `~/.trae-cn/skills/live2d-master-agent/.env` | 新名优先，**旧路径继续搜索** |

### 兼容规则（本 ADR 的核心约束）

路径解析只允许一条规则：**新目录优先；旧目录只要存在就继续沿用；两者都不存在才落到新名。**
不允许「找不到新目录就新建一个」——那会让角色库、导出产物与已存密钥在用户眼里凭空消失。

实现位置（两份同规则解析器，各自被测试钉住）：

- Python：`core/app_paths.py`（`api_server`、`scripts/optional_components` 统一取用）
  与 `core/secure_storage.py::master_key_path_for_platform`。
- Go：`api/config/desktop.go::resolveDataRoot` / `resolveDocumentsRoot`。

### 刻意不改的两处

1. `SecureStorage.APP_IDENTIFIER`（旧版保险库 PBKDF2 的派生输入）——改名会改变派生结果，
   导致**老密文永久解不开**。它只是历史上恰好含旧名，属于加密参数而非品牌。
2. Go 后端二进制名 `live2d-api(.exe)`（Go module 亦为 `live2d-api`）——内部标识，
   不出现在用户界面；`desktop/src/lib.rs` 与 NSIS/CI 按这个名字找后端进程，
   为品牌统一去改它只会制造断链。

## 后果

**正面**：品牌不再依赖第三方商标；名称覆盖「立绘→分层→绑定→桌宠→对话」整条链路；
数据目录解析规则被测试固化，后续再有改名/迁移有据可依。

**代价与风险（须在发布说明中写明）**：

- Tauri `identifier` 变更 = 操作系统眼中的**另一个应用**：老用户升级 v0.10.10 前
  应先用旧安装目录的 `uninstall.exe` 卸载，否则会出现新旧并存、快捷方式指向不明。
- 已发布的 v0.10.9 产物名仍是 `Live2D.Master.Agent_0.10.9_x64-setup.exe`，
  CHANGELOG/README 中相关历史记录**不改写**（改写会让验收记录与真实产物对不上）。
- 文档里出现的所有安装路径需按新名读；`tests/e2e/checklist.md` 顶部已加「验收旧包时换回旧名」的说明。

## 验证

- `tests/unit/test_product_name_consistency.py`：在用源码/配置/当前文档中不得残留旧名
  （刻意保留的回退行必须标 `legacy`）；逐项核对 Tauri/Cargo/NSIS/Rust/CI 的身份一致性。
- `tests/unit/test_product_rename_compat.py` + `api/config/desktop_paths_test.go`：
  新名优先、旧目录沿用、双存在取新、显式覆盖最优先，覆盖 Windows/macOS/Linux 三种根。
- `tests/unit/test_release_version_single_source.py`：Cargo.lock 条目随包名一起改名，
  防止「清单与锁文件不同步导致 `--locked` 编不出来」复发。
- 待办（未做即不算落地）：Windows runner 实跑 `scripts/build_desktop_bundle.py` +
  NSIS 构建，按 ADR-007 §验收与 `tests/e2e/checklist.md` 的改名兼容项验证。
