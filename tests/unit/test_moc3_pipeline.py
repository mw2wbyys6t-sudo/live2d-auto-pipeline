"""桥接层：管线数据 -> RigSpec 的坐标/UV/参数映射。"""
import json

import pytest

from live2d_builder.exporter import moc3_pipeline
from live2d_builder.exporter.moc3_model import ParameterSpec
from live2d_builder.exporter.moc3_pipeline import (
    build_rig_spec,
    ensure_front_facing,
)


@pytest.fixture(autouse=True)
def _enable_mesh_keyforms(monkeypatch):
    """本文件的用例都是为「网格键形开着」时的行为写的。

    ``MESH_KEYFORMS_ENABLED`` 现在是默认开启的急停开关，这里仍显式打开，
    保证有人临时关掉闸门时这些用例不会静默退化成静态路径。
    """
    monkeypatch.setattr(moc3_pipeline, "MESH_KEYFORMS_ENABLED", True)


def test_mesh_keyforms_are_enabled_by_default():
    """默认必须开着：关掉等于所有导出产物都没有形变。"""
    assert moc3_pipeline.MESH_KEYFORMS_ENABLED is True


def _mesh(width=128.0, height=64.0):
    verts = [[0.0, 0.0], [width, 0.0], [width, height], [0.0, height]]
    norm = [[x / width, y / height] for x, y in verts]
    return {
        "vertices": verts,
        "vertices_norm": norm,
        "indices": [[0, 1, 2], [0, 2, 3]],
        "width": width,
        "height": height,
    }


def _builder_result(meshes, deformers=(), params=()):
    return {
        "meshes": meshes,
        "deformer_tree": {"deformers": list(deformers)},
        "parameters": {"cubism_params": list(params)},
    }


def _uv(u0=0.0, v0=0.0, u1=1.0, v1=1.0):
    return {"u0": u0, "v0": v0, "u1": u1, "v1": v1}


def test_positions_are_centered_and_y_points_down():
    """模型坐标原点在画布中心，**y 正方向向下**（与图像坐标同向）。

    实测依据：单色块探针在官方内核里 1:1 落在原位；写成 ``h/2 - y`` 会整层上下翻转
    （见 moc3_pipeline.to_model_xy）。
    """
    result = _builder_result({"layer_a": _mesh()})
    spec = build_rig_spec(result, {"layer_a": _uv()})
    mesh = spec.meshes[0]
    assert mesh.vertices[0] == [-64.0, -32.0]   # 左上 -> (左, -y)
    assert mesh.vertices[2] == [64.0, 32.0]     # 右下 -> (右, +y)


def test_uv_uses_atlas_rect_without_extra_vertical_flip():
    """图集 v 按图像坐标直接写入 —— 多翻一次会让模型一个像素都画不出来。

    实测依据（tools/probe_uv_variant.py）：带翻转的导出在官方运行时里不透明
    像素为 0；去掉翻转得到 13.5 万像素且布局正确。live2d-py 不在上传时翻转纹理。
    """
    result = _builder_result({"layer_a": _mesh()})
    spec = build_rig_spec(result, {"layer_a": _uv(u0=0.25, v0=0.5, u1=0.75, v1=1.0)})
    (u_top, v_top), (u_bottom, v_bottom) = (
        spec.meshes[0].uvs[0], spec.meshes[0].uvs[2])
    assert (u_top, v_top) == (0.25, 0.5)        # 图像上边 -> 图集 rect 的 v0
    assert (u_bottom, v_bottom) == (0.75, 1.0)  # 图像下边 -> v1，不反转


def test_triangles_are_front_facing_after_the_y_flip():
    """y 翻转会把三角形手性反过来，必须归一回 CCW，否则会被背面剔除丢掉。"""
    result = _builder_result({"layer_a": _mesh()})
    spec = build_rig_spec(result, {"layer_a": _uv()})
    mesh = spec.meshes[0]
    for a, b, c in mesh.triangles:
        (ax, ay), (bx, by), (cx, cy) = mesh.vertices[a], mesh.vertices[b], mesh.vertices[c]
        assert (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) > 0, \
            f"三角形 {(a, b, c)} 在 y 向上坐标里是 CW，会被当成背面剔除"


@pytest.mark.parametrize("triangles, expect", [
    ([[0, 1, 2]], [[0, 1, 2]]),          # 已经是 CCW -> 原样
    ([[0, 2, 1]], [[0, 1, 2]]),          # CW -> 翻成 CCW
    ([], []),                            # 空网格不动
])
def test_ensure_front_facing_is_direction_only(triangles, expect):
    verts = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]
    assert ensure_front_facing(verts, triangles) == expect


def test_draw_order_follows_layer_stack_order():
    result = _builder_result({"bottom": _mesh(), "top": _mesh()})
    spec = build_rig_spec(result, {"bottom": _uv(), "top": _uv()})
    assert [m.draw_order for m in spec.meshes] == [0.0, 1.0]


def test_parameters_come_from_cubism_params():
    params = [{"Id": "ParamAngleX", "Min": -30, "Max": 30, "Value": 0}]
    result = _builder_result({"a": _mesh()}, params=params)
    spec = build_rig_spec(result, {"a": _uv()})
    assert spec.parameters[0].parameter_id == "ParamAngleX"
    assert spec.parameters[0].minimum == -30.0
    assert spec.parameters[0].default == 0.0


def test_warp_deformer_becomes_a_lattice_over_its_members():
    """管线的 warp 变形器 -> 覆盖成员网格（含余量）的规则点阵，并挂上成员。"""
    result = _builder_result(
        {"a": _mesh()},
        deformers=[{"name": "HairSwing", "type": "warp", "targets": ["a"],
                    "grid_rows": 3, "grid_cols": 2}])
    spec = build_rig_spec(result, {"a": _uv()})
    assert [d.deformer_id for d in spec.deformers] == ["HairSwing"]
    deformer = spec.deformers[0]
    assert (deformer.rows, deformer.cols) == (2, 1)      # rows/cols 是格子数
    points = deformer.grids[0].points
    assert len(points) == deformer.vertex_count == 6
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    # 成员范围 x[-64,64] y[-32,32]，各留 5% 余量，键形把顶点推出网格时不会被裁剪
    assert (min(xs), max(xs)) == pytest.approx((-70.4, 70.4))
    assert (min(ys), max(ys)) == pytest.approx((-35.2, 35.2))
    assert spec.meshes[0].deformer_id == "HairSwing"
    assert spec.uncompiled_deformers == []


def test_rotation_deformer_becomes_a_static_pivot():
    """管线的 rotation 条目 -> 单键形规格，枢轴换算到模型坐标，并挂上成员网格。"""
    from live2d_builder.exporter.moc3_model import RotationDeformerSpec

    result = _builder_result(
        {"a": _mesh()},
        deformers=[{"name": "EyeTrack_L", "type": "rotation", "targets": ["a"],
                    "pivot": [40.0, 20.0], "angle": 0.0}])
    spec = build_rig_spec(result, {"a": _uv()})
    assert len(spec.deformers) == 1
    deformer = spec.deformers[0]
    assert isinstance(deformer, RotationDeformerSpec)
    assert deformer.deformer_id == "EyeTrack_L"
    assert spec.meshes[0].deformer_id == "EyeTrack_L"
    assert spec.uncompiled_deformers == []
    # pivot 是图像坐标（左上原点）；画布 128x64 -> 模型坐标 (40-64, 20-32)
    assert len(deformer.keyforms) == 1
    assert deformer.keyforms[0].origin == pytest.approx((-24.0, -12.0))
    # 只有一个键形 => 不绑参数，也确实不会随参数动，如实记为静态
    assert deformer.parameter_id == ""
    assert spec.static_deformers == ["EyeTrack_L"]


# 与 BoneHierarchy.get_bone_positions() 的默认比例模型一致（图像坐标，左上原点）
_STANDARD_BONE_POSITIONS = {"Head": (0.0, -40.0), "Body": (0.0, 50.0)}


@pytest.mark.parametrize("bone, pid", [
    ("Head", "ParamAngleZ"),
    ("Body", "ParamBodyAngleZ"),
])
def test_rotation_deformer_binds_official_z_rotation_when_pivot_is_that_bone(bone, pid):
    """枢轴恰为其转动骨骼时，rotation 绑**官方**参数并烘焙逐键键形 —— 真的会动。

    这是"结构存在但不动"变成"可用"的分界：绑定后它不再出现在
    ``static_deformers`` 里，内核会按参数值在键之间插值。
    """
    params = [{"Id": pid, "Min": -30, "Max": 30, "Value": 0}]
    px, py = _STANDARD_BONE_POSITIONS[bone]
    result = _builder_result(
        {"a": _mesh()},
        deformers=[{"name": f"{bone}Tilt", "type": "rotation", "targets": ["a"],
                    "pivot": [px, py], "angle": 0.0}],
        params=params)
    result["bone_positions"] = dict(_STANDARD_BONE_POSITIONS)

    spec = build_rig_spec(result, {"a": _uv()})
    deformer = spec.deformers[0]
    assert deformer.parameter_id == pid
    # 逐键键形：键取 min/default/max；角度 = 键值 × 1°/单位（与网格键形同一约定）
    assert [k.key_value for k in deformer.keyforms] == [-30.0, 0.0, 30.0]
    assert [k.angle for k in deformer.keyforms] == [-30.0, 0.0, 30.0]
    assert spec.static_deformers == []       # 真的会动，不再算静态


def test_rotation_deformer_stays_static_when_pivot_is_not_a_known_turning_bone():
    """枢轴对不上任何已知转动骨骼时保持静态 —— 宁可不驱动，也不猜一个参数。"""
    params = [{"Id": "ParamAngleZ", "Min": -30, "Max": 30, "Value": 0}]
    result = _builder_result(
        {"a": _mesh()},
        deformers=[{"name": "EyeTrack_L", "type": "rotation", "targets": ["a"],
                    "pivot": [40.0, 20.0], "angle": 0.0}],
        params=params)
    result["bone_positions"] = dict(_STANDARD_BONE_POSITIONS)

    spec = build_rig_spec(result, {"a": _uv()})
    assert spec.deformers[0].parameter_id == ""
    assert spec.static_deformers == ["EyeTrack_L"]


def test_rotation_deformer_binds_via_proximity_when_pivot_is_near_bone():
    """自动绑骨生成的枢轴常因取整/比例换算偏离骨骼几个像素 —— 就近匹配兜底绑上。

    画布 128x64 -> 就近容差 = max(128,64)*5% = 6.4px。枢轴 (1.5, -41.0) 距
    Head (0.0, -40.0) 约 1.8px，在容差内且唯一最近 -> 绑 ParamAngleZ。
    """
    params = [{"Id": "ParamAngleZ", "Min": -30, "Max": 30, "Value": 0}]
    result = _builder_result(
        {"a": _mesh()},
        deformers=[{"name": "HeadTilt", "type": "rotation", "targets": ["a"],
                    "pivot": [1.5, -41.0], "angle": 0.0}],
        params=params)
    result["bone_positions"] = dict(_STANDARD_BONE_POSITIONS)

    spec = build_rig_spec(result, {"a": _uv()})
    assert spec.deformers[0].parameter_id == "ParamAngleZ"
    assert spec.static_deformers == []


def test_rotation_deformer_stays_static_when_two_bones_equidistant():
    """就近匹配仍守「唯一命中」：两个骨骼等距时不猜，保持静态。"""
    params = [{"Id": "ParamAngleZ", "Min": -30, "Max": 30, "Value": 0},
              {"Id": "ParamBodyAngleZ", "Min": -30, "Max": 30, "Value": 0}]
    # Head 与 Body 关于枢轴 (0, 5) 各偏 3px（均在 6.4px 容差内且等距）
    bones = {"Head": (0.0, 2.0), "Body": (0.0, 8.0)}
    result = _builder_result(
        {"a": _mesh()},
        deformers=[{"name": "AmbiguousTilt", "type": "rotation",
                    "targets": ["a"], "pivot": [0.0, 5.0], "angle": 0.0}],
        params=params)
    result["bone_positions"] = bones

    spec = build_rig_spec(result, {"a": _uv()})
    assert spec.deformers[0].parameter_id == ""
    assert spec.static_deformers == ["AmbiguousTilt"]


_EYE_PARAMS = [{"Id": "ParamEyeBallX", "Min": -1, "Max": 1, "Value": 0},
               {"Id": "ParamEyeBallY", "Min": -1, "Max": 1, "Value": 0}]


def _skinned_mesh(bones):
    """把网格顶点绑到给定骨骼上（后两个顶点给 0.9 权重，确保峰值超过阈值）。"""
    mesh = _mesh()
    rows = []
    for i, _ in enumerate(mesh["vertices"]):
        last = 0.9 if i >= len(mesh["vertices"]) - 2 else 0.0
        rows.append([1.0] + [last for _ in bones])
    mesh["weights"] = {"bone_names": ["Head"] + list(bones), "weights": rows}
    return mesh


def test_eye_mesh_gets_two_axis_keyforms_on_official_eyeball_params():
    """瞳孔（绑在 Eyeball_L）由官方 ParamEyeBallX/Y 双轴驱动；轴序 X 在前、步长 1。

    展平序 index = i_X + 3*i_Y：所以 index 2 = (X 最右, Y 居中)、index 6 = (X 居中, Y 最上)。
    """
    result = _builder_result({"pupil": _skinned_mesh(["Eyeball_L"])},
                             params=_EYE_PARAMS)
    spec = build_rig_spec(result, {"pupil": _uv()})
    mesh = spec.meshes[0]
    assert mesh.keyform_axes == (
        ("ParamEyeBallX", (-1.0, 0.0, 1.0)),
        ("ParamEyeBallY", (-1.0, 0.0, 1.0)),
    )
    assert len(mesh.keyform_shapes) == 9          # 3 x 3
    base_x, base_y = mesh.vertices[0]
    assert mesh.keyform_shapes[2].vertices[0][0] > base_x    # (X=+1, Y=0) -> 右移
    assert mesh.keyform_shapes[6].vertices[0][1] > base_y    # (X=0, Y=+1) -> 上移
    assert mesh.keyform_shapes[0].vertices[0][0] < base_x    # (X=-1, Y=-1) -> 左下


def test_mesh_influenced_by_both_eyeballs_stays_undriven():
    """同时受左右眼球影响说明分不清左右眼 —— 宁可不生成，也不猜。"""
    result = _builder_result({"pupil": _skinned_mesh(["Eyeball_L", "Eyeball_R"])},
                             params=_EYE_PARAMS)
    spec = build_rig_spec(result, {"pupil": _uv()})
    assert spec.meshes[0].keyform_axes == ()
    assert not spec.meshes[0].keyform_shapes


# 与 BoneHierarchy.get_bone_positions() 默认比例模型一致（图像坐标，左上原点）
_ARM_HAIR_BONES = {
    "ArmBack_L": (-90.0, 80.0), "ForearmBack_L": (-130.0, 150.0),
    "ArmBack_R": (90.0, 80.0), "ForearmBack_R": (130.0, 150.0),
    "Hair_Front": (0.0, -90.0), "Hair_Top": (0.0, -120.0),
    "Hair_Side_L": (-60.0, -40.0), "Hair_Side_R": (60.0, -40.0),
    "Hair_Back": (0.0, -80.0),
}


@pytest.mark.parametrize("bone, pid", [
    ("ArmBack_L", "ParamArmLA"), ("ForearmBack_L", "ParamArmLB"),
    ("ArmBack_R", "ParamArmRA"), ("ForearmBack_R", "ParamArmRB"),
    ("Hair_Front", "ParamHairFront"), ("Hair_Top", "ParamHairFront"),
    ("Hair_Side_L", "ParamHairSide"), ("Hair_Side_R", "ParamHairSide"),
    ("Hair_Back", "ParamHairBack"),
])
def test_arm_and_hair_deformers_bind_official_params(bone, pid):
    """手臂/头发的 rotation 变形器按枢轴绑官方参数。

    注意 ``ParamHairSide`` 只有**一个**官方参数却服务左右两条侧发 —— 因此表项支持
    多候选枢轴，两个枢轴各自成为独立变形器、共享同一参数的 binding。
    """
    params = [{"Id": pid, "Min": -30, "Max": 30, "Value": 0}]
    px, py = _ARM_HAIR_BONES[bone]
    result = _builder_result(
        {"a": _mesh()},
        deformers=[{"name": f"{bone}_d", "type": "rotation", "targets": ["a"],
                    "pivot": [px, py], "angle": 0.0}],
        params=params)
    result["bone_positions"] = dict(_ARM_HAIR_BONES)

    spec = build_rig_spec(result, {"a": _uv()})
    deformer = spec.deformers[0]
    assert deformer.parameter_id == pid
    assert [k.key_value for k in deformer.keyforms] == [-30.0, 0.0, 30.0]
    assert spec.static_deformers == []


_HEAD_PARAMS = [{"Id": "ParamAngleX", "Min": -30, "Max": 30, "Value": 0},
                {"Id": "ParamAngleY", "Min": -30, "Max": 30, "Value": 0},
                {"Id": "ParamAngleZ", "Min": -30, "Max": 30, "Value": 0}]


def _head_mesh():
    mesh = _mesh()
    mesh["weights"] = {"bone_names": ["Head"], "weights": [[1.0]] * 4}
    return mesh


def test_head_mesh_gets_three_axis_composite_when_all_three_angles_declared():
    """头部网格：点头/转头透视压缩 与 Z 旋转合成三轴带；展平序 index = i_X + 3·i_Y + 9·i_Z。"""
    result = _builder_result({"head": _head_mesh()}, params=_HEAD_PARAMS)
    # 枢轴取 Head 关节位置（未知则不生成三轴带），所以这里必须给出它
    result["bone_positions"] = {"Head": (0.0, -40.0)}
    spec = build_rig_spec(result, {"head": _uv()})
    mesh = spec.meshes[0]
    assert [pid for pid, _ in mesh.keyform_axes] == [
        "ParamAngleX", "ParamAngleY", "ParamAngleZ"]
    assert [len(keys) for _, keys in mesh.keyform_axes] == [3, 3, 3]
    assert len(mesh.keyform_shapes) == 27          # 3 × 3 × 3
    base = mesh.vertices[0]
    # 枢轴 = Head 关节 (0,-40) 图像坐标 -> 模型坐标 (-64, -72)（y 向下，图像上方是负 y）
    # (i_X=2, i_Y=1, i_Z=1) -> 14：只有 X 到极值（点头）=> 纯垂直压缩，不水平动
    v14 = mesh.keyform_shapes[14].vertices[0]
    assert v14[1] < base[1]                        # 向枢轴（图像上方，更小的 y）收拢
    assert v14[0] == pytest.approx(base[0])        # 点头不产生水平位移（交叉映射）
    # (i_X=1, i_Y=2, i_Z=1) -> 16：只有 Y 到极值（转头）=> 水平位移 + 水平压扁
    v16 = mesh.keyform_shapes[16].vertices
    assert v16[0][0] > base[0]                     # 转头产生水平弧位移
    assert v16[0][1] == pytest.approx(base[1])     # 转头不产生垂直位移（交叉映射）
    assert v16[1][0] < mesh.vertices[1][0]         # 远侧（右侧）被压扁
    # 非仿射：同一排（y 相同）的两个顶点转头后 y 不再相同 —— 透视随 x 连续变化
    assert abs(v16[1][1] - v16[0][1]) > 1.0
    # 默认键（i_X=i_Y=i_Z=1）必须逐顶点复现静止姿态
    for shape_v, rest_v in zip(mesh.keyform_shapes[13].vertices, mesh.vertices):
        assert shape_v == pytest.approx(rest_v)


def test_head_mesh_falls_back_to_single_axis_z_unless_all_three_are_declared():
    """只声明 ParamAngleZ 时**不**升级为三轴 —— 保证既有导出的产物不变。"""
    result = _builder_result(
        {"head": _head_mesh()},
        params=[{"Id": "ParamAngleZ", "Min": -30, "Max": 30, "Value": 0}])
    # Z 的单轴网格路径也需要枢轴；只给 Head，X/Y 未声明时不会命中，故只有 1 个参数。
    result["bone_positions"] = {"Head": (0.0, -40.0)}
    spec = build_rig_spec(result, {"head": _uv()})
    mesh = spec.meshes[0]
    assert mesh.keyform_axes == ()
    assert mesh.keyform_parameter_id == "ParamAngleZ"
    assert len(mesh.keyform_shapes) == 3          # 退回单轴 Z


def test_mesh_weighted_to_a_head_child_bone_also_follows_the_head():
    """只绑 Face（Head 的后代）的网格也要跟着头动 —— 覆盖子骨骼，不只 Head 本身。"""
    result = _builder_result({"face": _skinned_mesh(["Face"])},
                             params=_HEAD_PARAMS)
    result["bone_positions"] = {"Head": (0.0, -40.0)}
    spec = build_rig_spec(result, {"face": _uv()})
    mesh = spec.meshes[0]
    assert [pid for pid, _ in mesh.keyform_axes] == list(
        ("ParamAngleX", "ParamAngleY", "ParamAngleZ"))
    assert len(mesh.keyform_shapes) == 27


def test_head_parts_share_one_transform_scale():
    """脸部与五官必须用同一套头部形变参数，否则转头时五官相对脸滑动。

    尺度取网格**自己的**包围盒时：10px 的嘴 shift 0.4px、透视深度 40px，245px 的
    脸 shift 9.8px、深度 980px —— 同一个空间点按两套参数算两遍。实测 singer 样例
    在 ParamAngleY=±30° 时嘴相对脸滑 7px，而整张嘴只有 10px 宽。这里让鼻子的一角
    与脸的一角重合，逐个键形比对：同一点必须落到同一处。
    """
    def quad(x0, y0, x1, y1):
        verts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        return {
            "vertices": verts,
            "vertices_norm": [[x / 400.0, y / 400.0] for x, y in verts],
            "indices": [[0, 1, 2], [0, 2, 3]],
            "width": 400.0, "height": 400.0,
            "weights": {"bone_names": ["Head"], "weights": [[1.0]] * 4},
        }

    face = quad(0.0, 0.0, 300.0, 250.0)
    nose = quad(290.0, 240.0, 300.0, 250.0)   # 2 号顶点 (300,250) 与脸的 2 号重合
    result = _builder_result({"face": face, "nose": nose}, params=_HEAD_PARAMS)
    result["bone_positions"] = {"Head": (0.0, -40.0)}
    spec = build_rig_spec(result, {"face": _uv(), "nose": _uv()})
    by_id = {m.mesh_id: m for m in spec.meshes}

    face_spans = {i: by_id["face"].keyform_shapes[i].vertices[2] for i in range(27)}
    for i, want in face_spans.items():
        got = by_id["nose"].keyform_shapes[i].vertices[2]
        assert got == pytest.approx(want), f"keyform {i} 上鼻子与脸的同一点分开了"


def test_mesh_weighted_below_the_head_is_not_dragged_by_the_head():
    """Neck 属 ``body`` 组（躯干）—— 只绑它的网格不该被头部总成拖动。

    不能用 ``_skinned_mesh``：那个辅助函数总会把 ``Head`` 以权重 1.0 一起塞进
    ``bone_names``，构造出来的其实是头部加权网格（本用例第一版就栽在这上面）。
    """
    mesh = _mesh()
    mesh["weights"] = {"bone_names": ["Neck"], "weights": [[1.0]] * 4}
    result = _builder_result({"neck": mesh}, params=_HEAD_PARAMS)
    result["bone_positions"] = {"Head": (0.0, -40.0)}
    spec = build_rig_spec(result, {"neck": _uv()})
    assert spec.meshes[0].keyform_axes == ()


_CHEST_XY = (0.0, 100.0)


def test_breath_warp_gets_two_keyforms_lifting_the_chest():
    """枢轴 == Chest 的 warp 由 ParamBreath 驱动：2 个控制网格，第二个整体上抬。

    呼吸在 Live2D 里是缩放 + 垂直位移的复合，所以走 warp 而不是刚体旋转。
    """
    params = [{"Id": "ParamBreath", "Min": 0, "Max": 1, "Value": 0}]
    result = _builder_result(
        {"a": _mesh()},
        deformers=[{"name": "Breath", "type": "warp", "targets": ["a"],
                    "grid_rows": 2, "grid_cols": 2, "pivot": list(_CHEST_XY)}],
        params=params)
    result["bone_positions"] = {"Chest": _CHEST_XY}

    spec = build_rig_spec(result, {"a": _uv()})
    deformer = spec.deformers[0]
    assert deformer.parameter_id == "ParamBreath"
    assert [g.key_value for g in deformer.grids] == [0.0, 1.0]
    rest_y = [p[1] for p in deformer.grids[0].points]
    lifted_y = [p[1] for p in deformer.grids[1].points]
    assert len(lifted_y) == len(rest_y)
    assert min(lifted_y) > min(rest_y)          # 吸气键整体上抬
    assert all(b > a for a, b in zip(sorted(rest_y), sorted(lifted_y)))


def test_warp_without_chest_pivot_stays_a_single_static_grid():
    """枢轴对不上胸腔（或没有枢轴）时保持单网格静态 —— 宁可不驱动，也不猜。"""
    result = _builder_result(
        {"a": _mesh()},
        deformers=[{"name": "HairSwing", "type": "warp", "targets": ["a"],
                    "grid_rows": 2, "grid_cols": 2, "pivot": [0.0, 0.0]}],
        params=[{"Id": "ParamBreath", "Min": 0, "Max": 1, "Value": 0}])
    result["bone_positions"] = {"Chest": _CHEST_XY}
    spec = build_rig_spec(result, {"a": _uv()})
    assert spec.deformers[0].parameter_id == ""
    assert len(spec.deformers[0].grids) == 1


def test_uncompilable_deformers_are_reported_not_dropped_silently():
    """挂不上成员网格的变形器必须写明原因，而不是被静默丢掉。"""
    result = _builder_result(
        {"a": _mesh()},
        deformers=[{"name": "Orphan", "type": "warp", "targets": ["nope"]},
                   {"name": "Weird", "type": "glue", "targets": ["a"]}])
    spec = build_rig_spec(result, {"a": _uv()})
    assert spec.deformers == []
    assert spec.meshes[0].deformer_id == ""
    assert spec.uncompiled_deformers == [
        "Orphan(没有可挂的成员网格)", "Weird(type=glue)"]


def test_missing_atlas_placement_is_rejected():
    result = _builder_result({"a": _mesh()})
    with pytest.raises(Exception, match="UV 摆放"):
        build_rig_spec(result, {})


def test_mismatched_extents_are_rejected():
    result = _builder_result({"a": _mesh(), "b": _mesh(width=64.0)})
    with pytest.raises(Exception, match="尺寸不一致"):
        build_rig_spec(result, {"a": _uv(), "b": _uv()})


def test_empty_meshes_are_rejected():
    with pytest.raises(Exception, match="至少需要一个网格"):
        build_rig_spec(_builder_result({}), {})


def _rotated_mesh(bone="Head", weight=1.0, width=128.0, height=128.0):
    verts = [[32.0, 32.0], [96.0, 32.0], [96.0, 96.0], [32.0, 96.0]]
    return {
        "vertices": verts,
        "vertices_norm": [[x / width, y / height] for x, y in verts],
        "indices": [[0, 1, 2], [0, 2, 3]],
        "width": width, "height": height,
        "weights": {"bone_names": [bone], "weights": [[weight]] * len(verts)},
    }


def test_rotation_keyforms_bake_rigid_z_rotation():
    from live2d_builder.exporter.moc3_pipeline import rotation_keyforms
    from live2d_builder.bones.deformers import BoneHierarchy

    params = {"ParamAngleZ": ParameterSpec("ParamAngleZ", -30.0, 30.0, 0.0)}
    pid, shapes, candidates = rotation_keyforms(
        _rotated_mesh("Head"), params, {"Head": (64.0, 30.0)},
        BoneHierarchy.STANDARD_BONES, 128.0, 128.0)
    assert pid == "ParamAngleZ" and candidates == ["ParamAngleZ"]
    # min / default / max 三键，且 default 键必须完全复现静止姿态
    assert [s.key_value for s in shapes] == [-30.0, 0.0, 30.0]
    rest = shapes[1].vertices
    expected = [v for x, y in _rotated_mesh()["vertices"] for v in (x - 64.0, y - 64.0)]
    flat_rest = [v for point in rest for v in point]
    assert flat_rest == pytest.approx(expected, abs=1e-9)
    # 端点键形必须真的离开静止姿态，且绕枢轴做刚体旋转（距离枢轴保持不变）
    moved = shapes[2].vertices
    assert any(max(abs(a[0] - b[0]), abs(a[1] - b[1])) > 1.0
               for a, b in zip(moved, rest))
    pivot = (64.0 - 64.0, 30.0 - 64.0)
    for (rx, ry), (mx, my) in zip(rest, moved):
        assert (rx - pivot[0]) ** 2 + (ry - pivot[1]) ** 2 == pytest.approx(
            (mx - pivot[0]) ** 2 + (my - pivot[1]) ** 2, abs=1e-6)


def test_rotation_keyforms_need_weights_and_skip_multi_hit():
    from live2d_builder.exporter.moc3_pipeline import rotation_keyforms
    from live2d_builder.bones.deformers import BoneHierarchy

    params = {"ParamAngleZ": ParameterSpec("ParamAngleZ", -30.0, 30.0, 0.0),
              "ParamBodyAngleZ": ParameterSpec("ParamBodyAngleZ", -10.0, 10.0, 0.0)}
    mesh = _rotated_mesh("Head")
    # 没有权重 -> 静态
    plain = {k: v for k, v in mesh.items() if k != "weights"}
    assert rotation_keyforms(plain, params, {"Head": (64.0, 30.0)},
                             BoneHierarchy.STANDARD_BONES, 128.0, 128.0) == ("", [], [])
    # 同时受头与身体旋转影响 -> 多参数带，轴序未核实，不生成
    two = dict(mesh)
    two["weights"] = {"bone_names": ["Head", "Body"],
                      "weights": [[0.6, 0.4]] * 4}
    pid, shapes, candidates = rotation_keyforms(
        two, params, {"Head": (64.0, 30.0), "Body": (64.0, 100.0)},
        BoneHierarchy.STANDARD_BONES, 128.0, 128.0)
    assert pid == "" and shapes == []
    assert sorted(candidates) == ["ParamAngleZ", "ParamBodyAngleZ"]


def test_build_rig_spec_attaches_keyforms_from_pipeline_data():
    result = _builder_result({"hair": _rotated_mesh("Hair_Front")})
    result["bone_positions"] = {"Head": (64.0, 30.0), "Body": (64.0, 100.0)}
    result["parameters"] = {"cubism_params": [
        {"Id": "ParamAngleZ", "Min": -30, "Max": 30, "Value": 0}]}
    spec = build_rig_spec(result, {"hair": _uv()})
    mesh = spec.meshes[0]
    assert mesh.keyform_parameter_id == "ParamAngleZ"
    assert [s.key_value for s in mesh.keyform_shapes] == [-30.0, 0.0, 30.0]


def _write_manifest(tmp_path, physics_file="char.physics3.json"):
    manifest = tmp_path / "char.model3.json"
    manifest.write_text(json.dumps({
        "FileReferences": {"Physics": physics_file},
    }), encoding="utf-8")
    return manifest


def _motion_curve_ids(tmp_path, manifest):
    document = json.loads(manifest.read_text(encoding="utf-8"))
    ids = set()
    for entries in (document.get("FileReferences") or {}).get("Motions", {}).values():
        for entry in entries:
            curve_doc = json.loads(
                (tmp_path / entry["File"]).read_text(encoding="utf-8"))
            for curve in curve_doc.get("Curves") or []:
                if curve.get("Target") == "Parameter":
                    ids.add(str(curve.get("Id")))
    return ids


def test_idle_motion_skips_parameters_owned_by_physics(tmp_path):
    """physics3 的输出参数不排 motion 曲线 —— 否则内核逐帧对账会判失败。

    实测（2026-10-31 本机 singer 包）：27 条曲线全排上之后，官方内核报
    ``逐帧超限的曲线 ['ParamBodySway', 'ParamHairSwing']``。机制是 motion 先写值、
    physics 再覆写，观测值对不上文件值。这两个参数本来就该由物理摆动驱动。
    """
    manifest = _write_manifest(tmp_path)
    (tmp_path / "char.physics3.json").write_text(json.dumps({
        "PhysicsSettings": [{
            "Output": [
                {"Destination": {"Target": "Parameter", "Id": "ParamHairSwing"}},
                # Target != Parameter 的行不是参数输出，不该被当成排除项
                {"Destination": {"Target": "Model", "Id": "ParamAngleX"}},
            ],
        }],
    }), encoding="utf-8")

    driven = {"ParamAngleX": [-30.0, 0.0, 30.0],
              "ParamHairSwing": [-1.0, 0.0, 1.0]}
    moc3_pipeline.write_idle_motion(str(tmp_path), "char", driven)

    animated = _motion_curve_ids(tmp_path, manifest)
    assert animated == {"ParamAngleX"}, (
        f"physics 输出的 ParamHairSwing 不该排曲线，实际排了 {sorted(animated)}")


def test_idle_motion_covers_all_when_physics_is_unreadable(tmp_path):
    """读不到 physics3 时宁可多排：少排的表现是角色一动不动，且没有报错。"""
    manifest = _write_manifest(tmp_path, physics_file="missing.physics3.json")

    driven = {"ParamAngleX": [-30.0, 0.0, 30.0],
              "ParamHairSwing": [-1.0, 0.0, 1.0]}
    moc3_pipeline.write_idle_motion(str(tmp_path), "char", driven)

    assert _motion_curve_ids(tmp_path, manifest) == set(driven)


# ======================================================================
# Task 3：HeadRig 闭包里的网格不再自己拟合头部三轴
#
# 挂上 HeadRig 却留着 `head_angle_keyforms` 的烘焙 = 转头**施加两次**，中间提交
# 是一个比现状更差的画面（计划把撤烘焙放在 Task 5，本任务必须与挂 HeadRig 原子落地）。
# 反过来，撤了烘焙而 HeadRig 其实没建成 / 头部信息不全，头就彻底不动。
# 两组方向都要有用例钉住，任何一边单独改掉都会红。
# ======================================================================

_BODY_Z = {"Id": "ParamBodyAngleZ", "Min": -10, "Max": 10, "Value": 0}
_CHEEK = {"Id": "ParamCheek", "Min": 0, "Max": 1, "Value": 0}
_EYE_OPEN_L = {"Id": "ParamEyeLOpen", "Min": 0, "Max": 1, "Value": 1}
_HEAD_BONE_POSITIONS = {"Head": (0.0, -40.0), "Body": (0.0, 100.0)}


def _layer_mesh(bones, weight=1.0):
    """一张 128x64 的层网格，绑到给定骨骼上（与 ``_mesh()`` 同一副顶点）。"""
    mesh = _mesh()
    mesh["weights"] = {"bone_names": list(bones),
                       "weights": [[weight]] * len(mesh["vertices"])}
    return mesh


def _head_rig_entry(targets, under=""):
    entry = {"name": "HeadRig", "type": "warp", "parent": "Head",
             "grid_rows": 4, "grid_cols": 3, "profile": "head",
             "parameters": ["ParamAngleX", "ParamAngleY", "ParamAngleZ"],
             "targets": list(targets)}
    if under:
        entry["under"] = under
    return entry


def _result(meshes, deformers=(), params=()):
    result = _builder_result(meshes, deformers=deformers, params=params)
    result["bone_positions"] = dict(_HEAD_BONE_POSITIONS)
    return result


def test_resolve_membership_is_declaration_order_first_come():
    """一个网格只能挂一个变形器：归属按**声明顺序先到先得**，与 ``_resolve_warp_members`` 同规则。

    烘焙守卫在**烘焙前**就要知道归属（装配在烘焙之后才改写 ``mesh.deformer_id``），
    两处规则一旦不同就会出现「以为被主干接住、其实没有」的头颈脱节。
    """
    from live2d_builder.exporter.moc3_pipeline import resolve_membership
    entries = [
        {"name": "HeadRig", "type": "warp", "targets": ["face", "nose"]},
        {"name": "Other", "type": "warp", "targets": ["face"]},
    ]
    assert resolve_membership(entries, ["face", "nose", "orphan"]) == {
        "face": "HeadRig", "nose": "HeadRig"}


def test_head_rig_members_do_not_bake_their_own_head_rotation():
    """挂进 HeadRig 的 face：头部三轴只存在于变形器那一条带上，网格带里不再有一份。"""
    result = _result({"face": _layer_mesh(["Face"])},
                     deformers=[_head_rig_entry(["face"])],
                     params=_HEAD_PARAMS)
    spec = build_rig_spec(result, {"face": _uv()})
    mesh = spec.meshes[0]
    assert mesh.deformer_id == "HeadRig"
    assert not any(pid.startswith("ParamAngle")
                   for pid, _keys in (mesh.keyform_axes or ())), mesh.keyform_axes
    head = next(d for d in spec.deformers if d.deformer_id == "HeadRig")
    assert [pid for pid, _k in head.axes_spec] == list(moc3_pipeline.HEAD_ANGLE_AXES)


def test_covered_mesh_never_falls_back_to_a_single_axis_z_band():
    """撤掉头带以后也不许退化成单轴 Z 网格带 —— 那同样是**第二次** Z 旋转。

    ``rotation_keyforms`` 走的正是「没有头部三轴带 → 单轴 Z 逐顶点键形」这条降级路
    （见 ``test_head_mesh_falls_back_to_single_axis_z_unless_all_three_are_declared``）。
    挂在 HeadRig 之下再叠一条 Z 带，就是双份侧转。
    """
    result = _result({"face": _layer_mesh(["Face"])},
                     deformers=[_head_rig_entry(["face"])],
                     params=_HEAD_PARAMS)          # 没有 ParamBodyAngleZ
    spec = build_rig_spec(result, {"face": _uv()})
    mesh = spec.meshes[0]
    assert mesh.deformer_id == "HeadRig"
    assert (mesh.keyform_axes or ()) == () and not mesh.keyform_shapes
    assert mesh.keyform_parameter_id == ""


def test_covered_head_mesh_still_follows_the_body_lean():
    """撤掉头带以后，``ParamBodyAngleZ`` 的跟随必须留在网格这一层。

    BodySway 那条带里**没有** ``ParamBodyAngleZ``（今天 12 个躯干网格的刚体拟合：
    θ=10.000°、中心 (455.2,1038.8)），HeadRig 继承不到它。撤掉烘焙时若把身体跟随
    一并撤掉，头在侧倾时就不再跟身子 —— 那正是 ``tests/unit/test_head_follows_body.py``
    记过的「躯干 10° 时领口离脸 57px」。撤 BodySway 成员那份烘焙是 Task 4/5 的事。
    """
    result = _result({"face": _layer_mesh(["Face"])},
                     deformers=[_head_rig_entry(["face"], under="BodySway"),
                                {"name": "BodySway", "type": "warp",
                                 "grid_rows": 3, "grid_cols": 2,
                                 "profile": "torso",
                                 "parameters": ["ParamBodyAngleX"],
                                 "targets": ["torso"]}],
                     params=_HEAD_PARAMS + [_BODY_Z])
    spec = build_rig_spec(result, {"face": _uv()})
    mesh = next(m for m in spec.meshes if m.mesh_id == "face")
    assert [pid for pid, _k in mesh.keyform_axes] == ["ParamBodyAngleZ"], (
        mesh.keyform_axes)
    # 默认键必须逐顶点复现静止姿态（画面不变的构造保证）
    keys = list(mesh.keyform_axes[0][1])
    rest_index = keys.index(0.0)
    for got, want in zip(mesh.keyform_shapes[rest_index].vertices, mesh.vertices):
        assert got[0] == pytest.approx(want[0], abs=1e-9)
        assert got[1] == pytest.approx(want[1], abs=1e-9)


def test_covered_eye_mesh_keeps_the_eyeball_axes_and_loses_the_head_axes():
    """眼球跟踪是**局部表情**，必须留着；头部三轴改由 HeadRig 送到。"""
    result = _result(
        {"eyewhite_left": _skinned_mesh(["Eyeball_L"]),
         "face": _layer_mesh(["Face"])},
        deformers=[_head_rig_entry(["face"]),
                   {"name": "EyeBlink_L", "type": "warp", "parent": "Eye_L",
                    "grid_rows": 4, "grid_cols": 4, "profile": "eye",
                    "parameters": ["ParamEyeLOpen"],
                    "targets": ["eyewhite_left"], "under": "HeadRig"}],
        params=_EYE_PARAMS + _HEAD_PARAMS + [_EYE_OPEN_L])
    spec = build_rig_spec(result, {"eyewhite_left": _uv(), "face": _uv()})
    mesh = next(m for m in spec.meshes if m.mesh_id == "eyewhite_left")
    assert mesh.deformer_id == "EyeBlink_L", (
        "先声明的 HeadRig 不许把眼球层抢走：抢走就等于把眨眼一起丢掉")
    assert [pid for pid, _k in mesh.keyform_axes] == [
        "ParamEyeBallX", "ParamEyeBallY"], mesh.keyform_axes
    by_id = {d.deformer_id: d for d in spec.deformers}
    assert [pid for pid, _k in by_id["EyeBlink_L"].axes_spec] == ["ParamEyeLOpen"]
    assert [pid for pid, _k in by_id["HeadRig"].axes_spec] == list(
        moc3_pipeline.HEAD_ANGLE_AXES)
    assert by_id["EyeBlink_L"].parent_deformer_id == "HeadRig"


def test_covered_fade_layer_keeps_its_opacity_band_only():
    """腮红：不透明度带照旧留在网格上，头转由 HeadRig 送到（不再各拟合一份）。"""
    result = _result({"cheek": _layer_mesh(["Face"])},
                     deformers=[_head_rig_entry(["cheek"])],
                     params=_HEAD_PARAMS + [_CHEEK, _BODY_Z])
    spec = build_rig_spec(result, {"cheek": _uv()})
    mesh = spec.meshes[0]
    assert mesh.deformer_id == "HeadRig"
    assert [pid for pid, _k in mesh.keyform_axes] == [
        "ParamCheek", "ParamBodyAngleZ"], mesh.keyform_axes


def test_the_authored_table_does_not_double_apply_the_head_turn():
    """用**真实作者表**跑一遍：face / cheek / 眼皮 都归 HeadRig，且都不带角度带。

    手搓夹具测的是机制，这条测的是作者表与机制的接线：HeadRig 一旦写进
    ``DEFAULT_WARP_DEFORMERS`` 而守卫漏了某个入口，头转就会施加两次。
    """
    from live2d_builder.bones.deformers import DeformerHierarchy
    names = ["face", "cheek", "tear_left", "eyelid_left", "eyewhite_left",
             "irides_left", "eyebrow_L", "nose", "mouth", "mouth_cavity",
             "hair_front", "hair_back", "neck", "clothes_top", "accessories"]
    meshes = {}
    for n in names:
        bones = ["Neck"] if n in ("neck", "clothes_top") else (
            ["Eyeball_L"] if n in ("eyewhite_left", "irides_left") else ["Face"])
        meshes[n] = _layer_mesh(bones)
    built = DeformerHierarchy().build(
        {}, {n: {"vertices": m["vertices"]} for n, m in meshes.items()},
        layer_names=names)
    result = _builder_result(
        meshes, deformers=built["deformers"],
        params=_HEAD_PARAMS + [_BODY_Z, _CHEEK, _EYE_OPEN_L] + _EYE_PARAMS)
    result["bone_positions"] = dict(_HEAD_BONE_POSITIONS)
    spec = build_rig_spec(result, {n: _uv() for n in names})
    by_id = {m.mesh_id: m for m in spec.meshes}
    owner = {d.deformer_id: d for d in spec.deformers}

    assert by_id["face"].deformer_id == "HeadRig"
    assert by_id["cheek"].deformer_id == "HeadRig"
    assert by_id["eyelid_left"].deformer_id == "HeadRig"
    assert by_id["neck"].deformer_id == "BodySway"
    for n in ("face", "cheek", "tear_left", "eyelid_left", "eyebrow_L", "nose",
              "mouth", "mouth_cavity", "hair_front", "hair_back", "accessories"):
        axes = [pid for pid, _k in (by_id[n].keyform_axes or ())]
        assert not [p for p in axes if p.startswith("ParamAngle")], (n, axes)
    # 头部三轴在**产物**里也只有一个持有者
    carriers = [d.deformer_id for d in spec.deformers
                if any(pid in moc3_pipeline.HEAD_ANGLE_AXES
                       for pid, _k in d.axes_spec)]
    assert carriers == ["HeadRig"], carriers
    assert owner["HeadRig"].parent_deformer_id == "BodySway"


def test_meshes_outside_the_head_closure_keep_their_baked_head_band():
    """没进 HeadRig 闭包（``deformers=()`` 或不挂 HeadRig）时烘焙照旧 —— 守卫没扩大化。"""
    result = _result({"face": _layer_mesh(["Face"])},
                     deformers=[{"name": "SomethingElse", "type": "warp",
                                 "grid_rows": 2, "grid_cols": 2,
                                 "targets": ["face"]}],
                     params=_HEAD_PARAMS)
    spec = build_rig_spec(result, {"face": _uv()})
    mesh = spec.meshes[0]
    assert [pid for pid, _k in mesh.keyform_axes] == list(
        moc3_pipeline.HEAD_ANGLE_AXES)

    plain = _result({"face": _layer_mesh(["Face"])}, params=_HEAD_PARAMS)
    spec = build_rig_spec(plain, {"face": _uv()})
    assert [pid for pid, _k in spec.meshes[0].keyform_axes] == list(
        moc3_pipeline.HEAD_ANGLE_AXES)


def test_head_information_missing_keeps_the_baked_band():
    """Head 关节 / head_span 算不出来时不许「先撤烘焙再指望变形器」。

    ``_head_options`` 返回 None ⇒ HeadRig 的 ``head`` profile 编成**静态**点阵
    （``build_deformers`` 会记一条 ``HeadRig(头部信息不全…)``）。此时若照撤网格自己的
    带，头就一动不动 —— 比双份转动更难查，所以守卫要让位给烘焙。
    """
    result = _builder_result({"face": _layer_mesh(["Face"])},
                             deformers=[_head_rig_entry(["face"])],
                             params=_HEAD_PARAMS)
    result["bone_positions"] = {"Body": (0.0, 100.0)}   # 没有 Head 关节
    spec = build_rig_spec(result, {"face": _uv()})
    mesh = spec.meshes[0]
    assert mesh.deformer_id == "HeadRig"
    # 同一次导出里没有任何东西替它转：带必须留在**原样**的位置（这里即今天的空带），
    # 判据用「与不挂变形器时逐轴相同」写，不去猜某个具体键形是否存在。
    plain = _builder_result({"face": _layer_mesh(["Face"])}, params=_HEAD_PARAMS)
    plain["bone_positions"] = {"Body": (0.0, 100.0)}
    reference = build_rig_spec(plain, {"face": _uv()}).meshes[0]
    assert [pid for pid, _k in (mesh.keyform_axes or ())] == [
        pid for pid, _k in (reference.keyform_axes or ())]


def test_a_head_rig_without_the_head_profile_does_not_strip_the_bands():
    """HeadRig 挂名却没有 ``profile: "head"`` 时它只是静态点阵 —— 守卫必须让位。

    归属解析只看**名字**，形变方式写在 ``profile`` 里。名字接住了、profile 没了，
    照声明撤烘焙的结果是「网格没有自己的带 + 父级一动不动」= 头彻底冻结。
    """
    entry = _head_rig_entry(["face"])
    entry["profile"] = ""
    result = _result({"face": _layer_mesh(["Face"])}, deformers=[entry],
                     params=_HEAD_PARAMS)
    spec = build_rig_spec(result, {"face": _uv()})
    mesh = spec.meshes[0]
    assert mesh.deformer_id == "HeadRig"
    assert [pid for pid, _k in mesh.keyform_axes] == list(
        moc3_pipeline.HEAD_ANGLE_AXES)


def test_a_head_rig_that_never_lands_does_not_strip_its_children():
    """HeadRig 声明了但一个成员网格都没挂上时不编译 —— 此时子级的烘焙必须保留。

    归属解析只看**声明**，编译只看**成员**；两者对「HeadRig 到底存不存在」给出不同
    答案时，如果照声明撤烘焙，子级（嘴/眉/眼）就会既没有自己的带、也继承不到父级。
    """
    from live2d_builder.bones.deformers import DeformerHierarchy
    names = ["mouth", "eyebrow_L"]
    meshes = {n: _layer_mesh(["Face"]) for n in names}
    built = DeformerHierarchy().build(
        {}, {n: {"vertices": m["vertices"]} for n, m in meshes.items()},
        layer_names=names)
    result = _builder_result(meshes, deformers=built["deformers"],
                             params=_HEAD_PARAMS)
    result["bone_positions"] = dict(_HEAD_BONE_POSITIONS)
    spec = build_rig_spec(result, {n: _uv() for n in names})
    assert "HeadRig" not in [d.deformer_id for d in spec.deformers]
    for mesh in spec.meshes:
        assert [pid for pid, _k in mesh.keyform_axes] == list(
            moc3_pipeline.HEAD_ANGLE_AXES), mesh.mesh_id
