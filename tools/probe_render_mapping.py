#!/usr/bin/env python3
"""实验：确定 live2d-py 视口映射规律与坐标系朝向。

假设待验证：视口把「±1 单位」映射为整屏（非等比），与 moc3 声明的画布宽高无关。
若成立，则渲染 bbox 只取决于 位置/pixels_per_unit，可预测：
    bbox_w = 400 * (2*half/ppu) / 2,  bbox_h = 500 * (2*half/ppu) / 2

运行：.venv/Scripts/python.exe tools/probe_render_mapping.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.moc3_probe_kit import square, write_package  # noqa: E402
from drivers.live2d_runtime.moc3_verify import render_probe  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    MeshSpec, ParameterSpec, RigSpec, compile_static_rig,
)

WORK = ROOT / "Work" / "render-mapping"
VIEW_W, VIEW_H = 400, 500


def build(half: float, ppu: float, cx: float = 0.0, cy: float = 0.0,
          name: str = "probe"):
    verts, uvs, tris = square(cx, cy, half)
    spec = RigSpec(
        meshes=[MeshSpec("ArtMesh1", verts, tris, uvs)],
        parameters=[ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0)],
        canvas_width=512.0, canvas_height=512.0, pixels_per_unit=ppu)
    doc = compile_static_rig(spec)
    return write_package(WORK / name, doc.to_bytes(), name)


def report(label: str, manifest: Path, expect_units: float):
    result = render_probe(str(manifest), width=VIEW_W, height=VIEW_H,
                          png=str(manifest.parent / f"{label}.png"))
    if not result["ok"]:
        print(f"{label:26s} 渲染失败: {result['blocker']}")
        return
    x0, y0, x1, y1 = result["alpha_bbox"]
    exp_w = VIEW_W * expect_units / 2.0
    exp_h = VIEW_H * expect_units / 2.0
    # GL 原点在左下；换算成「图像坐标」便于判断上下
    top_from_top = VIEW_H - 1 - y1
    print(f"{label:26s} bbox=({x0},{y0})-({x1},{y1}) "
          f"w={x1 - x0 + 1} h={y1 - y0 + 1} "
          f"| 预测 w≈{exp_w:.0f} h≈{exp_h:.0f} "
          f"| 单位方框={expect_units:.3f} "
          f"| 图像坐标顶边={top_from_top} 不透明={result['opaque_pixels']}")


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    cases = [
        ("half100_ppu100", 100.0, 100.0, 2.0),      # 之前观测到的「铺满全屏」
        ("half10_ppu100", 10.0, 100.0, 0.2),        # 应当只占 1/10 屏
        ("half256_ppu512", 256.0, 512.0, 1.0),      # 官方惯例：画布宽=1 单位
        ("half256_ppu256", 256.0, 256.0, 2.0),      # 画布宽=2 单位
    ]
    for label, half, ppu, units in cases:
        manifest = build(half, ppu, name=label)
        report(label, manifest, units)

    # 朝向：在画布左上角放一个小标记，看它落在屏幕哪个角
    marker = build(30.0, 100.0, cx=-150.0, cy=150.0, name="marker_topleft")
    r = render_probe(str(marker), width=VIEW_W, height=VIEW_H,
                     png=str(WORK / "marker_topleft.png"))
    x0, y0, x1, y1 = r["alpha_bbox"]
    print(f"\n左上角标记: GL bbox=({x0},{y0})-({x1},{y1}) "
          f"→ 图像坐标 ({x0},{VIEW_H - 1 - y1})-({x1},{VIEW_H - 1 - y0})")
    print("若图像坐标落在左上，则 y 向上约定与 UV 约定一致；落在左下则为上下颠倒")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
