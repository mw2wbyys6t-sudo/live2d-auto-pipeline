# moc3 导出链路 — 架构风险与质量评审（2026-09-20）

评审目标：判断「从素材到可运行的 Live2D 模型」这条链路，在当前证据下是否
足够健康，以及风险集中在哪里。
干系人：本项目维护者（同时是唯一用户）；下游消费者为桌宠窗口与 Cubism 运行时。
风险容忍度：低。仓库既有纪律要求「未经验证的能力不得报告成功」，因此本评审把
**虚假成功**视为比功能缺失更严重的一类风险。
重点质量属性：正确性主张的可信度（honesty）、可测试性、可靠性（崩溃隔离）、
端到端可交付性、延迟/资源、法务（参考素材）。

## 证据基础

本次评审的结论只基于当场可复现的证据：

| 证据 | 内容 |
|---|---|
| `pytest tests/unit tests/integration`（带全部 GPU 环境变量） | **262 passed / 0 skipped**（F-04 闭环后的全量；含真实导出键形与像素形变判据） |
| `pytest tests/unit/` | 201 passed（含本轮新增的手性/UV/键形烘焙/就绪判定测试） |
| `pytest tests/integration/test_moc3_keyform_acceptance.py tests/integration/test_pipeline_moc3_export.py` | 12 passed，含 GPU 判据（真实导出画出 18769 像素、多 binding 形变、静态负对照为 0） |
|  conventions 修复前的全量运行 | 241 passed / 0 skipped（含全部 GPU 用例）——**注意**：这批绿色结果对应的是修复前的产物，其像素判据之所以能过，是因为合成几何恰好躲开了三个缺陷 |
| 修复后全量含 GPU 重跑 | 待补（本轮末次全量运行被中止，避免长时间开窗口；结论不依赖它） |
| `go test ./...` | handlers / services 全绿；`TestNativeExportLive2D`、`TestNativeDesktopHTTP` 实测通过 |
| `Work/moc3-keyform-verification.json` | 重新生成：驱动包 visible=24336 / changed=48672 / drift=0 / restore=0；静态负对照 changed=0 |
| `tools/probe_haru_keyforms.py` 输出 | 官方 Haru.moc3 全量结构核实（84 网格 / 47 带 / 42 绑定 / 126 键） |
| `tools/probe_haru_deformers.py` 输出 | 官方 97 个变形器结构核实（66 warp / 31 rotation / 383 warp 关键形） |
| `tools/check_go_export_path.py realistic` | 语义命名图层的真实导出：修复前 0 像素，修复后 135056 像素 |
| `git check-ignore -v Work/...` | 官方样例与生成本地包均被忽略，不会入库 |

## 发现（按严重度）

图例：**[已闭环]** 本轮已修复并有回归测试；**[已证实]** 有直接证据的现存风险；
**[假设]** 缺证据，附验证方法。

### F-03 变形器未编译：真实素材的导出只有静态几何 —— **warp 已修复**，遗留 rotation / 原为高

> 2026-09-21 更新：warp 变形器已编译进 moc3 并过官方内核 + 实渲染，根因与验收见
> **F-03b**。rotation 变形器仍不产出，所以下面「`runtime_ready=false`」这一状态
> 对含眼球跟踪的素材依然成立。

`live2d_builder/bones/deformers.py` 会按图层名（`hair_front`、`body`、`eye_left`…）
生成 warp/rotation 变形器。修复前 `moc3_model.py` 遇到变形器计数非零时抛
`UnsupportedRig`，`compile_export_moc3` 随即降级为静态编译并标注
`dropped=["deformers"]`、`runtime_ready=false`。

实测：以 `hair_front/body/eye_left` 命名的三层导出，得到 9600 字节、
**内核一致性通过**的 moc3，同时 `runtime_ready=false`。

- 可能性：必然（任何规范命名的素材都会触发）。
- 影响：这是产品主线缺口 —— 模型能加载、能显示，但头发摆动、身体摇晃、
  眼球跟踪都不会动。刚完成的参数形变键型只解决「网格自带逐键形状」，
  不解决「变形器网格驱动多个网格」。
- 置信度：高（直接观测）。
- 修复：编译 warp（控制网格 rows/cols + 键形）与 rotation（pivot/angle/scale/reflect）
  两类变形器，并把 `art_mesh.parent_deformer_indices` 指到真实索引。
- 验收：含变形器的包 `runtime_ready=true`，且设置 `ParamHairFront` 等参数时
  **跨多个网格**的像素发生变化，而关闭该参数时回到原帧（沿用现有负对照写法）。

### F-04 键型能力在产品路径上不可达 —— 已闭环 / 原为高

原先 `build_rig_spec()` 从不填 `keyform_shapes`，键型只有测试里手搓 `RigSpec` 才能用到。
现在管线**已有的**蒙皮权重（`compute_bone_weights`）与骨骼枢轴（新增
`builder_result["bone_positions"]`）被 `rotation_keyforms()` 烘焙成逐键形状：
线性蒙皮 `v' = v + w·(R(θ)·(v − pivot) − (v − pivot))`，键取 `min/default/max`，
default 键逐字复现静止姿态。

真实导出实测：受影响网格 `keyform_counts=[3,3]`、驱动参数 `ParamAngleZ`、
键 `[-30,0,30]`、内核一致性通过；在官方运行时里把 `ParamAngleZ` 从 −30 扫到 +30
使 **80345 个像素**变化、质心 (205,267)→(191,227)。两条守门测试：
`test_pipeline_bakes_keyforms_into_moc3`、
`test_pipeline_keyforms_deform_in_official_runtime`（GPU 开关）。

刻意保留的边界（不是遗漏）：X/Y 轴不生成（Live2D 里是位移 + 透视复合，属美术判断）；
一个网格同时命中多个旋转参数时保持静态（多参数带轴序未核实）——
这类网格会以 `log.warning` 报出，不会静默。

### F-05 视觉验收缺失，全绿不等于“看起来对” —— 已闭环（几何部分）/ 原为高

评审时只知道「未验证」。补上渲染判据之后，实测出**三个真实缺陷**，
每个都会让导出模型在官方运行时里**一个像素都画不出来**，而当时 213 项测试全绿：

| 缺陷 | 证据 | 状态 |
|---|---|---|
| 顶点写成画布像素，官方格式要单位坐标 | `tools/probe_ppu_convention.py`：绘制尺寸 = 像素 × 视口/画布 × ppu | 已修（`moc3_model` 写出处换算） |
| y 翻转反转三角形手性 → 背面剔除丢弃整批网格 | `tools/bisect_moc3_fields.py`：真实网格 0 像素，仅改手性 → 36087 像素 | 已修（`ensure_front_facing`） |
| 图集 UV 的 v 多翻一次 → 全部落在透明留白 | `tools/probe_uv_variant.py`：as_is 0 像素，去翻转 135056 像素 | 已修（`build_rig_spec`） |

修复后的产品路径实测：真实管线导出渲染出 bbox (12,63)-(379,429)、135056 不透明像素。

- 新增守门测试：`test_exported_package_actually_renders`（画出像素、落在视口内、居中）。
  它属于「导出包必须真的能被画出来」这一类判据，是之前整套验收里缺失的一层。
- **仍未闭环**：真实美术素材的观感签核（姿态、层间遮挡、表情像不像原画）。
  几何判据只能保证画得出来、位置尺度对，不能保证美术上正确。
- 参考模型对照：官方 Haru 在同一运行时下 bbox (92,31)-(307,473)、30652 像素，
  与我们的量级一致。

### F-14 验收判据存在结构性盲区 —— 已闭环 / 原为高

一致性、`LoadModelJson`、`GetParamIds` 三项都只证明**字节合法与可加载**，
对「画面上有没有东西」完全无感；而当时的像素验证只跑在手写的合成包上，
且其几何恰好躲开了上述缺陷（方块 + 全 0~1 UV + 手工 CCW）。
后果：三项最严重的缺陷可以长期躲在绿色测试后面。

- 修复方式：把「真实导出包必须画出像素」写成集成判据，并在断言消息里
  直接给出像素数与 bbox，避免再次退化为「只看退出码」。
- 附带修正一处自伤：`moc3_verify` 之前用 `text=True` 解码子进程输出，
  中文 Windows 的 locale 是 cp936，而 live2d-py 会输出 UTF-8 字节 ——
  解码失败会让 `stdout` 变成 `None`，整条验收链随机崩溃。
  现在统一 `encoding="utf-8", errors="replace"`，并有回归测试
  （`tests/unit/test_moc3_verify.py`）。

### F-07 每个参数只支持一个 binding —— 已闭环 / 原为中

`keyform_groups()` 按 (参数, 键值元组) 分组：同一参数的不同键布局各自成组，
`parameter.keyform_binding_{begin,counts}` 划出该参数名下连续的 binding。
官方内核已验收（一致性 + 加载），并有受控像素验证与「去掉键形必不通过」的负对照
（`test_moc3_keyform_acceptance.py::test_multi_binding_*`）。

### F-03a 变形器结构已取证 —— 已闭环（并入 F-03b）

`tools/probe_haru_deformers.py` 对官方 97 个变形器得到可直接实现编译的结论
（types 0/1 = warp/rotation、specific_indices 每类型稠密递增、
`art_mesh.parent_deformer_indices` 指向公共表且官方从不为 -1、
`rows/cols` 是格子数即顶点数 = (rows+1)(cols+1)、变形器可嵌套）。
当时唯一未定的「网格点坐标量级」正是 F-03b 的突破口，见下文。

### F-03b 变形器编译 —— **已修复**（warp）/ 遗留 rotation

曾经的状态：只要 deformer 表非空，官方内核一律拒绝，`RigSpec.validate()` 显式拒绝。
**真因有两条，都不在 deformer 表内部**：

1. **顶点空间搞反了。** 挂在 warp 下的网格，moc3 存的是**控制网格内的 `(s, t)`
   归一化坐标**，不是模型空间坐标（官方：warp 下 80 个网格顶点全在 ~[0,1]，
   rotation 下 4 个在像素模型空间）。我们存了模型空间坐标，内核把它当 (s,t) 用，
   于是画面被放大错位 —— 「恒等网格却仍然变形」就是这么来的。
   判据与复现：`tools/probe_warp_st_identity.py`（恒等必须与无变形器逐像素相同）。
2. **V3_03 的 `additional.quad_transforms` 不能为空。** deformer 表非空而该段为空时
   内核报 `Header section is invalid` —— 报错信息**完全不提变形器**，把整轮诊断
   引向了「deformer 表缺字段」。官方 Haru 是 **V3_00**（99 段、无该段），
   所以「与官方文件逐项差分」这条路永远看不到这条约束。
   复现与长度规则：`tools/probe_deformer_header_variants.py`、
   `tools/probe_additional_rule.py`（只需非空；编译器按每变形器一项写 0）。

诊断方法上的教训（值得保留）：**逐级生长法**（`tools/ladder_deformer_growth.py`）
比盲猜字段有效，但必须让每一级自身自洽 —— 它的 L1（有 warp 类型却没有 warp 子表）
先报了 `Data section is invalid`，掩盖了 L2 才出现的 `Header section is invalid`；
把「首个失败级别即结论」改成「每级独立重跑 + 打印内核原文」才暴露出两条不同的错误。

现在的验收状态：

| 判据 | 结果 | 证据 |
|---|---|---|
| 官方内核一致性（含 warp 表） | 通过 | `test_static_warp_is_accepted_by_official_core` |
| 官方内核加载 | 通过 | `test_warp_package_loads_in_official_core` |
| 恒等网格逐像素不变（非方形网格 + 非方形矩形，钉住点序与 y 朝向） | 通过 | `test_identity_warp_renders_exactly_like_no_warp` |
| 参数驱动真的移动画面（静止形 = 参数默认值那一形） | 通过 | `test_driven_warp_moves_the_mesh` |
| 负对照：additional 段清空必须被拒绝 | 通过 | `test_empty_additional_section_with_warp_is_rejected` |
| 真实导出（语义图层 → 2 个 warp 变形器）过内核且像素与无变形器相同 | 通过 | `tools/verify_deformer_export_pixels.py`：sha 相同、136530 不透明像素 |

新增 lint 规则：`_check_deformer_bands`（公共表带 == 子表带，官方 97/97）、
`_check_additional_section`（V3_03 + 非空变形器表 ⇒ 该段非空且与变形器数一致）。

### F-03c rotation 变形器编译 —— **已闭环**（2026-09-21）/ 原为 P0

`dropped: ["deformers"]` 曾经把**每一份真实导出**压在 `runtime_ready=false` 上
（语义分层必然产出 `EyeTrack_L/R` 这类 rotation 变形器）。现在它编译成
`RotationDeformerSpec`，四张表与官方 Haru 同构，并经逐像素验收。

单位与朝向全部由「**与预先转好的等价网格逐像素相同**」定死，不用 bbox/质心近似
（纹理遮罩与子像素覆盖会让几何量失真，我一开始就是这么被误导的）：

| 结论 | 判据 |
|---|---|
| `types`：0 warp / 1 rotation；子表按类型各自成数组，`specific_indices` 是类型内下标 | 官方 66+31 对账，`tools/probe_rotation_deformer.py` |
| `angles` 是**角度制**、正值 = 模型空间**逆时针** | ±30/±90/−120 与 CCW 预转参照 sha 相同 |
| `origin` 是**平移量**，不是枢轴：内核做 `p' = R·p + origin`，故绕枢轴 p 要写 `(I − R)·p` | 0°/30°/60°/−45° × 八候选，只有「原样平移」命中（`tools/probe_rotation_offset.py`） |
| `base_angles` 与键形角度**相加** | base=30/angle=0 与 angle=30 逐像素相同 |
| `scales` 1.0 恒等 / 2.0 翻倍 / 0 压没 / 负值=再转 180° | `tools/probe_rotation_pixels.py` |
| `reflect_*=1` 会让子树不画，官方全为 0 ⇒ 产物固定写 0 | 同上，语义未取证，不臆测 |
| 挂 rotation 的网格顶点仍是模型坐标（不像 warp 要换 `(s,t)`） | 恒等逐像素测试 |

**方法论收获（本轮最贵的一条）**：`I − R(A) = R(−A)` 只在 `cos A = 1/2`（A=60°）时成立。
最初只在 60° 上验证，就把「origin 写 R(−A)·p」当成了正确公式，30°/120° 立刻崩。
**单角度验证等价于没验证**；凡涉及角度/尺度换算，至少覆盖三个不成特殊角关系的角度。
新增 lint `_check_deformer_types`（类型计数与子表下界，官方 97/97 零误报）。

**仍遗留（不当作已落地）**：管线的 rotation 条目**没有参数绑定**，编出来是静态姿态
（单键形、角度 0）。它被单独记进 `deformers_static`，不会被 `deformers_compiled` 冒充成
「会动的变形器」。眼球跟随要真跟着参数转，还缺参数→变形器的绑定与幅度约定（美术判断）。

### F-09 `/api/export/live2d` 同步跑完整构建，超时预算未标定 —— **已标定并收口** / 低

本轮把 Go 桥接改为走 `Live2DBuilder.build()`（这是产出真实 moc3 的必要条件），
代价是该请求现在要跑完网格生成 + 烘焙 + 内核子进程验收。原先它吃的是
`config.GetPythonTimeout()` 的通用 120s，且超时被压成一句 `Python执行超时` → 500。

**实测预算依据**（`tools/measure_export_duration.py`，每种 2 次，官方内核验收含在内；
明细落盘 `Work/export-duration/export-duration.json`）：

| 图层 × 边长 | 总像素 | 中位耗时 | 最长 | 内核验收 |
|---|---|---|---|---|
| 3 × 256 | 0.2 Mpx | 0.61s | 0.65s | runtime_ready=true |
| 8 × 512 | 2.1 Mpx | 0.98s | 0.98s | 含 rotation 变形器 → false |
| 16 × 1024 | 16.8 Mpx | 4.39s | 4.41s | 同上 |
| 26 × 1024 | 27.3 Mpx | 6.62s | 6.64s | 同上，4 个 warp 编译 + 2 个 rotation 标注 |
| 12 × 2048 | 50.3 Mpx | 6.50s | 6.58s | 同上 |

结论：耗时随**总像素**增长，50 Mpx 规模 6.6s —— 距 120s 有 ~18 倍余量。

改动（按数据决定，不预先上异步）：

- `PythonConfig.ExportTimeoutSec`（默认 **150s**）+ `GetExportTimeout()`：导出走
  独立预算，并**刻意压在 `Server.WriteTimeout`（180s）之下** —— 超过它的话连接会
  先被 HTTP 服务器掐断，客户端拿到死连接而不是我们的错误。
- `services.ErrPythonTimeout` 哨兵错误 + `runInlinePythonTimeout`：超时与「Python
  真的报错」从此可区分；`/api/export/live2d` 的 JSON 与 zip 两条路径都回 **504**，
  并回传 `timeout_budget_seconds` 与 blocker。
- 测试：`export_timeout_test.go`（504/500 分流、预算随配置变化并被写超时压住）。

**为什么暂不做异步任务队列**：6.6s 的同步请求不值得引入 job store + 轮询 + 前端状态机。
触发重评的阈值：单次导出中位数接近预算的 1/3（≈50s），或并发导出成为常态
（那时先加并发闸，再谈任务化）。

### F-06 多参数带的 keyform 轴序未知 —— 已证实（缺口）/ 中

官方 Haru 中存在引用 2 个 binding 的带 15 条、3 个 binding 的带 5 条，
但「哪个参数轴对应 keyform 序号的步长」无法只靠结构读出。
当前 `moc3_model.py` 只编译单参数带，多参数输入直接报错而不是猜。

- 风险评估：这是**有意的保守**。若按直觉猜轴序，产出的 rig 会被内核接受但形状
  错乱 —— 属于最难发现的一类缺陷。
- 验证方法：对官方模型做受控实验（固定其他参数、扫单一参数、比对顶点位移），
  或用 py-moc3 读出多带网格并逐格比对位置。

### F-07 每个参数只支持一个 binding —— 已证实 / 中

同一参数驱动多个网格时要求键值集合完全一致，否则
`_validate_keyforms` 报「不一致」。官方数据里每参数恰好一个 binding，
但真实工程常有同参数不同关键帧布局。
修复：允许一个参数持有多个 binding（`parameter.keyform_binding_counts > 1`）。

### F-08 逆向格式随 Cubism 版本漂移 —— 已证实 / 中

section 布局由 py-moc3（MIT）转录，并经官方 Haru 差分核实；实测内核为
Cubism Native Core 05.01.0000。V3_03 起多出的 `additional.quad_transforms`
已有转录守卫测试，但未来版本新增段会使 SOT 槽位与计数假设失效。

- 缓解：`build_layout(version)` 已按版本分支；Haru 回归测试会在验收标准变化时变红。
- 建议：内核升级时把「跑官方回归 + Haru 重扫」列入 checklist；对未识别版本
  拒绝写出而不是尽力写出。

### F-10 关键形变测试默认跳过 —— 已证实 / 中

受控像素与桌面窗口用例需要 OpenGL，按仓库惯例用 `LIVE2D_TEST_PIXELS` /
`LIVE2D_TEST_MANIFEST` / `LIVE2D_TEST_ROOT` 显式开启；缺环境变量时静默 skip。
在没有 GPU runner 的 CI 上，形变回归（F-03/F-04/F-05 这一类）不会被发现。

- 修复：夜间带 GPU 的 job；或在评审 checklist 中要求本地带环境变量跑一次并
  落盘证据 JSON。

### F-01 / F-02 两处虚假成功路径已闭环 —— 已闭环 / 原为高

- **F-01**：Go 桥接此前直接调用 `Model3Exporter.export(builder_result={"meshes": {}})`，
  该路径根本不经过 moc3 编译，导出包里从来没有 `.moc3`。现在改走完整构建，
  并由 `TestNativeExportLive2D` 断言磁盘上真实存在 `MOC3` 头、体积 > 1984 字节、
  且 `official_core_consistent=true`。
- **F-02**：`api_server.runtime_readiness()` 此前以「model3.json 引用的 moc3 文件是否存在」
  判定 `runtime_ready`，被内核拒绝的产物同样会被报成开箱即用。现在唯一判据是
  构建期内核验收结论；回归测试
  `tests/unit/test_runtime_readiness.py::test_written_but_rejected_moc3_is_not_ready`
  专门构造「文件在磁盘上但内核未通过」的场景。
- 同时 Go 侧 `exportResponseData()` 对 `runtime_ready` 只做严格布尔判定，缺字段
  一律 false 且必须给出 `blocker`；导出未产模型时返回 422 而不是 200。

### F-11 变更未提交，且仓库有 367MB 机器产物 —— 已证实 / 低到中

`git status` 报 dubious ownership（需用户自行
`git config --global --add safe.directory E:/Live2D/beacon`，不得由工具改配置），
因此本轮全部工作仍未纳入版本控制，存在丢失风险。

一次 `git add -A --dry-run` 的实测结果（本轮已按结果补忽略）：

- `.venv-runtime/`（失效虚拟环境 143MB）与 `.go-cache/`（Go 模块缓存 224MB）
  此前会被直接吞入 —— 已加进 `.gitignore`。
- `.autocase/`（1 个审计基线文件）与 `.superpowers/`（12 个 brainstorm 中间产物）
  同样会被吞入 —— 已加进 `.gitignore`；提炼后的正式版本在 `docs/superpowers/`。
- `.venv/`（513MB）与 `.pytest_cache/` **不需要**处理：两者自带 `*` 的嵌套
  `.gitignore`，dry-run 实测 0 个文件入库。（我最初以为它们也是暴露面，
  是 dry-run 证伪了这个猜测。）
- 补完之后 `git add -A` 共 81 项，全部是源码/测试/文档，最大单文件 28KB，
  无 `Work/`、无官方样例、无 `.env`、无二进制。

需要注意：`git add -A` 同时会记录**删除**两个仍在 HEAD 里、磁盘上已不存在的
备份文件（`api_server.py.beacon.bak`、`api/handlers/handlers_test.go.beacon.bak`）。
这不是本轮改动，提交前请确认这个删除是你想要的。

### F-12 崩溃隔离做得扎实 —— 正面发现

所有 Cubism Core 调用都在独立子进程内（`moc3_consistency` / `moc3_load` /
`verify`），`moc3_verify` 契约是「永不抛出」，超时与崩溃都归类为 `ok=false` +
`blocker`。历史上内核既会 0xC0000005 也会 0xC0000409，这一层隔离是链路可用前提。
另：`Work/` 已忽略，官方样例不会被分发。

### F-15 motion3.json 曾被内核静默忽略 —— **已修复** / 原为 P0

现象：生成的 motion 文件被 model3.json 正常引用、包能加载、参数存在，但参数
一动不动。定位到两条互相独立的根因，都在**我们的文件**一侧：

1. **段布局错**。真实布局是 `[t0, v0]` 初始点，其后**每段**先写段类型再写点：
   `0 线性(1 点) / 1 贝塞尔(3 点：两控制点 + 终点) / 2 阶跃(1 点) / 3 逆阶跃(1 点)`。
   生成器原先写的是裸 `[t, v, t, v, ...]`，内核把第一个时间值当成段类型读。
   注意 **Live2D 文档写的 1/2/3/4 是错的**：文件里的类型就是 Cubism 的 C++ 枚举
   （Linear=0/Bezier=1/Stepped=2/InverseStepped=3）。定案依据：官方 Haru 六个动作
   文件按此假设解码后，`TotalPointCount`/`TotalSegmentCount` 与 Meta **6/6 逐项相等**，
   且恒等式 `floats = 2P + S` 成立；Part 曲线全部是 Stepped(2)，每条曲线结尾恰好
   一个 Linear(0) 到 (Duration, v)。
2. **写法错**。同一份内容：tab 缩进与 2 空格缩进能被加载并真的驱动参数；
   **紧凑单行一律 `Load extra motion failed`**（无论逗号后是否带空格、无论是否
   每元素换行 —— 只有「数组元素各占一行且行首有缩进」这一种形态可行）。
   官方 motion 文件本身就是 tab 缩进的。机制未证实（改的是它的二进制），
   产物侧只能照官方写法。

**上一轮「必须写紧凑，美化缩进会段错误」的结论是错的**：那是旧编码的副作用 ——
错误的段类型让内核越界崩溃，而被误导成「缩进的锅」。教训：**在两个变量都没隔离
时得到的判据，会在下一个变量上反噬**。

现在的判据（不接受「画面好像动了」）：
`drivers/live2d_runtime/moc3_motion_probe.py` 逐帧把内核读到的参数值与文件曲线
对账，只允许一帧内的时间对齐偏差，并按 model3.json 的 FadeInTime/FadeOutTime
跳过淡入淡出窗口；`compile_export_moc3` 在产物声明了 motion 时必须拿到
`motion_verified=True` 才算 `runtime_ready`，播放失败直接给 blocker。
实测产物：`observed_range=60.0`（键形全行程）、`max_abs_error=0.0`。
无 motion 的产物 `motion_verified=None`（不适用，不等于通过）。
新增一次内核子进程调用，导出总耗时 0.75s → 1.35s，远在 150s 预算内。
复现脚本：`tools/bisect_motion_encoding.py`、`tools/diag_extra_motion.py`、
`tools/diag_motion_start.py`、`tools/probe_motion_segment_layout.py`。

### F-16 physics3 曾被内核静默忽略 —— **已修复并验收**（2026-09-21）/ 原为 P1

判据升级为「输入阶跃后，输出必须**动起来 + 有滞后/过冲 + 能回摆**」
（`drivers/live2d_runtime/moc3_physics_probe.py`）。四条偏差同时存在过，
每一条都能让物理**看起来齐全而实际一动不动**：输出的 `Type` 写成 `"X"`（必须是
`"Angle"`）、`Weight` 用了 0-1 制（Cubism 是 0-100）、根顶点 `Radius=50`（必须是 0）、
`Delay` 全 0（等于逐帧复制输入，不是物理）。内部那套
`length/damping/stiffness/mass` 摆模型在 physics3 里没有对应字段，连 `Pendulum` 块
都是不合 schema 的 —— 已映射到官方的逐顶点字段并删掉该块。

结构性发现：**物理参数不得成环**。官方 Haru 中「既被输出又被别组读取」的参数是 **0 个**，
我们却有两处环，实测这些链被内核整个丢弃。产品侧新增
`prune_physics_to_parameters()`：剪掉引用未声明参数的链，剪空则连文件都不写。

真实导出验收：6/6 条链 moved+lagged+settled（发摆 peak 1.47、身摆 0.73、呼吸→肩 0.12），
`physics_verified=True` 且成为 `runtime_ready` 的必要条件之一；导出总耗时 0.75s → 2.37s
（三次内核子进程调用），仍在 150s 预算的 60 倍余量内。
回归：`test_physics3_setting_matches_cubism_shape`、`test_physics_parameters_do_not_form_a_cycle`、
`test_prune_*`、`test_dead_physics_blocks_runtime_ready`。

## 领域/生命周期视角（本仓库是业务工作流，不只是框架）

- 领域实体：Layer → MeshSpec/KeyformShape → RigSpec → Moc3Container → 模型包
  （model3.json + moc3 + 图集）→ build_meta → API 响应。
- 生命周期状态：`generated` → `compiled` → `core_accepted`（一致性）→
  `runtime_verified`（受控像素）→ `motion_verified`（内核真的在播曲线）→ `deployed`。
  当前 `compiled/core_accepted`、`runtime_verified`、`motion_verified` 与
  **`deformed` 的 warp 部分**都已有测试覆盖；仍缺 rotation 变形器（F-03c）
  与「物理真的产生运动」（F-16）。
- 关键不变量已被 lint 覆盖：带索引不得为 -1、keyform 数 == 键数之积（现含
  warp/rotation 变形器持有者，官方 Haru 66+31 个变形器 / 636 个关键形零误报）、
  绑定链各层总数互相铺平、段长度 == count info、变形器公共带 == 子表带、
  V3_03 下 additional 段非空。
  未覆盖：glue 持有者。

## 修复优先级

| 优先级 | 事项 | 依据 |
|---|---|---|
| P1 | rotation 变形器的**参数绑定**（眼球跟随真的动） | 结构与单位已定论（F-03c），缺的是「哪个参数、多大角度」的美术约定；当前产物只有静态姿态，记在 `deformers_static` |
| P1 | F-06 多参数带轴序实测定论 | 让跨参数网格可用且不靠猜 |
| P1 | F-17 分割质量（SAM2 + GroundingDINO 接入） | 分层决定网格决定成品；torch 已装并验通，后端待接 |
| P2 | F-05 剩余部分：真实素材观感签核 | 几何判据已闭环，观感仍需人 |
| P2 | F-10 带 GPU 的夜间回归、F-11 提交与环境卫生 | 防回归与防丢失 |

本轮已闭环：F-01、F-02、F-03b（warp 变形器）、F-03c（rotation 变形器）、F-04（管线键形）、
F-05（几何三项）、F-07、F-09（导出预算标定 + 504 分流）、F-14、F-15（motion3.json）、
**F-16（physics3 真实运动）**。真实导出第一次同时拿到
`runtime_ready=true` + `official_core_consistent=true` + `motion_verified=true` +
`physics_verified=true`（导出耗时 0.75s → 2.37s，仍在 150s 预算的 1/60）。

**本轮的方法论收获**（写给下一次评审）：
1. 判据必须包含「产物能不能被画出来」这一层。「字节合法 + 可加载 + 参数表正确」三项全绿时，
   导出模型仍然可能整屏空白；而只用手工合成的几何去跑像素判据，又会恰好绕过真实三角化
   才会踩到的坑（手性、图集子矩形）。判据要跑在**真实管线产物**上。
2. 内核的错误文案会**指错方向**（`Header section is invalid` 其实是 additional 段 +
   变形器的联动）。所以「每级独立重跑 + 打印内核原文」优于「首个失败即结论」。
3. 与官方样例逐项差分有**版本盲区**：官方 Haru 是 V3_00，凡是只在 V3_03+ 才出现的
   段（additional）永远差不出来 —— 这类约束只能靠受控实验。
4. 未定量的预算不要靠猜：`tools/measure_export_duration.py` 用 0.2~50 Mpx 五档实测
   替代了「120s 够不够」的争论。

## 下一步核查清单

1. 做 F-06 的轴序受控实验（扫单一参数、比对顶点位移），把结论写进
   `docs/native-runtime.md` 并同步 `moc3_lint` 规则。
2. rotation 变形器：先受控实测 `rotation_deformer.base_angles` 与
   `rotation_deformer_keyform.{angles,origin_xs,origin_ys,scales,reflect_*}` 的单位与
   朝向（同样用「恒等必须逐像素不变」当判据），再接进编译器。
3. 视觉验收签核（F-05）：截图基线入库，记录签核人与日期。
4. 多变形器 + 变形器嵌套（`parent_deformer_indices`）的实渲染回归 —— 目前验收只覆盖
   单层单个 warp。

## 外部实现对账（oracle reconciliation，2026-09-22）

目标：用**独立实现**为我们的 moc3 理解做第三方交叉验证，降低"只有我们自己这么认为"的风险。

方法：**源码级对账**（只读作对照、**不复制任何代码**）。因本机无 Rust/.NET 工具链，
**运行时**对账待环境就绪后执行。

对照对象：`AyagamiDev/ayagami`（Rust，**Apache-2.0 / MIT 双许可**），其
`ayagami/src/file/parse.rs` 是一份**独立实现**的 moc3 读取器。

| 我们的结论 | 独立实现的做法 | 对账 |
|---|---|---|
| 头部 `MOC3` + version u32；section 偏移表起于 `0x40`(64) | `FILE_MAGIC=b"MOC3"`、version@0x04、`advance_to(0x40)` 后读 offsets | ✅ 一致 |
| 段间填充必须为 0 | `advance_to()` 逐字节校验，非零即 `InvalidPadding`（且单次 skip ≤ 0x2000） | ✅ 一致 |
| canvas 段 = scale / center / width / height | 第二段顺序相同 | ✅ 一致 |
| 变形器 = 公共表 + 每类型稠密子索引 + `parent_deformer_indices` 指公共表（F-03a/b） | `TypedDeformerView::Warp/Rotation(t)`，`t.idx` 为**类型内**索引；`find_refs()` 反填公共表 | ✅ **独立佐证** |
| lint 需穷尽引用/边界校验 | `validate_ref` / `validate_opt_ref` / `validate_arrayref` + 重复引用检测 | ✅ 同构 |

**新线索（未定论，禁止据此改导出）**：ayagami 显式枚举版本演进
`V3_3 → V4_0 → V4_2 → V5_0 → V5_3`，其中 V3_3 追加 `warp_deformer.bilinear_interpolation`、
V4_2/V5_0 追加颜色/混合形、V5_3 追加 `offscreen_part` 与 `blend_config`。这与 **F-08
（逆向格式随 Cubism 版本漂移）** 同源，并提示我们遇到的 `additional.quad_transforms`
可能属于同一套"按版本追加段"机制 —— 需受控实验确认，**不作为结论**。

### oracle 许可登记（准入表）

| 候选 | 许可（实测） | 准入结论 |
|---|---|---|
| `AyagamiDev/ayagami` | **Apache-2.0** | 源码对照已用；如需复用须保留 NOTICE |
| `EasyLive2D/relive2d`（= `live2d-py` 所属组织） | **MIT** | 准用（仅隔离验证路径） |
| `py-moc3` | **MIT** | 准用（结构转录 / 交叉校验） |
| `shitagaki-lab/see-through` | **Apache-2.0** | 准用（与本项目同协议） |
| `UlyssesWu/D2Evil` | **NOASSERTION / Other** | **禁用**，直至对方澄清许可 |
| `tsunehimatoi/psd2live` | **GPL-3.0** | 仅借思想，禁用代码 |
| `CheshireMew/PuppetLoom` | **AGPL-3.0** | 仅借思想，禁用代码 |

### 本地跑验收的 runbook（工具链就绪后）

前置：Python 用本项目目标版本（CI 为 3.10–3.12）；Rust 可选（仅供把 ayagami 当**可执行** oracle）。

```bash
pip install -r requirements.txt            # 含 py-moc3(MIT) 与 live2d-py(仅隔离验证)
pip install pytest pytest-asyncio

# 1) 结构对账（不需要 GL）
python -m pytest tests/unit/test_moc3_sections.py tests/unit/test_moc3_container.py -q
python -m pytest tests/integration/test_moc3_official_acceptance.py -q

# 2) 逐像素形变/播放验收（需要可用 OpenGL；Linux 下用 xvfb-run）
LIVE2D_TEST_PIXELS=1 python -m pytest tests/integration -q
```

等价的 CI 入口：`.github/workflows/ci.yml` 的 **`moc3-acceptance`** job
（手动 `workflow_dispatch` 触发；装 `live2d-py` + `xvfb`，并设 `LIVE2D_TEST_PIXELS=1`）。

## 相关产物

- 证据：`Work/moc3-keyform-verification.json`、`Work/moc3-p0-verification.json`
  （`Work/` 不入库）
- 风险网络：`docs/reviews/2026-09-20-moc3-risk-map.dot`
  （本机未安装 graphviz，`dot` 渲染未验证，仅作为文本化的风险关系图）
- 复核脚本：`tools/probe_haru_keyforms.py`、`tools/verify_keyform_evidence.py`、
  `tools/check_go_export_path.py`
- 现状文档：`docs/native-runtime.md`
