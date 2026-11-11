"""渲染帧率高于物理 FPS 时，物理输出不许隔帧归零。

固定步长累加器只在子步帧写回输出：dt=1/116（页面实测 116 FPS）时每隔一帧
输出就被 restoreOutputs 撤回到基准，发摆/裙摆在「摆起」与「静止」之间以
58 Hz 闪烁，目视等于「没动」。parity 探针恰用 dt=1/60（每帧一个子步），
这条路径在那里永不触发，所以逐帧一致也量不出它。

判据：瞬态窗口内高帧率轨迹的零输出帧占比 ≤ 10%，且峰值与 dt=1/60 基准
一致（子步动力学相同，峰值不该随渲染帧率变）。窗口由基准轨迹自身划定
（|值| > 0.2 的帧），并要求窗口足够大、基准峰值足够高，避免死场景假绿。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

probe_physics_parity = pytest.importorskip("probe_physics_parity")


@pytest.mark.parametrize("fps", [116, 144])
def test_physics_output_holds_between_substeps_at_high_render_fps(tmp_path, fps):
    manifest, physics = probe_physics_parity._build(tmp_path)
    assert physics is not None and physics.is_file(), "产物里没有 physics3.json"
    doc = json.loads(physics.read_text(encoding="utf-8"))
    model = json.loads(manifest.read_text(encoding="utf-8"))

    ranges: dict = {}
    defaults: dict = {}
    for spec in model.get("Parameters") or []:
        name = str(spec.get("Id") or "")
        if not name:
            continue
        ranges[name] = {"min": float(spec.get("Min", -30.0)),
                        "max": float(spec.get("Max", 30.0))}
        defaults[name] = float(spec.get("Default", 0.0))

    pair = next(((s, d) for s, d, _sys, _kind in probe_physics_parity._chains(doc)
                 if d == "ParamHairSwing"), None)
    assert pair is not None, "physics3 里没有 ParamHairSwing 输出链"
    src, dest = pair

    ref = probe_physics_parity._run_ts(doc, ranges, defaults, src, dest,
                                       120, 1.0 / 60.0, ranges[src]["max"])
    assert ref.get("ok"), f"dt=1/60 基准跑失败：{ref.get('error')}"
    peak_ref = max(abs(v) for v in ref["trace"])
    assert peak_ref > 0.5, f"阶跃场景不敏感（峰值 {peak_ref}），判据无效"

    hi = probe_physics_parity._run_ts(doc, ranges, defaults, src, dest,
                                      2 * fps, 1.0 / fps, ranges[src]["max"])
    assert hi.get("ok"), f"dt=1/{fps} 跑失败：{hi.get('error')}"

    checked = zeros = 0
    for i, v in enumerate(ref["trace"]):
        if abs(v) <= 0.2:
            continue
        j = min(len(hi["trace"]) - 1, round(i / 60.0 * fps))
        checked += 1
        if abs(hi["trace"][j]) < 0.05:
            zeros += 1
    assert checked >= 10, f"瞬态窗口太小（{checked} 帧），判据无效"
    assert zeros <= max(1, checked // 10), (
        f"dt=1/{fps} 瞬态窗口 {checked} 帧里 {zeros} 帧输出被归零"
        f"（占比 {zeros / checked:.0%}）：子步间隔帧没有保持物理输出")

    peak_hi = max(abs(v) for v in hi["trace"])
    assert abs(peak_hi - peak_ref) <= 0.05 * peak_ref, (
        f"峰值随渲染帧率变化：dt=1/60 {peak_ref:.4f} vs dt=1/{fps} {peak_hi:.4f}")