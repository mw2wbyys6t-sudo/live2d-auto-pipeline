"""导出管线与 moc3 编译器的桥接。

把 `Live2DBuilder.build()` 产出的绑定数据（meshes / parameters / deformer_tree）
与 `Model3Exporter` 打包图集得到的逐层 UV 摆放，编译为真实的 `.moc3`。

坐标系换算（约定与官方 Haru 差分 + 官方运行实渲染核实）：
  * 位置：图层像素坐标（左上原点、y 向下）→ 模型坐标（原点在画布中心、y 向上），
    并以单位存储（= 像素 / pixels_per_unit）
  * 三角形：翻转 y 会同时反转手性，必须归一回 CCW，否则运行时按背面剔除丢弃整批网格
  * UV：图集摆放按图像坐标（左上原点）直接写入，不再翻 v —— live2d-py 不在
    上传时翻转纹理，翻了会让全部 UV 落进透明留白

诚实性约定：管线里的 warp 变形器会编译进 moc3（静止形网格 + (s, t) 顶点）；
不能表达的变形器（rotation、没有成员网格的）如实记入 ``dropped``，
绝不把丢了绑定信息的产物标成完全体。
"""
from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

from core.logger import get_logger
from live2d_builder.exporter.moc3_lint import lint_document
from live2d_builder.exporter.moc3_model import (
    DeformerGrid,
    KeyformShape,
    MeshSpec,
    ParameterSpec,
    RigSpec,
    RotationDeformerSpec,
    RotationKeyform,
    UnsupportedRig,
    WarpDeformerSpec,
    compile_static_rig,
)
from drivers.live2d_runtime.moc3_verify import (
    verify_moc3_consistency,
    verify_motion_playback,
    verify_physics_playback,
)

log = get_logger(__name__)

# 只烘焙 Z 轴刚体旋转：X/Y 在 Live2D 里是位移 + 透视的复合，属美术判断，不臆造。
ROTATION_KEYFORM_PARAMS: Dict[str, Dict[str, Any]] = {
    "ParamAngleZ": {
        "pivot": "Head",
        "groups": {"head", "face", "hair", "eyes", "brows", "ears"},
    },
    "ParamBodyAngleZ": {"pivot": "Body", "groups": {"body"}},
}
# 权重低于此值认为该骨骼对该网格无实际影响（避免贴图级抖动）
_MIN_INFLUENCE = 0.02
# 每 1 单位参数值转多少度：30 单位 -> 30 度，与 Cubism 头部转动的常规量级一致
_DEGREES_PER_UNIT = 1.0


def ensure_front_facing(vertices: List[List[float]],
                        triangles: List[List[int]]) -> List[List[int]]:
    """把三角形统一成 y 向上模型坐标里的 CCW（正面）。

    像素坐标 y 向下，翻成 y 向上会把每个三角形的手性反过来；运行时开启背面
    剔除时，整批网格会被当成背面全部丢弃 —— 实测真实导出的模型在官方运行时
    里一个像素都画不出来，翻转手性后立刻画出 3.6 万像素
    （复现：tools/bisect_moc3_fields.py 的 A/G 两组）。
    """
    if not triangles:
        return triangles
    area = 0.0
    for a, b, c in triangles:
        ax, ay = vertices[a]
        bx, by = vertices[b]
        cx, cy = vertices[c]
        area += (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if area >= 0:
        return triangles
    return [[a, c, b] for a, b, c in triangles]


def _affected_bones(groups: set, standard_bones: Dict[str, Dict]) -> set:
    """自身或任一祖先落在给定分组里的骨骼名。"""
    affected = set()
    for name in standard_bones:
        cursor, guard = name, 0
        while cursor and guard <= len(standard_bones):
            guard += 1
            info = standard_bones.get(cursor)
            if not info:
                break
            if info.get("group") in groups:
                affected.add(name)
                break
            cursor = info.get("parent")
    return affected


def _key_times(param: ParameterSpec) -> List[float]:
    """键时刻：min / default / max 去重后升序；default 必在其中，静止姿态可复现。"""
    raw = [param.minimum, param.default, param.maximum]
    keys = sorted({round(v, 6) for v in raw})
    return keys


def rotation_keyforms(mesh: Dict[str, Any], parameters_by_id: Dict[str, ParameterSpec],
                      bone_positions: Dict[str, Any], standard_bones: Dict[str, Dict],
                      width: float, height: float):
    """把骨骼的 Z 轴刚体旋转按蒙皮权重烘焙成逐键形状。

    返回 ``(参数 id, [KeyformShape])``；不适用时返回 ``(None, 候选参数列表)``：
      * 命中 1 个参数 —— 返回该参数的逐键形状；
      * 没有权重 / 没有枢轴 / 命中 0 个 —— 退回静态路径；
      * 命中 >1 个 —— 需要多参数带，其 keyform 轴序未核实，不猜。

    只做 Z 轴：那是无歧义的刚体旋转。X/Y 在 Live2D 里通常是位移 + 透视的复合，
    属于美术判断，宁可不生成也不臆造。
    """
    skin = mesh.get("weights") or {}
    bone_names = skin.get("bone_names") or []
    per_vertex = skin.get("weights") or []
    if not bone_names or not per_vertex or not bone_positions:
        return "", [], []

    hits = []
    for pid, spec in ROTATION_KEYFORM_PARAMS.items():
        param = parameters_by_id.get(pid)
        pivot = bone_positions.get(spec["pivot"])
        if param is None or pivot is None:
            continue
        affected = _affected_bones(spec["groups"], standard_bones)
        indices = [i for i, name in enumerate(bone_names) if name in affected]
        if not indices:
            continue
        if _peak_influence(per_vertex, indices) <= _MIN_INFLUENCE:
            continue
        hits.append((pid, param, indices, (float(pivot[0]), float(pivot[1]))))

    if len(hits) != 1:
        return "", [], [h[0] for h in hits]

    pid, param, indices, pivot = hits[0]
    # 枢轴同样换算成「原点在画布中心、y 向上」的模型坐标
    px = pivot[0] - width / 2.0
    py = height / 2.0 - pivot[1]
    shapes = []
    for key_value in _key_times(param):
        theta = math.radians(key_value * _DEGREES_PER_UNIT)
        cosine, sine = math.cos(theta), math.sin(theta)
        vertices = [
            _blend(float(x) - width / 2.0, height / 2.0 - float(y), px, py,
                   cosine, sine, _vertex_weight(per_vertex, index, indices))
            for index, (x, y) in enumerate(mesh["vertices"])
        ]
        shapes.append(KeyformShape(key_value=key_value, vertices=vertices))
    return pid, shapes, [pid]


def _peak_influence(per_vertex, indices) -> float:
    peak = 0.0
    for row in per_vertex:
        for i in indices:
            if i < len(row):
                peak = max(peak, float(row[i]))
    return peak


def _vertex_weight(per_vertex, index, indices) -> float:
    """该顶点在受影响骨骼上的合计权重，夹到 [0, 1]。"""
    if index >= len(per_vertex):
        return 0.0
    row = per_vertex[index]
    total = sum(float(row[i]) for i in indices if i < len(row))
    return min(1.0, max(0.0, total))


def _blend(mx, my, px, py, cosine, sine, weight):
    """线性蒙皮：v' = v + w * (R * (v - pivot) - (v - pivot))。"""
    if weight <= 0.0:
        return [mx, my]
    dx, dy = mx - px, my - py
    rx = cosine * dx - sine * dy
    ry = sine * dx + cosine * dy
    return [mx + weight * (rx - dx), my + weight * (ry - dy)]


def _norm(name: str) -> str:
    return str(name).strip().lower().replace(" ", "_").replace("-", "_")


def _member_rect(members: List[MeshSpec]) -> tuple:
    """成员网格（含全部键形）的并集范围再留 5% 余量，作为静止形网格的范围。

    余量是必需的：键形把顶点推出静止形网格会被内核按边界裁剪，画面就变形了。
    """
    xs: List[float] = []
    ys: List[float] = []
    for mesh in members:
        for shape in [mesh.vertices] + [s.vertices for s in mesh.keyform_shapes]:
            for x, y in shape:
                xs.append(float(x))
                ys.append(float(y))
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    margin_x = max((x1 - x0) * 0.05, 1e-3)
    margin_y = max((y1 - y0) * 0.05, 1e-3)
    return x0 - margin_x, y0 - margin_y, x1 + margin_x, y1 + margin_y


def _lattice(rect: tuple, n_cols: int, n_rows: int) -> List[List[float]]:
    """行优先（x 变化最快）、y 自下而上的规则点阵 —— 点序由官方内核实测钉住。"""
    x0, y0, x1, y1 = rect
    step_x = (x1 - x0) / (n_cols - 1)
    step_y = (y1 - y0) / (n_rows - 1)
    return [[x0 + c * step_x, y0 + r * step_y]
            for r in range(n_rows) for c in range(n_cols)]


def build_deformers(specs: List[MeshSpec],
                    entries: List[Dict],
                    width: float,
                    height: float) -> tuple:
    """把管线的 warp / rotation 变形器编成规格，并把成员网格就地挂上（改写 specs）。

    返回 ``(变形器规格, 未编译说明, 静态变形器名)``。一个网格只能挂一个变形器。

    rotation 编译成**单键形**：管线给的条目里没有参数绑定（眼球跟随由哪个参数、
    按多大幅度驱动属美术判断），所以这里只如实表达成「结构存在、当前不随参数动」，
    不冒充会动的追踪 —— 它们会出现在 ``deformers_static`` 里，不会被算成已落地。
    """
    by_norm = {_norm(m.mesh_id): m for m in specs}
    linked: Dict[str, str] = {}
    out: List = []
    static: List[str] = []
    skipped: List[str] = []
    for entry in entries:
        name = str(entry.get("name") or "")
        dtype = str(entry.get("type"))
        if dtype not in ("warp", "rotation"):
            # 先判类型再挂网格：否则未知类型会把成员网格挂到一个并不存在的
            # 变形器上，编译期直接报「引用了不存在的变形器」。
            skipped.append(f"{name}(type={dtype})")
            continue
        members: List[MeshSpec] = []
        for target in entry.get("targets") or []:
            mesh = by_norm.get(_norm(target))
            if mesh is not None and mesh.mesh_id not in linked:
                members.append(mesh)
                linked[mesh.mesh_id] = name
        if not members:
            skipped.append(f"{name}(没有可挂的成员网格)")
            continue
        if dtype == "warp":
            n_rows = max(2, int(entry.get("grid_rows") or 2))
            n_cols = max(2, int(entry.get("grid_cols") or 2))
            out.append(WarpDeformerSpec(
                deformer_id=name, rows=n_rows - 1, cols=n_cols - 1,
                grids=[DeformerGrid(0.0, _lattice(
                    _member_rect(members), n_cols, n_rows))]))
        elif dtype == "rotation":
            pivot = entry.get("pivot") or (0.0, 0.0)
            # 与网格顶点同一换算：图像坐标 -> 原点居中、y 向上的模型坐标
            origin = (float(pivot[0]) - width / 2.0,
                      height / 2.0 - float(pivot[1]))
            out.append(RotationDeformerSpec(
                deformer_id=name,
                keyforms=[RotationKeyform(0.0, angle=0.0, origin=origin)],
                base_angle=float(entry.get("angle") or 0.0)))
            static.append(name)
    if linked:
        specs[:] = [
            replace(mesh, deformer_id=linked[mesh.mesh_id])
            if mesh.mesh_id in linked else mesh
            for mesh in specs
        ]
    return out, skipped, static


def _parameter_specs(builder_result: Dict[str, Any]) -> List[ParameterSpec]:
    return [
        ParameterSpec(
            parameter_id=str(p["Id"]),
            minimum=float(p.get("Min", -30.0)),
            maximum=float(p.get("Max", 30.0)),
            default=float(p.get("Value", 0.0)),
        )
        for p in (builder_result.get("parameters") or {}).get("cubism_params", [])
    ]


def drivable_parameters(builder_result: Dict[str, Any]) -> Dict[str, List[float]]:
    """编译器实际会写出键形的参数 -> 其键值序列。motion 只给这些参数排曲线。

    「参数存在」不等于「参数能动」：本仓库反复出现过内核接受、画面却一动不动的
    产物，所以动画内容必须以真正有键形驱动的参数为准（多参数命中的网格会被
    rotation_keyforms 退回静态，因此也不会出现在这里）。
    """
    params_by_id = {p.parameter_id: p for p in _parameter_specs(builder_result)}
    bone_positions = builder_result.get("bone_positions") or {}
    from live2d_builder.bones.deformers import BoneHierarchy
    standard_bones = BoneHierarchy.STANDARD_BONES

    keys: Dict[str, set] = {}
    for mesh in (builder_result.get("meshes") or {}).values():
        width = float(mesh.get("width") or 0)
        height = float(mesh.get("height") or 0)
        if width <= 0 or height <= 0:
            continue
        pid, shapes, _candidates = rotation_keyforms(
            mesh, params_by_id, bone_positions, standard_bones, width, height)
        if pid and shapes:
            keys.setdefault(pid, set()).update(
                float(s.key_value) for s in shapes)
    return {pid: sorted(values) for pid, values in keys.items()}


def build_rig_spec(
    builder_result: Dict[str, Any],
    atlas_uvs: Dict[str, Dict[str, float]],
) -> RigSpec:
    """从管线产物构造 RigSpec；图集缺摆放信息时明确报错。"""
    meshes = builder_result.get("meshes") or {}
    if not meshes:
        raise UnsupportedRig("至少需要一个网格")

    extents = set()
    specs: List[MeshSpec] = []
    params = _parameter_specs(builder_result)
    params_by_id = {p.parameter_id: p for p in params}
    bone_positions = builder_result.get("bone_positions") or {}
    from live2d_builder.bones.deformers import BoneHierarchy
    standard_bones = BoneHierarchy.STANDARD_BONES
    keyformed, multi_hit = [], []

    for order, (name, mesh) in enumerate(meshes.items()):
        width = float(mesh.get("width") or 0)
        height = float(mesh.get("height") or 0)
        if width <= 0 or height <= 0:
            raise UnsupportedRig(f"{name}: 网格画布尺寸无效")
        extents.add((width, height))

        rect = atlas_uvs.get(name)
        if rect is None:
            raise UnsupportedRig(f"{name}: 图集中没有该层的 UV 摆放信息")
        u0, v0, u1, v1 = rect["u0"], rect["v0"], rect["u1"], rect["v1"]

        vertices: List[List[float]] = []
        uvs: List[List[float]] = []
        normals = mesh.get("vertices_norm")
        count = len(mesh["vertices"])
        for i, (x, y) in enumerate(mesh["vertices"]):
            x, y = float(x), float(y)
            vertices.append([x - width / 2.0, height / 2.0 - y])
            if normals is not None:
                nx, ny = float(normals[i][0]), float(normals[i][1])
            else:
                nx, ny = x / width, y / height
            # 图集 v 直接沿用图像坐标（左上原点）：实测 live2d-py 不在上传时
            # 翻转纹理，多翻一次会让每个 UV 落进图集的透明留白，模型一个像素
            # 都画不出来（对照实验 tools/probe_uv_variant.py：as_is=0 像素，
            # 去掉这层翻转 = 13.5 万像素）。
            uvs.append([u0 + nx * (u1 - u0), v0 + ny * (v1 - v0)])

        triangles = ensure_front_facing(
            vertices, [[int(a), int(b), int(c)] for a, b, c in mesh["indices"]])
        pid, shapes, candidates = rotation_keyforms(
            mesh, params_by_id, bone_positions, standard_bones, width, height)
        if len(candidates) > 1:
            multi_hit.append((name, candidates))
        elif pid:
            keyformed.append(name)
        specs.append(MeshSpec(
            mesh_id=str(name),
            vertices=vertices,
            triangles=triangles,
            uvs=uvs,
            draw_order=float(order),
            keyform_parameter_id=pid,
            keyform_shapes=shapes,
        ))

    if len(extents) > 1:
        raise UnsupportedRig(f"各网格画布尺寸不一致: {sorted(extents)}")
    width, height = extents.pop()

    if keyformed:
        driven = sorted({m.keyform_parameter_id for m in specs if m.keyform_shapes})
        log.info(f"参数形变键形已烘焙：{len(keyformed)} 个网格，驱动参数 {driven}")
    if multi_hit:
        log.warning(
            f"{len(multi_hit)} 个网格同时受多个旋转参数影响，需要多参数带；"
            f"其 keyform 轴序未核实，保持静态形状：{[n for n, _ in multi_hit][:5]}")
    if not keyformed:
        log.info("本次导出没有可烘焙的参数键形（无蒙皮权重或无 Z 轴参数）")

    deformers = (builder_result.get("deformer_tree") or {}).get("deformers") or []
    deformer_specs, uncompiled, static = build_deformers(
        specs, list(deformers), width, height)
    warps = [d for d in deformer_specs if isinstance(d, WarpDeformerSpec)]
    if deformer_specs:
        linked = sum(1 for m in specs if m.deformer_id)
        log.info(f"变形器已编译进 moc3：{len(deformer_specs)} 个 "
                 f"{[d.deformer_id for d in deformer_specs]}"
                 f"（warp {len(warps)}、rotation {len(static)}），挂上 {linked} 个网格")
    for note in uncompiled:
        log.info(f"变形器未编译（保持静态几何）：{note}")
    if static:
        log.info(f"rotation 变形器为静态姿态（条目里没有参数绑定）：{static}")

    return RigSpec(
        meshes=specs,
        parameters=params,
        canvas_width=width,
        canvas_height=height,
        deformers=deformer_specs,
        uncompiled_deformers=uncompiled,
        static_deformers=static,
    )


def _references(manifest: Path) -> dict:
    """清单里 FileReferences 的内容；读不到就当什么都没有。"""
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document.get("FileReferences") or {}


def _declares_motions(manifest: Path) -> bool:
    """清单里是否声明了至少一个 motion 文件。读不动就算「没有」。"""
    return any((_references(manifest).get("Motions") or {}).values())


def _declares_physics(manifest: Path) -> bool:
    """清单是否引用了 physics3，且该文件里真有参数->参数的链。"""
    references = _references(manifest)
    physics_file = references.get("Physics")
    if not physics_file:
        return False
    try:
        physics = json.loads((manifest.parent / physics_file).read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return False
    for setting in physics.get("PhysicsSettings") or []:
        inputs = {i["Source"]["Id"] for i in setting.get("Input") or []
                  if (i.get("Source") or {}).get("Target") == "Parameter"}
        outputs = {o["Destination"]["Id"] for o in setting.get("Output") or []
                   if (o.get("Destination") or {}).get("Target") == "Parameter"}
        if inputs and outputs:
            return True
    return False


def compile_export_moc3(
    builder_result: Dict[str, Any],
    atlas_uvs: Dict[str, Dict[str, float]],
    output_dir: str,
    character_name: str,
    timeout: int = 60,
) -> Dict[str, Any]:
    """编译并写出 `{character_name}.moc3`，随后用官方内核做一致性验收。

    Returns:
        {moc3_written, moc3_path, bytes, runtime_ready, blocker,
         dropped, lint_issues, consistency, motion_verified}
    """
    result: Dict[str, Any] = {
        "moc3_written": False,
        "moc3_path": None,
        "bytes": 0,
        "runtime_ready": False,
        "blocker": None,
        "dropped": [],
        "lint_issues": [],
        "consistency": None,
        "motion": None,
        # True = 内核真的在播；None = 产物里没有 motion（没有可动参数），不适用
        "motion_verified": None,
        "physics": None,
        # True = 内核真的产生带滞后的物理运动；None = 没有可验的物理链
        "physics_verified": None,
        "deformers_compiled": [],
        "deformers_uncompiled": [],
        "deformers_static": [],
    }

    try:
        spec = build_rig_spec(builder_result, atlas_uvs)
        spec.validate()
    except UnsupportedRig as exc:
        result["blocker"] = str(exc)
        return result

    result["deformers_compiled"] = [d.deformer_id for d in spec.deformers]
    result["deformers_uncompiled"] = list(spec.uncompiled_deformers)
    result["deformers_static"] = list(spec.static_deformers)
    if spec.uncompiled_deformers:
        result["dropped"] = ["deformers"]

    try:
        doc = compile_static_rig(spec)
    except UnsupportedRig as exc:
        result["blocker"] = str(exc)
        return result

    issues = lint_document(doc)
    result["lint_issues"] = [str(i) for i in issues]
    if issues:
        result["blocker"] = f"自洽性校验未通过: {issues[0]}"
        return result

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{character_name}.moc3"
    data = doc.to_bytes()
    path.write_bytes(data)
    result["moc3_written"] = True
    result["moc3_path"] = str(path)
    result["bytes"] = len(data)

    consistency = verify_moc3_consistency(str(path), timeout=timeout)
    result["consistency"] = consistency
    if not consistency["ok"]:
        result["blocker"] = f"官方内核拒绝: {consistency['blocker']}"
        return result

    # 「文件写出来了」不等于「画面会动」：产物里只要声明了 motion，就必须让
    # 官方内核真的把参数打到曲线的值上，否则整份产物不算可部署。
    manifest = out / f"{character_name}.model3.json"
    if _declares_motions(manifest):
        motion = verify_motion_playback(str(manifest), timeout=timeout)
        result["motion"] = motion
        if not motion["ok"]:
            result["blocker"] = f"motion 未被官方内核播放: {motion['blocker']}"
            return result
        result["motion_verified"] = True
    else:
        result["motion_verified"] = None   # 没有可动参数 → 不适用，不是通过

    # 物理同理：physics3 合 schema、被加载都不算，必须真把参数打出带滞后的运动。
    if _declares_physics(manifest):
        physics = verify_physics_playback(str(manifest), timeout=timeout)
        result["physics"] = physics
        if not physics["ok"]:
            result["blocker"] = f"physics 未通过官方内核验收: {physics['blocker']}"
            return result
        result["physics_verified"] = True
    else:
        result["physics_verified"] = None

    result["runtime_ready"] = not result["dropped"]
    if result["dropped"]:
        result["blocker"] = (
            f"部分变形器未编译（{result['deformers_uncompiled']}），"
            f"产物不得用于部署验收")
    log.success(
        f"moc3 已编译并通过官方内核一致性: {path} "
        f"({len(data)} bytes, runtime_ready={result['runtime_ready']}, "
        f"motion_verified={result['motion_verified']}, "
        f"physics_verified={result['physics_verified']})")
    return result


def summarize_for_meta(result: Dict[str, Any]) -> Dict[str, Any]:
    """缩略版结果，供 build_meta.json 落盘。"""
    return {
        "moc3_written": result["moc3_written"],
        "moc3_path": result["moc3_path"],
        "moc3_bytes": result["bytes"],
        "runtime_ready": result["runtime_ready"],
        "moc3_blocker": result["blocker"],
        "moc3_dropped": result["dropped"],
        "deformers_compiled": result.get("deformers_compiled") or [],
        "deformers_uncompiled": result.get("deformers_uncompiled") or [],
        "deformers_static": result.get("deformers_static") or [],
        "motion_verified": result.get("motion_verified"),
        "physics_verified": result.get("physics_verified"),
        "official_core_consistent": bool(
            (result.get("consistency") or {}).get("ok")),
    }
