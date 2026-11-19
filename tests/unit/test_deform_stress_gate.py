"""离线形变压力闸门的回归测试（INV-5）。

覆盖两件事：
1. 6 类真实动作（内核实际使用的 deformer）在安全范围内：空洞面积为 0、覆盖率达标；
2. 闸门本身有牙：一个会撕裂网格的假变形器必须被判失败。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import deform_stress_gate as gate  # noqa: E402


pytestmark = pytest.mark.skipif(
    not gate.HAS_CV2, reason="缺少 opencv，形变闸门无法光栅化")


def test_all_six_action_categories_are_available() -> None:
    report = gate.run_all()

    assert report["available"] is True
    keys = {case["case"] for case in report["cases"]}
    assert keys == {
        "face_turn", "blink", "mouth",
        "shoulder_lift", "skirt_sway", "hair_sway",
    }


def test_safe_ranges_are_hole_free() -> None:
    report = gate.run_all()

    assert report["all_safe_ranges_ok"] is True
    for case in report["cases"]:
        assert case["safe_range_ok"] is True, case
        for sample in case["samples"]:
            assert sample["hole_area"] == 0, (case["case"], sample)
            assert sample["coverage_ratio"] >= gate.MIN_COVERAGE, (case["case"], sample)


def test_gate_fails_a_broken_mesh() -> None:
    """负向对照：抽掉中间一整排三角形，测量必须报出内部空洞。

    共享顶点的规则网格在连续形变下不可能被撕开，所以「闸门有牙」只能
    用破损网格来证明：少了三角形 -> 轮廓内出现被围住的透明区。
    """
    rect = (100.0, 80.0, 260.0, 420.0)
    nx, ny = 9, 13
    verts, tris = gate._grid_mesh(rect, nx, ny)
    ring = gate._boundary_ring(nx, ny)

    intact = gate.measure_deformation(verts, tris, ring)
    assert intact["hole_area"] == 0, intact

    # 挖掉网格中部两格（不触及外圈）-> 轮廓内出现被围住的透明区。
    row, col0, col1 = 5, 3, 5
    per_row = 2 * (nx - 1)
    drop = {row * per_row + 2 * c + k for c in range(col0, col1) for k in (0, 1)}
    broken_tris = [t for i, t in enumerate(tris) if i not in drop]
    broken = gate.measure_deformation(verts, broken_tris, ring)

    assert broken["hole_area"] > 0, broken