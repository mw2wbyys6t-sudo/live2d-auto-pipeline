# 免费生图底模 + LoRA 微调接入设计

## 1. 目标

把「免费、可本地微调」的生图模型接入现有 `ProviderRouter`，并让用户能**显式选择**
引擎与模型；角色一致性由 LoRA 承担，分层友好由提示词约束与现有分割链路承担。

## 2. 背景与约束

| 事实 | 来源 |
|---|---|
| 生图路由已有 6 个 provider：`comfyui` / `sd_webui` / `seedream` / `sensenova` / `openai` / `pollinations`，另有不在默认序列的 `local_placeholder` | [router.py](../../../core/image_gen/router.py) `PRIORITY` |
| ComfyUI 已能自动发现 checkpoint（`/object_info/CheckpointLoaderSimple`）、支持 `COMFYUI_CHECKPOINT`、支持自定义 API 格式 workflow 与 `{{CHECKPOINT}}` 等占位符 | [comfyui.py](../../../core/image_gen/comfyui.py) `_build_graph` / `_apply_placeholders` |
| **内置 workflow 没有 LoRA 加载节点** —— 只有 `CheckpointLoaderSimple`，模型与 CLIP 都直接从它接出 | 同上，节点 `4` 的 `model`/`clip` 输出 |
| `/api/generate/character` 把 `provider=None` **硬编码**，调用方无法指定引擎 | [api_server.py](../../../api_server.py) `_run_generate_character` |
| `GenerationResult` 已有 `provider` / `model` 字段，但未写入生成历史，多模型并存后无法复盘 | [base.py](../../../core/image_gen/base.py) |
| `training/` 是**部位分割模型**（9 类 label map）的训练链路，不是 LoRA，也不属于本次生图模型范畴 | `training/README.md` |
| 目标机器显存约 6GB | 用户确认 |

用户已确认的优先级：**角色一致性 + 分层友好**（不追求统一画风）。

## 3. 方案对比

| 方案 | 免费 | 可微调 | 6GB 可行 | 结论 |
|---|---|---|---|---|
| **A. ComfyUI + SDXL 二次元 + LoRA**（Illustrious-XL / NoobAI-XL） | ✅ 开源权重 | ✅ kohya_ss | 推理可行（fp16 + lowvram）；训练紧 | **采纳** |
| B. ComfyUI + SD1.5 二次元 + LoRA | ✅ | ✅ | 训练宽松 | 保留为 6GB 快速迭代备选，同一路线只需换 checkpoint |
| C. 免费云 API（Pollinations / 免费额度） | ✅ | ❌ 无法微调 | — | 不满足「可微调」，仅作无本地引擎时的兜底（已有） |

选 A 的理由：仓库已有 ComfyUI 通道与本地回环限制（`local_http.ensure_local_endpoint`），
接入成本最低；权重免费且 LoRA 完全本地可控，不产生 API 费用。

## 4. 架构与数据流

```
前端选择引擎/模型
      │  provider="comfyui", model="<checkpoint 名>"
      ▼
POST /api/generate/character  ──► WorkflowEngine(provider=..., model=...)
      │
      ▼
ProviderRouter.generate(provider=..., model=...)
      │
      ▼
ComfyUIProvider
  ├─ checkpoint: 显式传入 > COMFYUI_CHECKPOINT > 自动取第一个
  ├─ LoRA（可选）: COMFYUI_LORA + COMFYUI_LORA_STRENGTH(+_CLIP)
  └─ workflow: 内置图（含 LoraLoader）或 COMFYUI_WORKFLOW 自定义
      │
      ▼
GenerationResult(provider, model, ...) ──► 生成历史（记录实际使用者）
```

## 5. 组件改动

### 5.1 `core/image_gen/comfyui.py`

1. 新增配置读取：`COMFYUI_LORA`（文件基名，不含扩展名）、`COMFYUI_LORA_STRENGTH`（默认 1.0）、
   `COMFYUI_LORA_STRENGTH_CLIP`（缺省等于前者）。
2. 新增 `_resolve_lora()`：显式配置优先；未配置时返回空串表示「不注入 LoRA」。
   配置了却查不到文件（`/object_info/LoraLoader` 列表不含它）时**报错而非静默忽略** ——
   静默忽略会让"以为加了 LoRA"的产物无法解释。
3. 内置 workflow：当 LoRA 生效时，在 `CheckpointLoaderSimple` 与采样器之间插入
   `LoraLoader`，并把 `KSampler.model`、两个 `CLIPTextEncode.clip` 改接 LoRA 输出。
   未配置 LoRA 时保持现有节点编号与结构不变（避免影响既有黄金断言）。
4. `_apply_placeholders` 增加 `{{LORA}}` / `{{LORA_STRENGTH}}` / `{{LORA_STRENGTH_CLIP}}`，
   使自定义 workflow 也能用。
5. `generate()` 的返回结果写入实际使用的 checkpoint 与 LoRA（`model` 字段拼成
   `<ckpt>[+<lora>]`，便于历史复盘）。

### 5.2 `core/config.py`

新增 `COMFYUI_LORA` / `COMFYUI_LORA_STRENGTH` / `COMFYUI_LORA_STRENGTH_CLIP` 默认值，
与既有 `COMFYUI_BASE_URL` 同段，保持"环境变量可覆盖"的一致约定。

### 5.3 `api_server.py`

1. `GenerateCharacterRequest` 增加可选 `provider` / `model`（`model` 即 checkpoint 名）。
2. `_run_generate_character` 不再硬编码 `provider=None`，改为透传；为空时仍走优先级自动选择。
3. 生成记录（`_record_generation`）写入实际 `provider` / `model`。
4. 校验：`provider` 必须是已注册名称；`model` 仅在 `comfyui` / `sd_webui` 上生效
   （这两个 provider 的模型是本地文件，可被指名）；在其他 provider 上同时传
   `model` 时返回 400，而不是静默忽略 —— 静默忽略会让用户以为换了模型。

### 5.4 工作台 UI（`web/components/workbench/panels`）

在生成面板增加「引擎 / 模型」下拉，数据来自既有 `/api/image/providers`
（`ProviderRouter.get_provider_info`）。默认值保持"自动"，不改变现有用户路径。

### 5.5 分层友好（不改生图模型）

不是 LoRA 能学会的东西，落到三处既有能力上：

1. 提示词模板：正面站立、四肢与躯干分离、无遮挡、纯净背景（写进 `prompts/image_generation.md`）。
2. `core/segment_engine/` 现有分层链路。
3. `core/segment_engine/layer_quality.py` 的 `REQUIRED_PARTS` 质量门禁。

如需更贴合本项目画风的分层，可复用 `training/` 的 9 类分割训练链路，与本设计解耦。

## 6. 错误处理

- LoRA 未配置 → 正常出图，不报错。
- LoRA 已配置但不存在 → `GenerationError(code="lora_not_found")`，消息里列出可用 LoRA。
- provider 未注册 / model 不被该 provider 支持 → HTTP 400，消息可读。
- ComfyUI 未启动 → 沿用现有 `is_available()` 与 `no_image_engine` 提示。

## 7. 测试策略

沙箱无 GPU，因此用**假 ComfyUI 服务**（本地 HTTP stub）+ 真实 HTTP 会话验证：

1. 未配置 LoRA 时，内置 workflow 的节点集合与现有实现逐字节一致（防回归）。
2. 配置 LoRA 后，workflow 出现 `LoraLoader`，且 `KSampler.model` / `CLIPTextEncode.clip`
   指向 LoRA 节点。
3. `COMFYUI_LORA_STRENGTH` 透传为 float；非法值回退默认并告警。
4. `COMFYUI_LORA` 指向不存在的文件 → 抛 `GenerationError`，不发出采样请求。
5. `/api/generate/character` 的 `provider` / `model` 透传与 400 校验（TestClient）。
6. 生成记录含实际 provider/model。

真实出图与 LoRA 训练需在有 GPU 的机器上执行，不在本设计的自动验证范围。

## 8. 非目标

- 不内置任何模型权重（体积与许可均不适合进仓库）。
- 不实现 kohya_ss 训练调度；训练在仓库外进行，产物 `.safetensors` 放入 ComfyUI 的 `models/loras`。
- 不承诺 LoRA 能改善分层边界或透明 alpha。
- 不改动 `training/` 分割模型链路。

## 9. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 6GB 下 SDXL LoRA 训练易 OOM | 训练无法在本机完成 | 768px + gradient checkpointing + batch=1；仍不行则借 Kaggle 免费 T4 训完拉回 |
| SDXL 在 6GB 推理很慢 | 迭代效率低 | 同时支持 SD1.5 checkpoint，先用 SD1.5 调提示词，定稿再用 SDXL 出图 |
| LoRA 注入改变节点编号 | 破坏既有 workflow 断言 | 未配 LoRA 时完全不改结构，并加逐字节回归测试 |
