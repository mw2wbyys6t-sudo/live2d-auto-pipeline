# Desktop-Ready Sprint 1 — 地基层实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变现有产品能力的前提下，把项目从"开发者手动启动"升级到"小白用户双击启动器即可用"，同时关闭前一次健康度评审里 W1/W2/W3/S1 四条最关键的债务。

**Architecture:** 本轮不动业务逻辑，只动地基（清理、CI、启动器、降级实现）。三栈分工不变（Python 内核 + Go 接入 + Next.js 前端）。新增的 `start.*` 脚本作为"桌面软件形态"的临时载体；后续 Sprint 2 会用 Tauri 把它升级到真正的桌面安装包。

**Tech Stack:** Bash / Batch / Python（启动器）；Python（架构 lint 脚本）；Go test（注入测试用例）；YAML（CI 配置）

**Out of scope（本轮不做）:**
- rotation 变形器参数绑定（需美术判断，放 Sprint 2）
- F-06 多参数带轴序编译器接入（Sprint 2）
- Tauri/Electron 桌面应用包装（Sprint 2）
- 任何业务逻辑变更

---

## File Structure

**新建：**
- `tools/archive/2026-09/.README.md` — 归档说明
- `start.sh` — Linux/macOS 一键启动器
- `start.bat` — Windows 一键启动器
- `start.py` — 跨平台启动器（核心实现，shell 脚本仅做薄包装）
- `scripts/lint_architecture.py` — FF-1 依赖方向 lint

**修改：**
- `api_server.py` — 把仅回 202 的桩路由明确标注 501 + 友好说明
- `.gitignore` — 增补过程性产物的忽略规则
- `install.sh` / `install.bat` — 增强错误处理与版本检测
- `.github/workflows/ci.yml` — 给 moc3-acceptance 加 schedule
- `api/services/python_bridge_test.go` — 补充 FF-16 注入测试用例

**移动（归档）：**
- `tools/probe_*` 系列 → `tools/archive/2026-09/probe/`
- `tools/bisect_*` 系列 → `tools/archive/2026-09/bisect/`
- `tools/mutate_*` 系列 → `tools/archive/2026-09/mutate/`
- `tools/diag_*` 系列 → `tools/archive/2026-09/diag/`
- `tools/diagnose_*` 系列 → `tools/archive/2026-09/diagnose/`
- `tools/writeback_*` 系列 → `tools/archive/2026-09/writeback/`
- `tools/ladder_*` 系列 → `tools/archive/2026-09/ladder/`
- `tools/retry_*` 系列 → `tools/archive/2026-09/retry/`
- `tools/check_*` 系列（已并入测试的）→ `tools/archive/2026-09/check/`

**保留（仍可复用）：**
- 所有 `verify_*` 脚本（仍可被新数据复用）
- 所有 `tune_*` 脚本（参数调优工具）
- 所有 `score_*` 脚本（评估工具）
- `measure_export_duration.py`（性能基准）
- `download_models.py`、`build_portable.py`（部署工具）

---

## Task 1: tools/ 归档清理

**Files:**
- Create: `tools/archive/2026-09/.README.md`
- Move: `tools/probe_*.py` 等 → `tools/archive/2026-09/<category>/`

**判定标准：**
- 一次性诊断脚本（probe/bisect/mutate/diag/diagnose/writeback/ladder/retry）→ 归档
- 已被测试集覆盖的 check_* 脚本 → 归档
- 仍能被新数据/新版本复用的 verify/tune/score/measure_* → 保留

**步骤：**
- [ ] Step 1: 创建 `tools/archive/2026-09/` 目录及子目录
- [ ] Step 2: 写 `tools/archive/2026-09/.README.md` 说明归档原因与原始位置
- [ ] Step 3: 按类别移动脚本（保留 git mv 历史）
- [ ] Step 4: 验证 `pytest tests/unit tests/integration` 仍通过（这些脚本不被测试 import）
- [ ] Step 5: 在 `docs/CODE_STANDARD.md` 或 `.gitignore` 同步说明

**预期收益：** `tools/` 体积减少约 40%，新读者能立刻分辨"现行 vs 考古"。

---

## Task 2: python_server.py 路由收缩

**Files:**
- Modify: `api_server.py` 的桩路由处理函数

**当前问题（来自 LIMITATIONS.md §6 自述）：** 多数 POST 处理器仅回 202，给用户造成"功能对等"的错觉。

**修复策略（按 Go API 已实现的能力对照）：**
- Go API 已实现：角色 CRUD、生成、聊天、导出、上传、面捕代理
- Python 降级实现：仅保留 `/api/health`、`/api/characters/list`、`/api/characters/{id}`、`/api/generations/latest`、`/api/providers` 这类只读端点
- 其余 POST 路由：明确返回 `501 Not Implemented` + JSON `{success: false, message: "此功能仅在 Go API 模式下可用，请启动 api/live2d-api"}`

**步骤：**
- [ ] Step 1: 通读 `api_server.py`，列出所有路由及其当前实现状态
- [ ] Step 2: 把仅回 202 的路由改成 501 + 友好说明
- [ ] Step 3: 在 `api_server.py` 顶部 docstring 明确标注"仅降级用途"
- [ ] Step 4: 跑 `pytest tests/integration/test_api_contract.py` 验证契约未破

---

## Task 3: .gitignore 完善

**Files:**
- Modify: `.gitignore`

**待覆盖项：**
- `Work/`（moc3 评审的产物落盘目录）
- `output/`（生成产物，运行时产生）
- `dist/`（打包产物）
- `.autocase/`（审计基线）
- `.superpowers/`（brainstorm 中间产物）
- `.venv-runtime/`、`.go-cache/`（本地缓存）
- `*.beacon.bak`（旧备份文件）
- `web/.next/`（Next.js 构建缓存）
- `web/public/mediapipe/wasm/`（如果已忽略则不动）

**步骤：**
- [ ] Step 1: 读现有 `.gitignore`
- [ ] Step 2: 补齐上述项（已存在的跳过）
- [ ] Step 3: 跑 `git status --ignored | head -50` 确认过程性产物都被忽略
- [ ] Step 4: 不入库任何已存在的产物（用 `git rm --cached` 而非删除文件）

---

## Task 4: install 脚本健壮性增强

**Files:**
- Modify: `install.sh`、`install.bat`、`install.py`

**新增能力：**
- 各依赖版本检测前给出明确的最小版本与建议版本
- 单步失败时不静默继续，而是打印"如何修复"的链接到 docs/FAQ.md
- 全部步骤结束后输出"下一步"提示（启动命令）
- `install.py` 增加 `--check` 子命令仅做环境检测、不安装

**步骤：**
- [ ] Step 1: 读现有三个 install 脚本
- [ ] Step 2: 抽出公共的版本检测逻辑到 `install.py` 的 helper
- [ ] Step 3: shell/bat 脚本调用 `python install.py --check` 做预检
- [ ] Step 4: 失败路径打印 docs/FAQ.md 链接
- [ ] Step 5: 验证 `python install.py --check` 在当前环境跑通

---

## Task 5: 一键启动器

**Files:**
- Create: `start.py`（核心实现）
- Create: `start.sh`（Linux/macOS 薄包装）
- Create: `start.bat`（Windows 薄包装）

**功能规格：**
1. 预检环境（Python ≥3.10、Node ≥18、Go ≥1.21）
2. 若环境缺失，给出 `python install.py` 的修复提示并退出
3. 并行启动 Go API（端口 8080）与 Next.js 前端（端口 3000）
4. 监听子进程输出，按行加前缀（`[api]` / `[web]`）后打印
5. 捕获 Ctrl+C，先停前端再停后端，确保不留僵尸进程
6. 启动完成后自动打开浏览器到 http://localhost:3000

**关键实现要点：**
- 用 `subprocess.Popen` + `select` (Unix) / `WaitForMultipleObjects` (Windows) 做并发输出读取
- Windows 上启动浏览器用 `os.startfile`，macOS 用 `open`，Linux 用 `xdg-open`
- 子进程崩溃时打印"查看日志"指引，不让整个启动器静默退出

**步骤：**
- [ ] Step 1: 写 `start.py` 核心逻辑（环境检测 + 并行启动 + 优雅退出）
- [ ] Step 2: 写 `start.sh`（bash 调 `python3 start.py`）
- [ ] Step 3: 写 `start.bat`（cmd 调 `python start.py`）
- [ ] Step 4: 写自检测试 `tests/unit/test_launcher.py`（mock subprocess）
- [ ] Step 5: 手动跑一次 `python start.py --check` 验证环境检测路径

---

## Task 6: FF-1 依赖方向 lint 脚本

**Files:**
- Create: `scripts/lint_architecture.py`

**检测规则（来自 ADR-002 / FF-1）：**
1. Python 不 import 任何 `*.go` / `*.ts` 文件（grep `import .* \.go` / `require.*\.py`）
2. Go 不 import Python 源码（仅通过 subprocess + FS）
3. `web/` 下文件不直接读磁盘路径（无 `fs.readFileSync.*output`）
4. 不允许反向依赖（drivers → core 是允许的，core → drivers 应触发告警）

**实现策略：**
- 用 Python ast 模块扫描 `core/`、`live2d_builder/`、`drivers/`、`llm_bridge/`、`api_server.py` 的 import
- 用正则扫 Go `import` 块与 TS `import` / `require`
- 输出违规清单到 stdout，exit code 1 = 有违规

**步骤：**
- [ ] Step 1: 写脚本骨架（argparse + main）
- [ ] Step 2: 实现四条检测规则
- [ ] Step 3: 在当前代码库跑一遍，记录误报
- [ ] Step 4: 调整规则避免误报（如允许 `drivers/live2d_runtime/moc3_verify.py` 里的 `import live2d`）
- [ ] Step 5: 写最小测试 `tests/unit/test_lint_architecture.py`（构造合规与违规两份样本）

---

## Task 7: FF-16 命令注入测试

**Files:**
- Modify: `api/services/python_bridge_test.go`（或新增 `_inject_test.go`）

**测试用例（参照已实现的 `validatePath` 与 `validateStrictID`）：**
1. 路径含 `..` 段必须被拒（防 `../etc/passwd`）
2. 路径含 shell 元字符 `; & | * $ \x00` 必须被拒
3. 文件名以 `-` 开头必须被拒（防被当成参数）
4. 角色ID含特殊字符必须被拒（`strictIDPattern`）
5. 路径分隔符跨平台（`/` 与 `\` 都要切分）
6. 空路径必须被拒

**步骤：**
- [ ] Step 1: 读 `python_bridge.go` 的 `validatePath` 与 `validateStrictID` 实现
- [ ] Step 2: 写表驱动测试，覆盖上述六类
- [ ] Step 3: 跑 `go test ./services -run ValidatePath -v` 确认全绿
- [ ] Step 4: 故意把某个用例改成"应该接受但脚本拒了"，确认测试能抓到（红 → 绿验证）

---

## Task 8: CI moc3-acceptance 加 cron

**Files:**
- Modify: `.github/workflows/ci.yml`

**变更点：**
- `moc3-acceptance` job 当前 `if: github.event_name == 'workflow_dispatch'`
- 改成同时支持 `schedule` 触发：`if: github.event_name == 'workflow_dispatch' || github.event_name == 'schedule'`
- 在 workflow 的 `on:` 块加 `schedule: - cron: '0 2 * * *'`（每日北京时间 10:00 跑一次）
- 在 job 步骤里上传证据 artifact（保留 14 天）

**步骤：**
- [ ] Step 1: 编辑 `.github/workflows/ci.yml` 加 schedule
- [ ] Step 2: 给 moc3-acceptance job 加 `actions/upload-artifact@v4` 上传 `Work/multiband-axis/`、`Work/moc3-keyform-verification.json`
- [ ] Step 3: 用 `actionlint` 或手动 review 检查 YAML 语法
- [ ] Step 4: 在 `docs/architecture/index.md` § 4 把 FF-1 / FF-16 的"待补"标记更新为"已落地"

---

## Verification

全部任务完成后跑：
```bash
# Python 单元测试
python3 -m pytest tests/unit -q

# Go 测试
cd api && go test ./... -v

# 前端类型检查
cd web && npx tsc --noEmit

# 架构 lint（新工具）
python3 scripts/lint_architecture.py

# 启动器自检
python3 start.py --check
```

预期：全部零回归 + 新增测试全绿 + lint 无违规。

---

## Self-Review

**Spec coverage:**
- W1（FF-1 / FF-16 待补）→ Task 6 + Task 7 + Task 8 ✅
- W2（CI 默认跳过官方内核验收）→ Task 8 ✅
- W3（tools/ 累积调试脚本）→ Task 1 ✅
- W4（handlers.go 千行）→ **本轮不做**，需要业务上下文拆分，放 Sprint 2
- S1（python_server.py 半桩）→ Task 2 ✅
- 用户"下载就能用" → Task 4 + Task 5 提供"双击启动"地基，Sprint 2 升级到桌面安装包 ✅
- 用户"清除一些没有的东西" → Task 1 + Task 3 ✅

**Out of scope 重申：**
- 业务功能补全（rotation、F-06）→ Sprint 2
- 桌面应用包装（Tauri/Electron）→ Sprint 2
- handlers.go 拆分 → Sprint 2

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-10-07-desktop-ready-sprint-1.md`.**

下一节将使用 superpowers:dispatching-parallel-agents 把独立任务并行派发给子代理执行；Task 之间有依赖时按顺序执行（依赖关系：Task 1/3 独立 → Task 6 独立 → Task 2 独立 → Task 5 独立 → Task 7 独立 → Task 4 独立 → Task 8 独立 → Verification）。
