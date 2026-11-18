# 实施计划：分层像素对齐（M1）

spec: docs/staging/specs/2026-11-15-layer-pixel-alignment.md
milestone: M1（见 docs/ROADMAP.md）

本计划只展开 M1「层像素守恒与严格重建」，M2/M3 保持 ROADMAP 桩位。

- [ ] T1: 层数据契约与 `layer_provenance.json`
  goal: 每层导出时写出 `bbox / origin / synthesized / alpha_checksum`，origin 为整数像素
  files: core/segment_engine/composer.py, tests/unit/test_layer_provenance.py
  acceptance: `python -m pytest tests/unit/test_layer_provenance.py -q` 全绿
  spec: #contract

- [ ] T2: 软 alpha 提取（边界 1~2px 软权重）
  goal: `_extract_layer` 支持 float 掩码；边界带按距离/颜色权重分配，不再二值化
  files: core/segment_engine/composer.py, tests/unit/test_alpha_partition.py
  acceptance: 测试断言基础层在同一前景像素上的 alpha 之和 == 255
  spec: #invariant（INV-2）

- [ ] T3: 互补取整导出
  goal: 相邻两层在边界带的 8bit alpha 互补（`a2 = 255 - a1`），避免取整后出现 ±2 缝
  files: core/segment_engine/composer.py, tests/unit/test_alpha_partition.py
  acceptance: 断言边界带内任意像素 `a1 + a2 == 255`，且整图重建误差 ≤1 LSB
  spec: #invariant（INV-2）

- [ ] [parallel] T4: 合成层/叠加层豁免清单
  goal: 闭眼图、口内、腮红、眼泪、补全层标记 `synthesized`，不参与 alpha 守恒判定
  files: core/segment_engine/composer.py, core/segment_engine/layer_manifest.py, tests/unit/test_layer_manifest.py
  acceptance: 测试断言豁免集合与 `STANDARD_LAYER_ORDER` 同步，且重建校验跳过这些层
  spec: #invariant（INV-1）

- [ ] T5: 重建校验器
  goal: 新增 `verify_reconstruction(layers, source)`，按绘制顺序合成并逐像素比对
  files: core/qa/layer_reconstruction.py, tests/unit/test_layer_reconstruction.py, core/workflow.py
  acceptance: `pytest tests/unit/test_layer_reconstruction.py -q` 全绿；workflow 结果的 `steps.layering.reconstruction` 出现 `mae` 字段
  spec: #test

- [ ] T6: 端到端回归（1024 与 4K 各一次）
  goal: 确认真实立绘在两种画布下重建 MAE == 0，层数不降，模型仍可导出
  files: 无新增（验证任务）
  acceptance: 两次 `python -m core.workflow -i <素材> -o <tmp> [--canvas-height 4096] --json` 后，
    `steps.layering.reconstruction.mae == 0` 且 `build_meta.runtime_ready == true`
  spec: #test
