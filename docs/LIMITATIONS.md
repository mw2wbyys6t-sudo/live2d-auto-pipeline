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
> **最后校验（Last verified）**：2026-09-22。
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

- **rotation 变形器目前只产出静态姿态**：结构与单位已定论、能过官方内核，但**没有参数绑定**，
  因此眼球跟随之类的效果**不会动**。它被单独记入 `deformers_static`，不会冒充成已落地。
- **warp 变形器**已可编译并过官方内核 + 逐像素验收。
- **X/Y 轴键形刻意不生成**（在 Live2D 里是位移 + 透视的复合，属美术判断，不臆造）。
- **同一网格命中多个旋转参数时保持静态**（多参数带的轴序尚未实测定论，见评审 F-06）。
- **glue 类型变形器不支持**，会被记入 `deformers_uncompiled` 并压住 `runtime_ready`。
- **真实美术观感未签核**：几何判据（画得出、位置尺度对）已闭环，但「像不像原画」仍需人工确认（F-05）。

## 4. 运行时与桌宠

- `drivers/live2d_runtime/renderer.py` 是**PNG 图层近似渲染器**（自述 "NOT a full Cubism renderer"），
  用于无 SDK 环境的预览，不等同于 Cubism 官方渲染结果。
- 原生透明桌宠窗口的三平台支持不均衡：`native_window.py` 仅 Windows，且为**色键透明**（非逐像素 alpha）。
- 面捕在缺少 MediaPipe 时进入 stub 模式；BlendShape 为**启发式近似**，并非官方面部模型输出。

## 5. 前端工作台

- `/live2d` 的「Model structure」树与 Physics 面板是**静态展示**，不随模型变化、不影响渲染。
- `/characters/[id]` 的 embedding 重算与参考图上传已接通后端（`POST /api/characters/:id/embedding`、
  `POST /api/characters/:id/references`，v0.10.1）；「Generation history」目前展示全局最近一次
  生成记录（`latest_generation.json`），**尚无按角色的生成历史**。
- 生成进度已走 WebSocket 实时推送（前端订阅 `/api/ws` 的 `type:"progress"` 广播）；导出页仍为
  一次性请求 + 阶段推送。
- `/preview` 的摄像头面捕**在浏览器内运行**（MediaPipe FaceLandmarker WASM，Go/Python 部署均可用）。
  WASM 运行时已本地化（`web/public/mediapipe/wasm`）；`face_landmarker.task` 模型默认从官方 CDN
  加载（可下载后放入 `web/public/models/` 或设 `NEXT_PUBLIC_FACE_LANDMARKER_URL`），离线且未放置
  模型时面捕不可用并明确报错。Python 后端 `/api/tracking/*` 与 `/ws/tracking` 仍保留（服务端面捕），
  Go 后端显式 501。
- 「Launch Desktop Pet」按钮已接通（`POST /api/deploy/desktop`）：真实可用的前提仍是
  Windows + `live2d-py` + 已通过官方内核验收的模型目录。

## 6. API 层

- `api/python_server.py` 是 Go 服务的降级替代实现，其多数 POST 处理器为**桩**（仅回 202）。
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
