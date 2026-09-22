#!/usr/bin/env python3
"""判别 rotation 键形 origin 的真实语义：用 sha 精确命中，不做 bbox 反解。

bbox 反解会被 1px 量化与子像素覆盖差异放大（小角度时 (I−R) 接近奇异，误差除以
det=4sin²(A/2) 会炸开）。这里改成零量化的判法：把网格顶点按「候选枢轴」预先转好
渲染一份参照，与变形器渲染对 sha —— 命中哪条候选，哪条就是内核真实使用的枢轴。

运行：LIVE2D_TEST_ROOT=E:/Live2D/beacon .venv/Scripts/python.exe tools/probe_rotation_origin.py [角度]
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drivers.live2d_runtime.moc3_verify import render_probe  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    MeshSpec,
    ParameterSpec,
    RigSpec,
    RotationDeformerSpec,
    RotationKeyform,
    compile_static_rig,
)

_CANVAS = 1024.0
_PPU = 100.0
_ANGLE = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
_VERTS = [(0.0, 0.0), (300.0, 0.0), (0.0, 100.0)]
_UVS = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
# 规格层想要的枢轴（模型单位坐标，两轴都非零）
_WANT = (0.6, -0.25)


def _rot(points, degrees, about=(0.0, 0.0)):
    theta = math.radians(degrees)
    cos, sin = math.cos(theta), math.sin(theta)
    ox, oy = about
    return [(ox + (x - ox) * cos - (y - oy) * sin,
             oy + (x - ox) * sin + (y - oy) * cos) for x, y in points]


def _package(tmp: Path, doc, name: str) -> Path:
    from PIL import Image

    folder = tmp / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "m.moc3").write_bytes(doc.to_bytes())
    Image.new("RGBA", (32, 32), (255, 0, 255, 255)).save(folder / "t.png")
    manifest = folder / "m.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3, "Meta": {"ArchiveName": name},
        "FileReferences": {"Moc": "m.moc3", "Textures": ["t.png"]},
    }), encoding="utf-8")
    return manifest


def _render(tmp: Path, doc, name: str) -> dict:
    return render_probe(str(_package(tmp, doc, name)))


def _via_deformer(tmp: Path, spec_origin_units) -> dict:
    """规格层传的是画布像素，所以单位值要先乘 ppu。"""
    mesh = MeshSpec(mesh_id="ArtMeshEye", vertices=list(_VERTS),
                    triangles=[(0, 1, 2)], uvs=list(_UVS),
                    deformer_id="RotEye")
    spec = RigSpec(
        meshes=[mesh],
        parameters=[ParameterSpec("ParamEyeRotX", -30.0, 30.0, 0.0)],
        canvas_width=_CANVAS, canvas_height=_CANVAS, pixels_per_unit=_PPU,
        deformers=[RotationDeformerSpec(deformer_id="RotEye", keyforms=[
            RotationKeyform(0.0, angle=_ANGLE,
                            origin=(spec_origin_units[0] * _PPU,
                                    spec_origin_units[1] * _PPU))])])
    return _render(tmp, compile_static_rig(spec), "via_deformer")


def _reference(tmp: Path, about_units, tag: int) -> dict:
    """没有变形器，顶点按 about（单位坐标）预先转过 _ANGLE。"""
    about_px = (about_units[0] * _PPU, about_units[1] * _PPU)
    verts = _rot([(x / _PPU * _PPU, y / _PPU * _PPU) for x, y in _VERTS],
                 _ANGLE, about_px)
    mesh = MeshSpec(mesh_id="ArtMeshEye", vertices=list(verts),
                    triangles=[(0, 1, 2)], uvs=list(_UVS))
    spec = RigSpec(meshes=[mesh],
                   parameters=[ParameterSpec("ParamEyeRotX", -30.0, 30.0, 0.0)],
                   canvas_width=_CANVAS, canvas_height=_CANVAS,
                   pixels_per_unit=_PPU)
    return _render(tmp, compile_static_rig(spec), f"ref{tag}")


def main() -> int:
    wx, wy = _WANT
    a = math.radians(_ANGLE)
    cos, sin = math.cos(a), math.sin(a)
    rot = lambda p: (p[0] * cos - p[1] * sin, p[0] * sin + p[1] * cos)   # noqa: E731
    rot_neg = lambda p: (p[0] * cos + p[1] * sin, -p[0] * sin + p[1] * cos)  # noqa: E731
    candidates = {
        "就是想要的 p        ": _WANT,
        "R(A)·p             ": rot(_WANT),
        "R(-A)·p            ": rot_neg(_WANT),
        "y 取反             ": (wx, -wy),
        "x 取反             ": (-wx, wy),
        "两轴都取反         ": (-wx, -wy),
        "y 取反后再 R(A)    ": rot((wx, -wy)),
        "y 取反后再 R(-A)   ": rot_neg((wx, -wy)),
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        got = _via_deformer(tmp, _WANT)
        if not got["ok"]:
            print(got["blocker"])
            return 1
        print(f"角度 {_ANGLE}° 想要绕的枢轴 p={_WANT}  "
              f"变形器渲染 sha={got['pixels_sha256'][:16]} bbox={got['alpha_bbox']}")
        hits = []
        for number, (label, about) in enumerate(candidates.items()):
            ref = _reference(tmp, about, number)
            same = ref["pixels_sha256"] == got["pixels_sha256"]
            print(f"  参照绕 {str(tuple(round(v, 3) for v in about)):>16s} "
                  f"{label} sha={ref['pixels_sha256'][:16]} "
                  f"bbox={ref['alpha_bbox']} 相同={same}")
            if same:
                hits.append((label, about))
    print("\n命中:", [(h[0], tuple(round(v, 4) for v in h[1])) for h in hits]
          or "无 —— 内核使用的枢轴不在这些候选里")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
