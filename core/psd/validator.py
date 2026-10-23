#!/usr/bin/env python3
"""
Live2D Master Agent - PSD Validator
Validates PSD files against Live2D Cubism Editor standards.
"""

import os
from pathlib import Path
from typing import Dict, List, Any, Optional, Sequence

from core.logger import get_logger
from core.psd.parser import PSDParser, PSDValidationError

log = get_logger("psd_validator")

# Live2D PSD requirements
LIVE2D_PSD_REQUIREMENTS = {
    "min_height_px": 1000,
    "max_height_px": 8192,
    "min_width_px": 500,
    "max_width_px": 8192,
    "color_mode": "RGB",
    "bit_depth": 8,
    "max_layers": 2000,
    "dpi": 72,
}


# ----------------------------------------------------------------------
# FF-2：18 层顺序契约的测量钩子
# ----------------------------------------------------------------------
# 架构文档 docs/architecture/index.md 的 FF-2 承诺「STANDARD_LAYER_ORDER 与 PSD
# 写出顺序 + Live2D model3 renderOrder 三者严格 1:1 匹配，任何层错位 0 容忍」，
# 并把测量来源写成 ``core/psd/validator.py::check_layer_order``。这个函数此前
# 只存在于文档里（全仓 0 处代码命中），于是层序错位既不会被发现也不会被阻断。

_ORDER_SOURCE_NAMES = {
    "psd": "PSD 层序",
    "render": "Live2D 渲染层序",
}


def _project(order: Sequence[str], allowed: set) -> List[str]:
    """把一份层序投影到「三份都具备的层」上，保留它自己的相对顺序。"""
    seen = set()
    projected: List[str] = []
    for name in order:
        if name in allowed and name not in seen:
            seen.add(name)
            projected.append(name)
    return projected


def check_layer_order(
    standard_order: Sequence[str],
    psd_order: Optional[Sequence[str]] = None,
    render_order: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """核对三份层序是否一致（FF-2 的测量钩子）。

    三份层序一律按 **后→前**（自底向上、先画的在前）给出：

    Args:
        standard_order: ``LayerComposer.STANDARD_LAYER_ORDER``，层序的唯一权威。
        psd_order: PSD 里图层的实际顺序（``read_psd_layer_order`` 可读出）。
        render_order: moc3 的绘制顺序（``build_meta`` / ``mesh_guide.json`` 的键序）。

    任一来源传 ``None`` 表示该来源本次不可得，跳过比对（不当作通过）。

    Returns:
        错层清单，按层名排序，便于直接高亮。每项形如::

            {
              "code": "layer_order_mismatch",
              "layer": "mouth_cavity",
              "expected_index": 26,          # 在 standard 投影后的位置
              "sources": {"psd": 26, "render": 27},
              "direction": "moved_forward",   # 相对 standard 画得更靠前
              "message": "…",
            }

        另有 ``layer_unknown_to_standard``：层名不在权威表里，无法判定其位置
        （历史上 ``mouth_cavity`` 正是这样被兜底排到最前、盖住嘴唇的）。

    说明：比较的是**相对顺序**而不是绝对下标。三份来源的下标基数不同
    （standard 是 65 项权重表，PSD/render 只含实际产出的层），直接比数字会
    得出满屏假阳性。投影到共同层集后逐位置比较，才是「1:1 匹配」的准确判据。
    """
    sources = {
        name: order
        for name, order in (("psd", psd_order), ("render", render_order))
        if order is not None
    }
    if not sources:
        return []

    known = set(standard_order)
    present = set(standard_order)
    for order in sources.values():
        present &= set(order)

    issues: List[Dict[str, Any]] = []

    # 1) 层名不在权威表里 —— 无法判定位置，必须显式报出（不许静默兜底）。
    #    同一层可能同时出现在多个来源里，按层合并成一条，便于一次性修完。
    unknown: Dict[str, List[str]] = {}
    for name, order in sources.items():
        for layer in order:
            if layer not in known:
                unknown.setdefault(layer, []).append(name)
    for layer, origins in sorted(unknown.items()):
        where = "、".join(_ORDER_SOURCE_NAMES[o] for o in origins)
        issues.append({
            "code": "layer_unknown_to_standard",
            "layer": layer,
            "sources": origins,
            "message": (
                f"{where} 中的图层 {layer!r} 未登记在 "
                f"STANDARD_LAYER_ORDER 中，无法判定其绘制位置；"
                f"请先更新权威表（层序变更须改 ADR-003）"
            ),
        })

    if not present:
        return _sort_issues(issues)

    expected = _project(standard_order, present)
    expected_index = {name: i for i, name in enumerate(expected)}

    # 2) 逐层比较相对位置。
    for name, order in sources.items():
        actual = _project(order, present)
        actual_index = {layer: i for i, layer in enumerate(actual)}
        for layer in expected:
            want = expected_index[layer]
            got = actual_index[layer]
            if got == want:
                continue
            issues.append({
                "code": "layer_order_mismatch",
                "layer": layer,
                "expected_index": want,
                "sources": {name: got},
                "direction": "moved_forward" if got > want else "moved_backward",
                "message": (
                    f"{_ORDER_SOURCE_NAMES[name]} 中 {layer!r} 位于第 {got} 位，"
                    f"按 STANDARD_LAYER_ORDER 应为第 {want} 位"
                    f"（{'画得更靠前' if got > want else '画得更靠后'}）"
                ),
            })

    return _sort_issues(issues)


def _sort_issues(issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """稳定排序，保证同一次输入总是产出同一份清单（可逐字断言）。"""
    def key(item: Dict[str, Any]):
        origins = item.get("sources")
        if isinstance(origins, dict):
            origins = sorted(origins)
        return (str(item.get("code")), str(item.get("layer")), str(origins))

    return sorted(issues, key=key)


def read_psd_layer_order(filepath: str) -> List[str]:
    """读出 PSD 里图层的真实顺序（后→前）。

    读不到就返回空列表 —— 调用方据此把该来源视为「不可得」，而不是「一致」。
    """
    try:
        from psd_tools import PSDImage
    except ImportError:
        log.warning("未安装 psd-tools，无法读取 PSD 层序")
        return []

    try:
        psd = PSDImage.open(filepath)
        return [layer.name for layer in psd]
    except Exception as exc:
        log.warning(f"读取 PSD 层序失败: {exc}")
        return []


class PSDValidator:
    """Validates PSD files against Live2D Cubism Editor import requirements."""

    # P2-2 FIX: Stable issue IDs (deterministic, not random)
    ISSUE_CODES = {
        "E_SIZE_SMALL": "Canvas too small (min 500x1000px for Live2D)",
        "E_SIZE_LARGE": "Canvas too large (max 8192x8192px)",
        "E_COLOR_MODE": "Color mode must be RGB",
        "E_BIT_DEPTH": "Bit depth must be 8-bit",
        "E_EMPTY": "PSD has no layers",
        "W_FEW_LAYERS": "Too few layers for full Live2D rig (< 10)",
        "W_MANY_LAYERS": "Very high layer count may cause performance issues",
        "W_HIDDEN_LAYERS": "Hidden layers found (may be intentional)",
        "I_LAYER_COUNT": "Layer count information",
    }

    def __init__(self):
        self.parser = PSDParser()

    def validate(self, filepath: str) -> Dict[str, Any]:
        """Validate a PSD file for Live2D import compatibility.

        Returns dict with: valid (bool), score (0-100), issues (list), metadata (dict)
        """
        issues = []
        score = 100

        try:
            data = self.parser.parse(filepath)
        except PSDValidationError as e:
            return {
                "valid": False,
                "score": 0,
                "issues": [{"id": "E_SECURITY", "severity": "error", "message": str(e)}],
                "metadata": {},
            }
        except Exception as e:
            return {
                "valid": False,
                "score": 0,
                "issues": [{"id": "E_PARSE", "severity": "error", "message": f"Parse error: {e}"}],
                "metadata": {},
            }

        w = data["width"]
        h = data["height"]
        req = LIVE2D_PSD_REQUIREMENTS

        # Size checks
        if h < req["min_height_px"] or w < req["min_width_px"]:
            issues.append(self._make_issue("E_SIZE_SMALL", "error",
                f"Canvas {w}x{h}px is below minimum {req['min_width_px']}x{req['min_height_px']}px"))
            score -= 30
        elif h > req["max_height_px"] or w > req["max_width_px"]:
            issues.append(self._make_issue("E_SIZE_LARGE", "error",
                f"Canvas {w}x{h}px exceeds maximum {req['max_width_px']}x{req['max_height_px']}px"))
            score -= 30
        else:
            # Optimal size: 2000-4000px height
            if req["min_height_px"] <= h <= 8000:
                pass
            else:
                issues.append(self._make_issue("I_LAYER_COUNT", "info",
                    f"Canvas height {h}px is outside optimal 2000-4000px range"))

        # Color mode check
        color_mode = data.get("color_mode", "")
        if "RGB" not in str(color_mode):
            issues.append(self._make_issue("E_COLOR_MODE", "error",
                f"Color mode is {color_mode}, must be RGB"))
            score -= 25

        # Bit depth
        depth = data.get("depth", 8)
        if depth != 8:
            issues.append(self._make_issue("E_BIT_DEPTH", "error",
                f"Bit depth is {depth}-bit, must be 8-bit"))
            score -= 20

        # Layer count
        layer_count = data.get("layer_count") or len(data.get("layers", []))
        if layer_count == 0:
            issues.append(self._make_issue("E_EMPTY", "error", "PSD contains no layers"))
            score -= 25
        elif layer_count < 10:
            issues.append(self._make_issue("W_FEW_LAYERS", "warning",
                f"Only {layer_count} layers; full Live2D rig requires 20+ layers"))
            score -= 10
        elif layer_count > 500:
            issues.append(self._make_issue("W_MANY_LAYERS", "warning",
                f"{layer_count} layers may cause performance issues in Cubism Editor"))
            score -= 5

        # Check for hidden layers
        hidden = [l for l in data.get("layers", []) if not l.get("visible", True)]
        if hidden:
            issues.append(self._make_issue("W_HIDDEN_LAYERS", "warning",
                f"{len(hidden)} hidden layer(s) found"))

        score = max(0, min(100, score))

        return {
            "valid": score >= 60,
            "score": score,
            "issues": issues,
            "metadata": {
                "filepath": filepath,
                "width": w,
                "height": h,
                "color_mode": color_mode,
                "depth": depth,
                "layer_count": layer_count,
            },
        }

    def _make_issue(self, code: str, severity: str, message: str) -> Dict:
        """P2-2 FIX: Create an issue with STABLE deterministic ID."""
        # Issue ID is the code itself (stable), not a random UUID
        return {
            "id": code,
            "severity": severity,
            "message": message,
            "description": self.ISSUE_CODES.get(code, ""),
        }
