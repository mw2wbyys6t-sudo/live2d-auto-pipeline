# tools/archive/2026-09 — 一次性诊断脚本归档

## 归档原因

本目录收录的是 2026 年 9 月期间为追查 MOC3 导出管线风险而写下的**一次性诊断脚本**。
这些脚本的结论已被下列两份评审文档「钉死」，脚本本身不再需要在日常开发中被调用，
故从 `tools/` 根目录移出，集中归档在此处以便日后溯源，同时保持 `tools/` 根目录只保留
**仍可被新数据复用**的工具脚本。

可参考的评审文档（结论权威来源，请优先阅读这两份而非脚本本身）：

- `docs/reviews/2026-09-20-moc3-export-risk-review.md` — MOC3 导出风险评审总览
- `docs/reviews/2026-09-21-kotlin-cpp-boundary.md` — Kotlin / C++ 边界评审

## 原始位置

所有脚本原本都位于仓库根的 `tools/` 目录下，文件名前缀即其原始名。
本次归档使用 `git mv` 完成，因此每个文件都能通过 `git log --follow` 追溯到归档前的完整提交历史。

## 子目录与脚本清单

| 子目录 | 前缀 | 数量 | 用途概述 |
| --- | --- | --- | --- |
| `probe/` | `probe_*.py` | 27 | 对渲染、旋转、形变、UV、warp 等字段语义的逐一试探性探测 |
| `bisect/` | `bisect_*.py` | 6 | 对 motion 编码 / 加载 / 播放与 moc3 字段、blank render 的二分定位 |
| `mutate/` | `mutate_*.py` | 2 | 对 deformer 文档与 haru 形变器做定向改动以观察行为 |
| `diag/` | `diag_*.py` | 2 | extra motion 与 motion 起点的诊断（注：本类仅 2 个，非预期的 4 个） |
| `diagnose/` | `diagnose_*.py` | 2 | deformer 二分与文档的诊断流程脚本 |
| `writeback/` | `writeback_*.py` | 2 | 把诊断出的差异 / haru 控制信息回写到产物 |
| `ladder/` | `ladder_*.py` | 1 | deformer growth 的阶梯式探查 |
| `retry/` | `retry_*.py` | 1 | deformer 候选的重试脚本 |
| `check/` | `check_*.py` | 5 | deformer keyforms / go 导出路径 / haru winding / parent 语义 / sot 单调性的一次性核查 |

合计：**48 个脚本**归档于此。

## 仍在 `tools/` 根目录的可复用脚本

以下脚本因其结论尚未被钉死、或本身是面向新数据的复用工具，故**未**归档，仍保留在 `tools/` 根目录：

- 验证类：`verify_breath_end_to_end.py`、`verify_deformer_export_pixels.py`、`verify_keyform_evidence.py`、`verify_sam2_gd.py`
- 调参 / 打分类：`tune_sam2_gd.py`、`score_layering.py`
- 测量与交叉校验：`measure_export_duration.py`、`diff_layout_vs_pymoc3.py`、`extract_moc3_layout.py`（容器交叉校验）
- 不变式电池：`deformer_invariant_battery.py`
- 探针套件：`moc3_probe_kit.py`（文件名前缀为 `moc3_` 而非 `probe_`，且属可对新数据复用的探针套件，故保留）

## 还原方法

如需把某个脚本恢复到根目录重新使用：

```bash
cd /workspace
git mv tools/archive/2026-09/<sub>/<script>.py tools/<script>.py
```

由于本次移动采用 `git mv`，`git log --follow` 可穿透归档目录继续追溯历史。
