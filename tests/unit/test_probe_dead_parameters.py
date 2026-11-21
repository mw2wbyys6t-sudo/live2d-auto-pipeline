# -*- coding: utf-8 -*-
"""死参数探针的探值选择：必须推到**离静止值最远**的极值。

背景（2026-11-21）：探针此前一律推 ``max``。``ParamEyeLOpen`` 是
min 0 / max 1 / default 1，推到 max 等于没动 → 眼睑开合被报成「死参数」，
而它恰恰是最常用的一个。诊断工具说谎比没有工具更糟。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from probe_dead_parameters import probe_value  # noqa: E402


def test_default_at_max_probes_the_min_side():
    """眼睑类：default == max，必须推 min，否则永远测不出变化。"""
    assert probe_value(0.0, 1.0, 1.0) == 0.0


def test_default_at_min_probes_the_max_side():
    assert probe_value(0.0, 1.0, 0.0) == 1.0


def test_centered_default_prefers_the_tie_break_max():
    """居中参数两侧等距：取 max（与旧行为一致，保持结果可复现）。"""
    assert probe_value(-30.0, 30.0, 0.0) == 30.0


def test_asymmetric_range_picks_the_farther_side():
    """默认值偏一侧时，走得更远的那一侧才是有效探针。"""
    assert probe_value(-10.0, 10.0, -8.0) == 10.0
    assert probe_value(-10.0, 10.0, 8.0) == -10.0


def test_degenerate_keyform_is_skipped_not_called_dead():
    """min == max == default 时无从验证：返回 None，让调用方跳过而不是判死。"""
    assert probe_value(0.0, 0.0, 0.0) is None
