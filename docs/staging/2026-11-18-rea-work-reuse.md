# F:\杂物\rea-work 复用清单（2026-11-18）

来源：`F:\杂物\rea-work`（Cubism 5.3 反编译源码 + 变形/网格/动作/分层专题笔记 + 参考实现）。
本文件只记录**能用在哪、用了什么、还差什么**，不复制上游代码。

## 一、已复用（本次已落地）

### 1. Warp 越界外推规则

- 上游资产：`deform-notes/ref/deform_ref.py::_warp_outside`（反射调用 Cubism Editor 5.3
  `WarpDeformerUtils` 对拍，30 点最大误差 7e-8）。
- 复用点：浏览器播放器与离线尺子的越界分支。
  - `web/lib/live2d-bindings.ts` 新增 `sampleWarpGridOutside()`：中心 + 两轴仿射框架，
    8 个区域分段线性，远处退化成纯仿射。
  - `tools/bindings_gap_probe.py` 新增同名 Python 实现（播放器与尺子必须同步）。
  - `tools/probe_warp_interpolation.py` 的候选表新增 `BDBox外推`，旧的双线性外推留作对照。
- 判据：`tests/unit/test_bindings_gap_probe.py`
  - `test_ruler_warp_extrapolation_matches_the_cubism_editor`
  - `test_shipped_player_warp_extrapolation_matches_the_cubism_editor`
  （15 个 quad=false 参考点，容差 1e-5；TS 实测最大误差 3.6e-8）
- 效果：越界顶点不再按边界格双线性外推。官方 Core 实测量级也从「差 32 模型单位」
  缩到约 14（`tools/probe_warp_interpolation.py` 的越界段），剩余差异来自 moc3 的
  逐级 (s,t) 空间换算，与编辑器公式本身无关。

### 2. 删掉两条空转的 rotation 变形器

- 上游结论（`03-mesh.md` §3 绑定字段）：变形器靠 parent GUID 组树，眼球跟随用
  `ParamEyeBallX/Y` 的网格键形更直接；再叠一层 rotation 会重复位移。
- 复用点：`live2d_builder/bones/deformers.py` 删除 `EyeTrack_L/R`（targets 指向分层
  根本不产出的 `eyeball_l` / `eye_left`），构建日志不再每次报
  「rotation 变形器未创建 2」。
- 保留能力：`rotation_skipped` 判定机制与测试仍在，将来新增目标未命中时照常告警。

### 3. 图集 margin / UV 四边形约定

- 上游资产：`cubism-src/.../SpriteDataCreate_Util.java`
  （`TEXTURE_MARGINE = 2`，UV 四边形只框内容，2px 只作间隔）。
- 复用点：只补判据，不改实现 —— 我们的 `TextureAtlas.pack()` 已经是「裁剪内容 +
  每边 padding + 边缘复制」，是上游约定的超集（多了防溢色）。
- 判据：`tests/unit/test_atlas_cropping.py`
  - `test_uv_rect_excludes_the_padding_ring`
  - `test_padding_ring_copies_the_edge_pixels`

## 二、可复用但未落地（按价值排序）

| 资产 | 可用之处 | 风险 / 前提 |
|---|---|---|
| `deform-notes/03-mesh.md` §5.1 moc3 数据布局 | 与 `live2d_builder/exporter/moc3_model.py` 的段布局做字段级对拍 | 需要逐段列字段；现有官方 Core 验收已覆盖行为，收益偏“防回归” |
| `deform-notes/03-mesh.md` §2.4 变形器链（父→子逐级烘焙） | 校验 `moc3_pipeline` 的父级空间换算与“规则 I” | 与现有 `test_deformers_gap_probe`/`test_moc3_deformers` 重叠，需先确认口径 |
| `motion-notes/ref/motion_ref.py` | 无 Core 环境下的 motion3 格式/求值 oracle | 现有 `verify_motion_playback` 用官方 Core 逐帧对账，更强；只在 CI 无 Core 时值得引入 |
| `engine/`（纯 TS 运行时，warp 已与 jar 对拍） | 替换/校准 Web 预览的变形链；将来接 WebGL2 | 体积大；当前播放器已能跑，属于长期项 |
| `gimp-pipeline/export_layers_for_engine.py` | 分层导出的 offset/manifest 约定（x/y 保留画布坐标） | 我们的图集已用 bbox 偏移达到同等效果，无需再引入 |
| `viewer-notes/03-io-diff-setting.md` | moc3/纹理导入设置的边界条件 | 主要服务 Cubism Viewer，和 moc3 编译不直接相关 |

## 三、不建议直接复用

- `cubism-src/sources/**/*.java`：反编译的 Live2D 专有实现，只作为**行为参考**。
  需要落地时按「数学规则」重写（如本次 `sampleWarpGridOutside`），不要拷贝代码或
  常量表进产品，以免许可证风险。
- `deform-notes/ref/warp_ref.csv`、`WarpRef.java`：对拍数据来自本机 Cubism jar，
  仅分析用途；本次只把 15 个数值抄成测试常量，不再分发 jar 或 CSV。

## 四、验证命令

```powershell
python -m pytest tests/unit/test_bindings_gap_probe.py -q
python -m pytest tests/unit/test_deformer_hierarchy_rotation.py -q
python -m pytest tests/unit/test_atlas_cropping.py -q
python tools/probe_warp_interpolation.py     # 需要官方 Cubism Core
```
