# 原生 Cubism 运行入口

本轮新增独立原生预览，不替换透明桌宠的软件窗口。原桌宠需要 Pillow 图像，而 Cubism 需要活动 OpenGL 上下文；直接替换类名会破坏现有窗口。

## 依赖与启动

在带有 `live2d-py`、`pygame` 和 `PyOpenGL` 的 Python 环境中，从项目根目录运行：

```powershell
python -m drivers.live2d_runtime "模型文件.model3.json"
python -m drivers.live2d_runtime "模型文件.model3.json" --frames 120 --parameter ParamAngleX=20
```

参数选项可重复使用。参数必须真实存在，数值必须有限，并会按模型定义限幅；写入后从原生模型读取校验。按 Escape 或关闭窗口退出。

## 已实现

- 加载前校验清单、moc3 文件头以及所有资源引用，禁止绝对路径和目录越界。
- 使用原生 Core 检查 moc3 一致性，再加载、调整画布、更新与绘制。
- SDK 和 OpenGL 生命周期由独立预览入口管理。
- 加载失败、重新加载失败、绘制失败均撤销当前成功状态。
- 原生参数读回和绘制次数单独报告，不将它们等同于视觉验收。
- 导出会生成 idle 动作（`live2d_builder/motion/motion3.py`），并由官方内核逐帧
  对账参数值后才记 `motion_verified`；写法约束见下文 motion3.json 定论一节。

## 验收界限

`runtime_verified` 当前始终为 false。绘制调用成功只标记“需要视觉验收”，不自动修改 API 的部署状态。

仍未完成：

1. 使用真实导出模型检验窗口中的有效像素与实际参数形变。
2. 将原生 OpenGL 渲染整合到透明桌宠窗口。
3. 将经验证的运行结果接入服务端部署流程。
4. 从素材自动建立 Cubism 工程并导出真实 moc3。
   → P0/P1 已完成：`live2d_builder/exporter/moc3_model.py` + `moc3_pipeline.py`
     把管线绑定数据编译为 moc3，pipeline 第 9b 步在导出时自动执行，
     通过官方 Cubism Core 一致性验收才落盘；`build_meta.json` 的
     `moc3.runtime_ready` 如实反映状态（含未编译特性时为 false 并写明 blocker）。
     官方内核已验证：一致性 + LoadModelJson + GetParamIds，证据见
     `Work/moc3-p0-verification.json`。
   → 参数形变键型已完成：`MeshSpec.keyform_shapes` 编译为
     keyform_binding 链，官方内核验收 + 受控像素验证证明参数真的驱动画面
     （`Work/moc3-keyform-verification.json`，含「无键形必不通过」的负对照）。
   → 管线键形来源已完成（F-04）：`moc3_pipeline.rotation_keyforms` 用管线**已有的**
     蒙皮权重 + 骨骼枢轴，把 Z 轴刚体旋转烘焙成逐键形状，`build_rig_spec` 自动接入。
     实测真实导出：受 Head/Hair 旋转影响的网格 `keyform_counts=[3,3]`、
     驱动参数 `ParamAngleZ`（键 -30/0/30，default 键逐字复现静止姿态），
     内核一致性通过，且在官方运行时里把参数从 -30 扫到 +30 会使
     **80345 个像素**变化、质心从 (205,267) 移到 (191,227)。
     守门测试：`tests/integration/test_pipeline_moc3_export.py::test_pipeline_bakes_keyforms_into_moc3`
     与 `::test_pipeline_keyforms_deform_in_official_runtime`（后者需 GPU 开关）。
     刻意不做：X/Y 轴（在 Live2D 里是位移 + 透视的复合，属美术判断，不臆造）；
     同一网格命中多个旋转参数时保持静态（多参数带轴序未核实）。
   → API 字段已完成：Go `/api/export/live2d` 改走完整构建路径（早期版本直接
     调 `Model3Exporter` 且不传网格，产物里根本没有 moc3），响应平铺
     `runtime_ready` / `blocker` / `moc3`；Python `api_server.runtime_readiness`
     改以官方内核验收为准，不再以「磁盘上有 .moc3」判定就绪。
     导出另有**独立且已标定的预算** `python.export_timeout_sec`（默认 150s，压在
     `server.write_timeout` 180s 之下）；超时回 **504** + `timeout_budget_seconds`，
     不再与「构建失败」共用 500。实测规模数据见
     `tools/measure_export_duration.py`（50 百万像素 6.6s）。
   → **warp 变形器已编译并过官方内核 + 实渲染**（见下节「变形器」）；
     仍缺 **rotation 变形器**：含这类变形器的导出仍标 `dropped: ["deformers"]` +
     `runtime_ready: false`，并在 `deformers_compiled` / `deformers_uncompiled`
     里写明编了哪些、漏了哪些；桌面窗口的观感签核；编辑器 UI。

### moc3 结构的实测定论（对官方 Haru.moc3 全量核实）

探针脚本 `tools/probe_haru_keyforms.py` 可复现以下结论：

- `binding i` 与 `parameter i` 一一对应；对象不被参数驱动时引用 band 0（空带），
  官方数据从不使用 -1 带索引。
- 对象的 `keyform_counts` == 其带内各 binding 键数之积（84/84 网格吻合）。
  违反该式仍会被内核接受，但参数不会生效，因此由 `moc3_lint` 拦下。
- `keyform_position_begin_indices` 全部是 16 个 f32（64 字节）的倍数；
  内核不强制对齐，但编译器按官方对齐方式产出。
- `art_mesh.position_index_counts` = 唯一顶点数、`vertex_counts` = 索引条目数，
  与字面直觉相反。
- 官方存在引用 2-3 个 binding 的带（多参数网格），其 keyform 轴序尚未核实，
  因此编译器目前只支持单参数带，多参数输入直接报错而不是猜。
- 同一参数可以有多个 binding（不同网格用不同键值布局）：`parameter.keyform_binding_
  {begin,counts}` 划出该参数名下的连续 binding，官方内核已验收
  （`test_multi_binding_rig_*`）。

### 变形器（warp 与 rotation 均已编译并实渲染验收；rotation 目前是静态姿态）

`tools/probe_haru_deformers.py` 对官方 Haru 的 97 个变形器实测得到：

- `deformer.types`：0 = warp（66 个）、1 = rotation（31 个）；
  `deformer.specific_indices` 在各自类型内是从 0 递增的稠密编号。
- `art_mesh.parent_deformer_indices` 引用的是**公共 deformer 表**的索引
  （不是 warp 子表）；官方 84 个网格**全部**引用真实变形器，无 -1。
- `warp_deformer.rows/cols` 是**格子数**，顶点数 = `(rows+1)×(cols+1)`
  （`vertex_counts` 与此一致，官方只有 5×5 与 6×6 两种）；
  `warp_deformer_keyform` 与网格关键形共用 `keyform_position.xys` 池，
  起点按 16 个 f32 对齐。
- 公共表的绑定带与其子表**始终一致**（官方 97/97），已写成 lint 规则。
- 变形器可嵌套：`deformer.parent_deformer_indices` 链到父变形器，
  `-1` 表示根；`deformer.parent_part_indices` 挂到部件。

**两条此前把整条路堵死的约定（都是实测定出来的，不是猜的）**：

1. **挂在 warp 下的网格，顶点存的是网格内的 `(s, t)`，不是模型空间坐标。**
   官方证据：warp 下 80 个网格的顶点全在 ~[0,1]（越界点略超出，最大 1.35），
   而 rotation 下 4 个网格的顶点在像素模型空间（x [-230,233]）；warp 控制网格
   自身与 rotation 的原点同在像素模型空间。内核做的是「(s,t) 经控制网格双线性
   映回模型空间」。判据：静止形网格 + 按该网格换算的 `(s,t)` 必须与「没有变形器」
   **逐像素相同** —— `tools/probe_warp_st_identity.py`、
   `tests/integration/test_moc3_deformer_acceptance.py` 都钉住了这一点
   （用非方形网格 + 非方形矩形，才能同时暴露行列序与 y 朝向）。
2. **V3_03 起，deformer 表非空时 `additional.quad_transforms` 必须非空。**
   该段为空时内核报的是 `Header section is invalid` —— 完全不提变形器，
   所以此前所有诊断都被引向「deformer 表内部缺字段」。官方 Haru 是 **V3_00**
   （99 段、没有该段），因此从官方文件差分永远看不到这条约束。
   复现与长度规则实测：`tools/probe_deformer_header_variants.py`、
   `tools/probe_additional_rule.py`（只需非空，不要求每变形器一项；编译器按
   「每变形器一项、值为 0 = 网格形变」写出）。

另外：**静止形 = 参数默认值对应的那一形控制网格**（`_rig((-30, 0, 30))` 在
param=0 时与无变形器逐像素相同，param=+30 时整体平移 —— 见验收测试）。

真实导出侧的双向验证：`tools/verify_deformer_export_pixels.py` 用同一批语义图层
各建一次（挂 warp / 不挂变形器），两份产物都过官方一致性，且渲染像素
`sha256` 完全相同（136530 不透明像素，bbox [12,63]-[381,431]）。

**仍未完成**：rotation 变形器**已能编译**，但管线给的条目没有参数绑定，所以编出来的是
**静态姿态**（单键形、角度 0），记在 `deformers_static` 里而不是 `deformers_compiled`
当成「已会动」。眼球跟随要真的跟着参数转，还需要参数→变形器的绑定与幅度约定（美术判断）。

### rotation 变形器的实测定论（官方 Haru + 逐像素对照）

编译入口 `RotationDeformerSpec` / `RotationKeyform`，判据一律是
**「与预先转好的等价网格逐像素相同」**（`tests/integration/test_moc3_rotation_acceptance.py`），
不用 bbox/质心近似（纹理遮罩与子像素覆盖会让几何量失真）。

| 项 | 结论 | 证据 |
|---|---|---|
| `deformer.types` | 0 = warp，1 = rotation（官方 66 warp + 31 rotation）；`specific_indices` 是**各自类型内**的下标 | `tools/probe_rotation_deformer.py` |
| 子表 | `warp_deformer.*` 与 `rotation_deformer.*` 是**两条独立数组**，必须按类型分别铺 | 同上 |
| `angles` 单位 | **角度制**（官方取值 −205.4..205.4，常见 179.1/180/181） | 同上 |
| 正方向 | **模型空间（y 向上）逆时针**；±30/±90/−120 四个角度各与「按 CCW 预转好的参照」sha 相同 | 验收测试参数化 4 例 |
| `origin` 语义 | **是平移量不是枢轴**：内核做 `p' = R·p + origin`。要绕枢轴 p 必须写 `(I − R)·p`；0°/30°/60°/−45° 四角度 × 八候选里只有「原样平移」命中 | `tools/probe_rotation_offset.py` |
| `base_angles` | 与键形角度**相加**成总角度（base=30/angle=0 与 angle=30 逐像素相同） | 验收测试 |
| `scales` | 1.0 = 恒等；2.0 两轴各翻倍；0 压没；负值等价再转 180° | `tools/probe_rotation_pixels.py` |
| `reflect_xs/reflect_ys` | 中性值下设 1 整棵子树**不画**；官方全为 0 ⇒ 产物固定写 0，语义未取证 | 同上 |
| 顶点空间 | 挂 rotation 的网格顶点**仍是模型坐标**（÷ppu 存储），不像 warp 要换成 `(s,t)` | 恒等测试 + 官方差分 |

踩过的坑，记下来防复发：`I − R(A) = R(−A)` **只在 cos A = 1/2（A=60°）时成立**。
最初只在 60° 上验证，于是把「origin 写 R(−A)·p」当成了正确公式 —— 30°/120° 立刻不命中。
**单角度验证等价于没验证**，凡涉及角度/尺度的换算，至少要覆盖三个彼此不成特殊角关系的角度。


### motion3.json 的实测定论（对官方 Haru 六个动作文件全量对账）

1. **段布局**：`Segments = [t0, v0]` 初始点，其后每段是「段类型 + 该段的点」。
   类型取值就是 Cubism C++ 枚举本身 —— **0 线性(1 点) / 1 贝塞尔(3 点：控制点1、
   控制点2、终点) / 2 阶跃(1 点) / 3 逆阶跃(1 点)**。
   Live2D 文档里写的「1 线性 / 2 贝塞尔 / 3 保持 / 4 反向保持」**偏了 1**，照它写
   出来的文件内核不认。定案依据：按上述点数假设解码官方六个文件，
   `TotalPointCount`/`TotalSegmentCount` 与各自 Meta **6/6 逐项相等**，
   恒等式 `floats = 2P + S` 成立；Part 曲线清一色 Stepped(2)；每条曲线结尾恰好
   一个 Linear(0) 段指向 `(Duration, 末值)`（Haru idle 里 63 条曲线 = 63 个 0 型段）。
2. **必须带缩进写，不能压成单行。** 同一份内容：tab 缩进 / 2 空格缩进 → 内核加载并
   真的驱动参数；紧凑单行（逗号后加不加空格、每元素是否换行都试过）→
   `Load extra motion failed`，参数一动不动。官方 motion 文件本身是 tab 缩进的。
   机制未证实（`live2d-py` 是二进制绑定），产物侧照官方写法。
   > 更正：本节此前记录的是「必须写紧凑单行，美化缩进会让 LoadModelJson 段错误」。
   > 那是**旧编码错误的副作用**——裸 `[t,v,t,v]` 让内核把时间值当段类型读从而越界崩溃，
   > 崩溃被误记在缩进头上。两个变量没隔离时得到的判据会在下一个变量上反噬。
3. **验收判据**：`drivers/live2d_runtime/moc3_motion_probe.py` 逐帧比对
   「内核读到的参数值」与「文件曲线在该时刻的取值」，只允许一帧内的时间对齐偏差，
   并按 `FadeInTime`/`FadeOutTime` 跳过淡入淡出窗口（淡入期是混合姿态，逐帧对账无意义）。
   `compile_export_moc3` 只要产物声明了 motion 就必须拿到 `motion_verified=True`
   才算 `runtime_ready`；没有 motion 的产物记 `None`（不适用，不等于通过）。
   复现：`tools/bisect_motion_encoding.py`、`tools/diag_extra_motion.py`。

### physics3 的实测定论（官方内核逐帧验证）

判据不是「文件合 schema / 能被加载 / 参数表正确」，而是**输入阶跃后输出必须
(a) 动起来、(b) 带滞后或过冲、(c) 输入回零后能回摆**；三条缺一不可，
由 `drivers/live2d_runtime/moc3_physics_probe.py` 在内核里实测。
四条曾经同时成立的致命偏差：

1. **输出 `Type` 写成 `"X"`** —— 参数输出必须是 `"Angle"`。写成 X 时内核**静默丢弃整条链**，
   响应恒为 0（同法在官方 Haru 上 14 条链全部正常，排除运行时因素）。
2. **`Weight` 混用了 0-1 制** —— Cubism 是 **0-100** 制，写 1.0 等于 1% 影响力。
3. **根顶点 `Radius` 给了 50** —— 根是锚点，官方是 0；子顶点的 `Radius` 等于它到父顶点的距离。
4. **`Delay` 全为 0** —— Delay=0 时输出逐帧等于输入，数值上「动了」但不是物理。
   内部那套 `length/damping/stiffness/mass` 摆模型在 physics3 里**根本不存在**
   （还附带一个非 schema 的 `Pendulum` 块，已删）；映射关系：
   `Delay<-damping`、`Mobility<-1-stiffness`、`Acceleration<-1+mass/2`、
   `Radius/Y <- 累计 length`。

还有一条结构性约束：**物理参数不得成环**。官方 Haru 里「既被某组输出、又被另一组读取」
的参数数量是 **0**；我们曾写出 `ParamAngleY → ParamBreath →（Breathing 组读 ParamBreath）`
与 `Breathing 写 ParamBodyAngleY →（BodyBounce 读它）`，实测这些链被内核整个丢掉（peak 恒 0）。

产品侧的兜底：`prune_physics_to_parameters()` 在写出前剪掉任一端未在本模型声明的参数链
（这类链内核同样静默丢弃），剪空则**连 physics3.json 都不写**、清单里 `Physics` 留空 ——
不为「文件看起来齐全」保留死链。校验器允许空引用，但绝对路径/越界照旧拒绝。

导出验收：`compile_export_moc3` 只要清单声明了可用物理链，就必须拿到
`physics_verified=True` 才算 `runtime_ready`；没有可验链时记 `None`（不适用≠通过）。
真实导出实测（语义图层预设）：6 条链全部 moved+lagged+settled，
发摆峰值 1.47、身摆 0.73、呼吸→双肩 0.12。

### SAM2 + GroundingDINO 分层后端的实测定论（本机 GPU）

`core/segment_engine/sam2_gd.py`，选择方式 `WorkflowEngine(segmentation_backend="sam2_gd")`。
复核入口 `tools/verify_sam2_gd.py`（带**覆盖率门槛**：跑通 ≠ 分得出来）。

实测（RTX 2060 6GB，`docs/assets/demo_input.png` 600×900）：`method=sam2_groundingdino`、
`facebook/sam2-hiera-small` + `IDEA-Research/grounding-dino-tiny`、cuda、分段 8.0s、
模型加载 17.6s、显存 0.92GB 实占 / 3.07GB 预留；**15 个部件里 14 个出真掩码**，
平均覆盖率 0.0277。

四条只能靠跑才知道的事实：

1. **transformers 5.17 的 SAM2 图提示路径是** `Sam2Processor(images, input_boxes=[[[x0,y0,x1,y1],…]])`
   → `get_image_embeddings()` → `forward(input_boxes=, image_embeddings=, multimask_output=False)`
   → `post_process_masks()`；一次视觉编码可复用给全部框。`input_boxes` 必须是**嵌套 list**，
   传 tuple / ndarray 会被 `ProcessorMixin._validate_fully_nested` 拒掉。
2. **单 token 提示返回 0 框**：`"face"` → 0 检出，`"face . character face"` → 0.72。
   因此每个提示词都至少给两个同义形式。
3. **GroundingDINO 会吐整幅尺寸的兜底大框**（实测眉毛 0.26 分时框住 70% 画面），
   必须按面积上限丢弃，否则每个部件都变成"近乎整图"的假掩码。
4. **开放词汇会认错位置**：`"mouth . lips"` 把分数最高的两个框落在角色**膝盖**上。
   处理方式不是加颜色启发式，而是用检测器自己给出的脸部包络做门控 —— 越界就判该部件
   **缺失**（`missing_parts`，不落 PNG），而不是产出一张错掩码。

诚实性接线：点名后端没产出部件而回落 K-means 时，`result["steps"]["layering"]` 会写
`{requested_backend, used_backend, degraded, reason, missing_parts}`（`auto` 的常规回落
不算降级）；权重/显存不可用时抛 `ModelUnavailable`，由 workflow 以 error 收尾。
测试：`tests/unit/test_workflow_segmentation_backend.py`。

**未验证**：SAM v1 替代路径（`method="sam1_groundingdino"`，SAM2 权重从未加载失败过，
该分支一次都没跑过）；`sam2-hiera-tiny`；冷缓存下载；CPU 设备。

### 坐标系与渲染映射（已实测确定）

moc3 里**顶点以单位坐标存储**：`单位 = 画布像素 / pixels_per_unit`，相对画布原点；
`canvasInfo` 各字段仍是像素（与官方 Haru 一致：2400×4500、ppu 2400、origin 1200/2250）。
运行时把画布按**等比**铺满视口：本仓库的 400×500 视口对 512 画布的系数是 0.78。

三条实测证据（都能用列出的脚本复现）：

| 约定 | 违反后果 | 复现 |
|---|---|---|
| 顶点必须是单位坐标 | 按像素写入 = 放大约 ppu 倍，模型整体移出画面 | `tools/probe_ppu_convention.py` |
| 三角形在 y 向上必须是 CCW | y 翻转会把手性反过来，运行时按背面剔除，**一个像素都不画** | `tools/bisect_moc3_fields.py` A 组 0 像素 / G 组 3.6 万像素 |
| 图集 UV 的 v 不再二次翻转 | live2d-py 上传时不翻纹理，多翻一次让所有 UV 落进透明留白，**一个像素都不画** | `tools/probe_uv_variant.py`（as_is=0，去翻转=13.5 万像素） |

三条修复都落在 `moc3_pipeline`（UV / 手性归一 `ensure_front_facing`）与
`moc3_model`（写出处换算成单位）。守门测试是
`tests/integration/test_pipeline_moc3_export.py::test_exported_package_actually_renders`
——它断言导出包真的画出像素、内容落在视口内且居中。此前「一致性 + 加载 +
参数表」全绿的判据**无法**发现这三类问题，因为它们只关乎字节合法性与可加载性。

仍然未做的：真实美术素材的观感签核（姿态是否自然、层间遮挡是否正确、
表情是否符合原画）。几何正确 ≠ 观感正确。

## 本轮测试

新增 moc3 相关测试：
- 单元：`tests/unit/test_moc3_sections.py`、`test_moc3_container.py`、
  `test_moc3_builder.py`、`test_moc3_lint.py`、`test_moc3_verify.py`、
  `test_moc3_model.py`、`test_moc3_pipeline.py`、`test_moc3_keyforms.py`、
  `test_runtime_readiness.py`
- 集成：`tests/integration/test_moc3_official_acceptance.py`
  （官方内核一致性、LoadModelJson、GetParamIds、Haru 回归、篡改负例、
  真实规模 13 网格/5733 顶点/28 参数、官方模型零误报回归）
- 集成：`tests/integration/test_pipeline_moc3_export.py`
  （导出目录端到端：真实 moc3 落盘、完整 model3.json 可被官方加载、
  变形器如实降级标注）
- 集成：`tests/integration/test_moc3_keyform_acceptance.py`
  （键型 moc3 过官方内核；受控像素验证证明参数改变画面，
  并含「静态包必须不通过」的负对照。像素部分需
  `LIVE2D_TEST_PIXELS=1` 显式开启）
- Go：`api/handlers/export_moc3_readiness_test.go`（就绪字段平铺与不默认成功）、
  `api/handlers/export_native_test.go`（HTTP→Python→moc3→官方内核全链路，
  需 `LIVE2D_TEST_PYTHON` + `LIVE2D_TEST_ROOT`）
