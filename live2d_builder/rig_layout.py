#!/usr/bin/env python3
"""绑定前的准备：把分层结果变成「可绑骨骼的部件 + 像素网格 + 覆盖率报告」。

这一层解决三个此前断开的问题：

1. **粒度**：语义分层给的是「一条完整手臂」这种大件，只能整体平移。这里按
   关节把它拆成上臂/前臂、大腿/小腿，长发按段拆开，每段一个枢轴——骨骼链
   才有地方落点。
2. **覆盖**：网格不再用 Delaunay 重心过滤（它会漏掉凹陷与细长部件的像素），
   改用自适应像素格网，覆盖在构造上就是 100%，可以逐像素验收。
3. **闭环**：输出一份覆盖率报告，明确「角色有多少像素被网格瞄住、每个部件
   用了多少顶点/三角形、网格又向外多框了多少透明区域」。不做无声猜测。

这一层不碰 moc3，只产出可直接导入 Cubism 的图层 + 网格 + 报告。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from core.segment_engine.rig_split import build_rig_parts
from live2d_builder.mesh.pixel_grid import (
    PixelGridMesher,
    coverage_of_parts,
    measure_coverage,
)

#: 图层 alpha 高于此值算作部件像素（与 MeshGenerator 的默认阈值一致）。
ALPHA_THRESHOLD = 128

#: 预览图里各部件的网格描边颜色，按名称哈希取色，保证同一部件颜色稳定。
_PREVIEW_COLORS = [
    (255, 99, 99), (99, 190, 255), (120, 230, 140), (255, 200, 80),
    (200, 130, 255), (80, 230, 220), (255, 140, 210), (170, 200, 90),
]


def _mask_of(layer: Image.Image, shape: Tuple[int, int]) -> np.ndarray:
    arr = np.array(layer.convert("RGBA"))
    mask = arr[:, :, 3] >= ALPHA_THRESHOLD
    if mask.shape != shape:
        raise ValueError(f"图层尺寸 {mask.shape} 与画布 {shape} 不一致")
    return mask


def _color_for(name: str) -> Tuple[int, int, int]:
    return _PREVIEW_COLORS[sum(bytearray(name.encode("utf-8"))) % len(_PREVIEW_COLORS)]


def build_rig_layout(
    layers: Dict[str, Image.Image],
    *,
    image: Optional[Image.Image] = None,
    silhouette: Optional[np.ndarray] = None,
    body_center: Optional[Tuple[float, float]] = None,
    spacing: int = 16,
    min_cell: int = 4,
    add_residual: bool = True,
    silhouette_source: str = "union_of_parts",
) -> Dict[str, Any]:
    """图层 -> 关节部件 + 像素网格 + 覆盖率报告。

    Args:
        layers: 部件名 -> RGBA 图层。
        image: 原图，用于切出各部件彩色 PNG 与画预览。
        silhouette: 角色轮廓布尔掩码。给了才能回答「角色是否 100% 被瞄住」，
            并能补出 ``residual``（残差）层收掉没被任何部件认领的像素。
        body_center: 躯干参考点，用于判定肢体近端/远端；默认取轮廓质心。
        spacing: 初始格边长（像素）。
        min_cell: 边界细分下界（像素）。
        add_residual: 有轮廓时是否补残差层。为 True 时覆盖率必然能到 100%。
        silhouette_source: 轮廓来源，如实写进报告（``alpha`` / ``model:*`` /
            ``corner-color(estimated)`` 等），避免把估计值当成精确结果。

    Returns:
        ``{"parts", "meshes", "meta", "report", "warnings", "shape"}``
    """
    if not layers:
        raise ValueError("layers 为空，没有可绑定的部件")

    first = next(iter(layers.values())).convert("RGBA")
    w, h = first.size
    shape = (h, w)

    masks: Dict[str, np.ndarray] = {}
    for name, layer in layers.items():
        mask = _mask_of(layer, shape)
        if mask.any():
            masks[str(name)] = mask
    if not masks:
        raise ValueError("所有图层都是空的，没有可绑定的部件")

    # 轮廓：外部没给就退化为「所有图层并集」，此时覆盖率只对部件自身有意义
    if silhouette is None:
        sil = np.zeros(shape, dtype=bool)
        for m in masks.values():
            sil |= m
        sil_from_input = False
    else:
        sil = np.asarray(silhouette, dtype=bool)
        if sil.shape != shape:
            raise ValueError(f"silhouette 尺寸 {sil.shape} 与画布 {shape} 不一致")
        sil_from_input = True

    if body_center is None:
        ys, xs = np.nonzero(sil)
        body_center = (float(xs.mean()), float(ys.mean())) if len(xs) else None

    parts, meta, warnings = build_rig_parts(masks, near=body_center)

    # 残差层：轮廓里没被任何部件认领的像素。不加这一层，「全部瞄点住」
    # 就无从谈起——实测 demo 输入有约 2% 的轮廓像素落在所有部件之外。
    residual_px = 0
    if add_residual and sil_from_input:
        claimed = np.zeros(shape, dtype=bool)
        for m in parts.values():
            claimed |= m
        residual = sil & ~claimed
        residual_px = int(residual.sum())
        if residual_px:
            parts["residual"] = residual
            meta["residual"] = {
                "role": "residual",
                "parent": None,
                "source": None,
                "pixels": residual_px,
                "note": "轮廓中未被任何部件认领的像素，用于保证网格100%覆盖",
            }
            warnings.append(
                f"轮廓有 {residual_px} px 未被任何部件认领，已收入 residual 层"
            )

    mesher = PixelGridMesher(spacing=spacing, min_cell=min_cell)
    meshes: Dict[str, Dict] = {}
    for name, mask in parts.items():
        meshes[name] = mesher.build(mask)

    for name, mask in parts.items():
        meta.setdefault(name, {})
        meta[name]["coverage"] = measure_coverage(mask, meshes[name])

    overall = coverage_of_parts(parts, meshes, sil if sil_from_input else None)

    report = {
        "canvas": {"width": w, "height": h},
        "params": {"spacing": spacing, "min_cell": min_cell},
        "silhouette_from_input": sil_from_input,
        "silhouette_source": silhouette_source if sil_from_input else "union_of_parts",
        "part_count": len(parts),
        "residual_px": residual_px,
        "overall": overall,
        "warnings": list(warnings),
    }

    return {
        "parts": parts,
        "meshes": meshes,
        "meta": meta,
        "report": report,
        "warnings": warnings,
        "shape": shape,
        "image": image,
    }


def _layer_rgba(
    image: Optional[Image.Image], mask: np.ndarray, shape: Tuple[int, int]
) -> Image.Image:
    """按掩码从原图切出 RGBA 图层；没有原图时用白底，只保留形状。"""
    if image is None:
        arr = np.zeros((shape[0], shape[1], 4), dtype=np.uint8)
        arr[mask] = (235, 235, 235, 255)
        return Image.fromarray(arr, "RGBA")
    rgb = np.array(image.convert("RGB"))
    out = np.zeros((shape[0], shape[1], 4), dtype=np.uint8)
    out[:, :, :3] = rgb
    out[:, :, 3] = np.where(mask, 255, 0).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def _draw_preview(
    result: Dict[str, Any], base: Optional[Image.Image]
) -> Image.Image:
    """把网格描边画在原图上：直接看「人物有没有被网格全部瞄住」。"""
    h, w = result["shape"]
    if base is None:
        canvas = np.full((h, w, 3), 32, dtype=np.uint8)
    else:
        canvas = (np.array(base.convert("RGB")).astype(np.float32) * 0.45).astype(np.uint8)

    for name, mesh in result["meshes"].items():
        color = _color_for(name)
        pts = np.asarray(mesh.get("vertices"), dtype=float)
        idx = mesh.get("indices") or []
        if pts.size == 0 or not idx:
            continue
        # 同一条边会被相邻三角形共享，先按顶点对去重，避免重复画
        edges = set()
        for tri in idx:
            a, b, c = int(tri[0]), int(tri[1]), int(tri[2])
            for u, v in ((a, b), (b, c), (c, a)):
                edges.add((u, v) if u < v else (v, u))
        for u, v in edges:
            p0 = pts[u]
            p1 = pts[v]
            canvas = _line(canvas, p0, p1, color)

    # 关节与枢轴单独标点：一眼能看出骨骼该落在哪
    for name, info in result["meta"].items():
        for key, radius in (("pivot", 5), ("joint", 4)):
            pos = info.get(key)
            if not pos:
                continue
            cx, cy = int(round(pos[0])), int(round(pos[1]))
            if 0 <= cy < h and 0 <= cx < w:
                canvas[
                    max(0, cy - radius) : min(h, cy + radius + 1),
                    max(0, cx - radius) : min(w, cx + radius + 1),
                ] = (255, 255, 0) if key == "joint" else (0, 255, 255)

    return Image.fromarray(canvas, "RGB")


def _line(canvas: np.ndarray, p0: np.ndarray, p1: np.ndarray, color) -> np.ndarray:
    """Bresenham 直线（避免为了画预览图引入 cv2 依赖分支）。"""
    h, w = canvas.shape[:2]
    x0, y0 = int(round(p0[0])), int(round(p0[1]))
    x1, y1 = int(round(p1[0])), int(round(p1[1]))
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    guard = 0
    while True:
        if 0 <= x0 < w and 0 <= y0 < h:
            canvas[y0, x0] = color
        if x0 == x1 and y0 == y1:
            break
        guard += 1
        if guard > 4 * (w + h):  # 防御：坏数据不进入死循环
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
    return canvas


def write_rig_layout(
    result: Dict[str, Any],
    out_dir: str | Path,
    *,
    preview: bool = True,
) -> Dict[str, str]:
    """把布局结果落盘：部件 PNG、meshes.json、coverage.json、预览图。

    Returns:
        ``{"layers_dir", "meshes", "coverage", "layout", "preview"}`` 路径字典。
    """
    out = Path(out_dir)
    layers_dir = out / "parts"
    layers_dir.mkdir(parents=True, exist_ok=True)

    image = result.get("image")
    shape = result["shape"]

    for name, mask in result["parts"].items():
        _layer_rgba(image, mask, shape).save(layers_dir / f"{name}.png")

    meshes_path = out / "meshes.json"
    payload = {}
    for name, mesh in result["meshes"].items():
        pts = np.asarray(mesh.get("vertices"), dtype=float)
        payload[name] = {
            "vertices": [[round(float(x), 3), round(float(y), 3)] for x, y in pts],
            "vertices_norm": [
                [round(float(x), 6), round(float(y), 6)]
                for x, y in np.asarray(mesh.get("vertices_norm"), dtype=float)
            ],
            "indices": [[int(a), int(b), int(c)] for a, b, c in (mesh.get("indices") or [])],
            "stats": mesh.get("stats", {}),
        }
    meshes_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    coverage_path = out / "coverage.json"
    coverage_payload = {
        "report": result["report"],
        "parts": {
            name: {
                **result["meta"].get(name, {}),
                "pixels": int(result["parts"][name].sum()),
                "coverage": result["meta"]
                .get(name, {})
                .get("coverage", measure_coverage(result["parts"][name], result["meshes"][name])),
            }
            for name in result["parts"]
        },
    }
    coverage_path.write_text(
        json.dumps(coverage_payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    layout_path = out / "rig_layout.json"
    layout_path.write_text(
        json.dumps(
            {
                "canvas": result["report"]["canvas"],
                "params": result["report"]["params"],
                "silhouette_source": result["report"].get("silhouette_source"),
                "part_count": result["report"]["part_count"],
                "residual_px": result["report"]["residual_px"],
                "overall": result["report"]["overall"],
                "meta": result["meta"],
                "warnings": result["warnings"],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    paths = {
        "layers_dir": str(layers_dir),
        "meshes": str(meshes_path),
        "coverage": str(coverage_path),
        "layout": str(layout_path),
        "preview": "",
    }
    if preview:
        preview_path = out / "mesh_preview.png"
        _draw_preview(result, image).save(preview_path)
        paths["preview"] = str(preview_path)
    return paths


def coverage_table(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """整理成可直接打印的表格行（按部件像素从大到小）。"""
    rows: List[Dict[str, Any]] = []
    for name, mask in result["parts"].items():
        info = result["meta"].get(name, {})
        cov = info.get("coverage") or measure_coverage(mask, result["meshes"][name])
        rows.append({
            "part": name,
            "role": info.get("role", ""),
            "parent": info.get("parent"),
            "pixels": int(mask.sum()),
            "vertices": cov["vertices"],
            "triangles": cov["triangles"],
            "coverage": cov["coverage"],
            "over_cover": cov["over_cover"],
            "pivot": info.get("pivot"),
            "joint": info.get("joint"),
        })
    rows.sort(key=lambda r: r["pixels"], reverse=True)
    return rows
