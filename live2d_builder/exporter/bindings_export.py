"""bindings.json：编译后绑定表的可读投影，供自研渲染器做真实形变。

moc3 里的键形与 warp 变形器是二进制；前端要「按参数把顶点摆到位」，就需要
同一份数据。这里不做任何推断，只做三件事：

1. 坐标统一换成**图层图像坐标**（左上原点、y 向下），与 ``{name}.meshes.json``
   同一空间。模型坐标 = 图像坐标减半画布（见 ``to_model_xy`` 的实测结论：
   内核把 y 当向下为正），所以这里只做平移，**不翻 y**。
2. 多轴带按 moc3 的展平序原样导出（第一个轴步长为 1），插值交给前端；
   warp 导出控制点网格，rotation 导出 ``angle/origin/scale`` 键形（按同一个
   ``p' = s·R·p + (I − R)·origin`` 由前端复算）。
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
    RotationDeformerSpec,
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
        # 图集页号：前端要把每个网格贴到正确的贴图上，缺了它整张脸会贴错页。
        "texture": int(mesh.texture_index),
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


def _bracket(keys: Sequence[float], value: float) -> tuple:
    """升序键值表中 value 的夹取区间：(低下标, 高下标, 高键权重)。"""
    n = len(keys)
    if n <= 1 or value <= keys[0]:
        return 0, 0, 0.0
    if value >= keys[-1]:
        return n - 1, n - 1, 0.0
    lo, hi = 0, n - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if keys[mid] <= value:
            lo = mid
        else:
            hi = mid
    span = keys[hi] - keys[lo]
    return lo, hi, ((value - keys[lo]) / span if span else 0.0)


def _grid_at_defaults(deformer: WarpDeformerSpec,
                      defaults: Dict[str, float]) -> Sequence[Sequence[float]]:
    """按默认参数值插值出的变形器网格（前端据此换算 (u, v) 的静止矩形）。

    不能直接拿 ``grids[0]``：那是**第一个键**的网格，不是静止姿态。实测
    （singer 的 ShoulderLift_R：键 -1/0/1）用它当基准，默认姿态下每个顶点都
    被平移约 57px —— 角色一站上去就是歪的。
    """
    if len(deformer.grids) == 1:
        return deformer.grids[0].points
    axes = [list(axis) for axis in deformer.axes_spec]
    if not axes:
        return deformer.grids[0].points

    brackets = []
    for pid, keys in axes:
        value = float(defaults.get(str(pid), 0.0))
        brackets.append(_bracket([float(k) for k in keys], value))

    corners: List[tuple] = []
    strides: List[int] = []
    stride = 1
    for _pid, keys in axes:
        strides.append(stride)
        stride *= len(keys)

    def walk(axis: int, index: int, weight: float) -> None:
        if weight <= 0:
            return
        if axis == len(axes):
            corners.append((index, weight))
            return
        lo, hi, w = brackets[axis]
        step = strides[axis]
        walk(axis + 1, index + lo * step, weight * (1.0 - w))
        if hi != lo:
            walk(axis + 1, index + hi * step, weight * w)

    walk(0, 0, 1.0)
    points = deformer.grids[0].points
    out = [[0.0, 0.0] for _ in points]
    for index, weight in corners:
        grid = deformer.grids[index] if index < len(deformer.grids) else None
        if grid is None:
            continue
        for k, (x, y) in enumerate(grid.points):
            out[k][0] += float(x) * weight
            out[k][1] += float(y) * weight
    return out


def _deformer_entry(deformer: WarpDeformerSpec, off_x: float,
                    off_y: float, defaults: Dict[str, float]) -> Dict[str, Any]:
    rest = _grid_at_defaults(deformer, defaults)
    xs = [float(p[0]) for p in rest]
    ys = [float(p[1]) for p in rest]
    return {
        "id": deformer.deformer_id,
        "type": "warp",
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


def _rotation_entry(deformer: RotationDeformerSpec, off_x: float,
                    off_y: float) -> Dict[str, Any]:
    """rotation 变形器：前端按 ``angle/origin/scale`` 自己做刚体变换。

    坐标同样换成图层图像坐标（枢轴加半画布），角度单位与 moc3 一致（度）。
    ``base_angle`` 必须一起导出：内核的总角度 = base + 键形角度，漏了它静止
    姿态就不是 0 度，整颗头会一直偏着。
    """
    return {
        "id": deformer.deformer_id,
        "type": "rotation",
        "parent": str(deformer.parent_deformer_id or ""),
        "baseAngle": float(deformer.base_angle),
        "axes": [[str(pid), _round(keys, _POSITION_DECIMALS)]
                 for pid, keys in deformer.axes_spec],
        "keyforms": [
            {
                "angle": float(k.angle),
                "origin": [round(float(k.origin[0]) + off_x, _POSITION_DECIMALS),
                           round(float(k.origin[1]) + off_y, _POSITION_DECIMALS)],
                "scale": float(k.scale),
            }
            for k in deformer.keyforms
        ],
    }


def build_bindings_document(spec: RigSpec) -> Dict[str, Any]:
    """RigSpec -> 可 JSON 化的绑定文档（坐标已换成图层图像坐标）。"""
    # to_model_xy 是 x - w/2, y - h/2（y 不翻转），逆变换就是加回半画布。
    off_x = float(spec.canvas_width) / 2.0
    off_y = float(spec.canvas_height) / 2.0

    defaults = {str(p.parameter_id): float(p.default) for p in spec.parameters}
    # 按规格顺序导出（与 moc3 的拓扑序一致），父级名字两边对得上。
    deformer_entries: List[Dict[str, Any]] = []
    for deformer in spec.deformers:
        if isinstance(deformer, WarpDeformerSpec):
            deformer_entries.append(
                _deformer_entry(deformer, off_x, off_y, defaults))
        else:
            deformer_entries.append(_rotation_entry(deformer, off_x, off_y))
    # 父级必须指向本文档里真实存在的变形器。上游 legal_deformer_parents() 已经
    # 裁过一遍，这里再查一次是因为「父级没落地」还有一条路径：某个变形器因为
    # 没有成员网格被整条跳过，子级的边就悬空了。前端会照着 parent 画层级线，
    # 指向空气的边等于把拓扑画错，所以宁可降级成根级并留痕。
    known = {entry["id"] for entry in deformer_entries}
    for entry in deformer_entries:
        parent = str(entry.get("parent") or "")
        if parent and parent not in known:
            log.warning(
                f"bindings: 变形器 {entry['id']} 的父级 {parent} 不在导出集合里，"
                "按根变形器处理")
            entry["parent"] = ""

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
        "deformers": deformer_entries,
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
