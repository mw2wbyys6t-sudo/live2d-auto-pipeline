#!/usr/bin/env python3
"""离线形变压力闸门：6 类动作在安全范围内不得撕开轮廓（空洞率为 0）。

为什么要有它：INV-5 要求「需独立活动的部件在安全动作范围内不得露透明洞」。
此前唯一的形变测试只验证网格生成器在 5% 仿射下数值有限，既不覆盖真实动作，
也不度量空洞。这个闸门直接调用**内核实际使用的形变函数**
（``live2d_builder/exporter/moc3_pipeline.py`` 的 ``DEFORMERS``），把网格顶点
按参数幅度形变后光栅化，再量两件事：

  * ``hole_area``      轮廓内被不透明像素围住的透明区面积（洞）；
  * ``coverage_ratio`` 实际不透明面积 / 形变后边界多边形的解析面积。
    撕开的裂缝若一直通到轮廓边缘，不算「内部洞」，但会让覆盖率掉下来。

安全范围内两条都必须通过；超出安全范围只记录风险，不宣称通过。
任何一步无法完成（缺 cv2）一律 ``available=false``，判据置 null = 未验证。

用法：
    python tools/deform_stress_gate.py            # 打印 JSON
    python -m pytest tests/unit/test_deform_stress_gate.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

try:
    import cv2
    HAS_CV2 = True
except ImportError:  # pragma: no cover - 环境缺 cv2
    cv2 = None  # type: ignore
    HAS_CV2 = False

from live2d_builder.exporter.moc3_pipeline import DEFORMER_PROFILES  # noqa: E402
from rig_visual_gate import interior_holes  # noqa: E402

#: 覆盖率下限：光栅化本身允许极小的缝（像素取整），但不允许真实撕裂。
MIN_COVERAGE = 0.985


@dataclass(frozen=True)
class StressCase:
    """一类动作的压力测试定义。"""

    key: str
    deformer: str
    param: str
    safe_max: float
    #: 幅度（0..safe_max）-> 形变参数值。
    values_at: Callable[[float], Dict[str, float]]
    #: 采样幅度（不含 0）。
    sweep: Tuple[float, ...]


def _scale(m: float) -> Dict[str, float]:
    return {"angle_x": 0.5 * m, "angle_y": 0.7 * m, "angle_z": m}


CASES: Tuple[StressCase, ...] = (
    StressCase("face_turn", "head", "ParamAngleZ", 30.0,
               _scale, (10.0, 20.0, 30.0)),
    StressCase("blink", "eye", "ParamEyeLOpen", 1.0,
               lambda m: {"open": -m}, (0.5, 1.0)),
    StressCase("mouth", "mouth", "ParamMouthOpenY", 1.0,
               lambda m: {"open": m, "form": 0.5 * m, "size": 0.3 * m}, (0.5, 1.0)),
    StressCase("shoulder_lift", "lift", "ParamShoulderLift", 1.0,
               lambda m: {"lift": m}, (0.5, 1.0)),
    StressCase("skirt_sway", "skirt_sway", "ParamSkirtSway", 1.0,
               lambda m: {"sway": m}, (0.5, 1.0)),
    StressCase("hair_sway", "hair_sway", "ParamHairSwing", 1.0,
               lambda m: {"swing": m}, (0.5, 1.0)),
)


def _grid_mesh(rect: Tuple[float, float, float, float],
               nx: int = 9, ny: int = 13) -> Tuple[List[Tuple[float, float]], List[Tuple[int, int, int]]]:
    """在 ``rect`` 内铺一张规则网格，返回顶点与三角形索引。"""
    x0, y0, x1, y1 = rect
    xs = np.linspace(x0, x1, nx)
    ys = np.linspace(y0, y1, ny)
    verts = [(float(x), float(y)) for y in ys for x in xs]
    tris: List[Tuple[int, int, int]] = []
    for r in range(ny - 1):
        for c in range(nx - 1):
            i = r * nx + c
            tris.append((i, i + 1, i + nx))
            tris.append((i + 1, i + nx + 1, i + nx))
    return verts, tris


def _boundary_ring(nx: int, ny: int) -> List[int]:
    """网格外圈的顶点索引（顺时针）。"""
    ring: List[int] = []
    ring.extend(range(nx))                                   # 上边
    ring.extend(r * nx + (nx - 1) for r in range(1, ny))      # 右边
    ring.extend(range((ny - 1) * nx + nx - 2, (ny - 1) * nx - 1, -1))  # 下边
    ring.extend(r * nx for r in range(ny - 2, 0, -1))         # 左边
    return ring


def _polygon_area(points: List[Tuple[float, float]]) -> float:
    """鞋带公式；取绝对值。"""
    if len(points) < 3:
        return 0.0
    xs = np.array([p[0] for p in points], dtype=np.float64)
    ys = np.array([p[1] for p in points], dtype=np.float64)
    return float(abs(np.dot(xs, np.roll(ys, -1)) - np.dot(ys, np.roll(xs, -1))) / 2.0)


def _rasterize(verts: List[Tuple[float, float]], tris: List[Tuple[int, int, int]],
               origin: Tuple[float, float], size: Tuple[int, int]) -> np.ndarray:
    """把三角形网格画成 alpha 掩码（``size`` 为 (h, w)）。"""
    h, w = size
    canvas = np.zeros((h, w), dtype=np.uint8)
    ox, oy = origin
    for a, b, c in tris:
        pts = np.array([
            [verts[a][0] - ox, verts[a][1] - oy],
            [verts[b][0] - ox, verts[b][1] - oy],
            [verts[c][0] - ox, verts[c][1] - oy],
        ], dtype=np.float32)
        cv2.fillConvexPoly(canvas, np.round(pts).astype(np.int32), 255)
    return canvas > 0


def measure_deformation(verts: List[Tuple[float, float]],
                        tris: List[Tuple[int, int, int]],
                        ring: List[int]) -> Dict[str, object]:
    """把一张已形变的网格光栅化，量轮廓内空洞与覆盖率。

    单独抽出来是为了让测试可以对**故意破损的网格**调用同一条测量路径 ——
    共享顶点的规则网格本身不可能被连续形变撕开，所以「闸门有牙」只能用
    缺三角形的网格来证明。
    """
    xs = [p[0] for p in verts]
    ys = [p[1] for p in verts]
    pad = 2
    origin = (min(xs) - pad, min(ys) - pad)
    size = (int(np.ceil(max(ys) - min(ys) + 2 * pad)),
            int(np.ceil(max(xs) - min(xs) + 2 * pad)))
    alpha = _rasterize(verts, tris, origin, size)
    holes = interior_holes(alpha.astype(np.uint8) * 255)
    expected = _polygon_area([verts[i] for i in ring])
    opaque = int(alpha.sum())
    return {
        "hole_area": int(holes["area"]),
        "hole_count": len(holes["regions"]),
        "coverage_ratio": round(float(opaque / expected) if expected > 1e-9 else 0.0, 6),
        "opaque_pixels": opaque,
        "expected_area": round(float(expected), 3),
    }


def run_case(case: StressCase, rect: Tuple[float, float, float, float],
             nx: int = 9, ny: int = 13) -> Dict[str, object]:
    """跑一类动作：逐个幅度形变后光栅化 + 量洞与覆盖率。"""
    spec = DEFORMER_PROFILES.get(case.deformer)
    result: Dict[str, object] = {
        "case": case.key,
        "deformer": case.deformer,
        "param": case.param,
        "safe_max": case.safe_max,
        "samples": [],
    }
    if spec is None:
        result["available"] = False
        result["reason"] = f"未知变形器 {case.deformer}"
        return result
    if not HAS_CV2:  # pragma: no cover
        result["available"] = False
        result["reason"] = "缺少 opencv，无法光栅化"
        return result

    _, deform = spec
    verts, tris = _grid_mesh(rect, nx, ny)
    ring = _boundary_ring(nx, ny)

    samples = []
    for mag in case.sweep:
        moved = deform(verts, rect, rect, case.values_at(mag), {})
        measurement = measure_deformation(moved, tris, ring)
        measurement["magnitude"] = mag
        samples.append(measurement)

    safe_samples = [s for s in samples if s["magnitude"] <= case.safe_max]
    ok = all(s["hole_area"] == 0 and s["coverage_ratio"] >= MIN_COVERAGE
             for s in safe_samples)
    result.update({
        "available": True,
        "min_coverage": MIN_COVERAGE,
        "samples": samples,
        "safe_range_ok": ok,
    })
    return result


def run_all(rect: Tuple[float, float, float, float] =
            (100.0, 80.0, 260.0, 420.0)) -> Dict[str, object]:
    cases = [run_case(case, rect) for case in CASES]
    available = all(c["available"] for c in cases)
    all_ok = available and all(bool(c.get("safe_range_ok")) for c in cases)
    return {
        "available": available,
        "all_safe_ranges_ok": all_ok if available else None,
        "cases": cases,
    }


def main() -> int:
    report = run_all()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["available"]:
        print("deform stress gate: 未验证（环境缺依赖）", file=sys.stderr)
        return 2
    return 0 if report["all_safe_ranges_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())