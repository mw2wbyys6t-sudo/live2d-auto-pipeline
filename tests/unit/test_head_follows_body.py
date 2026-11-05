"""头部网格要不要跟随躯干侧倾（ParamBodyAngleZ）。

实测依据（tools/bindings_gap_probe.py，singer 导出件）：躯干 12 个网格在
``ParamBodyAngleZ = 10`` 下解出的都是同一个刚体（θ=10.000°、中心 (455.2,1038.8)、
非刚体残差 ≤0.001px），而头组（脸/发/眼/眉/口/鼻/泪）完全不跟 —— 于是领口离脸
57px、15 参数复杂姿态最大撕开 137.8px。修法是把身体旋转**合成**进头组的多轴键形带，
让头与躯干在只驱动身体场时落在同一个位置上。
"""
from __future__ import annotations

import math

import pytest

from live2d_builder.exporter import moc3_pipeline as mp
from live2d_builder.exporter.moc3_model import ParameterSpec

W, H = 1024.0, 1536.0
HEAD_JOINT = (512.0, 300.0)
BODY_PIVOT = (455.2, 1038.8)


def params(with_body=True):
    p = {
        "ParamAngleX": ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0),
        "ParamAngleY": ParameterSpec("ParamAngleY", -30.0, 30.0, 0.0),
        "ParamAngleZ": ParameterSpec("ParamAngleZ", -30.0, 30.0, 0.0),
    }
    if with_body:
        p["ParamBodyAngleZ"] = ParameterSpec("ParamBodyAngleZ", -10.0, 10.0, 0.0)
    return p


BONES = {
    "Head": {"group": "head"},
    "Face": {"group": "face"},
    "Body": {"group": "body"},
    "Neck": {"group": "body"},
}
POSITIONS = {"Head": HEAD_JOINT, "Face": HEAD_JOINT,
             "Body": BODY_PIVOT, "Neck": BODY_PIVOT}


def head_mesh(vertices):
    return {"vertices": vertices,
            "weights": {"bone_names": ["Face"],
                        "weights": [[1.0] for _ in vertices]}}


def torso_mesh(vertices):
    return {"vertices": vertices,
            "weights": {"bone_names": ["Neck"],
                        "weights": [[1.0] for _ in vertices]}}


VERTS = [[120.0, 60.0], [-40.0, 88.0], [10.0, -30.0]]
SPAN = (240.0, 260.0)


def generate(with_body=True):
    """头部带 + 身体跟随并进后的结果 —— 调用站点就是这么串的。"""
    mesh = head_mesh(VERTS)
    axes, shapes, weights = mp.head_angle_keyforms(
        mesh, VERTS, params(with_body), BONES, POSITIONS, W, H, head_span=SPAN)
    return mp.with_body_follow(axes, shapes, VERTS, params(with_body),
                               POSITIONS, W, H, weights=weights)


def index_of(i_x, i_y, i_z, i_b, n=(3, 3, 3, 3)):
    """展平序：第一个轴步长为 1（F-06 实测定论）。"""
    return i_x + n[0] * i_y + n[0] * n[1] * i_z + n[0] * n[1] * n[2] * i_b


def mxy(x, y):
    """图像坐标（左上原点、y 向下）-> 模型坐标，测试里反复要用。"""
    return mp.to_model_xy(x, y, W, H)


def torso_key_index(shapes, value):
    """rotation_keyforms 返回的是单轴带，按 key_value 找下标。"""
    return [float(s.key_value) for s in shapes].index(value)


class TestHeadBandCarriesTheBodyAxis:
    def test_body_angle_is_the_fourth_axis(self):
        axes, shapes = generate()
        assert [pid for pid, _keys in axes] == [
            "ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamBodyAngleZ"]
        assert list(axes[3][1]) == [-10.0, 0.0, 10.0]
        assert len(shapes) == 3 * 3 * 3 * 3

    def test_without_the_body_parameter_it_stays_a_three_axis_band(self):
        """参数没声明时退回三轴 27 键形 —— 不因为缺一个参数就不出头。"""
        axes, shapes = generate(with_body=False)
        assert [pid for pid, _k in axes] == list(mp.HEAD_ANGLE_AXES)
        assert len(shapes) == 27

    def test_unknown_body_pivot_falls_back_instead_of_guessing(self):
        """枢轴未知就不合成：宁可不跟，也不拿包围盒中心顶替一个说不清的枢轴。"""
        positions = dict(POSITIONS)
        positions.pop("Body")
        axes, shapes, weights = mp.head_angle_keyforms(
            head_mesh(VERTS), VERTS, params(), BONES, positions, W, H,
            head_span=SPAN)
        axes, shapes = mp.with_body_follow(axes, shapes, VERTS, params(),
                                           positions, W, H, weights=weights)
        assert [pid for pid, _k in axes] == list(mp.HEAD_ANGLE_AXES)
        assert len(shapes) == 27


class TestCompositionIsRight:
    def test_all_default_keys_reproduce_rest(self):
        axes, shapes = generate()
        shape = shapes[index_of(1, 1, 1, 1)]
        for got, want in zip(shape.vertices, VERTS):
            assert got == pytest.approx(want, abs=1e-6)

    def test_body_only_key_is_a_rigid_rotation_about_the_body_pivot(self):
        """只把身体轴推到 +10：每个顶点都必须绕 Body 枢轴刚体转 10°。

        ``head_angle_keyforms`` 收的是**模型坐标**，所以 VERTS 直接当模型坐标用，
        只有枢轴需要从图像坐标换算一次。
        """
        axes, shapes = generate()
        shape = shapes[index_of(1, 1, 1, 2)]
        px, py = mxy(*BODY_PIVOT)
        theta = math.radians(10.0)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        for got, (mx, my) in zip(shape.vertices, VERTS):
            dx, dy = mx - px, my - py
            assert got[0] == pytest.approx(px + cos_t * dx - sin_t * dy, abs=1e-6)
            assert got[1] == pytest.approx(py + sin_t * dx + cos_t * dy, abs=1e-6)

    def test_head_axis_still_works_alongside_the_body_axis(self):
        """头的 Z 轴侧转与身体轴是复合关系：两者都推到极值时两次旋转都要在。"""
        axes, shapes = generate()
        shape = shapes[index_of(1, 1, 2, 2)]          # AngleZ=+30 且 BodyAngleZ=+10
        hx, hy = mxy(*HEAD_JOINT)
        bx, by = mxy(*BODY_PIVOT)
        for got, (mx, my) in zip(shape.vertices, VERTS):
            # 先头的侧转（在头枢轴上），再身体的侧倾（在髋枢轴上）
            d1x, d1y = mx - hx, my - hy
            c1, s1 = math.cos(math.radians(30.0)), math.sin(math.radians(30.0))
            t_x, t_y = hx + c1 * d1x - s1 * d1y, hy + s1 * d1x + c1 * d1y
            d2x, d2y = t_x - bx, t_y - by
            c2, s2 = math.cos(math.radians(10.0)), math.sin(math.radians(10.0))
            assert got[0] == pytest.approx(bx + c2 * d2x - s2 * d2y, abs=1e-4)
            assert got[1] == pytest.approx(by + s2 * d2x + c2 * d2y, abs=1e-4)


class TestHeadAndTorsoShareOneField:
    """这条就是验收判据：只驱动身体场时，头与躯干必须落在同一个位置。

    缝开没开看配对距离的变化；同一个刚体场下它恒为 0 —— 上一轮的假证据正是
    量了位移之差（同一场里天生等于 2·sin(θ/2)·间距）。

    注意两个入口的坐标口径不同：``head_angle_keyforms`` 收**已换算的模型坐标**，
    ``rotation_keyforms`` 自己从 ``mesh["vertices"]`` 做换算，收**图像坐标**。
    所以同一个物理点在这里要写成两份。
    """

    P_IMG = [542.0, 648.0]          # 模型坐标 (30, -120)
    Q_IMG = [457.0, 708.0]          # 模型坐标 (-55, -60)

    def _head_body_shape(self, vertices_model):
        axes, shapes, weights = mp.head_angle_keyforms(
            head_mesh(vertices_model), vertices_model, params(), BONES,
            POSITIONS, W, H, head_span=SPAN)
        axes, shapes = mp.with_body_follow(axes, shapes, vertices_model, params(),
                                           POSITIONS, W, H, weights=weights)
        body_key = list(axes[3][1]).index(10.0)
        return shapes[index_of(1, 1, 1, body_key)].vertices

    def _torso_body_shape(self, vertices_image):
        pid, shapes, _cand = mp.rotation_keyforms(
            torso_mesh(vertices_image), params(), POSITIONS, BONES, W, H)
        assert pid == "ParamBodyAngleZ"
        return shapes[torso_key_index(shapes, 10.0)].vertices

    def test_same_point_on_head_and_torso_moves_identically(self):
        head_out = self._head_body_shape([list(mxy(*self.P_IMG))])[0]
        torso_out = self._torso_body_shape([list(self.P_IMG)])[0]
        assert head_out[0] == pytest.approx(torso_out[0], abs=1e-6)
        assert head_out[1] == pytest.approx(torso_out[1], abs=1e-6)

    def test_distance_between_a_head_point_and_a_torso_point_is_preserved(self):
        h = self._head_body_shape([list(mxy(*self.P_IMG)),
                                   list(mxy(*self.Q_IMG))])
        t = self._torso_body_shape([list(self.P_IMG), list(self.Q_IMG)])
        # 头网格上的 P 与躯干网格上的 Q 是一对跨层顶点：静止相距 104px，
        # 只驱动身体场时这个距离必须原样保持（同一场 → 距离变化 0）。
        rest = math.hypot(mxy(*self.P_IMG)[0] - mxy(*self.Q_IMG)[0],
                          mxy(*self.P_IMG)[1] - mxy(*self.Q_IMG)[1])
        moved = math.hypot(h[0][0] - t[1][0], h[0][1] - t[1][1])
        assert moved == pytest.approx(rest, abs=1e-6)


class TestUpperTorsoEntersHeadField:
    def test_boundary_pair_distance_is_preserved_for_head_only_pose(self):
        """脸底与颈顶在只转头时必须仍是一对同场顶点。"""
        face_img = [HEAD_JOINT[0], 242.0]
        neck_img = [HEAD_JOINT[0], 242.0]
        head_bottom_y = mp.to_model_xy(*face_img, W, H)[1]

        face_mesh = head_mesh([list(mxy(*face_img))])
        head_axes, head_shapes, head_weights = mp.head_angle_keyforms(
            face_mesh, face_mesh["vertices"], params(), BONES, POSITIONS,
            W, H, head_span=SPAN)
        head_axes, head_shapes = mp.with_body_follow(
            head_axes, head_shapes, face_mesh["vertices"], params(), POSITIONS,
            W, H, weights=head_weights)
        face_out = head_shapes[index_of(1, 1, 2, 1)].vertices[0]

        neck_model = [list(mxy(*neck_img))]
        neck_mesh = torso_mesh(neck_img)
        _axes, shapes, _weights = mp.head_body_transition_keyforms(
            neck_mesh, neck_model, params(), BONES, POSITIONS, W, H,
            head_bottom_y)
        neck_out = shapes[2 + 3 * 1].vertices[0]

        rest = math.dist(face_mesh["vertices"][0], neck_model[0])
        moved = math.dist(face_out, neck_out)
        assert moved == pytest.approx(rest, abs=1e-6)

    def test_transition_is_limited_to_neck_bones(self):
        head_x, head_y = mxy(HEAD_JOINT[0], 242.0)
        for point, bone, parent in (
            ((head_x, head_y), "Neck", "Body"),
            ((head_x, head_y), "Torso", "Body"),
            ((head_x, head_y), "Chest", "Torso"),
            ((head_x, head_y), "ArmBack_L", "Body"),
            ((head_x, head_y), "Leg_L", "Waist"),
        ):
            mesh = torso_mesh([point])
            mesh["weights"] = {"bone_names": [bone, parent],
                               "weights": [[0.6, 0.4]]}
            axes, shapes, weights = mp.head_body_transition_keyforms(
                mesh, [list(point)], params(), BONES, POSITIONS, W, H, head_y)
            if bone == "Neck":
                assert len(shapes) == 9
            else:
                assert axes == () and shapes == [] and weights == []
