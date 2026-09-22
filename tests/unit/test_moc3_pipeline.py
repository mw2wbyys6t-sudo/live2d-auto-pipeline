"""桥接层：管线数据 -> RigSpec 的坐标/UV/参数映射。"""
import pytest

from live2d_builder.exporter.moc3_model import ParameterSpec
from live2d_builder.exporter.moc3_pipeline import (
    build_rig_spec,
    ensure_front_facing,
)


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


def test_positions_are_centered_and_y_flipped():
    result = _builder_result({"layer_a": _mesh()})
    spec = build_rig_spec(result, {"layer_a": _uv()})
    mesh = spec.meshes[0]
    assert mesh.vertices[0] == [-64.0, 32.0]    # 左上 -> (左, 上)
    assert mesh.vertices[2] == [64.0, -32.0]    # 右下 -> (右, 下)


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
    # pivot 是图像坐标（左上原点）；画布 128x64 -> 模型坐标 (40-64, 32-20)
    assert len(deformer.keyforms) == 1
    assert deformer.keyforms[0].origin == pytest.approx((-24.0, 12.0))
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
    expected = [v for x, y in _rotated_mesh()["vertices"] for v in (x - 64.0, 64.0 - y)]
    flat_rest = [v for point in rest for v in point]
    assert flat_rest == pytest.approx(expected, abs=1e-9)
    # 端点键形必须真的离开静止姿态，且绕枢轴做刚体旋转（距离枢轴保持不变）
    moved = shapes[2].vertices
    assert any(max(abs(a[0] - b[0]), abs(a[1] - b[1])) > 1.0
               for a, b in zip(moved, rest))
    pivot = (64.0 - 64.0, 64.0 - 30.0)
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
