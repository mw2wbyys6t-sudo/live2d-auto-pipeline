# 实施计划：Rig-ready 分层（M1）

spec: docs/staging/specs/2026-11-17-rig-ready-layering.md
milestone: M1（见 docs/ROADMAP.md）

本计划只展开 M1 P0；P1/P2 留在 ROADMAP 桩位。所有阶段保持离线验证，不启动 VTube Studio，不上传参考 PSD 或源素材。

- [x] T1: P0 语义部件契约和状态模型
  goal: 定义 canonical part ID、左右侧、父组、来源类型、深度/遮挡证据及 ready 状态，校正不合法映射（含 waistband 与 Waist_Hips）
  files: core/segment_engine/layer_manifest.py, core/segment_engine/layers52.py, tests/unit/test_layer_manifest.py, tests/unit/test_layers52_body_parts.py
  acceptance: 新契约测试证明错误语义不能映射为 ready；缺侧、未知深度和不支持部件明确报告 missing/manual_required；旧 manifest fixture 仍可读取
  spec: #contract, #invariant

- [x] T2: 分割来源、置信度与深度证据接线
  goal: 令 detector/ROI/颜色推断/几何派生/人工来源在 manifest 中可区分；建立近远侧和前后证据，证据不足不自动判 ready
  files: core/segment_engine/sam2_gd.py, core/segment_engine/semantic.py, core/segment_engine/part_split.py, tests/unit/test_segment_sam2_gd.py, tests/unit/test_part_split.py
  acceptance: 合成 fixture 覆盖有/无检测框、左右不平衡、无法确认深度；报告中的 masks、source、confidence、missing/manual_required 一致
  spec: #invariant（INV-2, INV-4）, #failure_modes

- [x] T3: 左右一致的 P0 四肢和胸腔结构
  goal: 输出可信的 chest/torso、upper-arm/forearm/hand、thigh/calf/foot 语义；不从粗 mask 伪造缺失部分，保留来源并将不可拆项阻断 ready
  files: core/segment_engine/part_split.py, core/segment_engine/composer.py, core/segment_engine/layer_manifest.py, tests/unit/test_part_split.py, tests/unit/test_part_refinement.py
  acceptance: 左右齐全 fixture 生成各层且分区守恒；缺任一侧、深度不可判或无法安全细分 fixture 不会被标成 ready；真实样例产生逐侧覆盖诊断
  spec: #in_scope（P0）, #quality_gates

- [x] T4: P0 衣物内外层与遮挡补全闸门
  goal: 区分身体主干与衣物内外层，补全面积/来源/置信度可追溯；不可信补全保持 manual_required 并阻断该层 ready
  files: core/segment_engine/composer.py, core/segment_engine/amodal.py, core/segment_engine/layer_manifest.py, tests/unit/test_composer_mask_stabilize.py, tests/unit/test_layer_manifest.py
  acceptance: 遮挡 fixture 证明可见区保持原像素、补全只发生在声明区域且无越界；膨胀异常和补全失败均不伪报成功
  spec: #invariant（INV-3, INV-4）, #failure_modes

- [x] [parallel] T5: 分组 PSD 写出与重新读取校验
  goal: 以统一 contract 生成分组 PSD，保留画布、图层坐标/顺序/命名/可见性；writer 不支持的输入结构显式失败，不降级冒充 PSD 成功
  files: core/psd/creator.py, core/psd/validator.py, tests/unit/test_psd_creator.py, tests/unit/test_psd_validator.py
  acceptance: 用 psd-tools 写出并重新读取 fixture，核对图层数、父组、顺序、尺寸、bbox、alpha 与输入 manifest；不支持特性触发可观察失败
  spec: #contract, #quality_gates, #failure_modes

- [x] T6: Workflow、API 与导出 readiness 串接
  goal: 把 manifest readiness 和分层质量状态传到 workflow、PSD 导出和 Live2D loader；P0 未通过时阻止报告为 ready/runtime success
  files: core/workflow.py, api_server.py, live2d_builder/layer_loader.py, live2d_builder/pipeline.py, tests/unit/test_workflow_segmentation_backend.py, tests/unit/test_layer_manifest.py
  acceptance: 端到端测试证明 P0 缺件/降级/PSD 写出失败不会被报告为 ready，合格 fixture 的层数、命名和顺序在各阶段一致
  spec: #invariant（INV-7）, #quality_gates

- [x] T7: 离线 P0 回归与独立审查
  goal: 在标准分辨率与 4K fixture 各跑一次完整生成/PSD 重读/导出校验，并复核所有 P0 缺口、用户改动和残留风险
  files: tests/integration/test_pipeline_moc3_export.py, tests/integration/test_moc3_product_invariant.py, docs/staging/specs/2026-11-17-rig-ready-layering.md
  acceptance: 相关测试、PSD round-trip、manifest/readiness 对账、MOC3 离线验证通过；报告明确标出未通过/人工复核部件，不声称零问题；不启动 VTube Studio
  spec: #quality_gates, #test

## 验证记录（2026-11-17，离线）

已完成：
- 单元回归：420 passed（manifest / PSD creator+validator / part_split / part_refinement / segment_engine / sam2_gd / l2d_sam / bone_weights / layers52 / workflow / deployment_status / layer_loader / artifact_regressions）。
- 新增集成回归 `tests/integration/test_rig_ready_layering.py`：1024×1024 与 1440×2160（4K 高度）各跑一次
  composer → manifest → 分组 PSD → 重新读取对账；两档均 rig_ready=true、`validate_manifest` 无错、
  PSD 组/顺序/闭区间 bbox/alpha 全部一致。
- MOC3 导出集成：15 passed；Go 导出脚本测试（`go test ./services -run ...`）通过。
- 未启动 VTube Studio；未复制或上传参考 PSD/素材。

未通过 / 未验证（不得当成已通过）：
- 13 个渲染级集成用例按设计跳过：`LIVE2D_TEST_PIXELS=1`（需要 OpenGL）未启用。
- 歌手整条产物重建用例跳过：本机没有 `output/live2d_exports/singer_demo/layers` 分层源。
- 真实 SAM2/GroundingDINO 权重未在本机实跑（无 GPU 权重环境），检测层证据接线仅由桩件单测覆盖。
- 含遮挡补全层或合成表情层的产物 `quality_ready=false`（按 INV-4 语义），只允许作为导出候选，
  报告里会带 `manual_required` / `derived_parts` / `completed_pixels` 供人工复核。

