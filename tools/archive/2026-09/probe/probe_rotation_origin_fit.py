#!/usr/bin/env python3
"""反解 rotation 键形 origin 的真实语义（实心纹理版，避免遮罩裁剪污染测量）。

模型：p' = o + R(p − o) = R(p) + (I − R)·o ⇒ 同一角度、不同 origin 的两次渲染
只差平移 t = (I − R)·o。用 bbox 左/下两个边各自测一次 t，两者必须一致
（不一致就说明测量被别的东西污染了）。再解 o_eff = (I − R)^{-1}·t，
与「写进文件的单位值」对表 —— 关系一看就知道，不靠猜符号。

运行：LIVE2D_TEST_ROOT=E:/Live2D/beacon .venv/Scripts/python.exe tools/probe_rotation_origin_fit.py
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
_ANGLE = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
_VERTS = [(0.0, 0.0), (300.0, 0.0), (0.0, 100.0)]   # 明显不对称的直角三角形
_UVS = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]


def _rot_ccw(points, degrees):
    theta = math.radians(degrees)
    cos, sin = math.cos(theta), math.sin(theta)
    return [(x * cos - y * sin, x * sin + y * cos) for x, y in points]


def _render(tmp: Path, written, name: str) -> dict:
    """written = 直接写进 moc3 的 origin（单位坐标）。

    编译器只做 ÷ppu（规格层是画布像素），所以规格层传 written*ppu。
    """
    mesh = MeshSpec(mesh_id="ArtMeshEye", vertices=list(_VERTS),
                    triangles=[(0, 1, 2)], uvs=list(_UVS),
                    deformer_id="RotEye")
    spec = RigSpec(
        meshes=[mesh],
        parameters=[ParameterSpec("ParamEyeRotX", -30.0, 30.0, 0.0)],
        canvas_width=_CANVAS, canvas_height=_CANVAS, pixels_per_unit=_PPU,
        deformers=[RotationDeformerSpec(deformer_id="RotEye", keyforms=[
            RotationKeyform(0.0, angle=_ANGLE,
                            origin=(written[0] * _PPU, written[1] * _PPU))])])
    folder = tmp / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "m.moc3").write_bytes(compile_static_rig(spec).to_bytes())
    from PIL import Image
    Image.new("RGBA", (32, 32), (255, 0, 255, 255)).save(folder / "t.png")
    manifest = folder / "m.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3, "Meta": {"ArchiveName": name},
        "FileReferences": {"Moc": "m.moc3", "Textures": ["t.png"]},
    }), encoding="utf-8")
    return render_probe(str(manifest))


def _solve(delta_units, degrees):
    theta = math.radians(degrees)
    cos, sin = math.cos(theta), math.sin(theta)
    # I − R = [[1-cos, sin], [-sin, 1-cos]]
    det = (1 - cos) ** 2 + sin ** 2
    tx, ty = delta_units
    return (((1 - cos) * tx - sin * ty) / det,
            (sin * tx + (1 - cos) * ty) / det)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        base = _render(tmp, (0.0, 0.0), "base")
        if not base["ok"]:
            print(base["blocker"])
            return 1
        # 视口尺度不硬猜：由基准渲染的 bbox 与已知的模型跨度反解（两轴必须一致）
        model = _rot_ccw([(x / _PPU, y / _PPU) for x, y in _VERTS], _ANGLE)
        span_x = max(p[0] for p in model) - min(p[0] for p in model)
        span_y = max(p[1] for p in model) - min(p[1] for p in model)
        bx0, by0, bx1, by1 = base["alpha_bbox"]
        scale_x = (bx1 - bx0) / span_x
        scale_y = (by1 - by0) / span_y
        print(f"角度 {int(_ANGLE)}°  px/单位: x={scale_x:.2f} y={scale_y:.2f}"
              f"{'（一致）' if abs(scale_x - scale_y) < 0.5 else '（不一致！）'}")
        print(f"{'想要的枢轴(单位)':>22s} {'内核实际绕的(单位)':>24s} "
              f"{'误差(单位)':>18s} 尺寸同")
        total = math.radians(_ANGLE)
        cos, sin = math.cos(total), math.sin(total)
        for want in ((0.5, 0.0), (0.0, 0.5), (0.6, -0.25), (-0.4, 0.8)):
            # 编译器现在会把规格值预先转 -角度；工具直接给「想要绕的点」
            got = _render(tmp, want, "w_" + "_".join(map(str, want)))
            gx0, gy0, gx1, gy1 = got["alpha_bbox"]
            # 基准是绕 0 转，样本绕 want 转：t = (I − R)·want（若规则正确）
            expect = ((1 - cos) * want[0] + sin * want[1],
                      -sin * want[0] + (1 - cos) * want[1])
            measured = ((gx0 - bx0) / scale_x, -(gy0 - by0) / scale_y)
            solved = _solve(measured, _ANGLE)
            same_size = abs((gx1 - gx0) - (bx1 - bx0)) <= 1 \
                and abs((gy1 - gy0) - (by1 - by0)) <= 1
            print(f"{str(want):>22s} ({solved[0]:+.3f},{solved[1]:+.3f})   "
                  f"({solved[0] - want[0]:+.3f},{solved[1] - want[1]:+.3f})"
                  f"  t实测({measured[0]:+.2f},{measured[1]:+.2f}) "
                  f"t应为({expect[0]:+.2f},{expect[1]:+.2f}) 尺寸同={same_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
