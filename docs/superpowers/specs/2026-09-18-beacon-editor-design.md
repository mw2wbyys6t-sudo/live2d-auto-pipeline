# Beacon 编辑器 — 设计方案

- 日期：2026-09-18
- 状态：待用户审阅
- 范围：为 beacon 项目补齐 `.moc3` 编译能力，并建立双栏编辑器工作区

---

## 1. 背景与问题

当前 beacon 项目已具备「图片 → 语义分层 → 网格/骨骼/变形器/参数/物理 → 导出 model3.json」的完整自动化管线，但**导出物缺少 `.moc3`**。这导致：

1. 导出在界面上显示成功，实际产物无法被任何 Cubism 运行时加载（"虚假成功"）
2. 模型无法在 VTube Studio / Cubism 等生态中互通
3. 人物"动不起来"缺少真正的运行时绑定数据

`docs/native-runtime.md` 已把此问题列为未完成项第 4 条：

> 从素材自动建立 Cubism 工程并导出真实 moc3。

本设计正是针对这一条。

---

## 2. 已确认的决策

经与用户逐项确认，以下决策已锁定：

| 编号 | 决策项 | 结论 |
|---|---|---|
| D1 | 总体路线 | **双轨制**：自建编辑能力 + 多目标导出（Cubism 兼容轨 / 自有轨） |
| D2 | 编辑器入口交互 | **优先「继续上次任务」** —— 直接加载最近一次生成结果，不在编辑器内重走全流程 |
| D3 | 编辑器布局 | **双栏专业工作区**：左图层树 / 中画布 / 底部参数条 |
| D4 | moc3 优先级 | **攻坚到官方内核加载通过**（以 `csmHasMocConsistency` + 官方加载为客观验收） |
| D5 | 交付顺序 | **先做 P0 验证闭环**，再投入 UI |
| D6 | py-moc3 用法 | **作为依赖 + 结构参考**；编译器由本项目自行实现（不用它直接写文件） |

---

## 3. 现状盘点

### 3.1 已存在、必须复用（不重写）

| 能力 | 位置 | 说明 |
|---|---|---|
| 原生 Cubism 渲染器 | [native.py](file:///c:/Users/admin/Desktop/Live2D/beacon/drivers/live2d_runtime/native.py) | `validate_package()` 校验清单/引用/越界；`package_sha256()` 指纹；`CubismRenderer` 含 `HasMocConsistencyFromFile()` 与参数读回校验 |
| 像素级运行验收 | [verify.py](file:///c:/Users/admin/Desktop/Live2D/beacon/drivers/live2d_runtime/verify.py) | 参数 min/max 驱动 + 控制组漂移对比 + 还原误差，产出 `runtime_verified` 与证据图 |
| 模型构建管线 | [pipeline.py](file:///c:/Users/admin/Desktop/Live2D/beacon/live2d_builder/pipeline.py) | 10 步构建，产出 meshes / uv / 骨骼 / 变形器 / 参数 / 表情 / 物理 / 图集 / model3.json |
| 官方参考模型 | `Work/native-sample/Haru/Haru.moc3` | 仓库内已有一份官方样例，可作为结构对照基准 |
| 验证结果样例 | `Work/native-verification.json` | 显示 `runtime_verified: true` 的完整验收产物格式 |

> **重要**：P0 的"隔离验证"环节**不需要新造**，`native.py` + `verify.py` 已具备。本设计的重点是**补齐 moc3 编译器并接入既有验证链**。

> **关于两个验证路径（易混，需明确）**：
> - `CubismRenderer.report()` **刻意保守返回 `runtime_verified=False`**，其
>   `deployment_status` 为 `requires_visual_verification`；这是设计意图，
>   防止"绘制成功"被误当作"验收通过"（`tests/unit/test_native_runtime.py`
>   有 `test_native_contract_does_not_claim_acceptance` 断言保护）。
> - **真正的验收结论由 `verify.py` 独立计算**，即
>   `Work/native-verification.json` 中 `runtime_verified: true` 的来源。
>
> 本设计一律采用 **`verify.py` 的结论**作为最终验收依据，不使用 `report()`。

### 3.2 缺失（本设计要补的）

1. **moc3 编译器** —— 无任何将绑定数据写成 `.moc3` 二进制的实现
2. **moc3 自洽性静态校验** —— 生成前拦截索引越界、长度不匹配、引用悬空
3. **轻量 moc3 一致性沙箱入口** —— 只做一致性检查、不启 OpenGL 的隔离进程
4. **编辑器界面** —— 无双栏编辑工作区

---

## 4. 设计目标与非目标

### 目标

- G1：产出能被官方 Cubism Core **接受**的 `.moc3`（`csmHasMocConsistency` 通过，且能被 `LAppModel` 加载）
- G2：任何导出结论必须**有客观证据**，禁止虚假成功
- G3：Cubism 相关调用**必须进程隔离**，崩溃不得影响后端服务
- G4：编辑器能"继续上次任务"，直接进入微调
- G5：复用既有 `native.py` / `verify.py`，不产生第二套验证逻辑

### 非目标（本轮明确不做）

- N1：不实现 Cubism Editor 级别的完整绑定 UI（顶点级网格编辑、贝塞尔变形器等）
- N2：不做 `.cmo3` 工程文件读写
- N3：不做动画关键帧编辑器（留给 P2）
- N4：不替换现有透明桌宠窗口实现

---

## 5. 架构总览

```
                    ┌─────────────────────────────────────────┐
   图片 → SAM 分层 → │  ① 绑定数据 (已有 pipeline 产出)         │
                    └────────────────┬────────────────────────┘
                                     ↓
                    ┌─────────────────────────────────────────┐
                    │  ② Moc3Compiler  (新增)                  │
                    │     sections 模型 → 构建 → 写字节         │
                    └────────────────┬────────────────────────┘
                                     ↓
                    ┌─────────────────────────────────────────┐
                    │  ③ Moc3Linter  (新增)                    │
                    │     自洽性静态校验（生成前拦截）          │
                    └────────────────┬────────────────────────┘
                                     ↓
                              model.moc3 + model3.json
                                     ↓
                    ┌─────────────────────────────────────────┐
                    │  ④ 隔离验证 (复用 native.py/verify.py)    │
                    │     子进程：sdk.init() → 一致性 → 像素验收 │
                    └────────────────┬────────────────────────┘
                                     ↓
                        runtime_verified: true / false + 诊断
```

---

## 6. 模块设计

### 6.1 新增：moc3 section 模型与编译器

**位置**：`live2d_builder/exporter/moc3_sections.py`、`live2d_builder/exporter/moc3_writer.py`

职责分离：

- `moc3_sections.py`：定义 99 个 section 的名称、元素类型、计数索引、对齐、分组；提供 `CountIdx` 计数表语义。**纯数据定义，无 IO**。
- `moc3_writer.py`：接收「绑定数据 dict」，构建 section 图，序列化为 `.moc3` 字节。**纯函数，无 IO、无外部进程**，可直接单元测试。

**关键约束**（由实测得出，必须遵守）：

| 约束 | 内容 |
|---|---|
| C1 | `.moc3` 不含贴图，图片靠 `model3.json` 的 `Textures` 引用 |
| C2 | `counts[UVS]` 计的是**浮点数个数**（= 2 × 顶点数），不是顶点数 |
| C3 | 必须产出完整绑定链：`parameter.keyform_binding_*` → `keyform_binding_band.*` → `keyform_binding_index.indices` → `keys.values` |
| C4 | 每个部件必须有 `part_keyform.draw_orders`；每个网格必须有 `art_mesh_keyform.*` |
| C5 | `keyform_position.xys` 必须覆盖所有关键形的顶点位置 |
| C6 | 必须有 `draw_order_group*` 且覆盖全部 art mesh |
| C7 | `runtime_space` 系列留空（运行期填充）；`glue.*` 可留空 |

> C3–C6 是本次实测失败的直接原因：官方 Core 报 `Data section is invalid`，
> 因为只写了"段"却没有写段之间的自洽引用关系。

### 6.2 新增：自洽性静态校验器

**位置**：`live2d_builder/exporter/moc3_lint.py`

在写出字节**之前**做纯 Python 校验，避免把非法数据交给会崩溃的 Cubism Core：

- 每个 section 的实际长度 == 对应 `counts` 期望长度
- 所有 `*_begin_indices` + `*_counts` 落在目标数组范围内
- 引用型索引（parent_part_indices / parent_deformer_indices / texture_indices）不越界
- 参数绑定链无悬空引用
- 输出结构化问题列表（section、预期、实际、建议）

### 6.3 新增：轻量一致性沙箱入口

**位置**：`drivers/live2d_runtime/moc3_consistency.py`

独立进程入口，**只做一致性检查，不启 OpenGL**：

```python
# 伪代码
import live2d.v3 as sdk
sdk.init()                      # 实测：不 init 直接调用会段错误
try:
    m = sdk.LAppModel()
    ok = m.HasMocConsistencyFromFile(path)
    print(json.dumps({"ok": ok}))
finally:
    sdk.dispose()
```

**调用方约束（必须有）**：

- 用 `subprocess` 独立进程执行，**不得 in-process 调用 Cubism Core**
- 设置**超时**（建议 30s）
- 崩溃（非零退出/信号）**视为"验证失败"**，不抛异常、不中断服务
- 返回结构化结果：`{ok, exit_code, stdout, stderr, timed_out, crashed}`

> **实测依据**：本设计中两次直接调用 Cubism Core 都触发了访问违规崩溃
> （`0xC0000005`）：一次是加载不自洽的 moc3 后崩溃，一次是未 `sdk.init()`
> 就调用一致性检查。这证明 Cubism Core 不可信，进程隔离是**硬性要求**。

### 6.4 编辑器 UI（P1）

**位置**：`web/pages/editor.tsx`（新增），复用 `components/LayerCanvas.tsx`、`components/ModelCanvas.tsx`

布局（D3）：左图层树 / 中画布 / 底部参数条。

- 进入时调 `GET /api/generations/latest` 加载已有层（D2）
- 图层树显示「中文名 + 语义名」双标签，便于核对 SAM 结果
- 画布用现有 `ModelCanvas` 渲染
- 底部参数条展示参数当前值，支持拖动微调
- 顶部显示导出就绪状态：`runtime_ready` 徽标 + blocker 原因

### 6.5 API 契约（新增/扩展）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/export` | 扩展返回体，增加 `runtime_ready`、`moc_reference`、`blocker`、`consistency` 字段 |
| GET | `/api/generations/latest` | 已存在，供编辑器加载最近任务 |
| POST | `/api/moc3/compile` | 用指定层数据编译 moc3，返回编译 + 校验结果 |
| POST | `/api/moc3/verify` | 对已有 moc3 跑隔离一致性检查 |

**`runtime_ready` 判定规则（关键）**：

```
runtime_ready = moc3_exists
              AND linter_passed
              AND consistency_subprocess.ok == true
```

任一项为假，`blocker` 必须写明具体原因，**绝不返回成功**。

---

## 7. 错误处理

| 场景 | 处理 |
|---|---|
| Linter 发现自洽性问题 | 不写出 moc3，返回问题清单 |
| 一致性检查超时 | 判为失败，`blocker = "consistency check timed out"` |
| 一致性检查崩溃 | 判为失败，记录退出码，`blocker = "cubism core crashed (code=N)"` |
| `live2d-py` 未安装 | 判为失败，`blocker` 提示缺失依赖，不静默降级 |
| 打包校验失败（引用越界/绝对路径） | 复用 `native.validate_package()` 抛错并阻断 |

---

## 8. 测试与验收标准

### 8.1 单元测试（纯 Python，快）

- `test_moc3_writer.py`：写出字节 → 用 `py-moc3` 读回 → section 逐项比对（长度、索引范围、ID）
- `test_moc3_lint.py`：构造越界/长度不符/悬空引用，断言被拦截
- `test_moc3_consistency_runner.py`：用**替身**模拟子进程，断言超时/崩溃被正确归类为失败

### 8.2 集成测试（需要 `live2d-py`）

- `test_moc3_official_acceptance.py`：编译最小模型 → 跑隔离一致性检查 → **断言 ok == true**
- 回归保护：对官方 `Work/native-sample/Haru/Haru.moc3` 跑一致性检查，**必须为 true**（防止把检查器写坏）
- 负例：篡改若干字节后检查**必须为 false**

### 8.3 P0 验收门槛（Go / No-Go）

> **必须同时满足，否则路线 A 判定为受阻，需回退讨论：**
>
> 1. 编译出的最小 moc3 通过 `csmHasMocConsistency`
> 2. 该 moc3 能被 `LAppModel.LoadModelJson()` 成功加载
> 3. 加载后 `GetParamIds()` 非空
> 4. 官方 Haru.moc3 的一致性检查回归为 true
> 5. 篡改文件的检查为 false

### 8.4 P1 验收门槛

- 编辑器能自动加载最近生成结果并显示图层树
- 导出页对未通过验证的模型**明确显示 blocker**，不显示成功
- 全量既有测试（当前 110 项）保持通过

---

## 9. 分期交付

### P0 — moc3 最小闭环（本次重点）

1. `moc3_sections.py` 数据定义
2. `moc3_writer.py` 编译器
3. `moc3_lint.py` 自洽校验
4. `moc3_consistency.py` 隔离沙箱入口
5. 单元 + 集成测试
6. **产出**：一个最小模型被官方内核接受的证据

### P1 — 编辑器骨架

1. `web/pages/editor.tsx` 双栏布局
2. 接入 `/api/generations/latest`
3. `/api/moc3/compile`、`/api/moc3/verify` 端点
4. 导出页 `runtime_ready` / `blocker` 展示
5. 把 P0 编译器接到 `pipeline.py` 第 9 步之后

### P2 — 可动性与自有轨道

1. 参数绑定编辑、物理配置面板
2. 动画序列
3. 自有格式 + 自有运行时轨道的成型

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Cubism Core 语义要求未知细节多 | 反复试错 | 以官方模型做结构对照；每轮有客观通过/不通过信号。**注意**：仓库内只有 `Work/native-sample/Haru/`；Mark/Hiyori 本次下载在系统临时目录（`%TEMP%/beacon_moc3_ref`），会被清理，需要时须重新获取 |
| `live2d-py` 是第三方封装（非官方） | 依赖风险 | 它仅用于**验证**，不进入生产数据路径；验证失败只降级结论 |
| Cubism Core 崩溃 | 服务中断 | 强制子进程隔离 + 超时；崩溃归类为验证失败 |
| `py-moc3` 单人维护、Beta | 依赖风险 | 仅作参考与交叉校验；编译器自研，不依赖其运行时 |
| 官方样例模型授权 | 法律风险 | **已核实**：`Work/` 未被 git 跟踪（`git ls-files Work` = 0），样例不会随项目分发，风险低。建议仍显式加入 `.gitignore` 以长期锁死 |
| moc3 格式存在 CVE（越界写） | 安全 | 自研写入器全程做边界校验；只生成、不加载不可信 moc3 |

---

## 11. 未决问题

1. `Work/native-sample/Haru/Haru.moc3` 已核实**未被 git 跟踪**，保留在本地作对照即可；建议在 `.gitignore` 增加 `Work/` 条目，防止将来误提交。
2. P0 首个目标模型的最小规模：建议「1 部件 + 1 网格 + 1 参数」起步，逐步加复杂度。
3. 编辑器是否需要"从零上传"入口？（D2 已定优先"继续上次"，但未排除后续补充）

---

## 12. 附：实测证据摘要

| 实验 | 结果 |
|---|---|
| `pip install py-moc3` | 成功，0.1.0，MIT |
| 从零构造 1部件/1网格/1参数 moc3 | 成功写出 4672 字节 |
| py-moc3 读回往返 | PASS，二次序列化字节一致 |
| `pip install live2d-py` | 成功，0.7.0.4，Cubism Native 05.01.0000 |
| 官方 Core 加载该 moc3 | **失败**：`csmHasMocConsistency: Data section is invalid` → `Inconsistent MOC3` → **段错误崩溃** |
| 下载官方 Mark/Hiyori/Haru | 成功；解出 Mark 的 99 个 section 结构（80 非空 / 19 空） |
| 未 `sdk.init()` 调用一致性检查 | **段错误崩溃**（`0xC0000005`） |
