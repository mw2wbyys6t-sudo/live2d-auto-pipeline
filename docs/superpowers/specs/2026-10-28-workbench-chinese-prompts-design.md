# 工作台形态改造 + 中文提示词友好 — 设计规格

- 日期：2026-10-28
- 状态：待实现（本文件不提交，随正式版本一并入库）
- 目标版本：v0.10.10

## 1. 问题（现场取证的现状）

### 1.1 不是工作台：九个独立页面，状态各管各的

- `web/pages/` 下 `generate / layers / live2d / preview / export` 是各自独立的路由，各自
  `useState` 拉数据；页面之间唯一的交接是 `export?layers_dir=`（`web/pages/export.tsx:70`）
  与后端 `latest_generation.json` 的「最近一次」记录。
- 生成完成后要去分层、再去绑定、再回预览，必须跳页；分层页重新上传图片才可能继续，
  中间状态（当前角色、源图、图层、模型）在跳页时丢失。
- 侧栏品牌仍写着 `Live2D Master / Agent Workbench`（`web/components/Sidebar.tsx:64-65`），
  `_app.tsx:22` 的兜底标题也是 `Live2D Master`，与 Pygmalion Studio 品牌不一致。
- 顶栏标题是「页面名」，不是「当前角色 + 当前阶段」；工作台该有的「我在做什么」信息缺失。

### 1.2 中文提示词：能输入，但会被悄悄改写

| 位置 | 现状 | 后果 |
| --- | --- | --- |
| `api/services/image_generator.go:152-155`、`305-309` | `len(prompt)` 按**字节**截断、`prompt[:4000]` 按**字节**切 | 4000 字节约 1333 个汉字；切点落在汉字/emoji 中间会产生半个 UTF-8 序列 |
| `core/security.py:224-242` | 删除 `\n \r`（词被粘连），再用 ASCII 子串黑名单把 `sh `、`del `、`format ` 等替换成下划线 | `English style` → `Engli____style`；`model girl` → `mo___girl`；提示词被静默篡改 |
| `core/image_gen/seedream.py:238-246`、`sensenova.py:160-166`、`pollinations.py:136-143`、`openai_compat.py:93-94` | 无条件追加英文风格标签/`Avoid:` | 中文提示词后挂一长串英文；用户看不出实际发了什么 |
| `core/character/card.py:315-365` | 一致性后缀直接用 `#3a2a1a hair` 这类 **hex 色值** 和英文枚举 | 图像模型读不懂 hex；中文用户拿到的是一串英文 |
| `web/pages/generate.tsx:168`、`180-187` | 只有正向提示词有 2000 计数与截断，负向没有；占位示例全英文 | 中文用户没有可参照的写法 |
| `web/pages/chat.tsx:166-171` | 回车直接发送，未判断 `isComposing` | 中文输入法选词回车触发发送 |
| `web/pages/chat.tsx:190` | `rec.lang = 'en-US'` | 语音输入只认英文 |
| `web/pages/_document.tsx:6` | `<Html lang="en">`；`globals.css:34` 无中文字体栈 | 中文排版与读屏语言都按英文处理 |

### 1.3 已确认的产品形态（用户选择）

单页三栏工作台：左栏「角色/作品」、中栏大画布按阶段切换（生成 → 分层 → 预览）、
右栏检查器（提示词/参数/图层/导出）、顶部阶段进度；所有步骤共享同一个当前角色与产物，
不跳页、不丢状态。

## 2. 目标与非目标

**目标**

1. 打开软件就是工作台：一个页面内完成「生成 → 分层 → 绑定/参数 → 预览 → 导出」。
2. 当前角色与产物在阶段间共享；刷新/重启后可恢复（本地持久化 + 后端记录）。
3. 中文提示词「输入即用」：不被改写、不被按字节截断、界面全中文、输入法安全，
   并且能看见**实际发送给模型**的提示词。
4. 只写中文也能得到质量标签：提供离线双语标签库与模板，用户选中文，提示词带英文 tag。

**非目标**

- 不新增绘画模型、不改 provider 优先级（seedream > sensenova > openai > pollinations）。
- 不做在线账号/云同步；作品历史只在本机。
- 不删除 `/chat`、`/health`、`/characters` 路由（工作台之外的独立工具）。

## 3. 界面设计

### 3.1 布局

```
┌───────────────────────────────────────────────────────────────┐
│ 品牌 · 当前角色名   [生成 ✓][分层 ✓][绑定 ·][预览 ·]   连接 · 设置 · 对话 │
├──────────┬──────────────────────────────────┬─────────────────┤
│ 角色      │                                  │ 检查器 tabs      │
│ 搜索/新建 │                                  │ 提示词 参数      │
│ · 小雪    │        中栏画布（按阶段切换）      │ 图层  导出       │
│ · 阿岚    │                                  │                 │
│          │                                  │  ……             │
│ 作品      │                                  │                 │
│ [图][图]  │                                  │                 │
├──────────┴──────────────────────────────────┴─────────────────┤
│ 状态条：provider · 分辨率 · 耗时            [生成] / [停止]      │
└───────────────────────────────────────────────────────────────┘
```

- 中栏阶段：`生成`（结果大图/空态/实时进度）、`分层`（合成预览 + 显示模式）、
  `预览`（Live2D 播放；子模式「参数编辑」/「实时驱动」承载原 live2d 与 preview 两个页面）。
- 右栏检查器：`提示词`（正向/负向/标签库/模板/实际发送预览）、`参数`（分辨率、
  角色一致性、种子、分层方法、Live2D 参数滑块）、`图层`（可见性/不透明度/混合/排序/
  重新分层）、`导出`（Live2D 包、PSD 方案、导出进度、桌宠部署）。
- 阶段条可点击跳转，但**不换页**；未满足前置条件时给出中文原因并置灰。

### 3.2 状态模型（`WorkbenchContext`）

```ts
interface WorkbenchState {
  characterId?: string;          // 当前角色
  stage: 'generate' | 'layers' | 'preview';
  inspectorTab: 'prompt' | 'params' | 'layers' | 'export';
  prompt: string;                 // 中文优先
  negativePrompt: string;
  resolution: 512 | 768 | 1024 | 2048;
  consistency: boolean;
  seed?: number;
  segmentation: 'semantic' | 'kmeans';
  artifact?: {                    // 当前作品产物
    imageUrl: string;
    layersDir?: string;
    model3Url?: string;
    outputDir?: string;
    effectivePrompt?: string;
    promptSent?: string;
    createdAt: string;
  };
  layers: LayerInfo[];
  busy: { generate: boolean; segment: boolean; export: boolean };
  steps: GenerationStep[];
}
```

- 持久化：`localStorage['pygmalion.workbench.v1']` 存 UI 状态与最近产物（不含大二进制）；
  启动时若本地无产物，回落到 `GET /api/generations/latest`。
- 作品列表来自新增 `GET /api/generations`（见 4.2），点击作品即恢复 `artifact` 与 `layersDir`，
  中栏切到对应阶段。

### 3.3 路由

- `/` → 工作台（新 `pages/index.tsx`）。
- `/generate` `/layers` `/live2d` `/preview` `/export` → 客户端 `router.replace('/?stage=…&tab=…')`，
  保留旧书签与手册里的链接（静态导出下不能用 `next.config` 的 redirects）。
- `/characters`、`/characters/[id]`、`/chat`、`/health` 保持独立页面；左栏角色项的
  「编辑」进入 `/characters/[id]`。

## 4. 中文提示词链路（后端契约变更）

### 4.1 不改写、不切坏

- **Go** `api/services/image_generator.go` 两处截断改为按 rune：`[]rune(prompt)` 取前 4000，
  并保证前导 `-` 处理不变。新增测试：4000 个汉字全保留、4001 个汉字截到 4000、
  含 emoji 的提示词不产生半个代理对。
- **Python** `core/security.py::sanitize_prompt`：
  - 删除 ASCII 子串黑名单改写（提示词只进 argv 与 JSON，从不进 shell；argv 注入由
    Go 侧 `--`/前导空格处理，JSON 由 `json.dumps` 转义）。
  - 只做：去控制字符（保留 `\n` `\t`，把 `\r\n` 归一为 `\n`）、按**码点**限长 4000、`strip()`。
  - 更新 `tests/unit/test_utils.py` 与安全测试断言：中文/英文原样通过，`rm -rf` 之类
    作为普通文本保留；文件名校验不变。
- **前端** 正向/负向都按码点计数与截断（`[...str].length`），上限 4000，与后端一致。

### 4.2 实际发送可见

- `core/image_gen/base.py::GenerationResult.prompt` 已存在，但 sensenova/seedream 记录的是
  原始提示词、openai_compat 记录空。改为记录**真正发给上游**的字符串（含风格标签与 `Avoid:`）。
- workflow 结果新增：

  ```json
  "prompt": {"user": "…", "effective": "…", "sent": "…", "negative": "…", "provider": "seedream"}
  ```

- Go `GenerateImageResponse` 新增 `effective_prompt`、`prompt_sent`；写入
  `latest_generation.json` 与作品历史。右栏「提示词」tab 展示这串内容（只读、可复制）。

### 4.3 风格标签按语言配对

- 新增 `core/image_gen/prompt_lang.py`：
  - `has_cjk(text) -> bool`（含 U+3000–U+9FFF 等常用中日韩区段）。
  - `ANIME_TAGS = {"zh": "…", "en": "…"}`：Live2D 质量/构图标签的中英两套。
  - `live2d_tags(lang) -> str`。
- seedream / sensenova：提示词含中文 → 追加中文标签；纯英文 → 追加英文标签。
- pollinations / openai_compat：上游是 SD/Flux 系，固定英文标签；中文提示词时在结果中标记
  `prompt_lang="zh"` 且 `prompt_mismatch=true`，前端在「实际发送」旁给出中文提示
  （「该模型以英文为主，建议用『译为英文』」），**不静默**。
- 负向提示：pollinations 的内置英文负向只在提示词为纯英文时使用；中文提示词时使用中文负向标签集。

### 4.4 角色一致性后缀

- `CharacterCard.generate_style_prompt(lang="en")` / `generate_negative_prompt(lang="en")`。
- `lang="zh"` 时用中文词表输出；枚举（脸型/发型/体型/眉形/眼形/鼻/嘴）映射为中文；
  hex 色值经 `core/character/colors.py::describe_color(hex)` 转为「深棕 / 浅金 / 天蓝」这类
  自然语言（同一函数提供英文名）。**修复现有 hex 直出**（`#3a2a1a hair` 模型读不懂）。
- 用户在卡片里手写的中文（`style_constraints`、名字、服装描述）原样保留。

### 4.5 中文提示词工作台（前端）

- 离线双语标签库 `web/lib/prompt-lexicon.ts`：类别 = 构图 / 发型 / 发色 / 瞳色 / 服装 / 表情 /
  画风 / 光照；每项 `{ zh, en, apply: 'both' }`，点击插入 `中文（英文 tag）` 形式。
- 模板：立绘、半身像、表情差分、换装、节日装扮（每条给出完整中文示例）。
- 占位示例改为中文：`正面全身立绘，粉色双马尾，蓝色眼睛，水手服，柔和光线，简洁白色背景`。
- 输入法安全：`lib/ime.ts::isComposingEnter(e)`，聊天与工作台共用；`Ctrl/⌘+Enter` 触发生成。
- 可选「译为英文 / 扩写」：`POST /api/prompt/translate`（Go）。复用 `ChatService` 的非流式
  调用；未配置 LLM 时返回 `501` + 中文原因，前端按钮置灰并显示原因。返回的文本进入可编辑的
  输入框，不自动覆盖用户原文。

## 5. 作品历史（左栏数据源）

- `api/services/latest_generation.go`：`RecordLatestGeneration` 在写 `latest_generation.json`
  的同时，向 `generations_history.json` 追加一条（按 `output_dir` 去重，最多 50 条，
  临时文件 + 原子重命名）。记录新增 `prompt`、`effective_prompt`。
- 新增 `GET /api/generations?limit=30` → `{ items: [ { …latest record 字段, image_url,
  model3_url, created_at } ] }`，复用现有 `enrichLatestGeneration` 的派生字段逻辑。
- 测试：追加、去重、上限、原子写、空记录跳过；handler 测试覆盖 limit 边界。

## 6. 实施顺序

1. **后端 · 中文链路**（独立可测）：Go rune 截断 → Python sanitize → `prompt_lang` 与
   provider 记录实际提示词 → 卡片中文后缀与颜色描述 → 单测。
2. **后端 · 作品历史**：历史文件 + `GET /api/generations` + 响应字段 + 单测。
3. **前端 · 骨架**：`WorkbenchContext`、三栏布局、阶段条、持久化、旧路由重定向、
   Sidebar 品牌修正、中文文案与字体、IME 工具。
4. **前端 · 面板迁移**：生成 → 分层 → 预览/参数 → 导出；每个面板接 Context，
   保留原有能力（上传、WS 进度、导出轮询、桌宠部署）。
5. **中文提示词体验**：标签库、模板、实际发送预览、负向计数、翻译接口接线。
6. **验收**：`pytest`、`go test ./...`、`npm run build`（standalone + 静态导出）、
   浏览器实跑（中文提示词走完生成→分层→导出；IME 合成期回车不发送；刷新后状态恢复）。

## 7. 验证判据

- 中文提示词 4000 字：后端收到完整 4000 个汉字，`effective_prompt` 与用户输入的前 4000 字一致
  （不含标签时），无 `�`。
- `English style, model girl` 经 sanitize 后与输入逐字节相同。
- 同一中文提示词对 seedream 发送中文标签、对 pollinations 发送英文标签，且响应里能读出实际字符串。
- 工作台：生成一次后不跳页完成分层、参数、预览、导出；刷新页面后当前角色与产物仍在。
- 界面无英文按钮/标签（`/chat`、`/characters` 之外的工作台区域），`<html lang="zh-CN">`。
