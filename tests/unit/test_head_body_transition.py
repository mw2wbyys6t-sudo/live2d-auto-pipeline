import pytest

from live2d_builder.exporter import moc3_pipeline as mp
from live2d_builder.exporter.moc3_model import ParameterSpec

W, H = 1024.0, 1536.0
HEAD_JOINT = (518.277, 190.758)
BODY_PIVOT = (521.930, 491.368)
HEAD_BOTTOM_IMG = (512.0, 242.0)

BONES = {
    "Body": {"parent": "Root", "group": "body"},
    "Torso": {"parent": "Body", "group": "body"},
    "Chest": {"parent": "Torso", "group": "body"},
    "Neck": {"parent": "Body", "group": "body"},
    "Head": {"parent": "Neck", "group": "head"},
    "Face": {"parent": "Head", "group": "face"},
    "ArmBack_L": {"parent": "Body", "group": "arms"},
    "Waist": {"parent": "Torso", "group": "body"},
    "Leg_L": {"parent": "Waist", "group": "legs"},
}
POSITIONS = {
    "Head": HEAD_JOINT,
    "Neck": HEAD_JOINT,
    "Body": BODY_PIVOT,
    "Torso": BODY_PIVOT,
}


HEAD_X_MODEL = mp.to_model_xy(*HEAD_JOINT, W, H)[0]


def params():
    return {
        "ParamAngleZ": ParameterSpec("ParamAngleZ", -30.0, 30.0, 0.0),
        "ParamBodyAngleZ": ParameterSpec("ParamBodyAngleZ", -10.0, 10.0, 0.0),
    }


def skin(bone, n, parent=None):
    if parent is None:
        return {"bone_names": [bone], "weights": [[1.0] for _ in range(n)]}
    return {"bone_names": [bone, parent], "weights": [[0.6, 0.4] for _ in range(n)]}


def transition(vertices, bone="Neck", head_bottom_y=None, parent=None):
    mesh = {"weights": skin(bone, len(vertices), parent)}
    if head_bottom_y is None:
        head_bottom_y = mp.to_model_xy(*HEAD_BOTTOM_IMG, W, H)[1]
    return mp.head_body_transition_keyforms(
        mesh, vertices, params(), BONES, POSITIONS, W, H, head_bottom_y)


def transition_radius():
    body_y = mp.to_model_xy(*BODY_PIVOT, W, H)[1]
    head_y = mp.to_model_xy(*HEAD_BOTTOM_IMG, W, H)[1]
    return (body_y - head_y) * mp.HEAD_BODY_TRANSITION_RATIO


def horizontal_radius():
    return transition_radius() * mp.HEAD_BODY_HORIZONTAL_RADIUS_RATIO


def shape_at(shapes, z_index, body_index):
    return shapes[z_index + 3 * body_index]


def sincos(value):
    sin, cos = mp._sincos(value)
    return cos, sin


def test_head_assembly_bottom_uses_lowest_head_vertex():
    vertices = [(500.0, 100.0), (500.0, 242.0)]
    meshes = {
        "face": {
            "vertices": vertices,
            "width": W,
            "height": H,
            "weights": {"bone_names": ["Face"], "weights": [[1.0], [1.0]]},
        }
    }
    got = mp.head_assembly_bottom(meshes, BONES, POSITIONS)
    assert got == mp.to_model_xy(500.0, 242.0, W, H)[1]


def test_upper_boundary_point_receives_full_head_weight():
    y = mp.to_model_xy(*HEAD_BOTTOM_IMG, W, H)[1]
    axes, shapes, weights = transition([(HEAD_X_MODEL, y)])
    assert [pid for pid, _keys in axes] == ["ParamAngleZ", "ParamBodyAngleZ"]
    assert weights == [1.0]
    assert len(shapes) == 9
    hx, hy = mp.to_model_xy(*HEAD_JOINT, W, H)
    expected = mp._blend(HEAD_X_MODEL, y, hx, hy, *sincos(30.0), 1.0)
    assert shape_at(shapes, 2, 1).vertices[0] == pytest.approx(expected, abs=1e-6)


def test_body_only_transform_is_unchanged():
    vertices = [mp.to_model_xy(x, y, W, H) for x, y in
                [(512.0, 242.0), (520.0, 300.0), (530.0, 450.0)]]
    _axes, shapes, _weights = transition(vertices)
    bx, by = mp.to_model_xy(*BODY_PIVOT, W, H)
    body_key = shape_at(shapes, 1, 2)
    for got, (x, y) in zip(body_key.vertices, vertices):
        assert got == pytest.approx(mp._blend(x, y, bx, by, *sincos(10.0), 1.0),
                                    abs=1e-6)


def test_default_keys_reproduce_rest_exactly():
    vertices = [mp.to_model_xy(512.0, 242.0, W, H)]
    _axes, shapes, _weights = transition(vertices)
    assert shapes[4].vertices[0] == pytest.approx(vertices[0], abs=1e-9)


def test_lower_torso_is_outside_transition_and_falls_back_to_body_only():
    y = mp.to_model_xy(512.0, 450.0, W, H)[1]
    axes, shapes, weights = transition([(HEAD_X_MODEL, y)])
    assert axes == () and shapes == [] and weights == []


def test_2d_falloff_includes_torso_center_and_excludes_shoulders():
    y = mp.to_model_xy(*HEAD_BOTTOM_IMG, W, H)[1]
    center = [(HEAD_X_MODEL, y)]
    axes, shapes, weights = transition(center, bone="Torso", parent="Body")
    assert [pid for pid, _keys in axes] == ["ParamAngleZ", "ParamBodyAngleZ"]
    assert weights == [1.0]
    assert len(shapes) == 9

    shoulder_x = HEAD_X_MODEL + 3 * horizontal_radius()
    axes, shapes, weights = transition([(shoulder_x, y)], bone="Torso",
                                       parent="Body")
    assert axes == () and shapes == [] and weights == []


def test_arms_and_legs_do_not_enter_head_transition():
    vertices = [mp.to_model_xy(512.0, 242.0, W, H)]
    for bone, parent in (("ArmBack_L", "Body"), ("Leg_L", "Waist")):
        axes, shapes, weights = transition(vertices, bone=bone, parent=parent)
        assert axes == () and shapes == [] and weights == []
