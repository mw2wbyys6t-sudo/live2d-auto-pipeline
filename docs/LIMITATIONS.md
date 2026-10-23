# ⚠️ 已知限制与缺陷

> **读者（Audience）**：使用者与贡献者 —— 用于在依赖某项能力前设定合理预期，并区分「已落地」与「如实标注的缺口」。
> **文档类型（Diátaxis）**：explanation。
> **责任路径（Responsibility）**：各模块负责人（见 [docs/architecture/index.md](architecture/index.md) § 2 限界上下文）。
> **真相源（Source of Truth）**：
> - 产品级风险与缓解 → [docs/architecture/index.md](architecture/index.md) § 5 风险登记册（R-01 ~ R-12）
> - 导出链路（moc3）的深度缺陷与证据 → [docs/reviews/2026-09-20-moc3-export-risk-review.md](reviews/2026-09-20-moc3-export-risk-review.md)
> - 原生运行时可用边界 → [docs/native-runtime.md](native-runtime.md) § 验收界限
>
> 本文档只做**面向用户的汇总**；细节与证据以上述真相源为准，避免重复维护。
> **最后校验（Last verified）**：2026-10-14。
> **新鲜度规则**：每个 minor 版本发布前复核一次；若真相源中关联条目（R-xx / F-xx）状态变化而本文未同步，即视为 `stale`。
> **变更触发**：导出链路、分割后端、驱动层运行时、前端页面实现度任一发生变化时。

---

> 本文替代了 v0.6.0 时期的同名文档（其中引用的 `master_tool.py`、See-through 安装器等均**已不存在**），
> 并移除了会把未完成项标成「✅ 已完成」的表述。

## 1. 图像生成

- **免费 Provider 质量不稳定**：默认走免费服务，可能生成失败、面部/手部异常或与描述不符。
- **中文提示词效果通常弱于英文**。
- 多 Provider 会自动降级，降级只保证「有结果」，不保证质量。v0.10.1 起路由为
  seedream > sensenova > **openai（OpenAI Images API 兼容端点，需 `OPENAI_API_KEY`，
  可接 one-api/本地网关）** > pollinations；`GET /api/providers` 可查各上游配置状态。
- 所有上游均不可用时回退 `local_image_generator.py` 占位图（带 LOCAL-PLACEHOLDER 水印），
  保证链路可走通，但**它不是 AI 生成结果**。

## 2. 语义分层

- 画布高度低于 Live2D 最低 1000px 时，workflow 会在 QA **之前**等比 LANCZOS 补到 **1000px**
  （只过 E001 硬门槛，**不去追 2000-4000 的"最佳区间"** —— 实测放大越狠分层越差：
  `demo_input.png` 900px=9 / 1000px=10 / 2000px=11 条缺陷，`character_1790515599`
  512px=15 → 2000px=**23** 条），并把 `from/to/method/note` 写进 `steps.upscale` ——
  不静默改用户素材。需要放大 >4 倍的极小图**拒绝插值**，让 E001 继续可见。
  根因有配置证据：SAM2 图像处理器写死 `size={1024,1024}, do_resize=True`
  （GroundingDINO 为 shortest 800 / longest 1333），**模型侧永远只看 ≤1024 长边**，
  放大过 1024 不会带来任何分割精度，只是把同一份掩码摊到更大画布。
  复核：`tests/unit/test_workflow_upscale_remediation.py`。
- 默认分层在模型缺失时会**降级为颜色启发式**（`semantic_hsv_fallback`），复杂服装/配饰容易分错。
- **无模型时不再"整幅图都算前景"**：`estimate_foreground_silhouette()`（`core/utils/image_utils.py`）
  取四边窄环颜色当背景调色板，把「与背景同色**且与画布边框连通**」的像素判为背景，再闭运算补网格线、
  开运算削掉标注文字、保留最大连通块并填洞。优化图（`_optimize_image` → `cutout_by_silhouette`）与
  分层（`SemanticLayerer._synthesise_foreground`）走**同一张**剪影。
  修复前 `demo_input.png`：优化图仍有 37.7% 像素被判成前景，`hair_back` 覆盖 68% 画布 / bbox 铺满全图，
  部件并集 = 100% 画布，质量门 **7 条缺陷**（`clothes_top` 碎成 27 块、`eyes_left`/`mouth` 缺失、
  `neck` 跑到 `clothes_top` 下方）。修复后：并集 23.1%、解释率 0.99、**0 条缺陷**，全链路
  `steps.layering.quality.ok = true`（此前恒为 false）。
- 肤色兜底定位脸时必须**按形状+位置挑，不能取"上半部分最大块"**：全身立绘里面积最大的肤色块是腿
  （被 65% 横线截断后 bbox 仍偏方），脸框落在大腿上，五官锚点整体错位。`_skin_face_component()`
  改为按 `面积 / (1 + 4·y0/H)` 打分并淘汰宽高比不在 0.5~2.0 的候选。
  复核：`tests/unit/test_face_features.py::TestSkinFaceSelection`、
  `tests/unit/test_segment_engine.py::TestSynthesisedSilhouette`。
- **脸底层导出为"干净皮肤"**（`LayerComposer.FACE_PARTS` / `_erase_features`）：脸层里被
  眼睛/眉毛/嘴/鼻盖住的像素用周围肤色 inpaint 掉，alpha 保持不变（不能挖洞，否则眨眼时透出背景）。
  不这么做，`face.png` 在瞳孔位置与 `irides_left.png` 完全同色——眼睛被压扁了脸层又把瞳孔
  原样露出来，`ParamEyeLOpen` 改了跟没改一样。
- 因此 `STANDARD_LAYER_ORDER` 必须把 `face`/`face_base` 排在五官**之前**（越靠后越靠前）。
  历史顺序把脸排在 `eyebrows`/`eyes` 之后，靠"脸层自带眼睛像素"掩盖着；脸层清理干净后
  立刻暴露出眼睛被盖成碎片的画面。真实层名（`face`/`eyewhite_*`/`irides_*`/`arms`/`legs`/
  `clothes_bottom` 等）也已补进顺序表——未列出的名字会按字母序被追加到最后，等于画到最前面。
  复核：`tests/unit/test_segment_engine.py::TestLayerComposer::test_face_is_drawn_before_every_facial_feature`。
- `segmentation_backend="sam2_gd"`（GroundingDINO + SAM2）**刻意不降级**：缺权重直接抛 `ModelUnavailable`，
  workflow 以 `error` 收尾。这是有意的诚实设计，但意味着该后端需要额外模型与依赖（torch/transformers）。
- `sam2_gd` 的分层质量由 `core/segment_engine/layer_quality.py` 逐图打分并写进
  `steps.layering.quality`（`ok / reasons / defect_count / explained_fraction`）。门槛 `MAX_DEFECTS=4`
  来自实测分布：`demo_input.png` 的去背图上 **9** 条、放大到 667×1000 后 **10** 条、`output/` 里四张
  生成图 **7/9/13/15** 条 —— **目前观测到的每一张都不过门槛**，角色面积只被解释 0.53~0.55。
  不达标只记录 + 告警，不中断导出：模型照样产出，但结果不会被读成"干净成功"。
- 该后端的**参数层已实测穷尽**：两轮共 22 个 config（box/text 阈值、面积上下限、NMS、top-N、
  face 门控、mouth/face/arms/hair 提示词改写）中现网默认即最优；`nms_iou`、`face_gate_margin`、
  `max_boxes_per_query` 在 0.26 阈值下**从不咬合**（全图总共 23 框）。降阈值能"找回" mouth
  但掩码只有 0.14~0.39 落在脸上，按本仓库原则比缺失更糟）。再往上只能换机制，不是继续调参。
  **已落地的第一个机制改动**：脸部裁剪二次检测（`_maybe_face_crop_pass`），把 mouth 从
  "检不出来"变成 `mouth_in_face=1.00`、`missing_parts` 清零、defects 12→11 —— 详见
  `docs/native-runtime.md`。仍未解决的：`eyebrows∩face` 只到 0.67，补回的嘴仅 130 px。
  复核：`tools/tune_sam2_gd.py`、`tools/score_layering.py --selftest`（8 条负对照）、
  `tests/unit/test_face_crop_redetect.py`。
- 被遮挡区域的 Amodal 补全在缺少学习式模型时回退到 OpenCV 修补；`pix2gestalt` 路径目前是**桩**。
  极端色偏可能在 QA 阶段被打标（见风险 R-07）。

## 3. Live2D 绑定与 moc3 导出

这部分最值得注意 —— 仓库用 `runtime_ready` / `dropped` / `deformers_static` 等字段**如实标注**能力状态，
请以产物里的这些字段为准，不要以「导出成功」推断「能动画」。

- **rotation 变形器在真实流水线里不会被创建**——注意这**不是**「已支持但没绑参数」，
  两者的修法完全不同。三条实测原因（2026-10-21，用 `Work/base` 的真实 32 层跑
  `DeformerHierarchy.build()` 复核）：
  1. 生成器 `DEFAULT_ROTATION_DEFORMERS` 声明的 targets 是 `eyeball_l` / `eye_left`，
     而分层阶段实际产出的层名是 `eyewhite_left` / `irides_left` / `eyelid_left`，
     `_matched_targets` 恒为空 → rotation 变形器 **0 个**；
  2. 枢轴 `(-35,-40)` 取自 `_DEFAULT_BONE_POSITIONS` 的**比例兜底**值，不是图像坐标；
  3. 即便前两条都修好，`_driving_rotation_parameter` 也只在 Head / Body / 双臂 / 三束
     头发里找 Z 轴参数，而眼球对应的是 `ParamEyeBallX/Y`（平移，非旋转），永远绑不上。

  「枢轴命中即绑定逐键键形」这条逻辑本身是好的（`tests/unit/test_moc3_pipeline.py`
  有覆盖），绑定失败时也会如实记入 `deformers_static`。**真正承载转动的两条路径是
  网格键形**：`head_angle_keyforms`（`ParamAngleX/Y/Z` 三轴 → face/hair/mouth/nose）
  与 `eye_track_keyforms`（`ParamEyeBallX/Y` 双轴 + `Eyeball_L/R` 权重 → 眼白/虹膜）。
  被跳过时现在会带 `rotation_skipped`（名字 + 原因 + 期望层名）并打 warning，
  不再静默消失，见 `tests/unit/test_deformer_hierarchy_rotation.py`。
  要让 rotation 变形器真正投入使用，必须换一个不被 warp 占用、语义确实是刚体旋转的
  部件（例如手臂绕肩/绕肘），那是美术决策，不做推断。
- **warp 变形器**已可编译并过官方内核 + 逐像素验收。
- **网格键形（逐顶点键形带）已开启**（`moc3_pipeline.MESH_KEYFORMS_ENABLED = True`），
  保留为急停开关。曾经整体关闭的现象是：模型里有 ≥2 个网格带键形时，官方 Cubism Core 在
  `LoadModelJson` 阶段段错误，且**概率性**复现（同一份字节约 90–95% 崩），
  `HasMocConsistencyFromFile` 仍返回 `true`。根因已定位：崩溃发生在
  `csmiCalculateRenderOrder`，它按 `draw_order - min_draw_order` 取绘制序桶下标，
  而桶数组按 `max - min` 分配 —— 声明 `max == 实际最大绘制序` 时最后一档越界一格，
  读到什么取决于堆布局，于是表现为「时崩时好、与 `draw_order` 非单调相关」。
  修复：`_write_draw_order` 的 `max` 改取开区间上界（实际最大值 + 1）；
  同时 `_validate_keyforms` 要求键值覆盖参数完整区间（键值只覆盖一段时，参数取到区间外的
  值同样会让内核越界读取）。已对 2 / 10 / 40 网格的带键形模型做过官方内核加载
  + 两端取帧逐像素比对。
- **X/Y 轴键形刻意不生成**（在 Live2D 里是位移 + 透视的复合，属美术判断，不臆造）。
- **自动绑骨必须按语义解析层名，不能"命中不了就挂 Face"**：`BoneHierarchy.resolve_bone()` 现在按
  「词表(`LAYER_BONE_MAP`) → 拆 `_L/_R` 选边(`SIDED_BONE_MAP`) → 按质心落点推断 → 兜底 `Torso`」
  四级解析。修复前词表只认识 `face_base`/`eye_left`/`thigh_left`，而实际产出是
  `face`/`arm_L`/`sock_L`/`clothes_top`，实测 22 层里 15 层落进 `Face`——**头一转整件衣服跟着甩**。
  修复后 25 层全部语义命中、0 次兜底；`ParamAngleX/Y/Z` 只烘焙到 `face`/`hair_back`/`hair_front`/
  `mouth`/`nose` 5 个网格（此前是全部网格），衣服/腿/袜只受 `ParamBodyAngleZ` 或各自的手臂参数驱动。
- **骨骼枢轴必须是图像坐标**：`get_bone_positions()` 曾返回"以画布中心为原点"的比例值
  （`Head = (0, -40)`），而调用方 `moc3_pipeline` 会再做一次 `to_model_xy`（减半画布），
  Head 枢轴于是跑到画布左下角外，头一转整颗头绕着角落甩。现在有质心时全部换到图像坐标，
  并由子骨骼补出 `Head/Root/Body/Chest`（`Head` 取「脸的水平 + 颈的纵向」）。
  复核：`tests/unit/test_bone_weights.py::TestBoneResolution / TestBonePivots / TestHeadAssemblyExclusion`。
- **同一网格命中多个旋转参数时保持静态**：参与网格路径的只有 `ParamAngleZ`
  （头/脸/发/眼/眉/耳）与 `ParamBodyAngleZ`（身体），而 `_affected_bones` 会沿祖先
  上溯 —— 一处同时带 Head 与 Neck 权重的网格（脖子、衣领）两个都会命中，于是退回静态、
  完全不跟随。
  多参数带的**格式**已不是障碍：2-binding 的轴序已实测定论（X 在前、步长 1，见
  `tools/probe_multiband_axis_order.py` 与评审 F-06），网格键形与 warp 变形器都在用。
  剩下的是**语义**判断：把「头部转动 × 身体转动」合成一条多轴带会让脖子同时受两级
  转动影响，是否符合美术预期需要确认，因此暂不生成。
- **绑定输入的层名 = 语义部件名**：绑定（骨骼绑定 / 变形器 / 参数键形）完全按**层名**解析 ——
  `live2d_builder` 的 `LAYER_BONE_MAP` 认 `hair_back`/`eye_left`，`DeformerHierarchy` 的
  targets 认 `hair_front`/`eyeball_l`。语义分层路径的层名本来就是这些名字，绑定成立；
  但 **K-means 回退路径**的层名是 `layer_000` 这类通用序号名，语义识别算出来的部件名
  （`part_name_en`）此前**没有被回写到正式绑定输入** —— 头发不绑 `Hair_Back`、眼睛不绑 `Eye_L`，
  参数键形整片失效，骨骼解析只能退到默认值或几何猜测。
  现在 `core/workflow.py` 的 `_rigging_layer_key()` 会把**已识别出的**语义部件名用作层键
  （识别不出时保持原名，绝不凭空编造；重名加 `N_` 前缀，因为 `live2d_builder` 的
  `_normalise` 会剥掉前导数字前缀，语义不丢）。见 `tests/unit/test_rigging_layer_key.py`。
  **仍存的边界**：K-means 回退路径的部件识别本身只是颜色/位置启发式（`core/segment_engine/
  part_identifier.py`），识别精度有限 —— 语义回写让「识别到的语义」不再丢失，但不会让
  识别变得更准；真正准的语义只有语义分层路径能给。
- **表情形变走 warp 变形器，同一个部件的多个参数合成一条「多轴带」**：内核里一个网格只能挂
  一个参数带（眼睛已被双眼球键形、嘴与眉已被头部三轴键形占满），一个变形器也只能挂一个带，
  所以口的「开合 × 口型 × 大小」、眉的「上下 × 角度 × 形状」必须压成一条多轴带
  （`WarpDeformerSpec.axes`，展平序同网格键形：第一个轴步长为 1）。
  绑定参数写死在变形器说明里（`profile` + `parameters`，见 `moc3_pipeline.DEFORMER_PROFILES`）
  ——这些效果没有对应骨骼，无法像转动/呼吸那样靠枢轴就近匹配推导。

  | 变形器 | 参数（一条带） | 形变 |
  |---|---|---|
  | `MouthOpen` | `MouthOpenY` × `MouthForm` × `MouthSize` | 上沿钉住下拉 2.5×；两端上/下弯 0.8×；横向 0.65~1.35 |
  | `BrowMove_L/R` | `BrowLY` × `BrowLAngle` × `BrowLForm`（R 侧镜像） | 上下 0.6×；倾斜 0.35×；中段上拱 0.25× |
  | `EyeBlink_L/R` | `EyeLOpen` / `EyeROpen` | 以纵向中心压到 8%（不给 0：控制网格退化成线会让内核逆映射退化） |
  | `HairFront/BackSwing` | `HairSwing` | 发根钉住、发梢横向摆幅 ∝ 深度^1.5 |
  | `BodySway` | `BodyAngleX` × `BodyAngleY` × `BodySway` × `Breath` | 越靠肩位移越大、胯部不动（4 轴 81 格） |
  | `ShoulderLift_L/R` | `ShoulderL` / `ShoulderR` | 整条上臂抬落 0.12×（没有独立肩层） |

  * 两个必须踩住的坑：**形变量要按网格自身尺寸算**，不能按控制网格（含头部键形摆动范围，
    嘴的 5px 会被当成 28px）；**横向归一化必须夹到 [-1,1]**，否则控制网格比网格宽 4 倍时
    `t²` 到 16，嘴角被拉起 21px，脸上拉出一道 V 形伤疤。
  * 实测（官方内核受控像素验证）：修复前 `ParamMouthOpenY`/`ParamEyeLOpen` 的
    `changed_pixels` **恒为 0**；现在声明的 28 个参数**全部**能改变画面，
    嘴 212px、睁闭眼 411/336px、眉 75~124px、口型 46~57px、腮红 217px、
    眼泪 221/129/92px。
  * **观感受素材限制**：嘴在多数立绘里只是一条 2~4px 的墨线，没有"张开"的画稿，能做的只是
    把线按比例撑开；眼皮也是同样——没有画稿，只能从脸层与眼球层合成一张闭眼图（见下一条）。
  * `ParamCheek` / `ParamTears` / `ParamTearsL/R` 曾经是"缺美术图层"而完全不响应
    （`verify` 里列为 `declared_but_not_driven`）。现在由分层阶段合成的
    **淡入层**（`cheek` / `tear_left` / `tear_right`）驱动，见下一条；
    `declared_but_not_driven` 为空。
  复核：`tests/unit/test_moc3_deformers.py::TestExpressionDeformerProfiles /
  TestMultiAxisBandHelpers / TestOpacityKeyformLayers / TestDrivenParameterReporting`、
  `tests/integration/test_pipeline_moc3_export.py::test_mouth_open_changes_rendered_pixels` /
  `::test_mouth_form_and_size_are_driven_independently` / `::test_eye_open_changes_rendered_pixels` /
  `::test_cheek_fades_in_only_when_the_parameter_moves` /
  `::test_tears_open_from_the_master_or_a_single_side`。
- **闭眼图与口内是分层阶段「合成」出来的两层**：原始立绘里没有这两个画稿（眼睛睁着、嘴闭着），
  缺了它们，眨眼只能把眼球压扁、张嘴只能把墨线撑粗。合成规则如下（`LayerComposer.
  synthesize_face_geometry`）：

  | 层 | 形状 | 颜色 | 静止时 |
  |---|---|---|---|
  | `eyelid_left/right` | 与眼睛轮廓**同形**（眼球层不透明的那块面积） | 脸层**原像素**填的皮肤 + **原画自己的上睫毛线** | 画在眼球**下面**，被眼球完全挡住 |
  | `mouth_cavity` | 与嘴线层**完全重合**的轮廓 | 嘴线中位色向暖红抬 45% | 正好被嘴唇盖住 |

  * **闭眼 = 眼球向上收拢，不是"眼皮盖下来"**。旧方案是合成一条"眼皮带"往下移盖住
    眼球，实测有两处硬伤：(1) 带子要从眼睛**上方**取像素，而眼睛正上方整片是头发，
    脸层在那里存的是**发色**（原图像素），照抄下来闭眼时眼睛上会留一条 40 px 宽的蓝带；
    (2) 带子本身画在眼球**上面**，与"闭眼图画在下面、眼球收拢让出来"的层级关系相反。
    现在改成：`eyelid_*` 是画在眼球**下面**的「闭眼图」（皮肤 + 原画上睫毛），
    眼球由 `EyeBlink_L/R`（`eye` profile）整体**向上收拢**进上睫毛线，收拢后露出来的
    就是这张图。闭眼图是**静态**的，不挂任何变形器。
  * 收拢轴心取眼睛轮廓的**上沿**（不是纵向中心）：用中心收拢会把眼球压成一条横在眼睛
    中间的线，像眯眼；贴上沿收拢才是"眼皮落下来盖住"的样子。收拢后保留眼高的 8%
    （不能给 0：控制网格退化成一条线后内核算变形时的逆映射会退化，实测 Cubism Core 5.0.0）。
  * `mouth_cavity` 仍是独立的一条带（`mouth_cavity` profile，上沿钉住、向下张 4.0×，
    必须明显大于嘴唇的 2.5×，否则口内永远正好被嘴唇盖住）。
  * 静止姿态**逐像素不变**（实测合成前后最大差 0）：这是整条路线的前提，也是
    `TestSynthesisedFaceGeometry::test_rest_pose_keeps_every_pixel` 的判据。
  * 因为要补色，**刘海必须排在闭眼图之后**（`hair_front`/`hair_bangs`）。实测这一改动顺带
    让合成图更接近原画（MAE 1.5767 → 1.5695），也修掉了「刘海靠脸的洞透出来」这一怪结构。
  * 官方内核逐像素实测（`test_eye_collapses_upward_and_reveals_the_closed_eye_image`）：
    闭眼后眼睛区域的深色像素**降到 0**，残余只贴在眼睛上沿那条 8% 的细带（抗锯齿）；
    残余行号必须落在上沿 20% 以内 —— 这条判据把"向上收拢"与"从中间压扁"区分开。
  * **观感仍受素材限制**：闭眼图是"皮肤 + 一条睫毛线"，不是有厚度、有睫毛翻动的真眼皮；
    口内是单一暖色，没有舌头/牙。真实美术观感仍需人工签核（同 F-05）。
  复核：`tests/unit/test_segment_engine.py::TestSynthesisedFaceGeometry`、
  `tests/unit/test_moc3_deformers.py::TestEyeCollapseAndCavityProfiles /
  TestSynthesisedLayerDeformersCompile`、
  `tests/integration/test_pipeline_moc3_export.py::test_eye_collapses_upward_and_reveals_the_closed_eye_image` /
  `::test_mouth_cavity_shows_when_the_mouth_opens`。
- **腮红与眼泪是「淡入层」：静止不可见，靠不透明度键形淡入**（`cheek` /
  `tear_left` / `tear_right`，见 `LayerComposer.REST_INVISIBLE_PARTS`）：

  | 层 | 形状 | 驱动 | 静止 |
  |---|---|---|---|
  | `cheek` | 两眼下方**各自向外偏**的柔粉椭圆（颜色取自脸层肤色 + 暖粉偏） | `ParamCheek` | 不透明度 0 |
  | `tear_left/right` | 下眼睑一条薄新月，淡蓝白 | `ParamTears`（总开关）× `ParamTearsL/R`（单侧） | 不透明度 0 |

  * 它们和闭眼图/口内的"静止不可见"策略不同：后两者**被别的层挡住**，这两层是
    **不透明度 0**。所以 `rest_opacity=0` 必须一路带到合成预览
    （`semantic._write_composite_preview` 会跳过），否则静止画面凭空多出腮红
    （实测最大差 100.9）。
  * 驱动只用**网格键形的 opacity**，不用 warp 变形器：warp 改不了透明度。
    一个网格只能挂一条键形带，所以 `ParamTears` 与 `ParamTearsL/R` 合成一条
    **多轴带**，多轴取值取各轴归一化值的 **max**：总开关打开 = 两侧都有，单侧打开
    = 只有那一侧。用乘积会让任何单轴都变不动（验收逐参数扫描，实测 changed=0）。
  * 这两层的网格走**覆盖 alpha 包围盒的规则网格**（`Live2DBuilder._patch_mesh`），
    不走轮廓采样：它们的形状是一整片柔和渐变，轮廓采样会把渐变外圈当噪声丢掉——
    实测腮红层 618 px 却连网格都没生成、眼泪层 28×11 的渐变只被 10×5 的小块覆盖，
    于是参数拉满也只亮一小块。
  * **观感仍受素材限制**：没有原画腮红/眼泪笔触，腮红是肤色色偏 + 径向衰减，
    眼泪是一条薄高光；不是有厚度的泪珠或带睫毛翻动的真眼泪。真实美术观感仍需人工签核。
  * **浏览器预览里它们是静止可见的（近似，不等于 moc3）**：`model3.json` 目前不写
    `PreviewLayers`，网页端把图集页整块绘制、不支持逐层不透明度，所以预览里腮红/
    泪光会一直挂着；moc3 里由参数键形正确控制（静止帧实测 0 像素）。这条如实记录，
    不当作"预览已对等"。
  复核：`tests/unit/test_segment_engine.py::test_fade_in_layers_are_invisible_at_rest`、
  `tests/unit/test_moc3_deformers.py::TestOpacityKeyformLayers`、
  `tests/unit/test_rig_mesh.py::TestFadeInLayerMesh`、
  `tests/integration/test_pipeline_moc3_export.py::test_cheek_fades_in_only_when_the_parameter_moves` /
  `::test_tears_open_from_the_master_or_a_single_side`。
- **受控像素验收按「模型自报的能力」逐项验，不再固定三个参数**：导出器把编译后真正被绑定的
  参数写进 `model3.json` 的 `Capabilities.DrivableParameters`（网格键形 + warp 变形器的带，
  见 `moc3_pipeline.driven_parameters`），`drivers/live2d_runtime/verify.py` 对**每一个**都做
  min/max 两端取帧比对，其余声明过的参数列为 `declared_but_not_driven`。
  验收视口取模型画布尺寸（最长边夹到 1024），让「1 模型像素 = 1 渲染像素」——曾经固定 400×500，
  只有画布的 0.6 倍，口型这类小变化被缩到 19px，卡在 `changed > 20` 阈值上误判为不动。
  历史读数（同一份产物）：修复前 28 个参数里 **19 个不动**，报告照样显示通过。
  复核：`tests/unit/test_moc3_verify.py::TestDrivableParameterSelection`。
- **指纹只覆盖模型字节，不覆盖验证自身的证据图**：`package_sha256` 会遍历整个产物目录，
  而受控像素验证会把 min/max 截图落盘——调用方把证据目录放在产物里（最自然的放法）时，
  「验证期间包没变」这条判断就会对任何产物恒假（实测报 `Model package changed during
  verification`）。`verify()` 现在把证据目录排除在指纹之外；模型文件仍然全部计入。
  复核：`tests/unit/test_native_runtime.py::test_package_fingerprint_ignores_verification_evidence`。
- **小图层一律用 3×2 面片**（`MeshGenerator.TINY_FEATURE_PIXELS = 400`）：轮廓按 12px 采样，
  39×30 的眼睛只取到 6 个边界点，三角化+质心过滤后剩 9 个三角形，实测只盖住眼睛的 27%
  （眼白 IoU 0.275、瞳孔 0.392），渲染出来是几块碎片。面片覆盖 alpha 包围盒（各留 1px），
  包围盒内的图层像素本身透明，所以画面不会多出内容。这条也是上面表情参数能起作用的前提
  ——嘴只有 27px，Delaunay 连三角形都出不来，整层会从模型里静默消失。
  中间那一列顶点是必需的：4 角面片的形变只能是双线性插值，"两端动、中间不动"的口型/眉角
  会退化成整体平移（实测嘴整体上移 4px）。
  复核：`tests/unit/test_rig_mesh.py::TestTinyFeatureMesh`。
- **glue 类型变形器不支持**，会被记入 `deformers_uncompiled` 并压住 `runtime_ready`。
- **真实美术观感未签核**：几何判据（画得出、位置尺度对）已闭环，但「像不像原画」仍需人工确认（F-05）。

## 4. 运行时与桌宠

- `drivers/live2d_runtime/renderer.py` 是**PNG 图层近似渲染器**（自述 "NOT a full Cubism renderer"），
  用于无 SDK 环境的预览，不等同于 Cubism 官方渲染结果。
- 原生透明桌宠窗口的三平台支持不均衡：`native_window.py` 仅 Windows，且为**色键透明**（非逐像素 alpha）。
- 面捕已改为**原生 Rust `native/face_tracker` 子进程**（ONNX YuNet 人脸检测）：摄像头由该进程
  独占采集（Windows 下设备不可共享），参数经 `/api/ws` 的 `tracking_frame` 广播给前端与桌宠。
  二进制未编译时接口显式 501（提示 `cd native/face_tracker && cargo build --release`），
  配置 `native_tracker.enabled=false` 可整体禁用。BlendShape 仍为**几何启发式近似**
  （由原 Python `mediapipe_tracker.py` 移植），并非官方面部模型输出。

## 5. 前端工作台

- `/live2d` 的「Model structure」静态树、Physics 面板与 Wireframe 开关已于 v1 真实性清理中**移除**
  （它们此前只是写死的展示，不随模型变化、也不影响渲染）。当前只保留真实生效的参数滑块、
  表情应用、Validate 与模型加载入口。
- `/characters/[id]` 的 embedding 重算与参考图上传已接通后端（`POST /api/characters/:id/embedding`、
  `POST /api/characters/:id/references`，v0.10.1）；「Generation history」目前展示全局最近一次
  生成记录（`latest_generation.json`），**尚无按角色的生成历史**。
- 生成进度已走 WebSocket 实时推送（前端订阅 `/api/ws` 的 `type:"progress"` 广播）；导出页仍为
  一次性请求 + 阶段推送。
- `/preview` 的摄像头面捕**不再在浏览器内运行**：改用原生 Rust `native/face_tracker` 子进程
  （Windows 下摄像头设备不可共享，浏览器拿不到画面）。前端 `web/lib/native-tracker.ts` 连接
  `/api/ws` 订阅 `tracking_frame`；`POST /api/tracking/start|stop`、`GET /api/tracking/status`
  为控制与状态接口。浏览器内 MediaPipe 面捕（`web/lib/face-tracker.ts`）已移除。
  降级实现 `api_server.py` 保留 `/api/tracking/*` 与 `/ws/tracking`（Python + MediaPipe），
  与 Go 的原生子进程是两条独立采集路径，但**对前端的协议已经统一**：
  降级模式同样在 `/api/ws` 上广播 `tracking_frame`（`data.live2d_params` + `blendshapes` +
  `head_rotation`，与 Go 的 `native_bridge` 同构）。此前降级模式的 `/api/ws` 只有一个阻塞的
  接收循环，前端能连上却永远收不到帧 —— 表现为「连着但模型不动」。
- 「Launch Desktop Pet」按钮已接通（`POST /api/deploy/desktop`）：真实可用的前提仍是
  Windows + `live2d-py` + 已通过官方内核验收的模型目录。

## 6. API 层

- `api_server.py` 是 Go 服务的降级替代实现。**它不是「多数 POST 都是桩」**：实测 21 个 POST
  端点里 16 个是真实实现，剩下 5 个（`/api/see-through`、`/api/export/psd`、`/api/cache/clear`、
  `/api/models/optional/download`、`/api/models/optional/cancel`）统一回 **501 + `go_endpoint` 指引**，
  不回 202 假成功。降级模式的边界是「这些能力只有 Go API 有真实实现」，不是「大部分接口没写」。
- 降级模式现已与 Go 侧**对齐前端实际调用的全部端点**（此前 8 个返回 404，导致健康页误报
  「无法连接后端」）：`GET /api/diagnostics`、`GET /api/providers`、`GET /api/models/optional`、
  `GET /api/generations/models`、`GET /api/tracking/status`、`POST /api/upload`、
  `POST /api/import/psd`、`POST /api/import/pngs`、`POST /api/characters/:id/references`、
  `POST /api/characters/:id/embedding`。端点对齐由「前端路径字面量 × 后端路由表」的全量对账
  核过（不是逐个端点手查）。
- 参数校验失败（422）现在也走统一响应信封 `{success, error, data}`。FastAPI 默认回
  `{"detail": [...]}`，前端解析不到 `error` 字段，只能显示一句 "Request failed: 422"；
  Go 侧一直是走 `models.Response` 的 —— 这是又一处 Go/Python 信封不一致。
- `/api/health` 现在带 `data.python_ok`（安装验收脚本与 e2e checklist 的硬判据，缺字段会被判 FAIL）；
  依赖缺失时回 503 而不是「一切正常」。
- `/api/status` 与 `/api/models` 的 `available` 改为**实测探测**（不再硬编码 true/false）：
  服务可用性看真实探测结果，模型可用性看 `ProviderRouter` 的 provider 状态（配了 Key 就变 true）。
  `/api/info` 的 `features` 同样改为按探测给出：语义分层降级时如实标成
  「颜色启发式分层（已降级：…）」，不再一边写着「语义分层」一边跑的是另一条路。
- 聊天会话存储为内存实现，**无 TTL/淘汰**。
- v0.10.1 新增（Go）：`GET /api/generations/latest`、`POST /api/segment`、`POST /api/upload`、
  `POST /api/characters/:id/references`、`POST /api/characters/:id/embedding`；`/output/*filepath`
  静态路由支持子目录。

## 7. 高级生成管线（可选能力）

- `LoRATrainer.train()` **不执行真实训练**：只装配配置并提示需要更多代码与算力。
- `ControlNetGenerator` / `Img2ImgStyleTransfer` 依赖 `diffusers`，缺依赖时明确返回不可用。

## 8. 可验证性边界（重要）

- 默认 CI **不安装官方 Cubism Core 运行时**，因此一致性 / 加载 / 逐像素形变判据在默认 job 中会被
  **skip**；「CI 全绿」**不能**证明导出模型能被官方内核接受或参数真的驱动画面。
- 真实验收入口是手动触发的 `moc3-acceptance` job（安装 live2d-py + xvfb + `LIVE2D_TEST_PIXELS=1`）。
  首次运行需人工确认环境可跑通。

## 9. 参考

- 详细运维/部署边界见 [docs/DEPLOY.md](DEPLOY.md)、[docs/FAQ.md](FAQ.md)。
- 架构不变量与适应性函数见 [docs/architecture/index.md](architecture/index.md) § 4（FF-1 ~ FF-16）。
