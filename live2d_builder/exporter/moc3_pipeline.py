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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

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


def to_model_xy(x, y, width, height):
    """图像坐标（左上原点、y 向下）-> moc3 模型坐标（原点在画布中心）。

    实测（单色块探针 + 官方内核渲染）：内核把这个 y 当**向下**为正 —— 画布上方的
    像素对应负 y。写成 ``height/2 - y`` 会让整个模型上下翻转：实测图层 y=96..224
    被画到 y=288..415，恰好关于画布中线镜像，且每层内容也跟着镜像。
    """
    return float(x) - width / 2.0, float(y) - height / 2.0


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
    px, py = to_model_xy(pivot[0], pivot[1], width, height)
    shapes = []
    for key_value in _key_times(param):
        theta = math.radians(key_value * _DEGREES_PER_UNIT)
        cosine, sine = math.cos(theta), math.sin(theta)
        vertices = [
            _blend(*to_model_xy(x, y, width, height), px, py,
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


#: 网格键形（逐顶点键形带）总开关，默认开启；保留为定位问题时的急停开关。
#:
#: 2026-10 曾整体关闭：模型里只要 ≥2 个网格带键形，官方 Cubism Core 就在
#: ``LoadModelJson`` 阶段段错误，且**概率性**复现（同一份字节约 90–95% 崩），
#: 一致性检查拦不住。当时怀疑是 moc3 键形索引表写错，实际根因在绘制顺序：
#: ``csmiCalculateRenderOrder`` 按 ``draw_order - min_draw_order`` 取桶下标，
#: 而桶数组只按 ``max - min`` 分配 —— 声明 ``max == 实际最大绘制序`` 时最后一档
#: 越界一格，读到什么取决于堆布局，于是表现为「时崩时好、和 draw_order 非单调相关」。
#: 修复在 ``moc3_model._write_draw_order``（max 取开区间上界）与
#: ``_validate_keyforms``（键值必须覆盖参数完整区间）。已对 2 / 10 / 40 网格的
#: 带键形模型做过官方内核加载 + 两端取帧逐像素比对。
MESH_KEYFORMS_ENABLED = True

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
                        width: float, height: float,
                        head_span: Optional[tuple] = None):
    """头部网格 -> ``((X, Y, Z) 轴, 逐键形状, 逐顶点头部权重)``；不适用时返回 ``((), [], [])``。

    条件：网格受**头部总成**中任一骨骼影响（``Head`` 及其后代，见
    ``_head_assembly``）、``Head`` **关节位置已知**、且 ``ParamAngleX/Y/Z``
    **三者都已声明**（缺任一则不生成）。

    返回的第三个值是各顶点在头部总成上的合计权重，调用方把它交给
    ``with_body_follow`` —— 躯干侧倾时头要跟着走，而跟多少必须和头自身的
    形变用同一套权重，否则半权重顶点会比邻居家多跑一截。

    枢轴取 **Head 骨骼关节点**（贴近官方习惯），不是网格包围盒中心；骨骼位置是图像
    坐标，按与顶点同一换算（原点居中、y 向上）转到模型坐标。关节位置未知时**不生成**
    —— 不拿包围盒中心顶替一个说不清的枢轴。

    顶点的形变量按其**在整个头部总成上的合计权重**线性混合，所以只绑 ``Face`` /
    ``Eye_L`` 等子骨骼的网格同样会跟着头动。

    ``head_span`` 是整颗头的 (宽, 高)（模型坐标），由 ``head_assembly_span`` 统一算好
    传进来：头部各部位刚性连在一起，形变尺度必须取整颗头。不传时退回网格自己的包围盒
    —— 那条路让 10px 的嘴和 245px 的脸各用一套 shift/透视深度，``ParamAngleY = 30``
    时嘴相对脸滑 35.6px（嘴本身才 10px 宽，等于被推出脸外）。
    """
    skin = mesh.get("weights") or {}
    bone_names = skin.get("bone_names") or []
    per_vertex = skin.get("weights") or []
    if not bone_names or not per_vertex or not vertices:
        return (), [], []
    if any(parameters_by_id.get(pid) is None for pid in HEAD_ANGLE_AXES):
        return (), [], []
    joint = bone_positions.get("Head")
    if joint is None:
        return (), [], []
    assembly = _head_assembly(standard_bones)
    indices = [i for i, b in enumerate(bone_names) if b in assembly]
    if not indices:
        return (), [], []
    if _peak_influence(per_vertex, indices) <= _MIN_INFLUENCE:
        return (), [], []

    px, py = to_model_xy(joint[0], joint[1], width, height)
    if head_span is None:
        xs = [float(v[0]) for v in vertices]
        ys = [float(v[1]) for v in vertices]
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)
    else:
        span_x, span_y = float(head_span[0]), float(head_span[1])
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
    return axes, shapes, weight_of


# 头组跟随躯干：``ParamBodyAngleZ`` 绕 Body 枢轴转整条上半身，头是挂在躯干上的，
# 所以它必须一起吃这个旋转 —— 否则躯干侧倾时颈/领口跟着走、脸和头发原地不动。
# 实测（singer，tools/bindings_gap_probe.py）：躯干 12 个网格解出同一个刚体
# （θ=10.000°、中心 (455.2,1038.8)、非刚体残差 ≤0.001px），而头组完全不跟，
# 于是 10° 侧倾时领口离脸 57px、15 参数复杂姿态最大撕开 137.8px。
#
# 这一条**并进任意一条既有键形带**（头部带 / 眼球带 / 淡入带）而不是只给头部带加轴：
# 一个网格只能挂一条带，只补头部带的话眼皮会跟着躯干走、眼球却留在原地 —— 实测
# 10° 侧倾时眼球相对眼皮滑 64px，比修之前更糟。
BODY_FOLLOW_PARAM = "ParamBodyAngleZ"


def _body_follow_axis(parameters_by_id: Dict[str, ParameterSpec],
                      bone_positions: Dict[str, Any],
                      width: float, height: float):
    """身体跟随轴 ``((键值…), 枢轴(模型坐标), [(sin, cos)…])``；不可用时 ``None``。

    枢轴取 `ROTATION_KEYFORM_PARAMS` 里该参数声明的转动骨骼，与躯干网格走的是
    **同一个枢轴** —— 两个场只有共用枢轴和角度才真的重合，配对距离才不变。
    参数没声明或枢轴未知时返回 None：宁可不跟，也不拿包围盒中心顶替一个说不清的枢轴。
    """
    spec = ROTATION_KEYFORM_PARAMS.get(BODY_FOLLOW_PARAM)
    param = parameters_by_id.get(BODY_FOLLOW_PARAM)
    if not spec or param is None:
        return None
    pivot = None
    for bone_name in _bone_names(spec):
        pivot = bone_positions.get(bone_name)
        if pivot is not None:
            break
    if pivot is None:
        return None
    keys = tuple(_key_times(param))
    bx, by = to_model_xy(pivot[0], pivot[1], width, height)
    return keys, (bx, by), [_sincos(k) for k in keys]


def with_body_follow(axes, shapes, vertices, parameters_by_id,
                     bone_positions, width, height, weights=None):
    """把 ``ParamBodyAngleZ`` 作为**最外层轴**并进一条既有键形带。

    展平序里最后一个轴的步长等于前面所有轴的键数之积，所以新下标就是
    ``原下标 + 原键形数 × 身体键下标`` —— 原带整体保持顺序，每个身体键复制一份。
    ``weights`` 逐顶点给出跟随比例（默认 1.0 = 全跟）：与 ``_blend`` 同一套线性蒙皮。

    身体参数没声明、枢轴未知、或带子是空的时候原样返回 —— 不因为缺一个参数就改语义。
    """
    if not axes or not shapes:
        return axes, shapes
    body = _body_follow_axis(parameters_by_id, bone_positions, width, height)
    if body is None:
        return axes, shapes
    body_keys, (bx, by), body_trig = body
    if weights is None:
        weights = [1.0] * len(vertices)
    merged = []
    for i_b, key_b in enumerate(body_keys):
        sin_b, cos_b = body_trig[i_b]
        if key_b == 0.0:
            # 身体角为 0 的那一档就是原带本身 —— 原样复用，别绕一圈旋转再转回来。
            merged.extend(shapes)
            continue
        for shape in shapes:
            merged.append(KeyformShape(
                key_value=float(len(merged)),
                vertices=[_blend(point[0], point[1], bx, by, cos_b, sin_b,
                                 weights[i] if i < len(weights) else 1.0)
                          for i, point in enumerate(shape.vertices)]))
    return tuple(list(axes) + [(BODY_FOLLOW_PARAM, body_keys)]), merged


def body_follow_band(vertices: List[List[float]],
                     parameters_by_id: Dict[str, ParameterSpec],
                     bone_positions: Dict[str, Any],
                     width: float, height: float,
                     weights: Optional[Sequence[float]] = None) -> tuple:
    """只有 ``ParamBodyAngleZ`` 的跟随带 ``((轴…), 形状…)``；条件不齐时 ``((), ())``。

    为什么主干覆盖以后的网格**还需要**它：撤销 ``head_angle_keyforms`` 是为了不把
    头转施加两次，但身体侧倾并不在任何祖先那条带上 —— ``BodySway`` 的轴是
    ``ParamBodyAngleX / Y / Sway / Breath``，**没有** ``ParamBodyAngleZ``（那一直
    是靠每个网格自己的带补进去的）。把 ``with_body_follow`` 连同头带一起撤掉的结果
    是头彻底不跟身子：实测（singer）躯干 10° 侧倾时领口离脸 57px，正是
    ``tests/unit/test_head_follows_body.py`` 钉住的那条缺陷。BodySway / NeckBand 闭包
    那份烘焙的撤销属 Task 4/5，且必须与「祖先真的带上 Z」同时落地。

    枢轴、键值与蒙皮权重全部取自与 :func:`with_body_follow` 同一套
    （``_body_follow_axis`` + ``_blend``），所以「只驱动身体场时头与躯干落在同一点」
    这条既有判据不变；默认键那一档逐顶点等于静止姿态（``_blend`` 在 cos=1、sin=0
    时是恒等），静止帧因此是构造上不变的。
    """
    body = _body_follow_axis(parameters_by_id, bone_positions, width, height)
    if body is None:
        return (), ()
    body_keys, (bx, by), body_trig = body
    if weights is None:
        weights = [1.0] * len(vertices)
    shapes: List[KeyformShape] = []
    for index, key_b in enumerate(body_keys):
        sin_b, cos_b = body_trig[index]
        shapes.append(KeyformShape(
            key_value=float(index),
            vertices=[_blend(point[0], point[1], bx, by, cos_b, sin_b,
                             weights[i] if i < len(weights) else 1.0)
                      for i, point in enumerate(vertices)]))
    return ((BODY_FOLLOW_PARAM, body_keys), ), tuple(shapes)


def head_assembly_span(meshes: Dict[str, Dict[str, Any]],
                       parameters_by_id: Dict[str, ParameterSpec],
                       standard_bones: Dict[str, Dict],
                       bone_positions: Dict[str, Any]) -> Optional[tuple]:
    """头部总成的**共享** (宽, 高)，模型坐标；没有可随头动的网格时返回 ``None``。

    头部各部位是刚性连在一起的一块，形变尺度（弧位移 shift、透视深度 depth）必须
    取**整颗头**。早先每个网格拿自己的包围盒：singer 样例里 10×9px 的嘴 shift 0.40px、
    透视深度 40px，245×185px 的脸 shift 9.80px、深度 980px —— 同一空间点被两套参数各
    算一遍，``ParamAngleY = 30`` 时嘴相对脸滑 35.6px。
    """
    joint = bone_positions.get("Head")
    if joint is None:
        return None
    if any(parameters_by_id.get(pid) is None for pid in HEAD_ANGLE_AXES):
        return None
    assembly = _head_assembly(standard_bones)
    lo = hi = None
    for mesh in meshes.values():
        skin = mesh.get("weights") or {}
        bone_names = skin.get("bone_names") or []
        per_vertex = skin.get("weights")
        vertices = mesh.get("vertices")
        if not bone_names or per_vertex is None or vertices is None or len(vertices) == 0:
            continue
        indices = [i for i, b in enumerate(bone_names) if b in assembly]
        if not indices or _peak_influence(per_vertex, indices) <= _MIN_INFLUENCE:
            continue
        width = float(mesh.get("width") or 0)
        height = float(mesh.get("height") or 0)
        if width <= 0 or height <= 0:
            continue
        pts = [to_model_xy(float(v[0]), float(v[1]), width, height)
               for v in vertices]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        mesh_lo = (min(xs), min(ys))
        mesh_hi = (max(xs), max(ys))
        lo = mesh_lo if lo is None else (min(lo[0], mesh_lo[0]), min(lo[1], mesh_lo[1]))
        hi = mesh_hi if hi is None else (max(hi[0], mesh_hi[0]), max(hi[1], mesh_hi[1]))
    if lo is None:
        return None
    return (hi[0] - lo[0], hi[1] - lo[1])


def head_assembly_bottom(meshes: Dict[str, Dict[str, Any]],
                         standard_bones: Dict[str, Dict],
                         bone_positions: Dict[str, Any]) -> Optional[float]:
    """头总成顶点的最大模型 y（头下缘），入选判据与 :func:`head_assembly_span` 同源。"""
    joint = (bone_positions or {}).get("Head")
    if joint is None:
        return None
    assembly = _head_assembly(standard_bones)
    bottom = None
    for mesh in meshes.values():
        skin = mesh.get("weights") or {}
        bone_names = skin.get("bone_names") or []
        per_vertex = skin.get("weights")
        vertices = mesh.get("vertices")
        if not bone_names or per_vertex is None or vertices is None or len(vertices) == 0:
            continue
        indices = [i for i, b in enumerate(bone_names) if b in assembly]
        if not indices or _peak_influence(per_vertex, indices) <= _MIN_INFLUENCE:
            continue
        width = float(mesh.get("width") or 0)
        height = float(mesh.get("height") or 0)
        if width <= 0 or height <= 0:
            continue
        ys = [to_model_xy(float(v[0]), float(v[1]), width, height)[1]
              for v in vertices]
        mesh_bottom = max(ys)
        bottom = mesh_bottom if bottom is None else max(bottom, mesh_bottom)
    return bottom


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


def _rest_rect(members: List[MeshSpec]) -> tuple:
    """成员网格**静止姿态**顶点的并集范围（不含键形摆动）。

    形变量要按它算，否则嘴/眼会被含键形的外包框放大好几倍（见 ``_open_down_grids``）。
    """
    return (
        min(float(x) for m in members for x, _ in m.vertices),
        min(float(y) for m in members for _, y in m.vertices),
        max(float(x) for m in members for x, _ in m.vertices),
        max(float(y) for m in members for _, y in m.vertices),
    )


def _all_rect(members: List[MeshSpec]) -> tuple:
    """成员网格连同**全部键形**的并集范围。"""
    return (
        min(float(x) for m in members
            for shape in [m.vertices] + [s.vertices for s in m.keyform_shapes]
            for x, _ in shape),
        min(float(y) for m in members
            for shape in [m.vertices] + [s.vertices for s in m.keyform_shapes]
            for _, y in shape),
        max(float(x) for m in members
            for shape in [m.vertices] + [s.vertices for s in m.keyform_shapes]
            for x, _ in shape),
        max(float(y) for m in members
            for shape in [m.vertices] + [s.vertices for s in m.keyform_shapes]
            for _, y in shape),
    )


def _member_rect(members: List[MeshSpec]) -> tuple:
    """成员网格（含全部键形）的并集范围再留 5% 余量，作为静止形网格的范围。

    余量是必需的：键形把顶点推出静止形网格会被内核按边界裁剪，画面就变形了。
    """
    x0, y0, x1, y1 = _all_rect(members)
    margin_x = max((x1 - x0) * 0.05, 1e-3)
    margin_y = max((y1 - y0) * 0.05, 1e-3)
    return x0 - margin_x, y0 - margin_y, x1 + margin_x, y1 + margin_y


def rect_covering_descendants(name: str,
                              members_by_name: Dict[str, List[MeshSpec]],
                              parents: Dict[str, str]) -> List[MeshSpec]:
    """``name`` 的静止矩形**应该覆盖**的网格：自己的成员 + 全部子孙的成员。

    为什么不能只看直接成员（``_member_rect(members)``）：内核把 warp 下的顶点当作
    **父级控制网格内的 (s,t)**，画落在父级网格之外时官方 Core 是按边界那一格线性
    外推的（#72 实测：越界 160px 的一条边，Core 画的位置与「根本没有变形器」的对照
    逐字节相同）。所以「挂上父级」本身不会打偏静止帧 —— 但官方美术约定是**画留在
    父级网格之内**，理由有三条，都量过：

      1. 矩形之内，官方 Core、自研播放器、离线尺子三方读数一致到 ≤1px；
      2. 越界 + **非仿射弯曲**的父级键形在驱动键上与 Core 实测差 29px
         （诊断 `H_out_4x3_bend`；夹取版差 118px，外推版也不等于内核）；
      3. 越界量会被离线尺子读成静止偏差（歌手件 hair_back 27/140 个顶点在
         HeadRig 矩形之外 ⇒ 假报「静止帧偏 27.183px」，而 Core 静止 SHA 没动）。

    让矩形把子孙罩住，三条同时消失；这是**参数化**的改动（父级场的跨度会变，
    因此挂父级与撤烘焙、扩矩形必须在同一批里量满幅弧幅，见 Task 3 的验收）。

    子孙按**合法** under 边（:func:`resolve_deformer_parents`）走；某级根本没
    编译出来时它不在 ``members_by_name`` 里，自然贡献 0 个网格。
    """
    children: Dict[str, List[str]] = {}
    for child, under in parents.items():
        children.setdefault(under, []).append(child)
    picked: List[MeshSpec] = list(members_by_name.get(name) or [])
    stack, seen = list(children.get(name, [])), {name}
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        picked.extend(members_by_name.get(current) or [])
        stack.extend(children.get(current, []))
    return picked


#: 用「躯干倾摆」这套形变的 profile。它们必须共用同一个尺度，见
#: :func:`_body_lean_rect`。
BODY_LEAN_PROFILES = ("torso", "lift_lean")


def resolve_membership(entries: List[Dict],
                       mesh_ids: Iterable[str]) -> Dict[str, str]:
    """层名 -> 变形器名。与 :func:`_resolve_warp_members` **同一套**规则：按声明顺序先到先得。

    烘焙守卫在**烘焙前**就要知道归属（``build_deformers`` 是在装配阶段才用
    ``replace(mesh, deformer_id=...)`` 改写 specs 的），而两处规则一旦不同，就会出现
    「以为被主干接住、其实没有」的头颈脱节 —— 所以这里把规则抽成一个函数，
    ``_resolve_warp_members`` 也从它取数，不给第二份实现留生存空间。
    """
    by_norm = {_norm(str(mid)): str(mid) for mid in mesh_ids}
    taken: Dict[str, str] = {}
    for entry in entries:
        if str(entry.get("type")) not in ("warp", "rotation"):
            continue
        name = str(entry.get("name") or "")
        if not name:
            continue
        for target in entry.get("targets") or []:
            mesh_id = by_norm.get(_norm(str(target)))
            if mesh_id is not None and mesh_id not in taken:
                taken[mesh_id] = name
    return taken


def _resolve_warp_members(specs: List[MeshSpec],
                          entries: List[Dict]) -> Dict[str, List[MeshSpec]]:
    """按 ``entries`` 的**声明顺序**解析每个变形器的成员网格。

    一个网格只能挂一个变形器，所以先到先得。变形器名 -> 成员；没有命中任何网格的
    变形器不在表里。:func:`build_deformers` 与 :func:`_body_lean_rect` 共用这一份解析，
    共享尺度算出来的范围才跟真正挂上去的成员一致。
    """
    by_id = {m.mesh_id: m for m in specs}
    members_by_name: Dict[str, List[MeshSpec]] = {}
    for mesh_id, name in resolve_membership(entries, by_id).items():
        members_by_name.setdefault(name, []).append(by_id[mesh_id])
    return members_by_name


def _body_lean_rect(entries: List[Dict],
                    members_by_name: Dict[str, List[MeshSpec]]) -> Optional[tuple]:
    """所有「随躯干倾摆」的网格合起来的 (x0, y0, x1, y1) —— 倾摆的**共享尺度**。

    倾摆量按 ``宽度 × 深度`` 算，而深度是「该点离胯部多远 / 躯干总高」。每个变形器各用
    自己成员的范围当尺度时，同一张图上的两块网格算出来的位移并不相同：实测（singer，
    ``ParamBodyAngleY = 10``）领子横移 26.03px、上身衣服 14.67px，而挂在另一条变形器上
    的手臂与脖子位移 **0px** —— 衣服整件从脖子上滑走。

    共用一个尺度以后，同一个 (x, y) 无论落在哪块网格上都得到同一个位移（倾摆沿 y 线性，
    2 行的控制网格能精确表示），手臂与领口就跟着躯干一起走。

    **锚点（y1 = 深度 0 的那条线）只按躯干本体（``torso`` profile）的成员取**，不跟着
    手臂往下走：手臂垂到腰以下，把并集底拖到大腿。实测分层器切出来的衣服三段是
    ``clothes_top`` 202..476 / ``waistband`` 477..505 / ``clothes_bottom`` 506..833 ——
    **零重叠的硬接**，腰带下缘只要平移一点，腰线就裂开一道贯通的缝（``ParamBreath = 1``
    时实测整条腰横穿背景）。锚点钉在腰带下缘以后，腰带在该处位移为 0，缝不再开。
    """
    lean = [
        (str(entry.get("profile") or ""),
         _rest_rect(members_by_name[str(entry.get("name") or "")]))
        for entry in entries
        if str(entry.get("profile") or "") in BODY_LEAN_PROFILES
        and str(entry.get("name") or "") in members_by_name
    ]
    if not lean:
        return None
    torso = [r for profile, r in lean if profile == "torso"] or [r for _p, r in lean]
    return (
        min(r[0] for _p, r in lean), min(r[1] for _p, r in lean),
        max(r[2] for _p, r in lean), max(r[3] for r in torso),
    )


def _lattice(rect: tuple, n_cols: int, n_rows: int) -> List[List[float]]:
    """行优先（x 变化最快）、y 自下而上的规则点阵 —— 点序由官方内核实测钉住。"""
    x0, y0, x1, y1 = rect
    step_x = (x1 - x0) / (n_cols - 1)
    step_y = (y1 - y0) / (n_rows - 1)
    return [[x0 + c * step_x, y0 + r * step_y]
            for r in range(n_rows) for c in range(n_cols)]


def _driving_rotation_parameter(pivot: Any,
                                parameters_by_id: Dict[str, ParameterSpec],
                                bone_positions: Dict[str, Any],
                                proximity_px: float = 0.0) -> tuple:
    """按「枢轴 == 官方转动骨骼」找出驱动该 rotation 变形器的唯一官方参数。

    对齐 ``ROTATION_KEYFORM_PARAMS``：枢轴即 Head 的绑 ``ParamAngleZ``、即 Body 的绑
    ``ParamBodyAngleZ``。只有**恰好命中一个**已声明参数时才绑定；否则返回
    ``("", None)`` —— 宁可不驱动，也不猜一个参数。

    ``proximity_px`` > 0 时启用**就近匹配**作为精确匹配的兜底：自动绑骨生成的枢轴
    常因取整或比例换算偏离骨骼坐标几个像素，标准 Live2D 实践按就近原则归属。
    就近匹配仍然守「唯一命中」规则 —— 多个骨骼等距时不猜，保持静态。
    """
    px, py = float(pivot[0]), float(pivot[1])

    # 第一轮：精确匹配（容差 1e-3，与原始行为一致）
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
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        return "", None

    # 第二轮：就近匹配（仅当 proximity_px > 0 且精确匹配落空时）
    if proximity_px <= 0:
        return "", None
    nearest = []  # [(距离, pid, param), ...]
    for pid, spec in ROTATION_KEYFORM_PARAMS.items():
        param = parameters_by_id.get(pid)
        if param is None:
            continue
        for bone_name in _bone_names(spec):
            bone = bone_positions.get(bone_name)
            if bone is None:
                continue
            dist = math.hypot(float(bone[0]) - px, float(bone[1]) - py)
            if dist <= proximity_px:
                nearest.append((dist, pid, param))
    if not nearest:
        return "", None
    nearest.sort(key=lambda t: t[0])
    best_dist = nearest[0][0]
    best_hits = [(pid, param) for dist, pid, param in nearest
                 if dist <= best_dist + 1e-3]
    if len(best_hits) != 1:
        return "", None
    return best_hits[0]


def _profile_parameter_ids(entry: Dict[str, Any]) -> List[str]:
    """入口声明的驱动参数，按 profile 的角色顺序。兼容旧的单参数写法。"""
    ids = entry.get("parameters")
    if ids:
        return [str(p) for p in ids]
    single = str(entry.get("parameter") or "")
    return [single] if single else []


#: 张嘴时下沿最多向下拉多少（相对网格自身高度）。嘴在多数立绘里只是一条 2~4 像素的
#: 墨线，没有"张开"的现成画稿，能做的只是把这条线按比例撑开：1.0 时几乎看不出，
#: 3 以上会把线拉成一条粗黑带。2.5 是两者之间的取值。
MOUTH_OPEN_STRETCH = 2.5
#: 口型（笑/撇嘴）把两端抬起或压下的幅度，相对网格高度。
MOUTH_FORM_BOW = 0.8
#: 嘴横向缩放幅度（``ParamMouthSize`` ±1 时整体宽度的 ±35%）。
MOUTH_SIZE_SCALE = 0.35

#: 闭眼时眼球向上收拢后剩下的一条线，占眼高比例。压到 0 会让控制网格退化成线、
#: 内核逆映射退化（实测 Cubism Core 5.0.0），所以留 8%。
EYE_CLOSED_SCALE = 0.08

#: 眉毛位移量（相对眉毛自身高度）：上下 / 倾斜 / 中段上拱。
BROW_LIFT = 0.6
BROW_TILT = 0.35
BROW_BOW = 0.25

#: 发梢横向摆幅（相对头发宽度），越靠下发梢摆得越远。
HAIR_SWAY = 0.10

#: 躯干：侧倾的横向位移、前后倾的纵向位移、呼吸抬升（均相对网格尺寸）。
TORSO_LEAN_X = 0.05
TORSO_LEAN_Y = 0.02
TORSO_BREATH = 0.03

#: 肩部抬落幅度（相对上臂网格高度）。没有独立肩部图层，只能抬整条上臂。
SHOULDER_LIFT = 0.12

#: 张嘴时口内向下张开的幅度，相对嘴线网格高度。必须**明显大于** ``MOUTH_OPEN_STRETCH``：
#: 口内与嘴唇同形同源，倍数一样就会永远正好被嘴唇盖住，做了等于没做。
MOUTH_CAVITY_STRETCH = 4.0


def _centered_fraction(param: ParameterSpec, value: float) -> float:
    """参数值 -> 相对默认值的归一化偏移 ``[-1, 1]``。

    默认值一定映射到 **0**，所以"静止形 = 恒等变换"是构造保证的，不依赖某个
    具体参数恰好以 0 为默认值（``ParamBreath`` 的默认是 0.5）。
    """
    default = float(param.default)
    span = max(abs(float(param.minimum) - default),
               abs(float(param.maximum) - default)) or 1.0
    return (float(value) - default) / span


def _band_indices(counts: Sequence[int]) -> List[tuple]:
    """展平序的索引组合：**第一个轴步长为 1**（与网格多轴键形同一定论）。"""
    total = 1
    for n in counts:
        total *= n
    out: List[tuple] = []
    for flat in range(total):
        rest, index = flat, []
        for n in counts:
            index.append(rest % n)
            rest //= n
        out.append(tuple(index))
    return out


def _mouth_deform(points, rect, mesh_rect, values, options):
    """嘴：横向缩放（大小）∘ 两端上/下弯（口型）∘ 上沿钉住下拉（开合）。

    横向归一化 ``t`` 必须夹到 ``[-1, 1]``：控制网格比网格本身宽得多（含头部键形的
    摆动范围，实测 55px vs 网格 14px），不夹的话 t² 会到 16，弯曲量被放大 16 倍
    —— 实测嘴角被拉起 21px，在脸上拉出一道 V 形伤疤。
    """
    cx = (float(mesh_rect[0]) + float(mesh_rect[2])) / 2.0
    half_w = max(1e-6, (float(mesh_rect[2]) - float(mesh_rect[0])) / 2.0)
    top = float(mesh_rect[1])
    height = max(1e-6, float(mesh_rect[3]) - top)
    scale_x = 1.0 + MOUTH_SIZE_SCALE * values.get("size", 0.0)
    bow = -MOUTH_FORM_BOW * height * values.get("form", 0.0)
    stretch = 1.0 + MOUTH_OPEN_STRETCH * max(0.0, values.get("open", 0.0))
    out = []
    for x, y in points:
        nx = cx + (x - cx) * scale_x
        t = max(-1.0, min(1.0, (nx - cx) / half_w))
        out.append((nx, top + (y - top) * stretch + bow * t * t))
    return out


def _eye_deform(points, rect, mesh_rect, values, options):
    """睁闭眼：把眼球**向上收拢**到睫毛线上。

    闭眼不是"眼球缩小"，而是"上眼皮落下来盖住眼球"。所以压缩的轴心取眼睛
    轮廓的**上沿**（不是纵向中心）：眼球整体向上收进睫毛线里，看上去就是眼皮
    盖下来了。收拢后露出来的是画在眼球**下面**的闭眼图（脸层肤色 + 原画上睫毛，
    见 ``LayerComposer._synthesize_eyelid``）。

    用纵向中心收拢（原来的做法）会把眼球压成一条横在眼睛中间的线，像眯眼。
    """
    top = float(mesh_rect[1])
    openness = min(1.0, max(0.0, 1.0 + values.get("open", 0.0)))
    scale = EYE_CLOSED_SCALE + (1.0 - EYE_CLOSED_SCALE) * openness
    return [(x, top + (y - top) * scale) for x, y in points]


def _brow_deform(points, rect, mesh_rect, values, options):
    """眉毛：上下平移 ∘ 沿 x 的倾斜 ∘ 中段上拱。

    ``options["mirror"]`` 为真时倾斜方向取反：左眉的"内侧"是右端、右眉是左端，
    同一个 ``ParamBrowAngle`` 在两侧必须是镜像的。
    """
    cx = (float(mesh_rect[0]) + float(mesh_rect[2])) / 2.0
    half_w = max(1e-6, (float(mesh_rect[2]) - float(mesh_rect[0])) / 2.0)
    height = max(1e-6, float(mesh_rect[3]) - float(mesh_rect[1]))
    lift = -BROW_LIFT * height * values.get("y", 0.0)      # 屏幕上方 = -y
    tilt = BROW_TILT * height * values.get("angle", 0.0)
    if options.get("mirror"):
        tilt = -tilt
    bow = -BROW_BOW * height * values.get("form", 0.0)
    out = []
    for x, y in points:
        t = max(-1.0, min(1.0, (x - cx) / half_w))
        out.append((x, y + lift + tilt * t + bow * (1.0 - t * t)))
    return out


def _hair_sway_deform(points, rect, mesh_rect, values, options):
    """头发摆动：越靠下发梢横向摆得越远（发根钉住）。"""
    width = max(1e-6, float(mesh_rect[2]) - float(mesh_rect[0]))
    top = float(mesh_rect[1])
    height = max(1e-6, float(mesh_rect[3]) - top)
    swing = values.get("swing", 0.0)
    out = []
    for x, y in points:
        depth = min(1.0, max(0.0, (y - top) / height))
        out.append((x + swing * HAIR_SWAY * width * depth ** 1.5, y))
    return out


def _torso_depth(mesh_rect, options):
    """倾摆这套形变共用的 (宽度, 高度, 胯部 y)。

    优先用 :func:`_body_lean_rect` 算出的**共享**尺度：各变形器各用自己的成员范围时，
    同一张图上相邻的两块网格会滑开（实测 ``ParamBodyAngleY = 10`` 时领子横移 26.03px、
    上身衣服 14.67px，而手臂与脖子 0px）。没有共享尺度（只有一个躯干变形器）时退回
    自己的范围，行为与原来一致。
    """
    rect = options.get("body_rect") or mesh_rect
    width = max(1e-6, float(rect[2]) - float(rect[0]))
    height = max(1e-6, float(rect[3]) - float(rect[1]))
    return width, height, float(rect[3])


def _torso_lean(values, width, height, bottom, y):
    """躯干倾摆在某个高度上的位移 ``(dx, dy)``：越靠上位移越大，胯部钉住。"""
    depth = min(1.0, max(0.0, (bottom - y) / height))   # 0 = 胯部, 1 = 肩
    lean_x = values.get("angle_y", 0.0) * TORSO_LEAN_X + values.get("sway", 0.0) * TORSO_LEAN_X
    lean_y = values.get("angle_x", 0.0)
    breath = values.get("breath", 0.0)
    return (lean_x * width * depth,
            -lean_y * TORSO_LEAN_Y * height * depth
            - breath * TORSO_BREATH * height * depth)


def _torso_deform(points, rect, mesh_rect, values, options):
    """躯干：侧倾/前后倾（越靠上位移越大，胯部不动）+ 呼吸抬升。"""
    width, height, bottom = _torso_depth(mesh_rect, options)
    out = []
    for x, y in points:
        dx, dy = _torso_lean(values, width, height, bottom, y)
        out.append((x + dx, y + dy))
    return out


def _lift_lean_deform(points, rect, mesh_rect, values, options):
    """肩部抬落 **叠加** 躯干的侧倾/前后倾/摆动/呼吸。

    抬落按这条变形器自己的成员高度算（保持既有幅度：``ParamShoulderL = 1`` 抬 47.76px、
    右侧 64.80px），倾摆按**共享尺度**算 —— 于是手臂与躯干在同一高度上位移逐点相同。
    倾摆沿 y 线性，2 行控制网格能精确表示，不会因网格更粗而失真。
    """
    width, height, bottom = _torso_depth(mesh_rect, options)
    lift_height = max(1e-6, float(mesh_rect[3]) - float(mesh_rect[1]))
    lift_dy = -SHOULDER_LIFT * lift_height * values.get("lift", 0.0)
    out = []
    for x, y in points:
        dx, dy = _torso_lean(values, width, height, bottom, y)
        out.append((x + dx, y + lift_dy + dy))
    return out


def _lift_deform(points, rect, mesh_rect, values, options):
    """整体上下平移（肩部抬落）：+值向上，屏幕上方 = -y。"""
    height = max(1e-6, float(mesh_rect[3]) - float(mesh_rect[1]))
    dy = -SHOULDER_LIFT * height * values.get("lift", 0.0)
    return [(x, y + dy) for x, y in points]


def _mouth_cavity_deform(points, rect, mesh_rect, values, options):
    """口内：上沿钉住、向下张得比嘴唇更深，于是从唇下露出一条腔体。"""
    top = float(mesh_rect[1])
    stretch = 1.0 + MOUTH_CAVITY_STRETCH * max(0.0, values.get("open", 0.0))
    return [(x, top + (y - top) * stretch) for x, y in points]


def _apply_head(points, values, options):
    """把整颗头的转头/点头/侧转作用到点上（与头部网格键形同一套公式）。

    ``options["head"]`` 是 ``(px, py, shift_x, shift_y, depth)``，由
    :func:`_head_options` 按**整颗头的共享尺度**算出；缺失时原样返回。
    """
    head = options.get("head")
    if not head:
        return list(points)
    px, py, shift_x, shift_y, depth = head
    angles = options.get("head_angles") or {}
    ang = [math.radians(values.get(role, 0.0) * angles.get(role, 0.0))
           for role in ("angle_x", "angle_y", "angle_z")]
    sin_x, cos_x = math.sin(ang[0]), math.cos(ang[0])
    sin_y, cos_y = math.sin(ang[1]), math.cos(ang[1])
    sin_z, cos_z = math.sin(ang[2]), math.cos(ang[2])
    out = []
    for x, y in points:
        tx, ty = _head_transform(x, y, px, py, shift_x, shift_y,
                                 sin_x, cos_x, sin_y, cos_y, sin_z, cos_z,
                                 depth, 1.0)
        out.append((tx, ty))
    return out


def _head_follow_deform(points, rect, mesh_rect, values, options):
    """只跟随头部三轴 —— Task 3 起这是**整颗头唯一**的一份头转（``HeadRig``）。

    它原本是给腮红 / 眼泪这类层补洞用的：那些层的网格键形带被**不透明度**键形
    （``ParamCheek`` / ``ParamTears``）占满，头部三轴进不去，而它们又没有别的变形器，
    于是各建一条只含三轴的 warp（实测 ``ParamAngleZ = 30`` 时脸转 13.15px、
    cheek / tear 位移 0px，腮红和泪痕留在原地）。代价是同一颗头被**六个**独立拟合的
    场各转一遍：满幅（``ParamAngleZ=30`` + ``ParamBodyAngleZ=10``）时同角度、不同
    圆心，配对距离没人约束 —— singer 件实测头内部 27.1272px。现在部件改成挂在
    HeadRig 之下，靠 ``parent_deformer_indices`` 继承同一份形变。
    """
    return _apply_head(points, values, options)


NECK_BAND_ROWS_MIN = 4          # 带内至少 4 行，否则连续过渡退化成二值

#: 颈胸场的**横向**衰减：颈部半宽之内满权重（脖子本身不能被削），再往外保留一段
#: 满权重（留给脖子自己的摆动/键形位移），最后用**升余弦**降到 0。倍数以颈部半宽
#: 为单位。
#:
#: 为什么必须有横向衰减：转头是绕颈轴的一整块刚体，只看 y 的话肩线（离颈轴
#: 250~330px）也会跟着转。实测（G 轨，ParamAngleZ=30 + ParamBodyAngleZ=10）
#: 整件上衣挂上去以后 clothes_top↔arm_L 撕到 112.4px、Core 洞数 1116→1731 ——
#: 衣服从肩膀上滑走，比原来的脸↔衣服缝更糟。横向衰减让领口跟头、肩线留在身上。
#:
#: 为什么是升余弦而不是直线：点阵之间是双线性插值，直线在折点处留下与步长成正比
#: 的权重误差（90px 过渡、53px 格距时约 0.2 ⇒ 30px）；升余弦两端一阶导为 0，
#: 同一套点阵下误差降到 1/5 左右。
NECK_BAND_SIDE_PLATEAU = 2.0
NECK_BAND_SIDE_FADE = 1.6


def neck_band_side_weight(x: float, axis_x: float, half_span: float,
                          core_span: Optional[float] = None) -> float:
    """离颈轴的横向权重：颈轴附近为 1，向外用升余弦降到 0。

    给了 ``core_span`` 时（几何切分场景）用「核心半宽 → 切分线」这一段做过渡，
    并且**在切分线上严格归零**：切缝两侧因此没有相对位移，缝不会张开。不给时退回
    按 ``NECK_BAND_SIDE_PLATEAU/FADE`` 的旧口径，行为与单轴版本一致。
    """
    if half_span is None or float(half_span) <= 1e-6:
        return 1.0
    half_span = float(half_span)
    offset = abs(float(x) - float(axis_x))
    if core_span is not None:
        core = min(max(float(core_span), 0.0), half_span)
        if offset <= core:
            return 1.0
        if offset >= half_span:
            return 0.0
        return 0.5 + 0.5 * math.cos(math.pi * (offset - core) / (half_span - core))
    plateau = NECK_BAND_SIDE_PLATEAU * half_span
    fade = NECK_BAND_SIDE_FADE * half_span
    if offset <= plateau:
        return 1.0
    if fade <= 1e-6 or offset >= plateau + fade:
        return 0.0
    return 0.5 + 0.5 * math.cos(math.pi * (offset - plateau) / fade)


def neck_band_weight(y: float, boundary_y: float, band_bottom_y: float,
                     x: Optional[float] = None, axis_x: Optional[float] = None,
                     half_span_x: Optional[float] = None,
                     core_span_x: Optional[float] = None) -> float:
    """颈胸过渡带权重：纵向（头下缘 1 → 带底 0）× 横向（颈轴 1 → 切分线 0）。

    不给横向参数时就是原来的单轴权重 —— 调用方拿不到颈轴信息时行为不变。
    """
    span = float(band_bottom_y) - float(boundary_y)
    if span <= 1e-6:
        return 0.0
    vertical = min(1.0, max(0.0, (float(band_bottom_y) - float(y)) / span))
    if vertical <= 0.0 or x is None or axis_x is None or half_span_x is None:
        return vertical
    return vertical * neck_band_side_weight(x, axis_x, half_span_x, core_span_x)


def _snap_band_bottom(bottom: float, rect: tuple, n_rows: int) -> float:
    """把带底对齐到控制点阵里**不大于它的那一行**。

    点阵行是均匀的，带底落在两行之间时，带底以下的格点仍带着正权重，插值到带底
    不为 0 —— 同一件衣服的下缘会相对腰带滑开（实测 5% 余量下约 6px）。对齐到行
    以后，带底及其以下的每一行权重都是 0，插值结果严格为 0。
    """
    y0, y1 = float(rect[1]), float(rect[3])
    step = (y1 - y0) / max(1, n_rows - 1)
    if step <= 1e-6:
        return float(bottom)
    steps = math.floor((float(bottom) - y0) / step + 1e-9)
    steps = min(max(steps, 0), n_rows - 1)
    return y0 + steps * step


def _neck_band_deform(points, rect, mesh_rect, values, options):
    """在同一个 warp 点阵里按格点位置混合头部场：头下缘满权重，带底与肩线归零。"""
    head = options.get("head")
    if not head:
        return list(points)
    px, py, shift_x, shift_y, depth = head
    boundary = float(options.get("boundary_y", py))
    bottom = float(options.get("band_bottom_y", rect[3]))
    axis_x = options.get("band_axis_x")
    half_span = options.get("band_half_span_x")
    core_span = options.get("band_core_half_x")
    angles = options.get("head_angles") or {}
    out = []
    for x, y in points:
        weight = neck_band_weight(float(y), boundary, bottom,
                                  x=float(x), axis_x=axis_x, half_span_x=half_span,
                                  core_span_x=core_span)
        if weight <= 0.0:
            head_point = [x, y]
        else:
            theta = [math.radians(values.get(role, 0.0) * angles.get(role, 0.0))
                     for role in ("angle_x", "angle_y", "angle_z")]
            sx, cx = math.sin(theta[0]), math.cos(theta[0])
            sy, cy = math.sin(theta[1]), math.cos(theta[1])
            sz, cz = math.sin(theta[2]), math.cos(theta[2])
            head_point = _head_transform(x, y, px, py, shift_x, shift_y,
                                         sx, cx, sy, cy, sz, cz, depth, weight)
        out.append(head_point)
    return out


def _eye_head_deform(points, rect, mesh_rect, values, options):
    """眨眼（眼球向上收拢）**叠加**整颗头的转头/点头/侧转。

    眼睛网格的键形带已经被双眼球跟踪（``ParamEyeBallX/Y``）占用，头部三轴只能压在
    变形器层；而一块网格只能挂一个变形器，所以眨眼与头部三轴必须合成同一条多轴带。
    顺序 = 先眨眼再随头（与"先做表情、再整体转动头部"一致）。
    """
    return _apply_head(
        _eye_deform(points, rect, mesh_rect, values, options), values, options)


def _head_options(bone_positions: Dict[str, Any], width: float, height: float,
                  head_span: Optional[tuple],
                  parameters_by_id: Dict[str, ParameterSpec],
                  angle_ids: Sequence[str]) -> Optional[Dict[str, Any]]:
    """头部驱动需要的参数：枢轴、共享尺度、各轴的角度幅度。

    缺任何一项都返回 ``None`` —— 调用方自行降级（``eye_head`` 退回只眨眼，
    ``head`` 保持静态），不交半套形变。
    """
    joint = bone_positions.get("Head")
    if joint is None or head_span is None or len(angle_ids) != 3:
        return None
    specs = [parameters_by_id.get(pid) for pid in angle_ids]
    if any(p is None for p in specs):
        return None
    span_x, span_y = float(head_span[0]), float(head_span[1])
    px, py = to_model_xy(joint[0], joint[1], width, height)
    return {
        "head": (px, py, span_x * HEAD_SHIFT_X, span_y * HEAD_SHIFT_Y,
                 max(span_x, span_y, 1.0) * HEAD_DEPTH_SPANS),
        "head_angles": {
            role: max(abs(float(p.minimum)), abs(float(p.maximum)))
            for role, p in zip(("angle_x", "angle_y", "angle_z"), specs)
        },
    }


#: ``profile`` -> (角色名序列, 形变函数)。
#:
#: 入口用 ``parameters`` 按**同样顺序**给出参数 id；任一参数没在模型里声明就整条
#: 不生成（宁可不驱动，也不交半套形变）。角色名只是给形变函数取值用的键。
DEFORMER_PROFILES: Dict[str, tuple] = {
    "mouth": (("open", "form", "size"), _mouth_deform),
    "mouth_cavity": (("open",), _mouth_cavity_deform),
    "eye": (("open",), _eye_deform),
    # 眨眼 + 头部三轴。**Task 3 起作者表不再使用它**（EyeBlink_L/R 退回 ``eye``，
    # 头的三轴由 HeadRig 一条带送到）；能力保留：`_apply_head` 那套公式还在被
    # ``head`` 用，而 ``eye_head`` 的组合顺序（先表情、后随头）仍是
    # ``tests/unit/test_moc3_deformers.py::TestEyeFollowsHead`` 的测试对象。
    "eye_head": (("open", "angle_x", "angle_y", "angle_z"), _eye_head_deform),
    # 只有头部三轴。Task 3 以前它是给淡入层（CheekFollow / TearFollow）各补一份
    # 头转用的 —— 那正是头内部各场各转各的来源；现在它是**整颗头唯一**的那一份
    # （HeadRig），挂在它下面的部件靠继承而不是靠各自拟合。
    "head": (("angle_x", "angle_y", "angle_z"), _head_follow_deform),
    "neck_band": (("angle_x", "angle_y", "angle_z"), _neck_band_deform),
    "brow": (("y", "angle", "form"), _brow_deform),
    "hair_sway": (("swing",), _hair_sway_deform),
    "torso": (("angle_x", "angle_y", "sway", "breath"), _torso_deform),
    "lift": (("lift",), _lift_deform),
    "lift_lean": (("lift", "angle_x", "angle_y", "sway", "breath"), _lift_lean_deform),
}


#: 「淡入淡出」型表情层：层名 -> ``((参数 id, 键值元组), ...)``。
#:
#: 腮红与眼泪没有形状变化，只有**不透明度**变化，所以用网格键形的 opacity 而不是
#: warp 变形器（warp 改不了透明度）。一个网格只能挂一条键形带，而
#: ``ParamTears`` 是总开关、``ParamTearsL/R`` 是左右，所以必须合成一条**多轴带**。
#:
#: 多轴组合取各轴归一化值的 **max** 而不是乘积：``ParamTears`` 打开就该两侧都有，
#: ``ParamTearsL`` 单独打开就该只有左侧。乘积会让任何单轴都变不动（官方内核验收
#: 是逐参数从 min 扫到 max、其它参数停在默认值，实测 changed=0），也不符合
#: "总开关"的语义。
#:
#: 官方 Cubism Core 5.0.0 实测（逐帧取像素）：键形 opacity=0 时 0 像素、=1 时
#: 2500 像素；多轴按 (总开关, 单侧) 落在对应键形上。参数没声明时整条不挂、
#: 保持静态 —— 同其它 profile 的「宁可不驱动，也不交半套」。
OPACITY_KEYFORM_LAYERS: Dict[str, tuple] = {
    "cheek": (("ParamCheek", (0.0, 1.0)),),
    "tear_left": (("ParamTears", (0.0, 1.0)), ("ParamTearsL", (0.0, 1.0))),
    "tear_right": (("ParamTears", (0.0, 1.0)), ("ParamTearsR", (0.0, 1.0))),
}


def _opacity_keyforms(name: str,
                      params_by_id: Dict[str, ParameterSpec],
                      vertices: List[List[float]]):
    """该层的不透明度键形 ``(axes, shapes)``；不是淡入层或参数缺失时返回 None。

    键形顶点与静止顶点完全相同 —— 只有 opacity 变。``shapes`` 按展平序排列，
    **第一个轴步长为 1**（与网格多轴键形同一定论，见
    :func:`_band_indices`）。多轴的不透明度取各轴归一化值的最大值（见
    ``OPACITY_KEYFORM_LAYERS`` 的说明）。
    """
    axes = OPACITY_KEYFORM_LAYERS.get(_norm(name))
    if not axes:
        return None
    resolved = []
    for pid, keys in axes:
        param = params_by_id.get(pid)
        if param is None:
            log.info(f"淡入层 {name} 的参数 {pid} 未声明，保持静态")
            return None
        ordered = tuple(float(v) for v in keys)
        if len(ordered) < 2 or not all(a < b for a, b in zip(ordered, ordered[1:])):
            return None
        resolved.append((pid, ordered))

    shapes: List[KeyformShape] = []
    counts = [len(keys) for _pid, keys in resolved]
    for index, combination in enumerate(_band_indices(counts)):
        opacity = 0.0
        for (_pid, keys), slot in zip(resolved, combination):
            lo, hi = keys[0], keys[-1]
            span = hi - lo
            frac = (keys[slot] - lo) / span if span > 0 else 1.0
            opacity = max(opacity, frac)
        shapes.append(KeyformShape(float(index), vertices, opacity=opacity))
    return tuple(resolved), tuple(shapes)


def _profile_grids(profile: str,
                   param_ids: Sequence[str],
                   rect: tuple,
                   n_cols: int,
                   n_rows: int,
                   mesh_rect: tuple,
                   parameters_by_id: Dict[str, ParameterSpec],
                   options: Dict[str, Any]):
    """按 profile 生成逐键控制网格：返回 ``(axes, grids)``。

    axes 是 ``((参数 id, 键值元组), ...)``，grids 按**第一个轴步长为 1** 展平排列。
    """
    roles, deform = DEFORMER_PROFILES[profile]
    if len(param_ids) != len(roles):
        return (), []
    params = [parameters_by_id.get(pid) for pid in param_ids]
    if any(p is None for p in params):
        return (), []
    axes = tuple((pid, tuple(_key_times(p))) for pid, p in zip(param_ids, params))
    base = _lattice(rect, n_cols, n_rows)
    grids: List[DeformerGrid] = []
    for index, combination in enumerate(_band_indices([len(k) for _pid, k in axes])):
        values = {}
        for role, (pid, keys), slot in zip(roles, axes, combination):
            param = parameters_by_id[pid]
            values[role] = _centered_fraction(param, keys[slot])
        grids.append(DeformerGrid(float(index),
                                  deform(base, rect, mesh_rect, values, options)))
    return axes, grids


def _edge_is_acyclic(name: str, raw: Dict[str, str]) -> bool:
    """从 ``name`` 的父级出发会不会绕回自己（或绕进已走过的节点）。"""
    seen: set = set()
    cursor = raw[name]
    while cursor:
        if cursor in seen or cursor == name:
            return False
        seen.add(cursor)
        cursor = raw.get(cursor, "")
    return True


def resolve_deformer_parents(entries: List[Dict]) -> Dict[str, str]:
    """``entry["under"]`` -> 父变形器名；只保留指向已声明节点、且不构成环的边。

    用 ``under`` 而不是复用 ``parent``：``parent`` 已经是**骨骼名**（
    ``bones/deformers.py`` 的 ``DEFAULT_WARP_DEFORMERS`` 每条都在写
    ``"parent": "Head"`` 这类骨骼），两层语义重载会把「骨骼归属」和「变形链」
    搅成一团 —— 骨骼链由 model3.json 的 Part 层级表达，变形链由 moc3 的
    ``deformer.parent_deformer_indices`` 表达。

    不自父、指向未声明节点、成环的边在这里**就地丢弃**（子级因此退回根级），
    不报错：未声明/自父的报错归 ``RigSpec._validate_deformers``，环的报错归
    ``deformer_space_map``（那条会点名整条链）。本函数只保证交出去的每条边都能用，
    所以它的判定必须比那两处更宽松，不能反过来重复它们的措辞。
    """
    declared = {str(entry.get("name") or "") for entry in entries} - {""}
    raw: Dict[str, str] = {}
    for entry in entries:
        name = str(entry.get("name") or "")
        under = str(entry.get("under") or "")
        if name and under and under in declared and under != name:
            raw[name] = under
    return {name: under for name, under in raw.items()
            if _edge_is_acyclic(name, raw)}


#: 编译侧的**链深上限**。Task 1.5 只用官方 Core 量到深度 3 的恒等链静止与键形继承
#: （``tests/unit/test_deformer_parent_chain.py``、
#: ``tests/unit/test_moc3_deformers.py::test_third_level_is_normalized_by_the_level_directly_above_it``），
#: 4 已经留了一级余量。这条守卫的存在理由不是「怕递归太慢」，而是：以前编译侧
#: 完全没有上限（``resolve_deformer_parents`` 无界走、``deformer_space_map.entry``
#: 只防环），只有离线尺子与探针各自限在 ``guard < 32`` —— 于是「作者多写一级」和
#: 「尺子还不认」之间没有任何一处会拦。超过上限的那一级**退回无父级并留痕**，
#: 而不是让未验证过的深度静默进产物。
TRUNK_MAX_CHAIN_DEPTH = 4


def deformer_chain_depth(name: str, parents: Dict[str, str]) -> int:
    """沿 ``parents`` 数到根级的层数；根级 = 1。"""
    depth, cursor, seen = 1, parents.get(name, ""), {name}
    while cursor and cursor not in seen:
        seen.add(cursor)
        cursor = parents.get(cursor, "")
        depth += 1
    return depth


def _drop_over_depth_edges(parents: Dict[str, str]) -> tuple:
    """把深度超过 ``TRUNK_MAX_CHAIN_DEPTH`` 的那些边退回根级，返回 ``(留下的边, 说明)``。"""
    depths = {name: deformer_chain_depth(name, parents) for name in parents}
    kept = {name: under for name, under in parents.items()
            if depths[name] <= TRUNK_MAX_CHAIN_DEPTH}
    notes = [f"{name}(under 链深 {depths[name]} 超过已验证上限 "
             f"{TRUNK_MAX_CHAIN_DEPTH}：按无父级处理)"
             for name in parents if name not in kept]
    return kept, notes


def under_edge_notes(entries: List[Dict]) -> List[str]:
    """被丢掉的 ``under`` 边必须**可见**：拼错的父级名与成环的父链都算。

    ``resolve_deformer_parents`` 在 ``RigSpec._validate_deformers`` **之前**就把未声明
    父级与自父滤掉了，所以 ``under: "HeadRgg"`` 会**静默**变成一片扁平林：既不报
    「父变形器不存在」，也不进 ``uncompiled``，画面还看着合理。本分支的约束是
    「宁可不驱动 … 并如实留痕」，而 Task 3~5 恰恰要手改这些条目 —— 少一个键、
    多打一个字母都是必然会犯的错误。
    """
    declared = {str(entry.get("name") or "") for entry in entries} - {""}
    legal = resolve_deformer_parents(entries)
    notes: List[str] = []
    for entry in entries:
        name = str(entry.get("name") or "")
        under = str(entry.get("under") or "")
        if not name or not under or name in legal:
            continue
        if under == name:
            notes.append(f"{name}(under 指向自己：按无父级处理)")
        elif under not in declared:
            notes.append(f"{name}(under {under} 未声明：按无父级处理)")
        else:
            notes.append(f"{name}(under 父链成环：按无父级处理)")
    return notes


def legal_deformer_parents(entries: List[Dict]) -> tuple:
    """``under`` 边的**唯一**裁定处： ``(留下的边, 被丢掉那些边的说明)``。

    两处消费方（``build_deformers`` 写 ``parent_deformer_id``、
    :func:`head_field_deformers` 判「继承到底送不送得到」）必须走同一个裁定，
    否则会出现「守卫以为送得到、编译器其实把这条边丢了」的头颈脱节。
    """
    parents, deep_notes = _drop_over_depth_edges(resolve_deformer_parents(entries))
    return parents, under_edge_notes(entries) + deep_notes


#: 带着「整颗头的三轴」这一份形变的 profile。``eye_head`` 是 Task 3 之前的历史形态
#: （眨眼 + 头部三轴一条带），作为**能力**保留在 ``DEFORMER_PROFILES`` 里，作者表
#: 已经不用它了；这里一并认出来，免得有人改回去时守卫静默失效。``neck_band`` 是
#: Task 5 扩进来的颈胸分级场，它那条带同样只有头部三轴。
HEAD_FIELD_PROFILES = frozenset({"head", "eye_head", "neck_band"})

#: 负责把头送到各部件的那条主干变形器的**名字**。HeadRig 送整颗头，NeckBand 送脖子
#: （Task 5 扩入）。BodySway 不在这里：它那条带是躯干 X/Y/摆动/呼吸，**没有**头部
#: 三轴，撤掉它成员的头部烘焙会让头彻底不动；它的子孙 HeadRig/NeckBand 各自是根。
HEAD_FIELD_DEFORMERS = frozenset({"HeadRig", "NeckBand"})


def head_field_deformers(entries: List[Dict],
                         mesh_ids: Iterable[str]) -> Set[str]:
    """HeadRig 及其**真的会编译出来**的子孙 —— 这些变形器负责成员网格的头部三轴。

    只看声明不够，三条理由都指向「声明存在 ≠ 边真的落地」：
      1. 条目没落地：``build_deformers`` 会因「没有成员网格」整条跳过某个变形器，
         并在第二趟把指向它的边退回根级；
      2. 边被裁定丢掉：未声明 / 成环 / 自父 / 超过 ``TRUNK_MAX_CHAIN_DEPTH`` 的
         ``under`` 都不再送形变（:func:`legal_deformer_parents`）；
      3. 父级只是静态点阵：``profile`` 不是 ``head`` 时那条带上没有头的三轴。
    照声明就撤烘焙、而继承其实断了，结果是部件**一动不动** —— 比双份转动更难查，
    也比现状更糟。
    """
    parents, _notes = legal_deformer_parents(entries)
    owned = set(resolve_membership(entries, mesh_ids).values())
    emitted = {str(entry.get("name") or "") for entry in entries
               if str(entry.get("type")) in ("warp", "rotation")
               and str(entry.get("name") or "") in owned}
    roots = {name for name in HEAD_FIELD_DEFORMERS
             if name in emitted
             and any(str(entry.get("name") or "") == name
                     and str(entry.get("profile") or "") in HEAD_FIELD_PROFILES
                     for entry in entries)}
    children: Dict[str, List[str]] = {}
    for name, under in parents.items():
        if name in emitted and under in emitted:
            children.setdefault(under, []).append(name)
    found: Set[str] = set()
    frontier = list(roots)
    while frontier:
        name = frontier.pop()
        if name in found:
            continue
        found.add(name)
        frontier.extend(children.get(name, []))
    return found


def head_field_covered_ids(entries: List[Dict],
                           mesh_ids: Iterable[str],
                           head_available: bool) -> Set[str]:
    """主干已经接住头部三轴的层名。``head_available=False`` 时返回空集 —— 宁回退烘焙。"""
    if not head_available:
        return set()
    owners = head_field_deformers(entries, mesh_ids)
    if not owners:
        return set()
    membership = resolve_membership(entries, mesh_ids)
    covered = {mesh_id for mesh_id, name in membership.items() if name in owners}
    return covered


def head_field_available(bone_positions: Dict[str, Any],
                         head_span: Optional[tuple],
                         parameters_by_id: Dict[str, ParameterSpec],
                         angle_ids: Sequence[str] = HEAD_ANGLE_AXES) -> bool:
    """``profile="head"`` 那条带**能不能**算出来 —— 与 :func:`_head_options` 同一套前提。

    ``_head_options`` 要画布尺寸才能把 Head 关节换算成模型坐标，而烘焙守卫必须在
    网格循环**之前**决定（那时 width/height 还攥在 ``extents`` 里没取出来），所以
    这里只查与尺寸无关的那几条前提。两边前提不同步时，宁可这里更严：守卫让位、
    烘焙留下，画面最差也就是回到今天的样子。
    """
    if head_span is None or len(angle_ids) != 3:
        return False
    if bone_positions.get("Head") is None:
        return False
    return all(parameters_by_id.get(pid) is not None for pid in angle_ids)


def build_deformers(specs: List[MeshSpec],
                    entries: List[Dict],
                    width: float,
                    height: float,
                    parameters_by_id: Dict[str, ParameterSpec] | None = None,
                    bone_positions: Dict[str, Any] | None = None,
                    head_span: Optional[tuple] = None,
                    head_bottom: Optional[float] = None) -> tuple:
    """把管线的 warp / rotation 变形器编成规格，并把成员网格就地挂上（改写 specs）。

    返回 ``(变形器规格, 未编译说明, 静态变形器名)``。一个网格只能挂一个变形器。

    条目里的 ``under``（父变形器名）解析成 ``parent_deformer_id``；父级因为
    「没有成员网格」等原因根本没被落地时，子级退回无父级并在 ``uncompiled`` 里
    留一条 ``X(父级 Y 未落地：按无父级处理)``，不静默保留一条指向空气的父链。

    rotation 的**参数绑定**由枢轴判定（见 ``_driving_rotation_parameter``）：命中唯一
    官方参数时烘焙**逐键键形**，它真的会随参数转动；枢轴对不上任何已知转动骨骼时退回
    **单键形**并记入 ``deformers_static``（结构存在、不随参数动），不冒充会动的追踪。
    """
    members_by_name = _resolve_warp_members(specs, entries)
    # 躯干倾摆的共享尺度：所有「会随身体 X/Y 轴倾摆」的变形器（躯干本身 + 挂在手臂上的
    # 肩部抬落）按同一块范围算位移，否则相邻两块网格会互相滑开（见 _body_lean_rect）。
    body_rect = _body_lean_rect(entries, members_by_name)
    linked: Dict[str, str] = {}
    for name, members in members_by_name.items():
        for mesh in members:
            linked[mesh.mesh_id] = name
    out: List = []
    static: List[str] = []
    bound: List[str] = []
    breath_bound: List[str] = []
    expression_bound: List[str] = []
    skipped: List[str] = []
    # ``under`` 的裁定只此一处：留下的边写进 parent_deformer_id，被丢掉的边在这里
    # 留痕（未声明 / 自父 / 成环 / 超过链深上限），不静默退回扁平林。
    parents, edge_notes = legal_deformer_parents(entries)
    skipped.extend(edge_notes)
    for entry in entries:
        name = str(entry.get("name") or "")
        dtype = str(entry.get("type"))
        if dtype not in ("warp", "rotation"):
            # 先判类型再挂网格：否则未知类型会把成员网格挂到一个并不存在的
            # 变形器上，编译期直接报「引用了不存在的变形器」。
            skipped.append(f"{name}(type={dtype})")
            continue
        members = members_by_name.get(name) or []
        if not members:
            skipped.append(f"{name}(没有可挂的成员网格)")
            continue
        if dtype == "warp":
            n_rows = max(2, int(entry.get("grid_rows") or 2))
            n_cols = max(2, int(entry.get("grid_cols") or 2))
            # 矩形按**子孙**的成员算，不只是直接成员：见
            # :func:`rect_covering_descendants`（#72 的产物侧修法 —— 画留在父级网格
            # 之内，Core / 自研播放器 / 离线尺子才读同一个数）。没有 warp 子孙时
            # 这份并集就是 members 本身，今天的根级产物一个字节都不变。
            rect = _member_rect(rect_covering_descendants(name, members_by_name, parents))
            # 成员网格**静止姿态**的并集范围（不含键形摆动）。形变量要按它算，
            # 否则嘴/眼会被含键形的外包框放大好几倍（见 _open_down_grids）。
            mesh_rect = _rest_rect(members)
            profile = str(entry.get("profile") or "")
            param_ids = _profile_parameter_ids(entry)
            options = dict(entry)
            if profile in BODY_LEAN_PROFILES and body_rect is not None:
                options["body_rect"] = body_rect
            if profile == "lift_lean" and any(
                    pid not in (parameters_by_id or {}) for pid in param_ids[1:]):
                # 身体轴参数不全：退回「只抬肩」，别把抬肩一起丢掉
                profile, param_ids = "lift", param_ids[:1]
            missing_head = False
            if profile in ("eye_head", "head", "neck_band"):
                angle_ids = (param_ids[1:] if profile == "eye_head" else
                             param_ids[:3] if profile == "neck_band" else param_ids)
                head_options = _head_options(
                    bone_positions or {}, width, height, head_span,
                    parameters_by_id or {}, angle_ids)
                if head_options is None:
                    if profile == "eye_head":
                        # 头部信息不全：退回只眨眼，别把眨眼一起丢掉
                        profile, param_ids = "eye", param_ids[:1]
                    else:
                        missing_head = True
                else:
                    options.update(head_options)
            bound_param = ""
            axes: tuple = ()
            if profile == "neck_band" and head_bottom is None:
                n_rows = max(n_rows, NECK_BAND_ROWS_MIN)
                skipped.append(f"{name}(缺头下缘：颈胸过渡场不生成)")
                axes, grids = (), [DeformerGrid(0.0, _lattice(rect, n_cols, n_rows))]
            elif profile == "neck_band" and missing_head:
                n_rows = max(n_rows, NECK_BAND_ROWS_MIN)
                skipped.append(f"{name}(头部信息不全：缺 Head 关节 / head_span / 角度参数)")
                axes, grids = (), [DeformerGrid(0.0, _lattice(rect, n_cols, n_rows))]
            elif missing_head:
                # 只剩空壳的「跟随头部」会让成员网格白占一个变形器名额，
                # 静态网格 + 如实记录才是本来的行为。
                if profile == "neck_band":
                    n_rows = max(n_rows, NECK_BAND_ROWS_MIN)
                skipped.append(f"{name}(头部信息不全：缺 Head 关节 / head_span / 角度参数)")
                axes, grids = (), [DeformerGrid(0.0, _lattice(rect, n_cols, n_rows))]
            elif profile in DEFORMER_PROFILES and param_ids:
                if profile == "neck_band":
                    # 必须在 _profile_grids 生成点阵之前抬行数，否则 rows 与点数不一致。
                    n_rows = max(n_rows, NECK_BAND_ROWS_MIN)
                    options["boundary_y"] = float(head_bottom)
                    # 带底与颈轴都取 ``axis_layer``（默认由条目声明）那一层的范围，
                    # 不是全体成员的下缘：衣服整件挂进来以后，用成员下缘会把过渡带
                    # 拉到腰上，脖子与衣服下缘的权重都跟着变。
                    axis_layer = str(entry.get("axis_layer") or "")
                    axis_member = next(
                        (m for m in members
                         if axis_layer and _norm(m.mesh_id) == _norm(axis_layer)),
                        None)
                    band_rect = mesh_rect
                    if axis_member is not None:
                        band_rect = _rest_rect([axis_member])
                        options["band_axis_x"] = (
                            float(band_rect[0]) + float(band_rect[2])) / 2.0
                        options["band_half_span_x"] = max(
                            1.0, (float(band_rect[2]) - float(band_rect[0])) / 2.0)
                        options["band_core_half_x"] = options["band_half_span_x"]
                    # 几何切分时，过渡片只覆盖到切分线为止：横向权重必须在那条线上
                    # 严格归零，否则紧贴切线的两片顶点各走各的，缝会张开
                    # （实测颈右侧 474 像素竖缝）。切分方把两条半宽写进条目。
                    if entry.get("side_span") is not None:
                        options["band_half_span_x"] = float(entry["side_span"])
                    if entry.get("core_span") is not None:
                        options["band_core_half_x"] = float(entry["core_span"])
                    options["band_bottom_y"] = _snap_band_bottom(
                        band_rect[3], rect, n_rows)
                axes, grids = _profile_grids(
                    profile, param_ids, rect, n_cols, n_rows, mesh_rect,
                    parameters_by_id or {}, options)
                if not grids:
                    missing = [pid for pid in param_ids
                               if pid not in (parameters_by_id or {})]
                    skipped.append(
                        f"{name}(profile={profile} 参数不可用: "
                        f"{missing or '数量与 profile 不符'})")
                    grids = [DeformerGrid(0.0, _lattice(rect, n_cols, n_rows))]
                else:
                    bound_param = param_ids[0]
                    expression_bound.append(f"{name}={('×'.join(param_ids))}")
            elif param_ids:
                # 声明了参数却没有可用形变方式：不猜形变，保持静态并如实记录
                skipped.append(f"{name}(未知 profile={profile or '-'})")
                grids = [DeformerGrid(0.0, _lattice(rect, n_cols, n_rows))]
            else:
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
                    bound_param = breath.parameter_id
                    breath_bound.append(f"{name}={breath.parameter_id}")
            out.append(WarpDeformerSpec(
                deformer_id=name, rows=n_rows - 1, cols=n_cols - 1,
                parameter_id=bound_param,
                axes=axes,
                grids=grids,
                parent_deformer_id=parents.get(name, "")))
        elif dtype == "rotation":
            pivot = entry.get("pivot") or (0.0, 0.0)
            # 与网格顶点同一换算：图像坐标 -> 原点居中、y 向上的模型坐标
            origin = to_model_xy(pivot[0], pivot[1], width, height)
            # 就近匹配容差：画布最大边的 5%。自动绑骨生成的枢轴常因取整/比例换算
            # 偏离骨骼坐标几个像素，标准 Live2D 实践按就近原则归属；5% 足以容纳
            # 正常偏差，又不会把不相关的枢轴错绑到近旁骨骼。
            _proximity = max(width, height) * 0.05 if width > 0 and height > 0 else 0.0
            pid, param = _driving_rotation_parameter(
                pivot, parameters_by_id or {}, bone_positions or {},
                proximity_px=_proximity)
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
                    base_angle=float(entry.get("angle") or 0.0),
                    parent_deformer_id=parents.get(name, "")))
                bound.append(f"{name}={pid}")
            else:
                out.append(RotationDeformerSpec(
                    deformer_id=name,
                    keyforms=[RotationKeyform(0.0, angle=0.0, origin=origin)],
                    base_angle=float(entry.get("angle") or 0.0),
                    parent_deformer_id=parents.get(name, "")))
                static.append(name)
    # 父级是否真的落地，要到循环结束才知道（成员网格空不空在循环里判），所以父链
    # 用**两趟**收尾：先按声明写好，再把指向「声明了但没进表」的父级的边退回无父级，
    # 并如实记一条说明。留着那条边等于交给编译器去报「父变形器不存在」。
    emitted = {deformer.deformer_id for deformer in out}
    finalized: List = []
    for deformer in out:
        parent_id = deformer.parent_deformer_id
        if parent_id and parent_id not in emitted:
            skipped.append(f"{deformer.deformer_id}(父级 {parent_id} "
                           "未落地：按无父级处理)")
            deformer = replace(deformer, parent_deformer_id="")
        finalized.append(deformer)
    out = finalized
    if linked:
        specs[:] = [
            replace(mesh, deformer_id=linked[mesh.mesh_id])
            if mesh.mesh_id in linked else mesh
            for mesh in specs
        ]
    if breath_bound:
        log.info(f"呼吸已烘焙（{BREATH_PARAM} 驱动的 warp 双键形）：{breath_bound}")
    if expression_bound:
        log.info(f"表情形变已烘焙（warp 逐键网格，真的会随参数变）：{expression_bound}")
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


#: 过渡片在颈轴两侧各占多少个「颈部半宽」。超出这条竖带的衣料仍留在 ``BodySway``：
#: 肩线跟着头转会和挂在 ``ShoulderLift_*`` 上的手臂脱开 —— 实测只按 y 切时 Core
#: 姿态在右肩开出 730 像素的新透明区（``clothes_top_neck`` ↔ ``arm_L`` 87.9px）。
CLOTHES_TRANSITION_SIDE_SPAN = 1.6


def _clip_half_plane(points: List[tuple], axis: str, value: float,
                     keep_le: bool, tag: str) -> List[tuple]:
    """Sutherland–Hodgman：把多边形裁到一条轴对齐半平面。

    ``keep_le=True`` 保留 ``coord <= value`` 的一侧。交点键里带 ``tag``：同一个
    三角形可能被两条平行平面各切一刀，只用边做键会让两个不同的交点塌成一个。
    """
    out: List[tuple] = []
    slot = 0 if axis == "x" else 1
    count = len(points)
    for i in range(count):
        current = points[i]
        following = points[(i + 1) % count]
        cv = float(current[1][slot])
        fv = float(following[1][slot])
        c_in = cv <= value if keep_le else cv >= value
        f_in = fv <= value if keep_le else fv >= value
        if c_in:
            out.append(current)
        if c_in == f_in:
            continue
        t = (value - cv) / (fv - cv)
        normal = None
        if current[2] is not None and following[2] is not None:
            normal = [a + (b - a) * t for a, b in zip(current[2], following[2])]
        weights = None
        if current[3] is not None and following[3] is not None:
            weights = [a + (b - a) * t for a, b in zip(current[3], following[3])]
        norm = None
        if current[4] is not None and following[4] is not None:
            norm = [a + (b - a) * t for a, b in zip(current[4], following[4])]
        key = ("e", tag) + tuple(sorted((repr(current[0]), repr(following[0]))))
        out.append((key,
                    [current[1][0] + (following[1][0] - current[1][0]) * t,
                     current[1][1] + (following[1][1] - current[1][1]) * t],
                    normal, weights, norm))
    return out


def _clip_mesh(mesh: Dict[str, Any],
               planes: List[tuple]) -> Optional[Dict[str, Any]]:
    """按若干 ``(axis, value, keep_le, tag)`` 半平面裁剪三角网格。

    全被裁掉时返回 ``None``。顶点法线、蒙皮权重都按交点线性插值 —— 共享边界两侧
    用的是同一条边、同一个 ``tag``，插出来的位置逐点相同，接缝不会因裁切而错开。
    """
    raw_vertices = mesh.get("vertices")
    if raw_vertices is None or len(raw_vertices) == 0:
        return None
    vertices = [[float(v[0]), float(v[1])] for v in raw_vertices]
    raw_normals = mesh.get("vertices_norm")
    normals = ([[float(n[0]), float(n[1])] for n in raw_normals]
               if raw_normals is not None else None)
    skin = mesh.get("weights") or {}
    bone_names = list(skin.get("bone_names") or [])
    raw_weights = skin.get("weights")
    weights = ([[float(w) for w in row] for row in raw_weights]
               if raw_weights is not None and len(raw_weights) == len(vertices)
               else None)
    width = float(mesh.get("width") or 0)
    height = float(mesh.get("height") or 0)
    span_x = width or 1.0
    span_y = height or 1.0

    out_v: List[List[float]] = []
    out_normals: List[Optional[List[float]]] = []
    out_weights: List[Optional[List[float]]] = []
    triangles: List[List[int]] = []
    index_of: Dict[Any, int] = {}

    def add(point) -> int:
        key = point[0]
        existing = index_of.get(key)
        if existing is not None:
            return existing
        index_of[key] = len(out_v)
        out_v.append([float(point[1][0]), float(point[1][1])])
        out_normals.append(None if point[2] is None
                           else [float(point[2][0]), float(point[2][1])])
        out_weights.append(None if point[3] is None
                           else [float(w) for w in point[3]])
        return len(out_v) - 1

    for tri in mesh.get("indices") or []:
        polygon = []
        for vi in map(int, tri):
            polygon.append((("v", vi), vertices[vi],
                            normals[vi] if normals is not None else None,
                            weights[vi] if weights is not None else None,
                            [vertices[vi][0] / span_x, vertices[vi][1] / span_y]))
        for axis, value, keep_le, tag in planes:
            if len(polygon) < 3:
                break
            polygon = _clip_half_plane(polygon, axis, float(value), keep_le, tag)
        if len(polygon) < 3:
            continue
        first = add(polygon[0])
        for i in range(1, len(polygon) - 1):
            b, c = add(polygon[i]), add(polygon[i + 1])
            if len({first, b, c}) == 3:
                triangles.append([first, b, c])
    if not triangles:
        return None

    result: Dict[str, Any] = {
        "vertices": out_v,
        "vertices_norm": (out_normals if normals is not None
                          else [[x / span_x, y / span_y] for x, y in out_v]),
        "indices": triangles,
        "width": width,
        "height": height,
    }
    if weights is not None and all(row is not None for row in out_weights):
        result["weights"] = {"bone_names": bone_names, "weights": out_weights}
    return result


def _merge_meshes(parts: List[Optional[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """把互不重叠的几片网格并成一个（顶点表直接拼接，索引整体平移）。"""
    usable = [part for part in parts if part]
    if not usable:
        return None
    if len(usable) == 1:
        return usable[0]
    merged: Dict[str, Any] = {
        "vertices": [], "vertices_norm": [], "indices": [],
        "width": usable[0]["width"], "height": usable[0]["height"],
    }
    bone_names: Optional[List[str]] = None
    merged_weights: List[List[float]] = []
    keep_weights = True
    for part in usable:
        offset = len(merged["vertices"])
        merged["vertices"].extend([list(v) for v in part["vertices"]])
        merged["vertices_norm"].extend(
            [list(n) for n in part.get("vertices_norm") or []])
        merged["indices"].extend(
            [[a + offset, b + offset, c + offset] for a, b, c in part["indices"]])
        skin = part.get("weights") or {}
        rows = skin.get("weights")
        if not bone_names and skin.get("bone_names"):
            bone_names = list(skin["bone_names"])
        if keep_weights and rows is not None:
            merged_weights.extend([list(row) for row in rows])
        else:
            keep_weights = False
    if keep_weights and bone_names and len(merged_weights) == len(merged["vertices"]):
        merged["weights"] = {"bone_names": bone_names, "weights": merged_weights}
    return merged


def split_mesh_at_y(mesh: Dict[str, Any], cut_y: float) -> tuple:
    """精确裁切三角网格，返回图像坐标 y<=cut_y 与 y>=cut_y 两片。"""
    return (_clip_mesh(mesh, [("y", cut_y, True, "y")]),
            _clip_mesh(mesh, [("y", cut_y, False, "y")]))


def split_mesh_region(mesh: Dict[str, Any], cut_y: float,
                      left: float, right: float) -> tuple:
    """把网格切成「颈带内的上片」与「其余」两片。

    上片 = ``y <= cut_y`` 且 ``left <= x <= right``；其余 = 上片之外的全部（左右
    两条竖带 + 下半部），并成一个网格。两片面积并集严格等于原网格。
    """
    inside = _clip_mesh(mesh, [("y", cut_y, True, "y"),
                               ("x", left, False, "left"),
                               ("x", right, True, "right")])
    outside = _merge_meshes([
        _clip_mesh(mesh, [("y", cut_y, True, "y"), ("x", left, True, "left")]),
        _clip_mesh(mesh, [("y", cut_y, True, "y"), ("x", right, False, "right")]),
        _clip_mesh(mesh, [("y", cut_y, False, "y")]),
    ])
    return inside, outside



def _mesh_bottom_y(mesh: Dict[str, Any]) -> float:
    raw = mesh.get("vertices")
    if raw is None or len(raw) == 0:
        return float("-inf")
    return max(float(vertex[1]) for vertex in raw)


def split_clothes_top_transition(meshes: Dict[str, Dict[str, Any]],
                                 atlas_uvs: Dict[str, Dict[str, Any]],
                                 entries: List[Dict],
                                 overlap_px: float = 12.0) -> tuple:
    """沿 neck 底缘下方切 clothes_top；窄上片挂 NeckBand，其余留 BodySway。"""
    neck = meshes.get("neck")
    clothes = meshes.get("clothes_top")
    neck_uv = atlas_uvs.get("neck")
    clothes_uv = atlas_uvs.get("clothes_top")
    band = next((entry for entry in entries
                 if entry.get("name") == "NeckBand"), None)
    body = next((entry for entry in entries
                 if entry.get("name") == "BodySway"), None)
    if not (neck and clothes and neck_uv and clothes_uv and band and body):
        return dict(meshes), dict(atlas_uvs), [dict(e) for e in entries], ""
    cut_y = _mesh_bottom_y(neck) + float(overlap_px)
    raw_vertices = clothes.get("vertices")
    ys = [float(v[1]) for v in raw_vertices] if raw_vertices is not None else []
    if not ys or not (min(ys) < cut_y < max(ys)):
        return dict(meshes), dict(atlas_uvs), [dict(e) for e in entries], ""
    neck_vertices = neck.get("vertices")
    neck_xs = ([float(v[0]) for v in neck_vertices]
               if neck_vertices is not None else [])
    if not neck_xs:
        return dict(meshes), dict(atlas_uvs), [dict(e) for e in entries], ""
    axis = (min(neck_xs) + max(neck_xs)) / 2.0
    core = max(1.0, (max(neck_xs) - min(neck_xs)) / 2.0)
    side = core * CLOTHES_TRANSITION_SIDE_SPAN
    upper, lower = split_mesh_region(clothes, cut_y, axis - side, axis + side)
    if upper is None or lower is None:
        return dict(meshes), dict(atlas_uvs), [dict(e) for e in entries], ""
    split_meshes = {}
    for name, mesh in meshes.items():
        if name == "clothes_top":
            split_meshes["clothes_top_neck"] = upper
            split_meshes["clothes_top"] = lower
        else:
            split_meshes[name] = mesh
    split_uvs = dict(atlas_uvs)
    split_uvs["clothes_top_neck"] = dict(clothes_uv)
    split_entries = [dict(e) for e in entries]
    for entry in split_entries:
        if entry.get("name") == "NeckBand":
            targets = list(entry.get("targets") or [])
            if "clothes_top_neck" not in targets:
                targets.append("clothes_top_neck")
            entry["targets"] = targets
            # 横向权重的定义域：颈轴附近满权重，到切分线严格降到 0
            entry["core_span"] = core
            entry["side_span"] = side
        elif entry.get("name") == "BodySway":
            targets = [t for t in entry.get("targets") or []
                       if _norm(str(t)) != "clothes_top"]
            targets.append("clothes_top")
            entry["targets"] = targets
    return (split_meshes, split_uvs, split_entries,
            f"clothes_top seam y={cut_y:.3f} x=[{axis - side:.1f},{axis + side:.1f}]")


def build_rig_spec(
    builder_result: Dict[str, Any],
    atlas_uvs: Dict[str, Dict[str, float]],
) -> RigSpec:
    """从管线产物构造 RigSpec；图集缺摆放信息时明确报错。"""
    meshes = builder_result.get("meshes") or {}
    if not meshes:
        raise UnsupportedRig("至少需要一个网格")

    entries = [dict(entry) for entry in
               ((builder_result.get("deformer_tree") or {}).get("deformers") or [])]
    meshes, atlas_uvs, entries, split_note = split_clothes_top_transition(
        meshes, atlas_uvs, entries)
    if split_note:
        log.info(f"颈胸网格细分：{split_note}")
    extents = set()
    specs: List[MeshSpec] = []
    params = _parameter_specs(builder_result)
    params_by_id = {p.parameter_id: p for p in params}
    bone_positions = builder_result.get("bone_positions") or {}
    from live2d_builder.bones.deformers import BoneHierarchy
    standard_bones = BoneHierarchy.STANDARD_BONES
    # 整颗头的共享尺度：所有头部网格用同一组 shift / 透视深度，五官才跟着脸走。
    head_span = head_assembly_span(meshes, params_by_id, standard_bones,
                                   bone_positions)
    head_bottom = head_assembly_bottom(meshes, standard_bones, bone_positions)
    keyformed, multi_hit, eyes_tracked, head_tracked = [], [], [], []
    opacity_driven: List[str] = []
    trunk_covered: List[str] = []
    # 主干已经接住头部三轴的层名（HeadRig 与 NeckBand 闭包；BodySway 那条带没有
    # 头部三轴，不在守卫根里）。归属必须在**烘焙之前**按层名解析一次：变形器装配
    # 在循环之后才改写 ``mesh.deformer_id``，等到那时候再问就晚了。
    deformer_entries = entries
    covered = head_field_covered_ids(
        deformer_entries, meshes.keys(),
        head_available=head_field_available(bone_positions, head_span,
                                           params_by_id))

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
        # 图集可能有多页，网格必须指向自己所在的那一页。曾经漏传 texture_index，
        # 于是所有网格都取 0 号纹理：多页图集下只有第 0 页的层能画对，其余层的 UV
        # 落在别的页上，画面只剩零星碎片（实测：29 层 / 7 页的模型只画出 1.3 万像素）。
        page = int(rect.get("page") or 0)

        vertices: List[List[float]] = []
        uvs: List[List[float]] = []
        normals = mesh.get("vertices_norm")
        count = len(mesh["vertices"])
        for i, (x, y) in enumerate(mesh["vertices"]):
            x, y = float(x), float(y)
            vertices.append(list(to_model_xy(x, y, width, height)))
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

        # 淡入层（腮红 / 眼泪）优先：它们只有不透明度变化，且必须是**独立网格**，
        # 否则会跟眼球追踪/头部键形抢同一条键形带（一个网格只能挂一条）。
        opacity_spec = (_opacity_keyforms(name, params_by_id, vertices)
                        if MESH_KEYFORMS_ENABLED else None)
        if opacity_spec is not None:
            opacity_axes, opacity_shapes = opacity_spec
            # 淡入层画在脸上/眼角，躯干侧倾时它必须和头一起走，否则静止贴合的
            # 腮红会在倾身时相对脸滑开。
            opacity_axes, opacity_shapes = with_body_follow(
                opacity_axes, opacity_shapes, vertices, params_by_id,
                bone_positions, width, height)
            opacity_driven.append(
                f"{name}={'×'.join(pid for pid, _keys in opacity_axes)}")
            specs.append(MeshSpec(
                mesh_id=str(name),
                vertices=vertices,
                triangles=triangles,
                uvs=uvs,
                draw_order=float(order),
                texture_index=page,
                keyform_axes=opacity_axes,
                keyform_shapes=opacity_shapes,
            ))
            continue

        if MESH_KEYFORMS_ENABLED:
            eye_axes, eye_shapes = eye_track_keyforms(mesh, vertices, params_by_id)
            # 眼球带也要跟身体：一个网格只能挂一条带，只给头部带加身体轴的话
            # 眼皮跟着走、眼球留在原地 —— 实测 10° 侧倾时眼球相对眼皮滑 64px。
            eye_axes, eye_shapes = with_body_follow(
                eye_axes, eye_shapes, vertices, params_by_id,
                bone_positions, width, height)
            head_axes, head_shapes, head_weights = head_angle_keyforms(
                mesh, vertices, params_by_id, standard_bones,
                bone_positions, width, height, head_span=head_span)
            if name in covered:
                # 头的三轴由 HeadRig（或其 warp 子级）送到 —— 这里必须**不再**烘一份，
                # 否则转头被施加两次（挂上父级却不撤烘焙 = 画面比现状更差）。
                # 上面那次调用只为拿 ``head_weights``：身体跟随的权重必须和头形变的
                # 权重同源，否则半权重顶点会比邻居家多跑一截。
                # 眼球跟踪带（``eye_axes``）与不透明度带**保留** —— 那是本来就该相对
                # 父级动的局部表情，不在「重复拟合」的那一份里。
                head_axes, head_shapes = body_follow_band(
                    vertices, params_by_id, bone_positions, width, height,
                    weights=head_weights)
                # 旋转带也必须一起撤：``head_axes`` 空了以后 rotation_keyforms 会把
                # 同一颗头降级成单轴 Z 逐顶点键形，那同样是第二次侧转。
                pid, shapes, candidates = "", [], []
            else:
                head_axes, head_shapes = with_body_follow(
                    head_axes, head_shapes, vertices, params_by_id,
                    bone_positions, width, height, weights=head_weights)
                pid, shapes, candidates = rotation_keyforms(
                    mesh, params_by_id, bone_positions, standard_bones,
                    width, height)
        else:
            # 见 MESH_KEYFORMS_ENABLED 的说明：网格键形会让内核在加载阶段崩溃。
            eye_axes = eye_shapes = head_axes = head_shapes = ()
            pid, shapes, candidates = "", [], []

        if eye_axes:
            # 眼球优先于旋转键形：瞳孔是平移，且必须由 X+Y 两轴共同驱动。
            eyes_tracked.append(name)
            specs.append(MeshSpec(
                mesh_id=str(name),
                vertices=vertices,
                triangles=triangles,
                uvs=uvs,
                draw_order=float(order),
                texture_index=page,
                keyform_shapes=eye_shapes,
                keyform_axes=eye_axes,
            ))
            continue
        if name in covered:
            # 挂进主干场的网格：带里只剩身体跟随（甚至什么都没有），头转在父级。
            trunk_covered.append(name)
            specs.append(MeshSpec(
                mesh_id=str(name),
                vertices=vertices,
                triangles=triangles,
                uvs=uvs,
                draw_order=float(order),
                texture_index=page,
                keyform_shapes=head_shapes,
                keyform_axes=head_axes,
            ))
            continue
        if head_axes:
            head_tracked.append(name)
            specs.append(MeshSpec(
                mesh_id=str(name),
                vertices=vertices,
                triangles=triangles,
                uvs=uvs,
                draw_order=float(order),
                texture_index=page,
                keyform_shapes=head_shapes,
                keyform_axes=head_axes,
            ))
            continue
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
            texture_index=page,
            keyform_parameter_id=pid,
            keyform_shapes=shapes,
        ))

    if len(extents) > 1:
        raise UnsupportedRig(f"各网格画布尺寸不一致: {sorted(extents)}")
    width, height = extents.pop()

    if head_tracked:
        log.info(f"头部三轴复合已烘焙（{'+'.join(HEAD_ANGLE_AXES)}）："
                 f"{len(head_tracked)} 个网格 {head_tracked[:5]}")
    if trunk_covered:
        log.info(f"头部三轴改由 HeadRig 继承，网格不再自带这份带："
                 f"{len(trunk_covered)} 个网格 {trunk_covered[:5]}"
                 f"{'…' if len(trunk_covered) > 5 else ''}")
    if eyes_tracked:
        log.info(f"眼球跟随已烘焙（{'+'.join(EYE_TRACK_AXES)} 双轴）："
                 f"{len(eyes_tracked)} 个网格 {eyes_tracked[:5]}")
    if keyformed:
        driven = sorted({m.keyform_parameter_id for m in specs if m.keyform_shapes})
        log.info(f"参数形变键形已烘焙：{len(keyformed)} 个网格，驱动参数 {driven}")
    if opacity_driven:
        log.info(f"淡入表情层已烘焙（不透明度键形）：{opacity_driven}")
    if multi_hit:
        log.warning(
            f"{len(multi_hit)} 个网格同时受多个旋转参数影响，需要多参数带；"
            f"其 keyform 轴序未核实，保持静态形状：{[n for n, _ in multi_hit][:5]}")
    if not keyformed:
        log.info("本次导出没有可烘焙的参数键形（无蒙皮权重或无 Z 轴参数）")

    deformer_specs, uncompiled, static = build_deformers(
        specs, list(deformer_entries), width, height,
        parameters_by_id=params_by_id, bone_positions=bone_positions,
        head_span=head_span, head_bottom=head_bottom)
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


def driven_parameters(spec: RigSpec) -> Dict[str, List[float]]:
    """编译后产物里**真正被绑定**的参数 -> 键值序列。

    两个来源：网格键形（``keyform_axes`` / 单轴 ``keyform_parameter_id``）与
    warp 变形器的带（``axes_spec``）。这是"模型自称能驱动什么"的唯一权威来源：
    参数表里声明了但没有任何绑定的参数，改了画面不会变（实测 28 个参数里曾有
    19 个属于这一类）。

    与 :func:`drivable_parameters` 的区别：后者是**编译前**给 motion 排曲线用的，
    只看单轴网格键形；这里看编译后的规格，包含多轴带与变形器。
    """
    from live2d_builder.exporter.moc3_model import _mesh_axes

    keys: Dict[str, set] = {}

    def add(pid, values):
        keys.setdefault(str(pid), set()).update(float(v) for v in values)

    for mesh in spec.meshes:
        if not mesh.keyform_shapes:
            continue
        for pid, values in _mesh_axes(mesh):
            if pid:
                add(pid, values)
    for deformer in spec.deformers:
        # warp 与 rotation 都有 axes_spec / is_keyformed（单参数带亦可）
        if not deformer.is_keyformed:
            continue
        for pid, values in deformer.axes_spec:
            if pid:
                add(pid, values)
    return {pid: sorted(values) for pid, values in keys.items()}


def _attach_capabilities(output_dir: str, character_name: str,
                        driven: Dict[str, List[float]]) -> None:
    """把「本模型能驱动哪些参数」写进 model3.json。

    验收端（``drivers.live2d_runtime.verify``）据此决定**该断言哪些参数会动**：
    没有这份声明就只能按固定的三个参数抽查，或者干脆把所有声明过的参数都当承诺
    ——前者漏检，后者把"需要美术图层"的参数（腮红/眼泪）误判成缺陷。
    非标准键，官方内核只读取它认识的字段，多余键不影响加载（已验证）。
    """
    path = Path(output_dir) / f"{character_name}.model3.json"
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    data.setdefault("Capabilities", {})["DrivableParameters"] = sorted(driven)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def write_idle_motion(output_dir: str, character_name: str,
                      driven: Dict[str, List[float]],
                      defaults: Optional[Dict[str, float]] = None) -> List[str]:
    """按**编译后**的权威可驱动集合生成 idle 动作，并回写清单。

    为什么必须在 moc3 编译之后、而不是在导出 model3.json 时顺手生成：
    导出期只有编译前的 :func:`drivable_parameters`，它只认「单轴 Z 网格键形」，
    看不见 warp 变形器的参数带。实测（2026-10-31，本机 singer 包）因此
    moc3 自称能驱动 27 个参数、idle 却只给 `ParamBodyAngleZ` 排了 1 条曲线 ——
    角色在播放器里只摆身子，没有呼吸、没有发摆、没有口型。

    写在这里还保住一条不变式：本函数跑在 motion 验收**之前**，而验收读的是
    磁盘上的清单（:func:`_declares_motions`），所以「verified=True」背书的就是
    最终交付的那份 motion，不是一份已被替换的旧文件。

    physics3 的**输出**参数一律排除。实测（2026-10-31 重导 singer）把它们也排进
    曲线时，官方内核逐帧对账直接判失败：``动到的参数 27/27，逐帧超限的曲线
    ['ParamBodySway', 'ParamHairSwing']`` —— 内核先按 motion 写值、物理再覆写，
    实测值对不上文件值。这两个参数本来就该由物理驱动（头发/裙摆的惯性摆动），
    它们的输入 AngleX/AngleY/BodyAngleY 已由 motion 排了曲线，摆动链是完整的。

    ``defaults`` 是参数声明里的默认值，用来定曲线绕哪个值摆。静止姿态不能按
    「离 0 最近的键」猜：眼睑的默认值是 1.0（睁开），猜成 0.0 时整段待机都是
    闭眼（实测 2026-11-01 重导 singer 的曲线是 0..0.2）。
    """
    from live2d_builder.motion.motion3 import Motion3Builder

    out = Path(output_dir)
    manifest_path = out / f"{character_name}.model3.json"
    if not manifest_path.is_file():
        return []

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning(f"回写 motion 清单失败（读不到 model3.json）: {exc}")
        return []

    physics_outputs = _physics_output_parameters(out, data)
    animatable = {pid: values for pid, values in (driven or {}).items()
                  if pid not in physics_outputs}
    if physics_outputs and len(animatable) != len(driven or {}):
        skipped = sorted(set(driven or {}) - set(animatable))
        if skipped:
            log.info(f"physics 负责的参数不排 motion 曲线（避免双写）：{skipped}")

    builder = Motion3Builder()
    document = builder.build_idle(animatable, defaults=defaults)
    if document is None:
        # 没有任何可驱动参数：不产出 motion 文件，清单里的 Motions 保持空。
        # 给动不了的参数排曲线会得到「文件齐全但画面一动不动」的假产物。
        return []
    manifest = builder.export_to_directory(str(out), character_name, {"idle": document})

    data.setdefault("FileReferences", {})["Motions"] = manifest
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    files = [str(out / entry["File"])
             for entries in manifest.values() for entry in entries]
    if files:
        log.success(f"idle 动作已按权威可驱动集合生成："
                    f"{len((document.get('Curves') or []))} 条曲线 -> {len(files)} 个文件")
    return files


def _physics_output_parameters(output_dir: Path, manifest_doc: Dict[str, Any]) -> set:
    """清单引用的 physics3 里所有**输出**参数 id。

    读不到文件就返回空集：宁可多排几条曲线让验收去拦，也不要静默少排 ——
    少排的表现是「角色一动不动」，和当初 27 个参数只动 1 个是同一个病。
    """
    physics_file = ((manifest_doc.get("FileReferences") or {}).get("Physics") or "")
    if not physics_file:
        return set()
    try:
        physics = json.loads((output_dir / physics_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    outputs = set()
    for setting in physics.get("PhysicsSettings") or []:
        for out in setting.get("Output") or []:
            dest = out.get("Destination") or {}
            if dest.get("Target") == "Parameter" and dest.get("Id"):
                outputs.add(str(dest["Id"]))
    return outputs


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
        # 真正被绑定的参数 -> 键值序列（写进 model3.json 的 Capabilities）
        "driven_parameters": {},
        # 前端形变用的绑定表；None = 没写出来，预览只能静态展示
        "bindings_path": None,
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

    # 「本模型能驱动哪些参数」写进 model3.json，供受控像素验收逐项断言
    driven = driven_parameters(spec)
    result["driven_parameters"] = driven
    _attach_capabilities(output_dir, character_name, driven)

    # 前端要按参数摆顶点，就得不只看得到 moc3 —— 绑定表与 model3.json 同目录，
    # 由清单的 FileReferences.Bindings 指过去。写失败只降级预览（前端退回静态
    # 几何），不影响 moc3 部署，所以不拦验收，但必须留下路径或原因。
    from live2d_builder.exporter.bindings_export import write_bindings
    bindings_path = write_bindings(spec, output_dir, character_name)
    result["bindings_path"] = str(bindings_path) if bindings_path else None
    if bindings_path is None:
        log.warning("bindings.json 未写出：前端只能静态展示，参数拨了不会变形")

    # motion 必须在拿到权威集合之后生成，且在下面的 motion 验收之前落盘。
    result["motion_files"] = write_idle_motion(
        output_dir, character_name, driven,
        defaults={p.parameter_id: p.default for p in spec.parameters})

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
        # 前端形变数据是否可用：moc3 能部署不代表浏览器里能按参数变形
        "bindings": result.get("bindings_path"),
        "official_core_consistent": bool(
            (result.get("consistency") or {}).get("ok")),
    }
