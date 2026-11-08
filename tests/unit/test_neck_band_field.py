"""颈胸过渡必须在同一个 warp 点阵里连续衰减。

验收姿态是 ParamAngleZ=30 + ParamBodyAngleZ=10：同属 BodySway 的
neck↔clothes_top 与 clothes_top↔collar 本来就刚性，NeckBand 要收的是跨场缝。
"""
import math

import pytest

from live2d_builder.exporter import moc3_pipeline as mp
from live2d_builder.exporter.moc3_model import MeshSpec, ParameterSpec


def test_weight_is_one_at_boundary_and_zero_at_band_bottom():
    assert mp.neck_band_weight(10.0, boundary_y=10.0, band_bottom_y=50.0) == 1.0
    assert mp.neck_band_weight(50.0, boundary_y=10.0, band_bottom_y=50.0) == 0.0
    assert mp.neck_band_weight(0.0, boundary_y=10.0, band_bottom_y=50.0) == 1.0
    assert mp.neck_band_weight(60.0, boundary_y=10.0, band_bottom_y=50.0) == 0.0


def test_weight_is_continuous_across_the_band():
    values = [mp.neck_band_weight(y, 10.0, 50.0) for y in range(10, 51, 4)]
    steps = [abs(b - a) for a, b in zip(values, values[1:])]
    assert max(steps) < 0.2, steps
    assert mp.neck_band_weight(30.0, 10.0, 50.0) == pytest.approx(0.5)


def test_neck_band_grids_match_head_field_at_the_boundary():
    """边界行的位移必须等于 HeadRig 在该点的位移，带底则保持不动。"""
    # 枢轴放在带外：若枢轴恰好落在边界点，顶行位移为 0，无法检验权重单调性。
    head = (0.0, 0.0, 8.0, 6.0, 240.0)
    options = {"head": head, "head_angles": {"angle_x": 1.0, "angle_y": 1.0,
                                             "angle_z": 1.0},
               "boundary_y": 40.0, "band_bottom_y": 80.0}
    boundary, bottom = 40.0, 80.0
    points = [[0.0, boundary], [0.0, boundary + (bottom - boundary) / 2.0],
              [0.0, bottom]]
    # values 是归一化分数，不是原始参数值；角度 = 分数 × head_angles[role]。
    values = {"angle_z": 1.0}
    got = mp._neck_band_deform(points, (-100.0, 40.0, 100.0, 80.0),
                               (-100.0, 40.0, 100.0, 80.0), values, options)
    want = mp._apply_head(points, values, options)
    assert (got[0][0] - points[0][0], got[0][1] - points[0][1]) == pytest.approx(
        (want[0][0] - points[0][0], want[0][1] - points[0][1]), abs=1e-6)
    assert got[2] == pytest.approx(points[2], abs=1e-6)
    assert got[1] != pytest.approx(points[1], abs=1e-9)
    # 比较总位移，避免把旋转的 x/y 分量之一误当成权重。
    assert math.hypot(got[1][0] - points[1][0], got[1][1] - points[1][1]) < \
        math.hypot(got[0][0] - points[0][0], got[0][1] - points[0][1])


def test_gradient_orientation_is_pinned_in_authored_space():
    """靠头那一侧的权重大于靠身那一侧，只比较作者空间的模型 y。"""
    boundary, bottom = 40.0, 80.0
    assert mp.neck_band_weight(41.0, boundary, bottom) > mp.neck_band_weight(
        79.0, boundary, bottom)
    assert boundary < bottom, "head_assembly_bottom 与成员下缘的相对位置反了"


def test_side_weight_is_one_near_the_axis_and_zero_at_the_shoulders():
    """横向衰减：颈轴附近满权重，肩线归零。

    没有这一条时，整件上衣（横跨 580px）会整块跟头转 —— 实测
    clothes_top↔arm_L 112.4px、Core 洞数 1116→1731。
    """
    axis, half = 100.0, 50.0
    assert mp.neck_band_side_weight(axis, axis, half) == 1.0
    # 半宽内必须严格满权重：脖子自己不能被削（否则 face↔neck 会退）
    assert mp.neck_band_side_weight(axis + half, axis, half) == 1.0
    assert mp.neck_band_side_weight(axis - half, axis, half) == 1.0
    # 过渡段之外归零
    zero_at = mp.NECK_BAND_SIDE_PLATEAU * half + mp.NECK_BAND_SIDE_FADE * half
    assert mp.neck_band_side_weight(axis + zero_at, axis, half) == 0.0
    assert mp.neck_band_side_weight(axis - zero_at - 1.0, axis, half) == 0.0


def test_side_weight_is_monotone_and_continuous():
    axis, half = 0.0, 40.0
    values = [mp.neck_band_side_weight(x, axis, half)
              for x in range(0, 200, 4)]
    assert all(b <= a + 1e-12 for a, b in zip(values, values[1:])), values
    assert all(abs(b - a) < 0.2 for a, b in zip(values, values[1:])), values


def test_missing_axis_info_keeps_the_old_single_axis_field():
    """拿不到颈轴信息时行为必须与单轴版本逐点一致（老模型不被迫变脸）。"""
    assert mp.neck_band_weight(30.0, 10.0, 50.0) == 0.5
    assert mp.neck_band_weight(30.0, 10.0, 50.0, x=999.0) == 0.5


def test_side_weight_reduces_the_weight_far_from_the_neck():
    """同一高度上，靠肩的格点位移必须小于颈轴附近的格点。"""
    head = (0.0, 0.0, 8.0, 6.0, 240.0)
    options = {"head": head,
               "head_angles": {"angle_x": 1.0, "angle_y": 1.0, "angle_z": 1.0},
               "boundary_y": 0.0, "band_bottom_y": 100.0,
               "band_axis_x": 0.0, "band_half_span_x": 20.0}
    points = [[0.0, 10.0], [200.0, 10.0]]
    got = mp._neck_band_deform(points, (-250.0, 0.0, 250.0, 100.0),
                               (-250.0, 0.0, 250.0, 100.0),
                               {"angle_z": 1.0}, options)
    near = math.hypot(got[0][0] - points[0][0], got[0][1] - points[0][1])
    far = math.hypot(got[1][0] - points[1][0], got[1][1] - points[1][1])
    assert near > 0.0
    assert far == pytest.approx(0.0, abs=1e-9)


def test_side_weight_reaches_zero_exactly_at_the_transition_cut_line():
    """横向权重必须在**切分线**上归零，切缝两侧才没有相对位移。

    只按几何切开、而权重在切分线上仍是 1 时，紧挨着切线的两片顶点会各走各的
    —— 实测 Core 姿态在颈右侧开出一条 474 像素的竖缝。
    """
    axis, span, core = 0.0, 120.0, 75.0
    assert mp.neck_band_side_weight(0.0, axis, span, core) == 1.0
    assert mp.neck_band_side_weight(75.0, axis, span, core) == 1.0
    assert mp.neck_band_side_weight(-75.0, axis, span, core) == 1.0
    assert mp.neck_band_side_weight(120.0, axis, span, core) == 0.0
    assert mp.neck_band_side_weight(-120.0, axis, span, core) == 0.0
    middle = mp.neck_band_side_weight(97.5, axis, span, core)
    assert 0.0 < middle < 1.0


def test_band_bottom_snaps_to_a_lattice_row():
    """带底对齐到不大于它的那一行：否则带底以下的格点仍带正权重，下缘会滑开。"""
    rect = (0.0, 0.0, 100.0, 90.0)      # 10 行 -> 步长 10
    assert mp._snap_band_bottom(84.0, rect, 10) == pytest.approx(80.0)
    assert mp._snap_band_bottom(80.0, rect, 10) == pytest.approx(80.0)
    assert mp._snap_band_bottom(-5.0, rect, 10) == pytest.approx(0.0)
    assert mp._snap_band_bottom(200.0, rect, 10) == pytest.approx(90.0)


def test_top_rows_move_more_than_bottom_rows_in_compiled_grids():
    """同一 ParamAngleZ 非默认键上，靠近头下缘的行位移更大，底行近似零。"""
    mesh = MeshSpec(
        mesh_id="neck",
        vertices=[[-20.0, 40.0], [20.0, 40.0], [-20.0, 80.0], [20.0, 80.0]],
        triangles=[(0, 1, 2), (1, 3, 2)],
        uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)],
    )
    entry = {"name": "NeckBand", "type": "warp", "parent": "Neck",
             "grid_rows": 2, "grid_cols": 2, "profile": "neck_band",
             "parameters": ["ParamAngleX", "ParamAngleY", "ParamAngleZ"],
             "targets": ["neck"]}
    params = {pid: ParameterSpec(pid, -30.0, 30.0, 0.0)
              for pid in ("ParamAngleX", "ParamAngleY", "ParamAngleZ")}
    warps, notes, _static = mp.build_deformers(
        [mesh], [entry], 200.0, 200.0, params, {"Head": (100.0, 40.0)},
        head_span=(100.0, 100.0), head_bottom=40.0)
    assert not notes, notes
    band = warps[0]
    assert band.rows == 3, "带内不足 4 行会退成二值；必须在生成点阵前抬行数"
    assert band.cols == 1
    keys = [axis[1] for axis in band.axes_spec]
    index = keys[0].index(0.0) + len(keys[0]) * keys[1].index(0.0) + \
        len(keys[0]) * len(keys[1]) * keys[2].index(30.0)
    moved = band.grids[index].points
    default_index = keys[0].index(0.0) + len(keys[0]) * keys[1].index(0.0) + \
        len(keys[0]) * len(keys[1]) * keys[2].index(0.0)
    # build_deformers 的参考矩形含既有 5% 余量，静止基准必须取默认键本身。
    rest = band.grids[default_index].points

    def distance(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    top = [distance(a, b) for a, b in zip(moved[:2], rest[:2])]
    bottom = [distance(a, b) for a, b in zip(moved[-2:], rest[-2:])]
    assert min(top) > max(bottom)
    assert max(bottom) == pytest.approx(0.0, abs=1e-9)


def test_head_assembly_bottom_uses_the_same_head_membership_as_span():
    """头下缘是入选头部网格的最大模型 y，身体网格不得把它往下拖。"""
    def mesh(bone, ys):
        return {"width": 200.0, "height": 200.0,
                "vertices": [[90.0, ys[0]], [110.0, ys[1]]],
                "weights": {"bone_names": [bone], "weights": [[1.0], [1.0]]}}

    meshes = {"face": mesh("Face", (20.0, 60.0)),
              "hair": mesh("Hair", (10.0, 50.0)),
              "neck": mesh("Neck", (70.0, 150.0))}
    bones = {"Head": {"group": "head"}, "Face": {"group": "face"},
             "Hair": {"group": "hair"}, "Neck": {"group": "body"}}
    assert mp.head_assembly_bottom(meshes, bones, {"Head": (100.0, 20.0)}) == \
        pytest.approx(-40.0)
    assert mp.head_assembly_bottom(meshes, bones, {}) is None


def test_split_mesh_at_y_conserves_area_and_shared_boundary():
    mesh = {
        "vertices": [[0.0, 100.0], [100.0, 100.0],
                     [100.0, 300.0], [0.0, 300.0]],
        "vertices_norm": [[0.0, 0.25], [0.25, 0.25],
                          [0.25, 0.75], [0.0, 0.75]],
        "indices": [[0, 1, 2], [0, 2, 3]],
        "width": 400.0,
        "height": 400.0,
        "weights": {"bone_names": ["Torso", "Body"],
                    "weights": [[0.25, 0.75]] * 4},
    }
    upper, lower = mp.split_mesh_at_y(mesh, 192.0)

    def area(part):
        vertices = part["vertices"]
        return sum(abs((vertices[b][0] - vertices[a][0])
                       * (vertices[c][1] - vertices[a][1])
                       - (vertices[b][1] - vertices[a][1])
                       * (vertices[c][0] - vertices[a][0])) / 2.0
                   for a, b, c in part["indices"])

    assert upper is not None and lower is not None
    assert area(upper) + area(lower) == pytest.approx(area(mesh))
    assert max(y for _x, y in upper["vertices"]) == pytest.approx(192.0)
    assert min(y for _x, y in lower["vertices"]) == pytest.approx(192.0)
    assert upper["weights"]["bone_names"] == lower["weights"]["bone_names"]
    assert sorted(x for x, y in upper["vertices"] if y == 192.0) == \
        sorted(x for x, y in lower["vertices"] if y == 192.0)


def test_transition_split_follows_neck_bottom_and_keeps_torso_lower_piece():
    uv = {"u0": 0.1, "v0": 0.2, "u1": 0.3, "v1": 0.4, "page": 2}
    meshes = {
        "neck": {
            "vertices": [[40.0, 120.0], [60.0, 120.0],
                         [60.0, 180.0], [40.0, 180.0]],
            "vertices_norm": [[0.1, 0.3], [0.15, 0.3],
                              [0.15, 0.45], [0.1, 0.45]],
            "indices": [[0, 1, 2], [0, 2, 3]],
            "width": 400.0, "height": 400.0,
        },
        "clothes_top": {
            "vertices": [[0.0, 100.0], [100.0, 100.0],
                         [100.0, 300.0], [0.0, 300.0]],
            "vertices_norm": [[0.0, 0.25], [0.25, 0.25],
                              [0.25, 0.75], [0.0, 0.75]],
            "indices": [[0, 1, 2], [0, 2, 3]],
            "width": 400.0, "height": 400.0,
        },
    }
    uvs = {"neck": uv, "clothes_top": uv}
    entries = [
        {"name": "BodySway", "type": "warp", "targets": ["clothes_top"]},
        {"name": "NeckBand", "type": "warp", "targets": ["neck"]},
    ]
    split_meshes, split_uvs, split_entries, note = mp.split_clothes_top_transition(
        meshes, uvs, entries, overlap_px=12.0)

    assert note
    assert max(y for _x, y in split_meshes["clothes_top_neck"]["vertices"]) == 192.0
    # 下片仍然覆盖切分线以下的整幅；线上方只剩颈带之外的两条肩带
    lower = split_meshes["clothes_top"]
    assert min(y for _x, y in lower["vertices"]) == 100.0
    assert all(x <= 34.0 + 1e-6 or x >= 66.0 - 1e-6
               for x, y in lower["vertices"] if y < 192.0 - 1e-6)
    assert split_uvs["clothes_top_neck"] == uv
    owners = mp.resolve_membership(split_entries, split_meshes.keys())
    assert owners["clothes_top_neck"] == "NeckBand"
    assert owners["clothes_top"] == "BodySway"


def _mesh_area(mesh):
    verts = mesh["vertices"]
    return sum(abs((verts[b][0] - verts[a][0]) * (verts[c][1] - verts[a][1])
                   - (verts[b][1] - verts[a][1]) * (verts[c][0] - verts[a][0])) / 2.0
               for a, b, c in mesh["indices"])


def test_transition_piece_is_bounded_to_the_neck_strip():
    """过渡片只能罩住脖子那一条竖带，不能横扫整个肩宽。

    只按 y 切时肩部衣料也跟着头转，与挂在 ``ShoulderLift_*`` 上的手臂脱开 ——
    实测 Core 姿态在右肩开出 730 像素的新透明区（``clothes_top_neck`` ↔ ``arm_L``
    87.9px）。肩线必须留在 ``BodySway``，所以切分要同时限制横向范围。
    """
    uv = {"u0": 0.1, "v0": 0.2, "u1": 0.3, "v1": 0.4, "page": 2}
    neck = {
        "vertices": [[120.0, 120.0], [180.0, 120.0],
                     [180.0, 180.0], [120.0, 180.0]],
        "vertices_norm": [[0.3, 0.3], [0.45, 0.3], [0.45, 0.45], [0.3, 0.45]],
        "indices": [[0, 1, 2], [0, 2, 3]],
        "width": 400.0, "height": 400.0,
    }
    clothes = {
        "vertices": [[0.0, 100.0], [300.0, 100.0],
                     [300.0, 300.0], [0.0, 300.0]],
        "vertices_norm": [[0.0, 0.25], [0.75, 0.25], [0.75, 0.75], [0.0, 0.75]],
        "indices": [[0, 1, 2], [0, 2, 3]],
        "width": 400.0, "height": 400.0,
    }
    entries = [
        {"name": "BodySway", "type": "warp", "targets": ["clothes_top"]},
        {"name": "NeckBand", "type": "warp", "targets": ["neck"]},
    ]
    out, _uvs, split_entries, note = mp.split_clothes_top_transition(
        {"neck": neck, "clothes_top": clothes},
        {"neck": uv, "clothes_top": uv}, entries, overlap_px=12.0)

    assert note
    axis, half = 150.0, 30.0
    limit = mp.CLOTHES_TRANSITION_SIDE_SPAN * half
    piece = out["clothes_top_neck"]
    assert max(x for x, _y in piece["vertices"]) <= axis + limit + 1e-6
    assert min(x for x, _y in piece["vertices"]) >= axis - limit - 1e-6
    # 横向也被切开以后，两片的面积并集仍须等于原网格（不能切出空洞或重叠）
    assert _mesh_area(piece) + _mesh_area(out["clothes_top"]) == \
        pytest.approx(_mesh_area(clothes))
