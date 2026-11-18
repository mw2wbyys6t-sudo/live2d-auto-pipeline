# 分层像素对齐与高难度动作鲁棒性

date: 2026-11-15
milestone: M1（见 docs/ROADMAP.md）
status: 待确认

## 目标

导出的每个图层必须与源图像素严格对应（零偏移、零重采样），且在 Live2D 中做大角度/大幅形变时
不出现层缝、露底、色边。当前链路（mask 二值化 → 1px 外扩 → 网格简化 → 图集 padding=0）
在高难度动作下会放大三类误差：边界归属误差、网格收缩误差、纹理过滤误差。

## 决定

contract:
- 层文件：`layers/<name>.png` 为 RGBA8，尺寸等于源画布；同目录附 `layer_provenance.json`，
  记录每层 `bbox / origin / synthesized / alpha_checksum`。
- 网格：顶点坐标统一为画布像素坐标；网格轮廓必须覆盖该层 alpha 的全部支持域。
- 图集：允许 4096×8192 长方形页；任何层的四邻域 padding 不得为 0。

invariant:
- INV-1 像素锚定：`alpha > 0` 的像素，其 RGB 必须与源图同坐标像素逐值相等。
  例外：显式补全层（`synthesized=true`）与合成层（闭眼图/口内/腮红/眼泪）。
- INV-2 分割完备：基础覆盖层（不含合成层与叠加层）在同一前景像素上的 alpha 之和恒为 255；
  边界 1~2px 带内按软权重分配，按互补取整导出（`a2 = 255 - a1`）。
- INV-3 网格覆盖：把 alpha 支持域栅格化后，其所有像素中心必须落在 mesh 多边形内；
  mesh 外扩不得超过 1px。
- INV-4 图集隔离：每层在 atlas 中四周 ≥2px 环带必须为本层边缘延展色，不得与邻层纹理相邻。
- INV-5 坐标一致：PSD、model3 `CanvasInfo`、moc3 网格、atlas UV 共用同一像素原点；
  只允许整数平移，禁止非整数缩放。

failure modes:
- 软 alpha 取整到 8bit 产生 ±1 LSB：允许；重建误差 >1 LSB 判为缺陷。
- 单层最长边 >4096：使用 4096×8192 页，禁止通过 `padding=0` 硬塞 4096 方图。
- 补全层风格不一致：保留"重建误差闸门"，不达标不补，不生成假层。

test:
- `tests/unit/test_layer_reconstruction.py`：按 `STANDARD_LAYER_ORDER` 合成全部层，
  与原图逐像素比较，要求 MAE == 0（软 alpha 情形 ≤1 LSB）。
- `tests/unit/test_mesh_coverage.py`：栅格化每层 mesh，断言 alpha 支持域被完全覆盖。
- `tests/unit/test_deformation_stress.py`：每层做 ±3px 平移与 5% 仿射扰动，
  断言前景区域空洞像素率 == 0。
- `tests/unit/test_atlas_isolation.py`：断言 atlas 中每层矩形外 2px 环带属于本层延展色。

deferred:
- 与 Part-Graph / MDL 分层规划（更细的分层数量）合并，等层数突破 80 后再评估。
- 关节重叠带宽度按素材实测调参（先固定 8px）。

## Working notes

现状偏移来源（按链路顺序）：

1. `core/utils/image_utils.normalize_character_canvas`：LANCZOS 缩放会重采样源像素。
   这本身定义新像素网格，可接受；但**掩码必须在该网格上重算**，不能缩放旧掩码。
2. `core/segment_engine/composer.py`：mask 直接 `astype(bool)` 取像素，边界像素二选一，
   相邻层可能同时取到或同时漏掉 → 1px 缝/重叠。
3. `live2d_builder/pipeline.bleed_layers(1)`：外扩 1px 用邻域色，动作时外扩区被拉出 → 色边。
4. `live2d_builder/mesh`：轮廓 spacing 12/20px + 简化 → 网格比 alpha 收缩数像素，
   弯曲时相邻层之间露底。
5. `live2d_builder/exporter/texture_atlas`：4K 下 `padding=0`（为省内存）会让线性过滤
   采到邻层像素；UV 若按整数截断而非半像素中心，会有亚像素偏移。

方案对比：

- A. 硬掩码 + 严格归属：实现最便宜，但边界丢抗锯齿半像素，高难度动作边缘闪。
- B. 软 alpha 守恒（partition of unity）：边界带内软分配、互补取整，重建无损、动作不闪。
- C. 过完备层 + 重叠带：关节处允许双层覆盖，弯曲不露缝；实现最重。

建议：**B 为主 + C 的关节重叠带（8px）+ A 作为退化路径**。
