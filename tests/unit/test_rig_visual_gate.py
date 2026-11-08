"""视觉缺口闸门（tools/rig_visual_gate.py）的可验证内核。

今天的黑缝说明：只看「内部洞数」不够——它把轮廓之间的空洞和真正新开的口子混在
一起。真正该量的是**当前渲染相对基线渲染**新出现的透明区。这里先钉住两个纯函数，
以及把读数映射成 PASS/FAIL/BLOCKED 的判据，避免再用手搓脚本得出无法复现的结论。
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import rig_visual_gate as gate  # noqa: E402


def _alpha(mask):
    return np.where(np.asarray(mask, dtype=bool), 255, 0).astype(np.uint8)


def test_interior_holes_ignores_a_fully_opaque_canvas():
    assert gate.interior_holes(_alpha(np.ones((8, 8))))["area"] == 0


def test_interior_holes_counts_an_enclosed_transparent_region():
    mask = np.ones((10, 10), dtype=bool)
    mask[3:7, 3:7] = False
    result = gate.interior_holes(_alpha(mask))
    assert result["area"] == 16
    assert len(result["regions"]) == 1
    assert result["regions"][0]["bbox"] == (3, 6, 3, 6)


def test_interior_holes_ignores_transparency_connected_to_the_border():
    mask = np.ones((10, 10), dtype=bool)
    mask[0:4, 0:4] = False          # 开口到画布边缘，不算"洞"
    assert gate.interior_holes(_alpha(mask))["area"] == 0


def test_new_transparent_only_counts_places_the_baseline_covered():
    base = np.ones((10, 10), dtype=bool)
    current = np.ones((10, 10), dtype=bool)
    current[2:5, 4:7] = False       # 9 像素新开的口子
    current[8:10, 8:10] = False     # 基线这里本来就是透明的
    base[8:10, 8:10] = False
    result = gate.new_transparent(_alpha(base), _alpha(current))
    assert result["area"] == 9
    assert result["bbox"] == (4, 6, 2, 4)


def test_checks_report_pass_fail_and_blocked_with_measurements():
    checks = gate.evaluate_checks(
        {"new_transparent_px": 730, "rest_sha_match": False,
         "not_measured_metric": None},
        {"new_transparent_px": 200})
    by_id = {check["id"]: check for check in checks}
    assert by_id["new_transparent_px"]["status"] == "FAIL"
    assert by_id["new_transparent_px"]["measured"] == 730
    assert by_id["new_transparent_px"]["threshold"] == 200
    assert by_id["not_measured_metric"]["status"] == "BLOCKED"
    assert gate.overall_status(checks) == "FAIL"


def test_rest_sha_is_recorded_but_never_gates():
    """静止帧 SHA 只作记录：几何切分必然改动切分线两侧的抗锯齿，逐字节相等
    不可达。真正该判的是不透明像素一致 + 静态像素差异面积。
    """
    checks = gate.evaluate_checks(
        {"rest_sha_match": False, "rest_changed_px": 11},
        {"rest_changed_px": 64})
    by_id = {check["id"]: check for check in checks}
    assert by_id["rest_sha_match"]["status"] == "INFO"
    assert by_id["rest_changed_px"]["status"] == "PASS"
    assert gate.overall_status(checks) == "VALIDATED"


def test_rest_static_diff_over_budget_fails():
    checks = gate.evaluate_checks({"rest_changed_px": 900},
                                  {"rest_changed_px": 64})
    assert checks[0]["status"] == "FAIL"
    assert gate.overall_status(checks) == "FAIL"


def test_changed_pixels_counts_only_pixels_that_really_moved():
    width, height = 10, 8
    base = np.zeros((height, width, 4), dtype=np.uint8)
    base[:, :, :3] = 200
    base[:, :, 3] = 255
    current = base.copy()
    current[2:4, 3:6, 0] = 0        # 6 个像素的可见颜色变了
    current[6, 6, 3] = 200          # 仍是不透明、颜色没变，不算
    result = gate.changed_pixels(base, current)
    assert result["area"] == 6
    assert result["bbox"] == (3, 5, 2, 3)


def test_checks_carry_localization_evidence():
    """失败必须能定位到画面位置，而不是只给一个面积数字（PRD §8.3）。"""
    checks = gate.evaluate_checks(
        {"new_transparent_px": 730}, {"new_transparent_px": 200},
        evidence={"new_transparent_px": {"bbox": (112, 291, 44, 105)}})
    assert checks[0]["status"] == "FAIL"
    assert checks[0]["evidence"] == {"bbox": (112, 291, 44, 105)}


def test_overall_status_is_validated_only_when_every_check_passes():
    checks = gate.evaluate_checks({"new_transparent_px": 10},
                                  {"new_transparent_px": 200})
    assert gate.overall_status(checks) == "VALIDATED"
