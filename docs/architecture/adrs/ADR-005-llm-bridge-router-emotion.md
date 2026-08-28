# ADR-005：LLM 对话网关 — 多模型路由 + 7 类情绪 → 表情 + 物理参数联动

| 字段 | 内容 |
|------|------|
| **状态** | Accepted（v10.0 起） |
| **日期** | 2026-07-30 |
| **决策者** | Live2D Master Agent Team |
| **本地校验** | [`llm_bridge/providers/router.py`](file:///workspace/llm_bridge/providers/router.py)、[`llm_bridge/emotion/analyzer.py`](file:///workspace/llm_bridge/emotion/analyzer.py)、[`llm_bridge/chat_session.py`](file:///workspace/llm_bridge/chat_session.py)、[`llm_bridge/asr/`](file:///workspace/llm_bridge/asr/) / [`tts/`](file:///workspace/llm_bridge/tts/) 多 Provider |

---

## 1. 决策问题

在一个"角色能和用户聊天、有声音、有表情、有动作"的 AI 桌宠系统中：

1. 如何让 LLM（对话大模型）、TTS（文字转语音）、ASR（语音转文字）三者**都支持多 Provider 且可切换**，避免单点依赖（如 OpenAI 挂了系统就死）？
2. LLM 流式吐字的同时，如何把"当前情绪"**实时映射到 Live2D 表情+物理参数**（开心时嘴角上扬 + 头发弹性大；生气时眉毛压低 + 全身抖动）？
3. 如何同时覆盖**零配置免费用户**（不设 API Key）和**高质量付费用户**（有 OpenAI / Claude Key）？

## 2. 上下文与推动力

| 推动力 | 说明（项目事实） |
|--------|------------------|
| F1 · 用户从纯小白到进阶长尾 | `.env.example` 有 OpenAI Key / Volcengine Key / SenseNova Key；但 README 明确写了"默认 Pollinations 免费生成，无需配置"。**系统不可强迫用户配 Key**。 |
| F2 · 对话是桌宠体验的核心情绪来源 | README 核心功能 §6 写了"7 类情绪 → 表情 + 肢体参数联动"+"语音指令"。若 LLM 输出只有文字没有情绪映射，用户会感觉角色是"冰冷的机器人"。 |
| F3 · 流式对话对延迟极度敏感 | LLM 首 token 延迟 + TTS 首包延迟 > 2s 就会让用户觉得"AI 在发呆"。必须流水线化：**首 token 到 → 情绪快速打标 → 表情同步 → 首段 TTS 同时播放**。 |
| F4 · Provider 多样性 = 容错 + 成本控制 | 免费 TTS（Edge TTS，见 `requirements.txt #L38 edge-tts>=6.1.0`）已跑通，与付费 OpenAI TTS 并存；ASR 同时有 `whisper_local` 与 `funasr_provider`。这套多 Provider 模式必须扩展到 LLM。 |

## 3. 备选方案对比

### 3.1 多 Provider 路由架构

| 方案 | 零配置可用 | 容错 | 延迟 | 维护成本 | 结论 |
|------|------------|------|------|----------|------|
| A · 只接 OpenAI GPT-4o | ❌ 强制 Key | ❌ 单点故障 | 优 | 低 | ❌ F1（零配置）+ F4（容错）不满足。 |
| B · 前端散调用 | ❌ Key 泄漏风险 | ❌ 前端切换复杂 | 好 | 高 | ❌ 安全红线：API Key 不能出现在前端。 |
| **✅ C · LLM Bridge 路由层（当前）** | ✅ 有 `local_provider.py` + `Ollama` 本地；无 Key 时退化为仅角色卡描述 | ✅ 5 层降级：Key 配置的 LLM → Ollama → 角色卡话术模板 | ✅ 流式逐字透传，Router 只加 <1ms | 中（每个 Provider 一个派生类）| ✅ F1~F4 全命中。 |

### 3.2 情绪 → 表情联动策略

| 方案 | 精度 | 延迟 | 可解释性 | 结论 |
|------|------|------|----------|------|
| A · 每条回复跑完再做情绪分类 | 高（整段文本理解） | ❌ 差：等整段回复 > 3~5s | 好 | ❌ F3 不满足，用户看到文字先跳出来表情僵住。 |
| B · LLM 同时输出 JSON（内容+情绪+动作） | 最高（模型自己打标） | 中（依赖模型按 schema 输出） | 最好 | ⚠️ 强依赖 GPT-4 级模型遵循 JSON；免费/Ollama 小模型经常输出错 schema；不可作为唯一方案。 |
| **✅ C · 双通道：首窗口快速情绪 + 整段确认**（当前实现） | 中上 | **首 token 300ms 内有表情** | 好 | ✅ 首 3 词到了就用 `emotion/analyzer.py` 轻量分类器（关键词 + 情感词典）打标；整段输出完了再跑一次精细分类；不一致则平滑过渡。F3 达标 + 精度可接受。 |

## 4. 决策

### 4.1 LLM Bridge 路由层（多 Provider 可插拔 + 5 级降级）

**统一接口抽象**在 `llm_bridge/providers/base.py::BaseLLMProvider`：
- `complete(prompt, stream=True) -> Iterator[Token]`
- `async acomplete(prompt) -> str`

**具体 Providers（按优先级，可在 `.env` 切换）：**
1. `openai_provider`（首选：OpenAI GPT-4o / 兼容如 DeepSeek / Qwen 云端）
2. `anthropic_provider`（次选：Claude 3）
3. `local_provider`（Ollama：默认 `qwen2.5:3b` 本地完全免费，无 Key）
4. **零 Key 降级**：角色卡人设 + 规则模板（硬编码话术），保证不报错
5. **错误兜底**：任何 Provider 失败，切到下一级

**TTS/ASR 亦同理：**
- TTS：`edge_tts`（免费中文晓晓/日语 Nanami/英文 Aria）↔ `openai_tts`
- ASR：`funasr_provider`（阿里 FunASR 本地中文强项）↔ `whisper_local`（本地通用）

### 4.2 7 类情绪 → Live2D 参数映射表

| 7 类情绪 | 触发关键词示例 | 表情联动（Param 名字） | 物理/动作联动 |
|----------|---------------|----------------------|----------------|
| 😄 Happy（开心） | 哈哈/好棒/谢谢 | `ParamMouthSmile=0.8` `ParamEyeSmile=0.6` `ParamBrowForm=0.3` | 呼吸频率 +30%、头发弹性 k↑ |
| 😠 Angry（生气） | 笨蛋/生气/不行 | `ParamBrowForm=-1.0` `ParamEyeLOpen=1.1` `ParamMouthForm=0.7` | 全身振动 ±3px、物理阻尼↓ |
| 😢 Sad（悲伤） | 难过/遗憾/哭 | `ParamEyeLOpen=0.6` `ParamBrowForm=-0.3` `ParamMouthForm=-0.5` | 呼吸慢 -40%、头发几乎不动 |
| 😲 Surprise（惊讶） | 啊！/怎么可能 | `ParamEyeLOpen=1.2` `ParamMouthOpenY=0.8` `ParamBrowY=-0.8` | 头部快速 `ParamAngleY=+5°` 回弹 |
| 😨 Fear（恐惧） | 危险/怕/吓 | `ParamEyeLOpen=0.9` `ParamBrowForm=-0.6` | 静止不动（物理全部静止）|
| 😊 Neutral（默认） | （无情绪词） | 全部归默认值 | 正常呼吸/物理 |
| 💕 Love（喜爱） | 喜欢/爱你/可爱 | `ParamEyeSmile=0.9` `ParamCheek=0.8` `ParamMouthSmile=0.5` | 左右小幅摆头、尾巴摇（若有） |

> 实现：`llm_bridge/emotion/analyzer.py::EmotionAnalyzer` 输出 `(emotion, confidence)`，`chat_session.py` 将其转为 28 参数 dict，走 WS → Go → Web 或桌宠运行时。

### 4.3 流式流水线（关键）

```
ASR 用户语音转文字（0~400ms）
     │
     ▼ LLM 首 token 到达（~300~1200ms）
     ├── 首 3 词 → EmotionAnalyzer.quick()  → 首表情立即推送
     ├── TTS.queue 首句 → Edge TTS 首包（~300ms） → 扬声器
     │     同时 RMS 兜底嘴型
     ▼ LLM 逐字输出
           ▼ EmotionAnalyzer 每 10 词增量打标
           ▼ 参数 EMA 平滑（避免表情切太猛）
     ▼ 整段回复结束 → EmotionAnalyzer.fine() 确认最终表情
           （与 quick 不一致则 300ms 渐变）
```

## 5. 后果

### 正面 ✅
- **零 Key 也能用**：完全符合 README "无需配置即可跑聊天"的承诺。Ollama 本地 3B 模型虽不聪明，但能完成基本对话+情绪。
- **抗 Provider 故障**：5 级降级链确保用户不会遇到"聊天完全挂了"。
- **体验不僵**：首 token 300ms 内表情先动，消除"文字在跳但角色脸僵"的违和感。
- **角色一致性**：`core/character/card.py` 人设卡注入到所有 Provider 的 system prompt，保证角色语气/世界观稳定。
- **成本可控**：小白用户默认走 Edge TTS（免费）+ Ollama（本地），零支出；高质量用户走自己的 Key。

### 负面 / 缓解 ⚠️
- **快速情绪打标偶尔错**（首 3 词信息不足）→ 缓解：整段跑 fine 分类器，若差异大则 300ms 渐变过渡而不是突变；信心 < 0.45 的直接归 Neutral。
- **多 Provider 维护成本** → 缓解：Base 接口收敛成 2 个方法（见 §4.1），新增 Provider 只需复制一份派生类模板（`openai_provider.py` 30 行）；每月 CI 跑 smoke test 查接口一致性。
- **Edge TTS 依赖微软服务器**（海外访问）→ 缓解：TTS 层有 5s timeout，超了自动切到本地 pyttsx3 兜底；README 注明中国大陆用户可选"本地 TTS"。

## 6. 可逆性

| 项 | 说明 |
|----|------|
| **撤销成本** | **中等**。Router 抽象本身是轻量的，删除整层只需把 Provider 写死；但情绪→表情映射已被 VTube Studio OSC 通道、桌宠窗口、Web 预览三处消费，移除需改三处。 |
| **重新考虑触发条件** | ① 有一个特别强的 LLM 官方 SDK（如字节豆包、阿里通义千问）需要接入且用户要求 >50%；② 项目决定把 LLM 对话能力独立成单独微服务（届时 Router 变为 gRPC 调用）。 |
| **责任方** | LLM 网关负责人。 |

## 7. Fitness Functions

| 属性 | 度量 | 阈值 | 来源 | 频率 | 失败响应 |
|------|------|------|------|------|----------|
| 首 token → 表情出现延迟 | 本地 Ollama 下，首 token 到 WS 推送表情参数 WS 帧到达时间差 | ≤ 300ms | `llm_bridge/tests/` 人工 fixture + 计时 | 每改 chat_session | 优化 quick() 分类器 |
| Provider 切换成功率 | 模拟 OpenAI 返回 503/429，切到下一级成功率 | 100% | `tests/integration/test_llm_router_fallback.py`（待补） | 每次 PR | 修复降级链 |
| 零 Key 用户可用性 | 环境变量全空，启动 + 跑 10 轮对话无 Exception | 100% | CI 空 env 专项 | 每版 | 修复默认降级路径 |
| 情绪分类 F1（标准数据集） | 中文 7 类情绪 2000 条评测集 | ≥ 0.68（轻量分类器基线） | `llm_bridge/emotion/tests/fixtures_*.jsonl` | 每改 analyzer | 优化词典/规则 |
| 表情参数 EMA 平滑 | 同一段回复，参数突变（Δ > 0.5 / 100ms）次数 | ≤ 1 次 / 10 句 | 离线播放抓包 | 每版 | 降低平滑 α / 增加时间窗口 |

## 8. 证据与校验点

- ✅ `llm_bridge/providers/` 已有 `openai_provider.py`、`anthropic_provider.py`、`local_provider.py`、`router.py`
- ✅ `llm_bridge/tts/edge_tts.py`、`openai_tts.py` 双 Provider
- ✅ `llm_bridge/asr/funasr_provider.py`、`whisper_local.py` 双 Provider
- ✅ `llm_bridge/emotion/analyzer.py` 有分类器与 confidence 阈值概念
- ✅ `llm_bridge/chat_session.py` 集成全部
- ⏳ 待补：fallback 集成测试 / 情绪分类 fixture / EMA 平滑测试
