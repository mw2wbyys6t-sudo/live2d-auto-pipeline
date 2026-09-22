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

> 本文替代了 v6.0 时期的同名文档（其中引用的 `master_tool.py`、See-through 安装器等均**已不存在**），
> 并移除了会把未完成项标成「✅ 已完成」的表述。

## 1. 图像生成

- **免费 Provider 质量不稳定**：默认走免费服务，可能生成失败、面部/手部异常或与描述不符。
- **中文提示词效果通常弱于英文**。
- 多 Provider 会自动降级（优先级见 `core/image_gen/router.py`），但降级只保证「有结果」，不保证质量。

## 2. 语义分层

- 默认分层在模型缺失时会**降级为颜色启发式**（`semantic_hsv_fallback`），复杂服装/配饰容易分错。
- `segmentation_backend="sam2_gd"`（GroundingDINO + SAM2）**刻意不降级**：缺权重直接抛 `ModelUnavailable`，
  workflow 以 `error` 收尾。这是有意的诚实设计，但意味着该后端需要额外模型与依赖（torch/transformers）。
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
- `/characters/[id]` 的 embedding 重算、参考图上传、生成历史为**占位**。
- 生成进度**未走 WebSocket 实时推送**（一次性 POST + 结果步进）。
- `/preview` 的摄像头面捕依赖 `/api/tracking/start|stop` 与 `/ws/tracking`：这两个端点**只在
  Python `api_server.py` 中实现**，Go 后端没有对应路由，因此默认（Go）部署下该功能不可用。
- 「Launch Desktop Pet」按钮禁用（后端返回 501）。

## 6. API 层

- `/api/export/spine` 是**占位端点**，恒返回 `available:false`。
- `api/python_server.py` 是 Go 服务的降级替代实现，其多数 POST 处理器为**桩**（仅回 202）。
- 聊天会话存储为内存实现，**无 TTL/淘汰**。

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
