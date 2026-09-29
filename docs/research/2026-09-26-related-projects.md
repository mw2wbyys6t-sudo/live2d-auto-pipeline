# 相关开源项目调研（2026-09-26）

> 目的：针对链路五大痛点（生成质量/分层导入/导出/预览/桌宠部署）寻找可借鉴的开源实现。
> 结论按"可直接借鉴的动作"组织，供后续迭代引用。

## 0. psd2live（PSD → 自动绑定 → .cmo3/.moc3，★ 重点学习对象）

- 仓库：https://github.com/tsunehimatoi/psd2live （GPL-3.0，~461 stars，Java/JDK21）
- 做了什么：分层 PSD → **自动绑定**（网格生成、头/身变形器、眼嘴开合与视线参数、
  基础动作、头发/眼睛物理）→ 导出 **.cmo3（Cubism 编辑器工程）+ .moc3 + model3.json**；
  另有单文件工程格式（含分支历史/撤销重做）与画布编辑 UI。
- **与本仓库的对应关系**：它的管线 = 我们的 `live2d_builder`（自研 moc3 编译器）+
  `core/psd` + `templates/`，且它补上了我们最大的缺口 —— **变形器的参数绑定**
  （rotation deformer 目前只产出静态姿态、眼球跟随不会动，见 LIMITATIONS §3）。
- ⚠️ **许可注意：GPL-3.0 与本仓库 Apache-2.0 不兼容，只可学习思路，禁止复制代码。**
- 已落地的借鉴（v0.10.1 当日实现）：
  1. **图层名识别部件**（多语言：英/中/日；左右配对；口型 a/i/u/e/o 素材识别）——
     新增 `core/psd/part_naming.py`，接入 `POST /api/import/psd`：
     每个导入图层附带 `part`/`side`/`mouth_vowel`/`part_name`，为参数绑定提供稳定目标；
  2. `templates/psd_structure.md` 增补其推荐的 PSD 分层规范
     （眼白/虹膜/上睫毛分离、口型 a-i-u-e-o 素材、前后发分离、身体直立）。
- 后续可继续借鉴（按价值排序）：
  1. 其变形器/参数绑定的**规则结构**（头/身变形器层级、XY 轴键形如何分配到部件）——
     直接服务于 moc3 rotation 绑定攻坚；
  2. **.cmo3 导出**：能把工程交回 Cubism Editor 手工精修，"全自动不完美 + 可编辑兜底"
     的产品思路值得复制（需逆向其 cmo3 写出方式，注意许可）；
  3. **MCP server（11 个工具）**：让 AI 代理观察/编辑模型、管理物理与历史 ——
     与本仓库的 Agent Workbench 定位天然契合，可作为 `live2d_builder` 的代理接口蓝图；
  4. STATUS.md（成功/失败用例日志）+ ROADMAP.md 的诚实工程文化，与本仓库
     LIMITATIONS.md 同路，可互相参照粒度。

## 1. See-through（单图 → 23 层 Live2D PSD）

- 仓库：GitHub 搜索 `See-through Layer Decomposition Anime`（论文 arXiv: "Single-image Layer
  Decomposition for Anime Characters"，配套数据集仓库 CubismPartExtr）。
- 做了什么：单张二次元立绘 → 自动拆成**语义固定的 23 层**（前发/后发/眉/眼/鼻/嘴/躯干/四肢…），
  每层带遮挡补全（amodal completion），直接输出可绑定 PSD。
- 对本项目的借鉴：
  1. `core/segment_engine/layers52.py` / `part_identifier.py` 可对照其固定层分类法收敛层名，
     保证下游 moc3 参数绑定层名稳定；
  2. 其 amodal 思路对应本仓库 `core/segment_engine/amodal.py` 的 pix2gestalt 桩 —— 短期可借鉴
     其"按语义部件分别 inpaint"的工程化替代，而不是等一个通用 amodal 模型；
  3. 数据集准备流程（CubismPartExtr）可用于将来微调自研分割头。

## 2. Open-LLM-VTuber（本地语音陪伴 + Live2D + 桌宠模式）

- 仓库：https://github.com/Open-LLM-VTuber/Open-LLM-VTuber （文档 open-llm-vtuber.github.io）
- 做了什么：全本地运行的语音交互 AI VTuber；v1.0.1 加了 **Desktop Pet Mode（透明、可点击穿透）**，
  v1.2.0 修了穿透点击 bug 并加了 Browser Live View。
- 对本项目的借鉴：
  1. 桌宠窗口的点击穿透与置顶细节（`drivers/desktop_pet/native_window.py` 是色键透明，
     它们用分层窗口 API 实现逐像素 alpha + 穿透，值得对照）；
  2. "Browser Live View"：浏览器里也能看到桌宠画面 —— 与本项目 /preview 的定位一致，
     可参考其前后端解耦方式；
  3. LLM/ASR/TTS 全部可插拔的配置化思路（本项目 llm_bridge 已类似，可对照补齐 provider 热切换）。

## 3. Project AIRI（moeru-ai，Web 技术栈的 AI 虚拟形象）

- 站点：https://airi.moeru.ai ，GitHub moeru-ai/airi（WebGPU/WebAudio/Web Workers）。
- 对本项目的借鉴：**浏览器端渲染与推理**是它的核心架构 —— 本项目已把面捕迁到浏览器端
  MediaPipe WASM（web/lib/face-tracker.ts），下一步预览渲染也可以考虑 WebGPU 化的 pixi-live2d
  路线，彻底摆脱对原生渲染的依赖。

## 4. live2d-py（Cubism SDK 的 Python 绑定）

- 已是本项目可选依赖（官方内核逐像素验收、原生桌宠窗口都用它）。
- 注意：官方内核验收链路需要 `pip install live2d-py` + `pip install pygame-ce`
  （Python ≥3.14 用 pygame-ce）；缺失时的报错现已带上确切安装命令
  （drivers/live2d_runtime/moc3_load.py / moc3_consistency.py / moc3_physics_probe.py）。

## 5. 社区共识管线（多来源交叉）

当前社区主流组合是：**AI 生成立绘（SD / Seedream / Gemini 类）→ AI 自动拆层（See-through 或
SAM2 交互式）→ Cubism 绑定（手动或半自动）**。"一张图全自动出 Live2D"的完整端到端开源项目仍然
稀缺 —— 本仓库的自研 moc3 编译器 + 官方内核验收是差异化优势，值得继续投入。

## 本次调研对应的落地改动（v0.10.1 已实现）

1. 生成上游接入 `openai` provider（core/image_gen/openai_compat.py）：任何 OpenAI Images API
   兼容端点（官方/one-api 网关/本地自建）均可接入，带 SSRF 边界校验；
   `GET /api/providers` 暴露各上游配置状态，生成页展示可用徽标。
2. 分层页新增外部素材导入：`POST /api/import/psd`（psd-tools 逐层导出整幅画布 PNG）、
   `POST /api/import/pngs`（多 PNG 统一规范到最大画布，避免导出期"画布尺寸不一致"拒绝）。
3. 导出成功后把模型产物并入 `latest_generation.json`（RecordLatestExport），预览页自动加载、
   桌宠部署 model_dir 立即可用 —— 打通"导出 → 预览/桌宠"交接。
4. 预览页新增模型选择列表（`GET /api/generations/models`）与重新载入。
5. 官方内核验收相关报错全部附带确切的 pip 安装命令。
