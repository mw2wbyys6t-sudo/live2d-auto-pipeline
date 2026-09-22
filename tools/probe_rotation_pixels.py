#!/usr/bin/env python3
"""用真实渲染定 rotation 变形器的单位、朝向与原点空间（判据：恒等必须逐像素不变）。

编译器已经能写出 rotation 段，但「angles 是角度还是弧度、正方向是顺时针还是逆时针、
origin 在单位空间还是像素空间、base_angle 是否参与求和、scale 是不是倍率」这些
都必须由官方内核的实际画面定，不能照文档猜（文档在 motion 段类型上就已经错了一位）。

做法：视口映射先用「已知顶点的恒等渲染」反解（bbox 四个边界 = 四个方程），
之后所有预测都在模型单位空间里算，再映回视口比对。
运行：LIVE2D_TEST_ROOT=E:/Live2D/beacon .venv/Scripts/python.exe tools/probe_rotation_pixels.py
"""
from __future__ import annotations

import json
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

_PPU = 100.0
_PARAM = "ParamEyeRotX"
# 不对称直角三角形：能同时暴露角度大小与旋转方向（中心对称图形做不到）
_VERTS_PX = [(0.0, 0.0), (200.0, 0.0), (0.0, 150.0)]
_UVS = [(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)]


def _spec(keyforms, *, base_angle=0.0, deformed=True, parameter=False):
    mesh = MeshSpec(mesh_id="ArtMeshEye", vertices=_VERTS_PX,
                    triangles=[(0, 1, 2)], uvs=_UVS,
                    deformer_id="RotEye" if deformed else "")
    deformer = RotationDeformerSpec(
        deformer_id="RotEye", keyforms=keyforms, base_angle=base_angle,
        parameter_id=_PARAM if parameter else "")
    return RigSpec(
        meshes=[mesh],
        parameters=[ParameterSpec(_PARAM, minimum=-30.0, maximum=30.0,
                                  default=0.0)],
        canvas_width=1024.0, canvas_height=1024.0, pixels_per_unit=_PPU,
        deformers=[deformer] if deformed else [])


def _package(tmp: Path, spec: RigSpec, name: str) -> Path:
    from PIL import Image

    folder = tmp / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.moc3").write_bytes(compile_static_rig(spec).to_bytes())
    # 纹理左下半不透明、右上半透明：质心偏移能反映镜像/翻转
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for y in range(size):
        for x in range(size):
            if x + y < size:
                img.putpixel((x, y), (255, 0, 255, 255))
    img.save(folder / "t.png")
    manifest = folder / f"{name}.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3, "Meta": {"ArchiveName": name},
        "FileReferences": {"Moc": f"{name}.moc3", "Textures": ["t.png"]},
    }), encoding="utf-8")
    return manifest


def _units(vertices_px):
    return [(x / _PPU, y / _PPU) for x, y in vertices_px]


def _fit_transform(points_px, bbox, centroid):
    """由恒等渲染反解 视口 = (a*u + b, c*v + d)：用 bbox 四边 + 质心。"""
    x0, y0, x1, y1 = bbox
    us = _units(points_px)
    u_min = min(u for u, _v in us)
    u_max = max(u for u, _v in us)
    v_min = min(v for _u, v in us)
    v_max = max(v for _u, v in us)
    # bbox 是整幅内容的包围盒，三角形只有一个像素级不对称，先用质心兜住偏移
    return {"bbox": [x0, y0, x1, y1], "centroid": list(centroid),
            "unit_extents": [u_min, v_min, u_max, v_max]}


def main() -> int:
    cases = {
        "plain_no_deformer": (_spec([], deformed=False), None),
        "identity_angle0": (
            _spec([RotationKeyform(0.0, angle=0.0)], parameter=False), None),
        "angle90_origin0": (
            _spec([RotationKeyform(0.0, angle=90.0)], parameter=False), None),
        "angle_minus90_origin0": (
            _spec([RotationKeyform(0.0, angle=-90.0)], parameter=False), None),
        "angle90_origin_1unit": (
            _spec([RotationKeyform(0.0, angle=90.0, origin=(1.0, 0.0))],
                  parameter=False), None),
        "angle90_origin_100px": (
            _spec([RotationKeyform(0.0, angle=90.0, origin=(100.0, 0.0))],
                  parameter=False), None),
        "base10_angle0": (
            _spec([RotationKeyform(0.0, angle=0.0)], base_angle=10.0,
                  parameter=False), None),
        "angle10_base0": (
            _spec([RotationKeyform(0.0, angle=10.0)], parameter=False), None),
        "scale2": (
            _spec([RotationKeyform(0.0, angle=0.0, scale=2.0)],
                  parameter=False), None),
        "scale0": (
            _spec([RotationKeyform(0.0, angle=0.0, scale=0.0)],
                  parameter=False), None),
        "scale_minus1": (
            _spec([RotationKeyform(0.0, angle=0.0, scale=-1.0)],
                  parameter=False), None),
        "reflect_x": (
            _spec([RotationKeyform(0.0, angle=0.0, reflect_x=1)],
                  parameter=False), None),
        "driven_keys": (
            _spec([RotationKeyform(-30.0, angle=-30.0), RotationKeyform(0.0),
                   RotationKeyform(30.0, angle=30.0)], parameter=True), None),
    }
    out = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for name, (spec, _extra) in cases.items():
            manifest = _package(tmp, spec, name)
            shots = {}
            values = [-30.0, 0.0, 30.0] if name == "driven_keys" else [None]
            for value in values:
                probe_params = {_PARAM: value} if value is not None else None
                shots[str(value)] = render_probe(str(manifest), probe_params)
            out[name] = {
                key: {"ok": r["ok"], "blocker": r["blocker"],
                      "bbox": r["alpha_bbox"], "centroid": r["centroid"],
                      "opaque": r["opaque_pixels"], "sha": r["pixels_sha256"][:12]}
                for key, r in shots.items()}
    base = out["plain_no_deformer"]["None"]
    print("\ncase / value          ok    bbox l,b,r,t        centroid        opaque  =plain  sha")
    for name, shots in out.items():
        for value, r in shots.items():
            bbox = r["bbox"] or [-1, -1, -1, -1]
            centroid = r["centroid"] or [float("nan"), float("nan")]
            same = "Y" if (value == "None" and r["sha"] == base["sha"]) else ""
            print(f"{name:20s} {str(r['ok']):5s} "
                  f"{bbox[0]:4d},{bbox[1]:4d},{bbox[2]:4d},{bbox[3]:4d}  "
                  f"{centroid[0]:8.1f},{centroid[1]:8.1f} {r['opaque']:8d}  "
                  f"{same:5s}  {r['sha']} ({value})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
