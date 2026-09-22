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
#
# 表项可以是 "pivot"（单个转动骨骼）或 "pivots"（多个候选 —— 官方只有**一个**
# ParamHairSide，却对应左右两条侧发，枢轴不同即为不同变形器，同参数共享 binding）。
# "groups" 供网格路径（rotation_keyforms）按蒙皮骨骼判定影响范围。
ROTATION_KEYFORM_PARAMS: Dict[str, Dict[str, Any]] = {
    "ParamAngleZ": {
        "pivot": "Head",
        "groups": {"head", "face", "hair", "eyes", "brows", "ears"},
        "mesh": True,          # 也用于网格路径（逐顶点烘焙进网格自身的键形）
    },
    "ParamBodyAngleZ": {"pivot": "Body", "groups": {"body"}, "mesh": True},
    # 手臂：官方四参数 = 左/右上臂(LA/RA) 与 左/右前臂(LB/RB)。
    "ParamArmLA": {"pivot": "ArmBack_L", "groups": {"arms"}},
    "ParamArmLB": {"pivot": "ForearmBack_L", "groups": {"arms"}},
    "ParamArmRA": {"pivot": "ArmBack_R", "groups": {"arms"}},
    "ParamArmRB": {"pivot": "ForearmBack_R", "groups": {"arms"}},
    # 头发：官方三参数（前/侧/后）。呆毛(Hair_Top)归入前发组 —— 这是命名约定，
    # 不是格式推断；左右侧发共用 ParamHairSide。
    "ParamHairFront": {"pivots": ("Hair_Front", "Hair_Top"), "groups": {"hair"}},
    "ParamHairSide": {"pivots": ("Hair_Side_L", "Hair_Side_R"), "groups": {"hair"}},
    "ParamHairBack": {"pivot": "Hair_Back", "groups": {"hair"}},
}


def _bone_names(spec: Dict[str, Any]) -> tuple:
    """表项的候选转动骨骼（单个 pivot 或 pivots 元组）。"""
    return tuple(spec.get("pivots") or (spec["pivot"],))


# 呼吸：胸腔起伏在 Live2D 里是**缩放 + 垂直位移的复合**，所以用 warp 变形器而不是
# 刚体旋转（用旋转去实现它等于语义错误，与已排除的 ParamAngleX/Y 同类）。枢轴为
# Chest 的 warp 由 ParamBreath(0..1) 驱动：上抬幅度 = 该变形器控制网格高度的 2%
# （相对值，随网格自适应；美术可调）。
BREATH_PARAM = "ParamBreath"
BREATH_PIVOT = "Chest"
BREATH_AMPLITUDE = 0.02


def _breath_parameter(entry: Dict[str, Any],
                      parameters_by_id: Dict[str, ParameterSpec],
                      bone_positions: Dict[str, Any]):
    """该 warp 变形器是否由 ParamBreath 驱动（枢轴 == 胸腔骨骼）；否则 None。"""
    param = parameters_by_id.get(BREATH_PARAM)
    bone = bone_positions.get(BREATH_PIVOT)
    pivot = entry.get("pivot")
    if param is None or bone is None or pivot is None:
        return None
    if abs(float(pivot[0]) - float(bone[0])) > 1e-3:
        return None
    if abs(float(pivot[1]) - float(bone[1])) > 1e-3:
        return None
    return param
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
        # 网格路径只认 "mesh": True 的表项：手臂/头发与 ParamAngleZ 在骨骼分组上
        # 有重叠，若一并参与，头发网格会同时命中两个参数而被判 multi_hit 退回静态
        # —— 比改动前更差。手臂/头发归**变形器**路径（各自有独立枢轴）。
        if not spec.get("mesh"):
            continue
        param = parameters_by_id.get(pid)
        pivot = None
        for bone_name in _bone_names(spec):
            pivot = bone_positions.get(bone_name)
            if pivot is not None:
                break
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


# 眼球跟随：瞳孔网格由 ParamEyeBallX / ParamEyeBallY **两轴**驱动（官方标准参数）。
#
# 与 Z 轴旋转不同，眼球在 Live2D 里就是**平移** —— 不存在 X/Y 那种"位移 + 透视的
# 复合"，所以这里可以如实烘焙而不是臆造。轴序按 F-06 实测定论：**X 在前、步长为 1**，
# 即展平序 index = i_X + k_X * i_Y（tools/probe_multiband_axis_order.py）。
EYE_TRACK_AXES = ("ParamEyeBallX", "ParamEyeBallY")
# 极值键下的位移 = 瞳孔自身包围盒的 10%（相对值，随网格自适应；美术可调）。
EYE_TRACK_AMPLITUDE = 0.10


def _key_fraction(param: ParameterSpec, value: float) -> float:
    """键值归一化到 [-1, 1]（按参数的最大绝对值），用于换算位移。"""
    span = max(abs(float(param.minimum)), abs(float(param.maximum))) or 1.0
    return float(value) / span


def eye_track_keyforms(mesh: Dict[str, Any], vertices: List[List[float]],
                       parameters_by_id: Dict[str, ParameterSpec]):
    """眼球网格 -> ``(轴, 逐键形状)``；不适用时返回 ``((), [])``。

    判定：网格在 ``Eyeball_L`` / ``Eyeball_R`` 上的合计权重超过阈值，且**恰好只有
    一个**眼球骨骼受影响（两个都受影响说明左右眼分不清，宁可不生成）。返回的轴序
    是 ``(X, Y)``，与 F-06 定论一致；形状顶点在**模型坐标**（与 MeshSpec.vertices 同空间）。
    """
    skin = mesh.get("weights") or {}
    bone_names = skin.get("bone_names") or []
    per_vertex = skin.get("weights") or []
    if not bone_names or not per_vertex or not vertices:
        return (), []
    hits = []
    for bone in ("Eyeball_L", "Eyeball_R"):
        if bone not in bone_names:
            continue
        if _peak_influence(per_vertex, [bone_names.index(bone)]) > _MIN_INFLUENCE:
            hits.append(bone)
    if len(hits) != 1:
        return (), []
    if any(parameters_by_id.get(pid) is None for pid in EYE_TRACK_AXES):
        return (), []

    xs = [float(v[0]) for v in vertices]
    ys = [float(v[1]) for v in vertices]
    amp_x = (max(xs) - min(xs)) * EYE_TRACK_AMPLITUDE
    amp_y = (max(ys) - min(ys)) * EYE_TRACK_AMPLITUDE

    keys = [tuple(_key_times(parameters_by_id[pid])) for pid in EYE_TRACK_AXES]
    axes = tuple((pid, values) for pid, values in zip(EYE_TRACK_AXES, keys))
    shapes = []
    for i_y, key_y in enumerate(keys[1]):
        for i_x, key_x in enumerate(keys[0]):
            dx = _key_fraction(parameters_by_id[EYE_TRACK_AXES[0]], key_x) * amp_x
            dy = _key_fraction(parameters_by_id[EYE_TRACK_AXES[1]], key_y) * amp_y
            shapes.append(KeyformShape(
                key_value=float(i_x + len(keys[0]) * i_y),   # 展平序号（仅备注）
                vertices=[(x + dx, y + dy) for x, y in vertices]))
    return axes, shapes


# 头部整体：ParamAngleX / ParamAngleY 在 Live2D 里不是纯旋转，而是**点头/转头的
# 透视复合** —— 正面轮廓按 cos θ 各向异性压扁（转头压水平、点头压垂直，与压扁方向
# 交叉的弧位移 ∝ sin θ 模拟颈关节在枢轴下方），且离枢轴远的点深度变化大、透视缩放
# 更强（远侧比近侧扁，非仿射）。只有 Z 是无歧义的刚体旋转。所以头部网格升级为
# **(X, Y, Z) 三轴带**，每个键形 = [绕 Head 关节旋转 Z] ∘ [点头/转头的透视压缩]。
#
# 轴序按 F-06 实测定论（第一个轴步长为 1）：index = i_X + kX·i_Y + kX·kY·i_Z。
# 幅度取网格自身尺寸的比例（相对值，随网格自适应；美术可调），并且**必须 X/Y/Z
# 三参数都已声明才会生效** —— 这让本次升级是显式 opt-in，不会悄然改变既有导出。
HEAD_ANGLE_AXES = ("ParamAngleX", "ParamAngleY", "ParamAngleZ")
HEAD_SHIFT_X = 0.04          # 转头(ParamAngleY)的水平弧位移 = 网格宽 × 比例 × sin θ
HEAD_SHIFT_Y = 0.03          # 点头(ParamAngleX)的垂直弧位移 = 网格高 × 比例 × sin θ
HEAD_DEPTH_SPANS = 4.0       # 透视参考深度 = 网格最大跨度的 4 倍（相机距离的近似）


def _sincos(degrees: float) -> tuple:
    """角度 -> (sin, cos)；度 -> 弧度换算与 Z 轴旋转同一约定。"""
    theta = math.radians(float(degrees) * _DEGREES_PER_UNIT)
    return math.sin(theta), math.cos(theta)


def _head_transform(x, y, px, py, shift_x, shift_y,
                    sin_x, cos_x, sin_y, cos_y, sin_z, cos_z, depth, weight):
    """线性蒙皮：v' = v + w·(T(v) − v)，T = 绕 Head 关节的 点头/转头/侧转 复合。

    * 点头 θx（ParamAngleX）：垂直偏移 × cos θx（上下轮廓压扁）；
    * 转头 θy（ParamAngleY）：水平偏移 × cos θy（左右轮廓压扁）；
    * 侧转 θz（ParamAngleZ）：绕枢轴刚体旋转（模型空间逆时针，同 ``_blend``）；
    * 弧位移 ∝ sin θ：颈关节在枢轴下方，头绕它转动时整体平移 —— 点头推垂直、
      转头推水平（与压扁方向交叉，和真人转头时头心划弧一致）；
    * 透视 p = 1 + z/depth：转动把一侧推近、另一侧推远，近大远小。压缩量随顶点
      距枢轴的距离连续变化 —— 非仿射部分（keyform 存的是逐顶点位置，装得下）；
      竖直特征线因此倾斜，剪切效果由此而来，无需显式剪切项。

    ``sin_x`` / ``cos_x`` = sin/cos θx，``sin_y`` / ``cos_y`` = sin/cos θy。
    ``weight`` 是顶点在头部总成骨骼上的合计权重（0 = 不随头动）。
    """
    if weight <= 0.0:
        return [x, y]
    dx, dy = x - px, y - py
    # 深度：两个轴的转动各贡献一份（扁平卡近似，静止深度记 0；θy > 0 时枢轴右侧
    # 远去、左侧近来 —— 近侧选哪边只是约定，耦合关系才是要点）
    z = -(dx * sin_y + dy * sin_x)
    p = 1.0 + z / depth
    # 弧位移加在枢轴深度上（枢轴处 p = 1），不参与透视缩放
    tx = px + dx * cos_y * p + shift_x * sin_y
    ty = py + dy * cos_x * p + shift_y * sin_x
    rx, ry = tx - px, ty - py
    return [x + weight * (px + cos_z * rx - sin_z * ry - x),
            y + weight * (py + sin_z * rx + cos_z * ry - y)]


# 头部总成**按骨骼表的 ``group`` 判定**，不用 ``parent`` 链推导：实测层级里
# ``Neck``(group=body) 是 ``Head`` 的父级，用层级会把躯干误算进头部总成。
HEAD_ASSEMBLY_GROUPS = frozenset(
    {"head", "face", "hair", "ears", "eyes", "brows", "nose", "mouth"})


def _head_assembly(standard_bones: Dict[str, Dict]) -> set:
    """``Head`` 及其头上各部位（脸、眼、眉、口、鼻、耳、发……）。

    ``head`` / ``face`` / ``hair`` / ``ears`` / ``eyes`` / ``brows`` / ``nose`` /
    ``mouth`` 随头一起运动；``body`` / ``arms`` / ``clothes`` / ``legs`` 不随
    （``Neck`` 属 ``body`` 组，是躯干的一部分）。
    """
    return {name for name, info in standard_bones.items()
            if (info or {}).get("group") in HEAD_ASSEMBLY_GROUPS}


def head_angle_keyforms(mesh: Dict[str, Any], vertices: List[List[float]],
                        parameters_by_id: Dict[str, ParameterSpec],
                        standard_bones: Dict[str, Dict],
                        bone_positions: Dict[str, Any],
                        width: float, height: float):
    """头部网格 -> ``((X, Y, Z) 轴, 逐键形状)``；不适用时返回 ``((), [])``。

    条件：网格受**头部总成**中任一骨骼影响（``Head`` 及其后代，见
    ``_head_assembly``）、``Head`` **关节位置已知**、且 ``ParamAngleX/Y/Z``
    **三者都已声明**（缺任一则退回原有的单轴 Z 路径，保证既有产物不变）。

    枢轴取 **Head 骨骼关节点**（贴近官方习惯），不是网格包围盒中心；骨骼位置是图像
    坐标，按与顶点同一换算（原点居中、y 向上）转到模型坐标。关节位置未知时**不生成**
    —— 不拿包围盒中心顶替一个说不清的枢轴。

    顶点的形变量按其**在整个头部总成上的合计权重**线性混合，所以只绑 ``Face`` /
    ``Eye_L`` 等子骨骼的网格同样会跟着头动。
    """
    skin = mesh.get("weights") or {}
    bone_names = skin.get("bone_names") or []
    per_vertex = skin.get("weights") or []
    if not bone_names or not per_vertex or not vertices:
        return (), []
    if any(parameters_by_id.get(pid) is None for pid in HEAD_ANGLE_AXES):
        return (), []
    joint = bone_positions.get("Head")
    if joint is None:
        return (), []
    assembly = _head_assembly(standard_bones)
    indices = [i for i, b in enumerate(bone_names) if b in assembly]
    if not indices:
        return (), []
    if _peak_influence(per_vertex, indices) <= _MIN_INFLUENCE:
        return (), []

    px = float(joint[0]) - width / 2.0
    py = height / 2.0 - float(joint[1])
    xs = [float(v[0]) for v in vertices]
    ys = [float(v[1]) for v in vertices]
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    shift_x = span_x * HEAD_SHIFT_X
    shift_y = span_y * HEAD_SHIFT_Y
    depth = max(span_x, span_y, 1.0) * HEAD_DEPTH_SPANS

    keys = [tuple(_key_times(parameters_by_id[pid])) for pid in HEAD_ANGLE_AXES]
    axes = tuple(zip(HEAD_ANGLE_AXES, keys))
    weight_of = [_vertex_weight(per_vertex, i, indices)
                 for i in range(len(vertices))]
    trig_x = [_sincos(k) for k in keys[0]]
    trig_y = [_sincos(k) for k in keys[1]]
    shapes = []
    for i_z, key_z in enumerate(keys[2]):
        sin_z, cos_z = _sincos(key_z)
        for i_y, key_y in enumerate(keys[1]):
            sin_y, cos_y = trig_y[i_y]
            for i_x, key_x in enumerate(keys[0]):
                sin_x, cos_x = trig_x[i_x]
                index = (i_x + len(keys[0]) * i_y
                         + len(keys[0]) * len(keys[1]) * i_z)
                shapes.append(KeyformShape(
                    key_value=float(index),           # 展平序号（仅备注）
                    vertices=[_head_transform(x, y, px, py, shift_x, shift_y,
                                              sin_x, cos_x, sin_y, cos_y,
                                              sin_z, cos_z, depth,
                                              weight_of[i])
                              for i, (x, y) in enumerate(vertices)]))
    return axes, shapes


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


def _driving_rotation_parameter(pivot: Any,
                                parameters_by_id: Dict[str, ParameterSpec],
                                bone_positions: Dict[str, Any]) -> tuple:
    """按「枢轴 == 官方转动骨骼」找出驱动该 rotation 变形器的唯一官方参数。

    对齐 ``ROTATION_KEYFORM_PARAMS``：枢轴即 Head 的绑 ``ParamAngleZ``、即 Body 的绑
    ``ParamBodyAngleZ``。只有**恰好命中一个**已声明参数时才绑定；否则返回
    ``("", None)`` —— 宁可不驱动，也不猜一个参数。
    """
    px, py = float(pivot[0]), float(pivot[1])
    hits = []
    for pid, spec in ROTATION_KEYFORM_PARAMS.items():
        param = parameters_by_id.get(pid)
        if param is None:
            continue
        for bone_name in _bone_names(spec):
            bone = bone_positions.get(bone_name)
            if bone is None:
                continue
            if abs(float(bone[0]) - px) <= 1e-3 and abs(float(bone[1]) - py) <= 1e-3:
                hits.append((pid, param))
                break
    if len(hits) != 1:
        return "", None
    return hits[0]


def build_deformers(specs: List[MeshSpec],
                    entries: List[Dict],
                    width: float,
                    height: float,
                    parameters_by_id: Dict[str, ParameterSpec] | None = None,
                    bone_positions: Dict[str, Any] | None = None) -> tuple:
    """把管线的 warp / rotation 变形器编成规格，并把成员网格就地挂上（改写 specs）。

    返回 ``(变形器规格, 未编译说明, 静态变形器名)``。一个网格只能挂一个变形器。

    rotation 的**参数绑定**由枢轴判定（见 ``_driving_rotation_parameter``）：命中唯一
    官方参数时烘焙**逐键键形**，它真的会随参数转动；枢轴对不上任何已知转动骨骼时退回
    **单键形**并记入 ``deformers_static``（结构存在、不随参数动），不冒充会动的追踪。
    """
    by_norm = {_norm(m.mesh_id): m for m in specs}
    linked: Dict[str, str] = {}
    out: List = []
    static: List[str] = []
    bound: List[str] = []
    breath_bound: List[str] = []
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
            rect = _member_rect(members)
            grids = [DeformerGrid(0.0, _lattice(rect, n_cols, n_rows))]
            breath = _breath_parameter(
                entry, parameters_by_id or {}, bone_positions or {})
            if breath is not None:
                lift = (rect[3] - rect[1]) * BREATH_AMPLITUDE
                span = max(abs(float(breath.minimum)),
                           abs(float(breath.maximum))) or 1.0
                for value in _key_times(breath)[1:]:
                    grids.append(DeformerGrid(
                        float(value),
                        [[x, y + lift * (float(value) / span)]
                         for x, y in _lattice(rect, n_cols, n_rows)]))
                breath_bound.append(f"{name}={breath.parameter_id}")
            out.append(WarpDeformerSpec(
                deformer_id=name, rows=n_rows - 1, cols=n_cols - 1,
                parameter_id=breath.parameter_id if breath else "",
                grids=grids))
        elif dtype == "rotation":
            pivot = entry.get("pivot") or (0.0, 0.0)
            # 与网格顶点同一换算：图像坐标 -> 原点居中、y 向上的模型坐标
            origin = (float(pivot[0]) - width / 2.0,
                      height / 2.0 - float(pivot[1]))
            pid, param = _driving_rotation_parameter(
                pivot, parameters_by_id or {}, bone_positions or {})
            if pid and param is not None:
                # 逐键键形：内核按参数值在键之间插值，整棵子树真的绕枢轴转。
                out.append(RotationDeformerSpec(
                    deformer_id=name,
                    parameter_id=pid,
                    keyforms=[
                        RotationKeyform(value,
                                        angle=float(value) * _DEGREES_PER_UNIT,
                                        origin=origin)
                        for value in _key_times(param)
                    ],
                    base_angle=float(entry.get("angle") or 0.0)))
                bound.append(f"{name}={pid}")
            else:
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
    if breath_bound:
        log.info(f"呼吸已烘焙（{BREATH_PARAM} 驱动的 warp 双键形）：{breath_bound}")
    if bound:
        log.info(f"rotation 变形器已绑定官方参数（真的会动）：{bound}")
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
    keyformed, multi_hit, eyes_tracked, head_tracked = [], [], [], []

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
        eye_axes, eye_shapes = eye_track_keyforms(mesh, vertices, params_by_id)
        if eye_axes:
            # 眼球优先于旋转键形：瞳孔是平移，且必须由 X+Y 两轴共同驱动。
            eyes_tracked.append(name)
            specs.append(MeshSpec(
                mesh_id=str(name),
                vertices=vertices,
                triangles=triangles,
                uvs=uvs,
                draw_order=float(order),
                keyform_shapes=eye_shapes,
                keyform_axes=eye_axes,
            ))
            continue
        head_axes, head_shapes = head_angle_keyforms(
            mesh, vertices, params_by_id, standard_bones,
            bone_positions, width, height)
        if head_axes:
            # 头部整体：X/Y 位移+缩放 与 Z 旋转合成一个三轴带（优先于单轴 Z 路径）。
            head_tracked.append(name)
            specs.append(MeshSpec(
                mesh_id=str(name),
                vertices=vertices,
                triangles=triangles,
                uvs=uvs,
                draw_order=float(order),
                keyform_shapes=head_shapes,
                keyform_axes=head_axes,
            ))
            continue
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

    if head_tracked:
        log.info(f"头部三轴复合已烘焙（{'+'.join(HEAD_ANGLE_AXES)}）："
                 f"{len(head_tracked)} 个网格 {head_tracked[:5]}")
    if eyes_tracked:
        log.info(f"眼球跟随已烘焙（{'+'.join(EYE_TRACK_AXES)} 双轴）："
                 f"{len(eyes_tracked)} 个网格 {eyes_tracked[:5]}")
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
        specs, list(deformers), width, height,
        parameters_by_id=params_by_id, bone_positions=bone_positions)
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
