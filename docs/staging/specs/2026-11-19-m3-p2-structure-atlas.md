# M3：P2 素材结构保留与图集/导出一致性验收

date: 2026-11-19
milestone: M3（见 docs/ROADMAP.md；父规格 2026-11-17-rig-ready-layering.md）
status: 已批准（延续用户「按路线图推进」的指示）

## 目标

补齐 M3 两个可验收的缺口：
1. **P2 输入素材结构保留**：PSD 输入的组父子关系、可见性、层名、画布偏移不再被扁平化
   丢失；识别「互斥可见的替代组」为变体（表情/手势/服装），并明确记录；
   单张 PNG 输入显式声明 N/A，不伪造变体（INV-6）。
2. **图集坐标/导出一致性**：把「UV 矩形 ↔ 图集像素 ↔ 画布 bbox ↔ 模型顶点 UV」
   四者的一致性做成可复跑的校验器，而不是只靠肉眼。

## 范围

in_scope:
- `PSDParser` 输出补齐 `parent` / `group_path` / `is_group` / `index_in_parent`，
  并保留既有 `visible` / `offset` / `size` / `opacity` / `index`。
- 新增 `detect_variants(psd_meta)`：把「同组内可见性互斥、或组名带变体语义标记」的
  组/层识别为变体集合，输出稳定结构；不改变任何像素。
- 单张 PNG 输入路径显式返回 `variants: []` + `variants_status: "not_applicable"`。
- 新增 `live2d_builder/exporter/atlas_consistency.py`：
  - `verify_atlas_layout(pack_result)`：UV ∈ [0,1]、`u/v` 与 `top_left/bottom_right`
    互相一致、`bbox` 尺寸与 UV 像素尺寸一致、同页矩形不重叠；
  - `verify_atlas_pixels(pack_result, layers)`：按 UV 矩形回采图集，与图层裁剪区逐字节相等；
  - `verify_model_uvs(model_json, atlas_uvs)`：模型里每个顶点的 uv 必须等于
    `uv_for_vertex(rect, 顶点画布坐标)`（容差 1e-6）。

out_of_scope:
- 不实现 PSD 写入端的变体导出（只做识别与保留声明）。
- 不改变图集打包算法（skyline/shelf 现状不动）。
- 不做 GPU/官方 Core 渲染级验收。

## 决定

contract:
- `PSDParser.parse()` 返回结构向后兼容：既有键全部保留，仅新增键。
- 变体结构：`{"name": str, "group_path": str, "kind": "expression"|"gesture"|"outfit"|"unknown",
  "members": [layer_name, ...], "visible_members": [layer_name, ...]}`。
- 校验器统一返回 `{"available": bool, "ok": bool|None, "errors": [...], "checked": int}`；
  依赖缺失或输入不完整时 `ok=None`（未验证），绝不洗成通过。

invariant:
- INV-M3-1 结构保真：每个非根图层都必须有 `group_path`，且其各级父组都在输出里出现。
- INV-M3-2 不伪造变体：PNG 输入不得出现任何 variant 条目；PSD 输入的 variant
  只能来自真实可见性证据或组名语义，不得凭空生成。
- INV-M3-3 坐标一致：任一层 `u0*page_w == top_left[0]`、`u1*page_w == top_left[0]+bbox宽`，
  且回采像素与图层裁剪区逐字节相等。

test:
- `tests/unit/test_psd_structure.py`：父子路径完整、可见性保留、互斥组识别为变体、
  PNG 输入 N/A、无伪变体。
- `tests/unit/test_atlas_consistency.py`：正常打包通过；构造重叠/越界/bbox 不一致/
  顶点 UV 篡改四类缺陷各被捕获；缺依赖时 `ok is None`。
- 全量 `pytest tests/unit` 无回归。

failure_modes:
- psd-tools 缺失：`parse()` 走 header-only，变体检测返回空并标 `variants_status="unavailable"`。
- 图集单页放不下而分页：校验按页分组进行，不跨页比较重叠。
- 图层被 LANCZOS 重采样（超尺寸缩放）：像素逐字节校验跳过该层并记入 errors 的
  显式说明，不静默通过。

deferred:
- PSD 变体组的实际导出与切换。
- 混合模式/剪贴蒙版的结构保真（父规格已知限制）。

## Working notes

- 现状：`core/psd/parser.py:85` 用 `psd.descendants()` 遍历，只记 `kind`，父子关系丢失；
  变体重命名/互斥组无任何识别。
- 现状：`texture_atlas.pack()` 产出 `uvs[name]` 含 `u0/v0/u1/v1/bbox`，但没有任何校验；
  `model3_exporter.py:537-542` 用 `uv_for_vertex` 生成顶点 uv，也未与图集对账。
- `core/psd/validator.py` 已有 PSD 重读 / 画布 / 组序 / 可见性校验，M3 直接复用其口径，
  不重复实现。

## 实施记录（2026-11-19）

已落地：
- `core/psd/parser.py`：改用手工递归遍历，`layers` 新增
  `parent` / `group_path` / `depth` / `is_group` / `index_in_parent`；
  新增 `detect_variants()` 与 `png_input_meta()`，`parse()` 追加
  `variants` / `variants_status`（`ok` / `unavailable` / `not_applicable`）。
- `live2d_builder/exporter/atlas_consistency.py`：`verify_atlas_layout` /
  `verify_atlas_pixels` / `verify_model_uvs` 三个校验器，统一返回
  `{available, ok|None, errors, checked}`。
- 测试：`tests/unit/test_psd_structure.py`（12 项：假树验证结构/变体判据 + 真实 PSD
  往返 + 安全校验三条回归）、`tests/unit/test_atlas_consistency.py`（11 项：正例 +
  六类篡改负例）。

刻意未接线：
- 未把校验器塞进 `model3_exporter.py` 的导出主流程。该文件当前有其他人未提交的改动，
  贸然插入既会制造冲突，也会把「新增校验」和「导出行为变更」混在一起。校验器保持
  独立可调用，接入留到该文件本轮改动落地之后。

## 过程中发现的真实缺陷（已修）

`core/security.py::validate_psd_file` 直接复用 `validate_image_path`，而后者按
**图片**扩展名白名单（png/jpg/webp/gif/bmp/tiff）校验，白名单里没有 `.psd`/`.psb`。
结果是：**任何 PSD 都会在安全校验阶段被判「不支持的图片格式」**，`PSDParser.parse()`
永远走不到解析逻辑 —— 即 M3 要保留的 PSD 结构在这条路径上从来没被真正读过。
（注：`api_server.py::import_psd` 走的是另一条直接调 psd-tools 的路径，未受影响；
但任何经由 `PSDParser` 的 PSD 结构审计都失败了。）

修复：`validate_psd_file` 改为自行校验 `.psd`/`.psb` 扩展名，路径安全、体积上限、
magic/版本/通道/尺寸/层数检查全部保留。回归测试见
`tests/unit/test_psd_structure.py::test_validate_psd_file_*` 三条。


