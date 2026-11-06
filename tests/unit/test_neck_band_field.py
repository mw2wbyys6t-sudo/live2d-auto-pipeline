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
