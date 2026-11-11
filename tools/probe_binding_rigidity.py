"""诊断：绑定骨的权重分布与旋转键形的**刚性**（可复跑的度量）。

要回答两个问题：

1. 每个网格的蒙皮权重（``compute_bone_weights`` 给的是"assigned 骨 + 父骨"的
   距离倒数分配）落到某个参数的受影响骨骼集合上之后，合计权重是多少？
   合计 = 1 → 顶点整体刚体旋转；合计落在 (0,1) → ``_blend`` 做的是**部分旋转**，
   即弦插值 —— 部件会被拉长而不是转动。
2. 烘焙出来的旋转键形**保持刚体吗**（网格内部任意两点距离不变）？

用法：python tools/probe_binding_rigidity.py
"""
from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image

from live2d_builder.bones.deformers import BoneHierarchy
from live2d_builder.exporter.moc3_pipeline import (
    ROTATION_KEYFORM_PARAMS,
    _affected_bones,
    _blend,
    _vertex_weight,
)
from live2d_builder.pipeline import Live2DBuilder


def _layer(w=128, h=128, color=(200, 100, 100, 255)):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    arr = np.array(img)
    margin = w // 6
    arr[margin:h - margin, margin:w - margin] = color
    return Image.fromarray(arr, "RGBA")


def _layers():
    """覆盖不同骨骼归属的层：头/脸/发/眼/衣服/手臂/腿。"""
    return {
        "hair_back": _layer(color=(80, 80, 200, 255)),
        "face_base": _layer(color=(255, 220, 180, 255)),
        "eyes": _layer(color=(30, 30, 80, 255)),
        "hair_front": _layer(color=(80, 80, 200, 255)),
        "clothes_top": _layer(color=(50, 150, 50, 255)),
        "arm_L": _layer(color=(120, 90, 60, 255)),
        "leg_L": _layer(color=(90, 60, 120, 255)),
    }


def _rigidity(vertices, weights, pivot, angle_deg):
    """按 ``_blend`` 烘焙一次旋转，返回网格内部最大成对距离变化（px）。"""
    px, py = pivot
    theta = math.radians(angle_deg)
    cosine, sine = math.cos(theta), math.sin(theta)
    moved = [_blend(x, y, px, py, cosine, sine, w)
             for (x, y), w in zip(vertices, weights)]
    worst = 0.0
    for i in range(len(vertices)):
        for j in range(i + 1, len(vertices)):
            d0 = math.dist(vertices[i], vertices[j])
            d1 = math.dist(moved[i], moved[j])
            worst = max(worst, abs(d1 - d0))
    return worst


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="bind_probe_") as tmp:
        builder = Live2DBuilder(output_dir=tmp, character_name="probe")
        builder.build(_layers())
        meshes = builder._meshes
        positions = builder._bone_positions
        standard = BoneHierarchy.STANDARD_BONES

        if not meshes:
            print("没有网格")
            return 2

        print(f"{'layer':14s} {'param':16s} {'bones':34s} "
              f"{'w_min':>6s} {'w_max':>6s} {'frac':>10s}  rigidity(px)")
        worst_overall = 0.0
        fractional_meshes = []
        for name, mesh in meshes.items():
            skin = mesh.get("weights") or {}
            bone_names = skin.get("bone_names") or []
            per_vertex = skin.get("weights") or []
            if not bone_names or not per_vertex:
                continue
            vertices = [(float(x), float(y)) for x, y in mesh["vertices"]]
            for pid, spec in ROTATION_KEYFORM_PARAMS.items():
                if not spec.get("mesh"):
                    continue
                affected = _affected_bones(spec["groups"], standard)
                indices = [i for i, b in enumerate(bone_names) if b in affected]
                if not indices:
                    continue
                ws = [_vertex_weight(per_vertex, i, indices)
                      for i in range(len(per_vertex))]
                frac = sum(1 for w in ws if 1e-3 < w < 1 - 1e-3)
                pivot_bone = spec.get("pivot")
                pivot = positions.get(pivot_bone)
                if pivot is None:
                    continue
                worst = _rigidity(vertices, ws, (float(pivot[0]), float(pivot[1])), 30.0)
                worst_overall = max(worst_overall, worst)
                if frac:
                    fractional_meshes.append((name, pid, frac, len(ws)))
                print(f"{name:14s} {pid:16s} {str(bone_names):34s} "
                      f"{min(ws):6.3f} {max(ws):6.3f} {frac:5d}/{len(ws):<4d}  "
                      f"{worst:8.3f}")

        print(f"\n最坏刚体偏差 = {worst_overall:.3f}px（旋转 30°）")
        if fractional_meshes:
            print("存在部分权重的网格（部分旋转 = 弦插值 = 部件被拉长）：")
            for name, pid, frac, total in fractional_meshes:
                print(f"  {name} / {pid}: {frac}/{total} 个顶点权重落在 (0,1)")
        else:
            print("没有网格落到部分权重上 —— 旋转键形整体是刚体的")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
