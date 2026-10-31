"""bindings.json：编译后绑定表的可读投影，供自研渲染器做真实形变。

moc3 里的键形与 warp 变形器是二进制；前端要「按参数把顶点摆到位」，就需要
同一份数据。这里不做任何推断，只做三件事：

1. 坐标统一换成**图层图像坐标**（左上原点、y 向下），与 ``{name}.meshes.json``
   同一空间。模型坐标 = 图像坐标减半画布（见 ``to_model_xy`` 的实测结论：
   内核把 y 当向下为正），所以这里只做平移，**不翻 y**。
2. 多轴带按 moc3 的展平序原样导出（第一个轴步长为 1），插值交给前端。
3. 引用挂进 model3.json 的 ``FileReferences.Bindings``（非标准键，官方内核
   只读它认识的字段，多余键不影响加载）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from core.logger import get_logger

from live2d_builder.exporter.moc3_model import (
    RigSpec,
    WarpDeformerSpec,
)

log = get_logger("exporter.bindings")

BINDINGS_VERSION = 1
# 千分之一像素已经远低于渲染精度，保留更多位数只是让文件变大。
_POSITION_DECIMALS = 3
_UV_DECIMALS = 5


def _round(values: Sequence[float], digits: int) -> List[float]:
    return [round(float(v), digits) for v in values]


def _flat(points: Sequence[Sequence[float]], off_x: float, off_y: float,
          digits: int = _POSITION_DECIMALS) -> List[float]:
    out: List[float] = []
    for x, y in points:
        out.append(round(float(x) + off_x, digits))
        out.append(round(float(y) + off_y, digits))
    return out


def _axes_of(mesh) -> List[List[Any]]:
    """网格的驱动轴：优先多轴带；单轴由 keyform_parameter_id 推出。"""
    if mesh.keyform_axes:
        return [[str(pid), _round(keys, _POSITION_DECIMALS)]
                for pid, keys in mesh.keyform_axes]
    if mesh.keyform_parameter_id and mesh.keyform_shapes:
        keys = [shape.key_value for shape in mesh.keyform_shapes]
        return [[str(mesh.keyform_parameter_id), _round(keys, _POSITION_DECIMALS)]]
    return []


def _mesh_entry(mesh, off_x: float, off_y: float) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "id": mesh.mesh_id,
        "part": mesh.effective_part_id,
        "vertices": _flat(mesh.vertices, off_x, off_y),
        "uvs": [round(float(c), _UV_DECIMALS) for uv in mesh.uvs for c in uv],
        "indices": [int(i) for tri in mesh.triangles for i in tri],
        "drawOrder": float(mesh.draw_order),
        "opacity": float(mesh.opacity),
        "deformer": str(mesh.deformer_id or ""),
    }
    axes = _axes_of(mesh)
    if axes and mesh.keyform_shapes:
        entry["keyforms"] = {
            "axes": axes,
            "vertices": [_flat(shape.vertices, off_x, off_y)
                         for shape in mesh.keyform_shapes],
        }
        opacities = [shape.opacity for shape in mesh.keyform_shapes]
        if any(o is not None for o in opacities):
            # 未指定的键沿用网格自身不透明度；缺省值在这里就填平，前端不用猜。
            entry["keyforms"]["opacities"] = [
                float(mesh.opacity if o is None else o) for o in opacities]
    return entry


def _deformer_entry(deformer: WarpDeformerSpec, off_x: float,
                    off_y: float) -> Dict[str, Any]:
    rest = deformer.grids[0].points
    xs = [float(p[0]) for p in rest]
    ys = [float(p[1]) for p in rest]
    return {
        "id": deformer.deformer_id,
        "rows": int(deformer.rows),
        "cols": int(deformer.cols),
        "parent": str(deformer.parent_deformer_id or ""),
        "rest": [
            round(min(xs) + off_x, _POSITION_DECIMALS),
            round(min(ys) + off_y, _POSITION_DECIMALS),
            round(max(xs) + off_x, _POSITION_DECIMALS),
            round(max(ys) + off_y, _POSITION_DECIMALS),
        ],
        "axes": [[str(pid), _round(keys, _POSITION_DECIMALS)]
                 for pid, keys in deformer.axes_spec],
        "grids": [_flat(grid.points, off_x, off_y) for grid in deformer.grids],
    }


def build_bindings_document(spec: RigSpec) -> Dict[str, Any]:
    """RigSpec -> 可 JSON 化的绑定文档（坐标已换成图层图像坐标）。"""
    # to_model_xy 是 x - w/2, y - h/2（y 不翻转），逆变换就是加回半画布。
    off_x = float(spec.canvas_width) / 2.0
    off_y = float(spec.canvas_height) / 2.0

    warps = [d for d in spec.deformers if isinstance(d, WarpDeformerSpec)]
    skipped_rotation = [d.deformer_id for d in spec.deformers
                        if not isinstance(d, WarpDeformerSpec)]
    if skipped_rotation:
        log.info(f"rotation 变形器不导出到 bindings（当前全为静态姿态）："
                 f"{skipped_rotation}")

    return {
        "version": BINDINGS_VERSION,
        "canvas": {
            "width": float(spec.canvas_width),
            "height": float(spec.canvas_height),
            "pixelsPerUnit": float(spec.pixels_per_unit),
        },
        "parameters": [
            {"id": p.parameter_id, "min": float(p.minimum),
             "max": float(p.maximum), "default": float(p.default)}
            for p in spec.parameters
        ],
        "meshes": [_mesh_entry(m, off_x, off_y) for m in spec.meshes],
        "deformers": [_deformer_entry(d, off_x, off_y) for d in warps],
        "skippedRotationDeformers": skipped_rotation,
    }


def write_bindings(spec: RigSpec, output_dir: str,
                   character_name: str) -> Optional[Path]:
    """写出 ``{character_name}.bindings.json`` 并在 model3.json 里挂上引用。

    没有 model3.json 就返回 None —— 不产出没人引用的孤儿文件；前端是从清单
    ``FileReferences.Bindings`` 发现它的。
    """
    out = Path(output_dir)
    manifest_path = out / f"{character_name}.model3.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning(f"bindings 未写出（读不到 model3.json）: {exc}")
        return None

    document = build_bindings_document(spec)
    filename = f"{character_name}.bindings.json"
    path = out / filename
    path.write_text(json.dumps(document, ensure_ascii=False,
                               separators=(",", ":")), encoding="utf-8")

    manifest.setdefault("FileReferences", {})["Bindings"] = filename
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return path
