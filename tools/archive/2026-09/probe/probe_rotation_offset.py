#!/usr/bin/env python3
"""定 rotation 键形 origin 的真实变换：先证伪「纯枢轴」，再测 平移分量。

实测推翻了两轮推测：角度为 0 时 origin 依然改变画面
（tests/integration/test_moc3_rotation_acceptance.py::test_origin_is_irrelevant_at_zero_angle
  曾因此失败），所以它不是纯枢轴 —— 变换里有一项与 origin 成正比的平移。

判法保持零量化：把网格按「先绕 0 转 A，再平移 v」预处理好渲染一份参照，
与变形器渲染对 sha；命中的 v 就是该角度下 origin 实际产生的平移。
运行：LIVE2D_TEST_ROOT=E:/Live2D/beacon .venv/Scripts/python.exe tools/probe_rotation_offset.py
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
_VERTS = [(0.0, 0.0), (300.0, 0.0), (0.0, 100.0)]      # 模型像素
_UVS = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
_ORIGIN_UNITS = (0.6, -0.25)      # 规格层会除以 ppu 写进文件；这里直接用单位值
_ANGLES = (0.0, 30.0, 60.0, -45.0)


def _build(tmp: Path, doc, name: str) -> Path:
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


def _rig_spec(meshes, deformers=()):
    return RigSpec(meshes=meshes,
                   parameters=[ParameterSpec("ParamEyeRotX", -30.0, 30.0, 0.0)],
                   canvas_width=_CANVAS, canvas_height=_CANVAS,
                   pixels_per_unit=_PPU, deformers=list(deformers))


def _mesh(vertices, parent=""):
    return MeshSpec(mesh_id="ArtMeshEye", vertices=list(vertices),
                    triangles=[(0, 1, 2)], uvs=list(_UVS), deformer_id=parent)


def _reference(angle, offset_units):
    """参照：顶点先绕模型原点转 angle，再平移 offset（都在模型像素里做）。"""
    theta = math.radians(angle)
    cos, sin = math.cos(theta), math.sin(theta)
    verts = []
    for x, y in _VERTS:
        px = x * cos - y * sin + offset_units[0] * _PPU
        py = x * sin + y * cos + offset_units[1] * _PPU
        verts.append((px, py))
    return _rig_spec([_mesh(verts)])


def _via_deformer(angle, origin_units):
    """编译器只接受中性值（非零角度 + 非零原点被明确拒绝），所以这里编译完
    再直接改写那三个字段 —— 探测工具要能观察内核的真实行为，不受产品侧守卫限制。
    """
    spec = _rig_spec(
        [_mesh(_VERTS, parent="RotEye")],
        [RotationDeformerSpec(deformer_id="RotEye", keyforms=[
            RotationKeyform(0.0, angle=0.0, origin=(0.0, 0.0))])])
    doc = compile_static_rig(spec)
    doc.set("rotation_deformer_keyform.angles", [float(angle)])
    doc.set("rotation_deformer_keyform.origin_xs", [float(origin_units[0])])
    doc.set("rotation_deformer_keyform.origin_ys", [float(origin_units[1])])
    return doc


def main() -> int:
    wx, wy = _ORIGIN_UNITS
    theta_all = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for angle in _ANGLES:
            got = render_probe(str(_build(tmp, _via_deformer(angle, _ORIGIN_UNITS),
                                          f"rot{angle}")))
            if not got["ok"]:
                print(f"{angle}° 渲染失败: {got['blocker']}")
                continue
            a = math.radians(angle)
            cos, sin = math.cos(a), math.sin(a)
            candidates = {
                "w": (wx, wy),
                "-w": (-wx, -wy),
                "conj_y(w)": (wx, -wy),
                "-conj_y(w)": (-wx, wy),
                "R(A)w": (wx * cos - wy * sin, wx * sin + wy * cos),
                "R(-A)w": (wx * cos + wy * sin, -wx * sin + wy * cos),
                "(I-R)w": (wx - (wx * cos - wy * sin),
                           wy - (wx * sin + wy * cos)),
                "R(A)w - w": (wx * cos - wy * sin - wx,
                              wx * sin + wy * cos - wy),
            }
            hits = []
            for tag, (vx, vy) in candidates.items():
                ref = render_probe(str(_build(tmp, compile_static_rig(
                    _reference(angle, (vx, vy))), f"ref{angle}{abs(hash(tag))}")),
                    prime=False)
                if ref["ok"] and ref["pixels_sha256"] == got["pixels_sha256"]:
                    hits.append(tag)
            theta_all[angle] = (got["pixels_sha256"][:12], hits)
            print(f"{angle:+6.1f}° 变形器 sha={got['pixels_sha256'][:12]} "
                  f"bbox={got['alpha_bbox']}  命中的平移候选: {hits or '无'}")
    print("\n结论候选：origin 产生的平移 =", theta_all)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
