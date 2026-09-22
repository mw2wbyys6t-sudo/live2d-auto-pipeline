"""手臂 / 头发：rotation 变形器**参数绑定**的官方内核逐像素验收。

被验证的接线：``build_rig_spec`` 按枢轴把 rotation 变形器绑到官方参数
（``ParamArmLA/LB/RA/RB``、``ParamHairFront/Side/Back``）。

判据与旋转验收同源（README/评审里的 F-03c）：**参数取极值时的画面，必须与
「顶点已按同一角度预转好的等价网格」逐像素相同** —— 角度方向、枢轴位置、
绑定到底有没有生效，任一错都会让 sha 立刻不同；反之若只是"结构存在但不动"，
它也不会碰巧等于预转后的参照。

经验教训（见 tools/verify_breath_end_to_end.py）：画布必须**大于**网格，否则网格
填满画布、形变被裁掉而画面全饱和；参照必须走**同一条渲染路径**；参照也要
``ensure_front_facing`` 且声明参数（否则 render_probe 拒绝验收）。
"""
import json
import math
import os
from pathlib import Path

import pytest

from drivers.live2d_runtime.moc3_verify import render_probe
from live2d_builder.exporter.moc3_lint import lint_document
from live2d_builder.exporter.moc3_model import (
    MeshSpec,
    ParameterSpec,
    RigSpec,
    compile_static_rig,
)
from live2d_builder.exporter.moc3_pipeline import (
    HEAD_DEPTH_SPANS,
    HEAD_SHIFT_X,
    HEAD_SHIFT_Y,
    _DEGREES_PER_UNIT,
    _sincos,
    build_rig_spec,
    ensure_front_facing,
)

CANVAS = 1024.0
PPU = 100.0
UVS = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
TRIANGLES = [[0, 1, 2], [0, 2, 3]]
# 网格放在画布中央、明显小于画布（见模块 docstring 的教训一）
VERT_IMAGE = [[412.0, 362.0], [612.0, 362.0], [612.0, 662.0], [412.0, 662.0]]

# 与 BoneHierarchy.get_bone_positions() 的默认比例模型一致（图像坐标，左上原点）
BONES = {
    "Head": (0.0, -40.0),
    "ArmBack_L": (-90.0, 80.0), "ForearmBack_L": (-130.0, 150.0),
    "ArmBack_R": (90.0, 80.0), "ForearmBack_R": (130.0, 150.0),
    "Hair_Front": (0.0, -90.0), "Hair_Top": (0.0, -120.0),
    "Hair_Side_L": (-60.0, -40.0), "Hair_Side_R": (60.0, -40.0),
    "Hair_Back": (0.0, -80.0),
}

# (枢轴骨骼, 期望绑定的官方参数)
CASES = [
    ("ArmBack_L", "ParamArmLA"), ("ForearmBack_L", "ParamArmLB"),
    ("ArmBack_R", "ParamArmRA"), ("ForearmBack_R", "ParamArmRB"),
    ("Hair_Front", "ParamHairFront"), ("Hair_Top", "ParamHairFront"),
    ("Hair_Side_L", "ParamHairSide"), ("Hair_Side_R", "ParamHairSide"),
    ("Hair_Back", "ParamHairBack"),
]
MAX_KEY = 30.0


def _has_live2d() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("live2d.v3") is not None
    except (ImportError, ValueError):
        return False


requires_live2d = pytest.mark.skipif(
    not _has_live2d(), reason="需要 live2d-py 运行时（pip install live2d-py）")
requires_pixels = pytest.mark.skipif(
    os.environ.get("LIVE2D_TEST_PIXELS") != "1",
    reason="需要 OpenGL：设 LIVE2D_TEST_PIXELS=1 开启受控像素验证")


def _model_vertices():
    """图像坐标 -> 模型坐标（原点居中、y 向上），与 build_rig_spec 同一换算。"""
    return [[x - CANVAS / 2.0, CANVAS / 2.0 - y] for x, y in VERT_IMAGE]


def _pivot_model(bone):
    px, py = BONES[bone]
    return (px - CANVAS / 2.0, CANVAS / 2.0 - py)


def _rotate(points, degrees, about):
    """模型坐标（y 向上）下绕 about 逆时针旋转 —— 待验证的假设本身。"""
    theta = math.radians(degrees)
    cos, sin = math.cos(theta), math.sin(theta)
    ox, oy = about
    return [(ox + (x - ox) * cos - (y - oy) * sin,
             oy + (x - ox) * sin + (y - oy) * cos) for x, y in points]


def _builder_result(bone, param):
    """管线产物：一个网格 + 枢轴等于该骨骼的 rotation 变形器 + 官方参数。"""
    px, py = BONES[bone]
    mesh = {
        "vertices": VERT_IMAGE,
        "vertices_norm": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
        "indices": TRIANGLES,
        "width": CANVAS, "height": CANVAS,
    }
    return {
        "meshes": {"part": mesh},
        "deformer_tree": {"deformers": [
            {"name": "D", "type": "rotation", "targets": ["part"],
             "pivot": [px, py]}]},
        "parameters": {"cubism_params": [
            {"Id": param, "Min": -30, "Max": 30, "Value": 0}]},
        "bone_positions": dict(BONES),
    }


def _package(tmp_path: Path, doc, name: str) -> Path:
    from PIL import Image

    folder = tmp_path / name
    folder.mkdir()
    (folder / f"{name}.moc3").write_bytes(doc.to_bytes())
    Image.new("RGBA", (64, 64), (255, 0, 255, 255)).save(
        folder / "texture_00.png")
    manifest = folder / f"{name}.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3, "Meta": {"ArchiveName": name},
        "FileReferences": {"Moc": f"{name}.moc3",
                           "Textures": ["texture_00.png"]},
    }), encoding="utf-8")
    return manifest


def _reference_manifest(tmp_path: Path, name: str, param: str,
                        degrees: float, about) -> Path:
    """参照：**不带变形器**、顶点已按同一角度预转好的等价网格。

    rotation 下成员网格的顶点照旧存模型坐标（官方 Haru：rotation 下 4 个网格在
    像素模型空间），所以普通网格与它在**同一条渲染路径**上，逐像素可比。
    """
    rotated = _rotate(_model_vertices(), degrees, about)
    mesh = MeshSpec(
        mesh_id="part", vertices=rotated,
        triangles=ensure_front_facing(rotated, TRIANGLES), uvs=list(UVS))
    spec = RigSpec(
        meshes=[mesh],
        # 参照仍须**声明**参数：render_probe 对"没有任何原生参数"的模型直接拒验收。
        parameters=[ParameterSpec(param, minimum=-30.0, maximum=30.0,
                                  default=0.0)],
        canvas_width=CANVAS, canvas_height=CANVAS, pixels_per_unit=PPU)
    return _package(tmp_path, compile_static_rig(spec), name)


@pytest.mark.parametrize("bone, param", CASES)
@requires_live2d
def test_pipeline_binds_the_official_param_for_arm_and_hair(bone, param):
    """接线层：枢轴等于该骨骼的 rotation 变形器必须绑到对应官方参数。"""
    atlas = {"part": {"u0": 0.0, "v0": 0.0, "u1": 1.0, "v1": 1.0}}
    spec = build_rig_spec(_builder_result(bone, param), atlas)
    deformer = spec.deformers[0]
    assert deformer.parameter_id == param
    assert [k.key_value for k in deformer.keyforms] == [-30.0, 0.0, 30.0]
    assert spec.static_deformers == []


@pytest.mark.parametrize("bone, param", CASES)
@requires_live2d
@requires_pixels
def test_param_extreme_matches_a_pre_rotated_mesh_pixel_for_pixel(
        bone, param, tmp_path):
    """像素层：参数取极值时，画面必须与「同角度预转的等价网格」完全相同。"""
    atlas = {"part": {"u0": 0.0, "v0": 0.0, "u1": 1.0, "v1": 1.0}}
    spec = build_rig_spec(_builder_result(bone, param), atlas)
    doc = compile_static_rig(spec)
    assert lint_document(doc) == [], [str(i) for i in lint_document(doc)]
    manifest = _package(tmp_path, doc, "driven")

    about = _pivot_model(bone)
    angle = MAX_KEY * _DEGREES_PER_UNIT

    rest = render_probe(str(manifest), {param: 0.0})
    full = render_probe(str(manifest), {param: MAX_KEY})
    assert rest["ok"] is True, rest["blocker"]
    assert full["ok"] is True, full["blocker"]

    rest_ref = render_probe(str(_reference_manifest(
        tmp_path, "ref_rest", param, 0.0, about)))
    full_ref = render_probe(str(_reference_manifest(
        tmp_path, "ref_rot", param, angle, about)))
    assert rest_ref["ok"] is True, rest_ref["blocker"]
    assert full_ref["ok"] is True, full_ref["blocker"]

    # 默认键复现静止姿态；极值键等于按同一角度预转的网格（方向/枢轴/绑定任一错即红）
    assert rest["pixels_sha256"] == rest_ref["pixels_sha256"]
    assert full["pixels_sha256"] == full_ref["pixels_sha256"]
    # 该测试确实能区分"动"与"不动"
    assert rest["pixels_sha256"] != full["pixels_sha256"]


# ---------------------------------------------------------------- 头部三轴复合

_HEAD_PARAMS = ("ParamAngleX", "ParamAngleY", "ParamAngleZ")


def _head_builder_result():
    """头部网格（受 Head 骨骼影响）+ X/Y/Z 三参数都声明 -> 走三轴复合路径。"""
    mesh = {
        "vertices": VERT_IMAGE,
        "vertices_norm": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
        "indices": TRIANGLES,
        "width": CANVAS, "height": CANVAS,
        "weights": {"bone_names": ["Head"], "weights": [[1.0]] * 4},
    }
    return {
        "meshes": {"part": mesh},
        "deformer_tree": {"deformers": []},
        "parameters": {"cubism_params": [
            {"Id": p, "Min": -30, "Max": 30, "Value": 0} for p in _HEAD_PARAMS]},
        "bone_positions": dict(BONES),
    }


def _head_reference(tmp_path: Path, name: str,
                    degrees_x: float, degrees_y: float, degrees_z: float) -> Path:
    """参照：顶点已按同一复合变换（点头/转头透视压缩 ∘ 绕枢轴旋转 Z）预做的等价网格。

    与 ``_head_transform`` 逐字节一致的独立实现 —— 判据是"同款预变换网格"，两边
    公式必须同步改。
    """
    verts = _model_vertices()
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    # 枢轴 = Head 关节（与产物同一换算），不是包围盒中心
    px, py = _pivot_model("Head")
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    shift_x = span_x * HEAD_SHIFT_X
    shift_y = span_y * HEAD_SHIFT_Y
    depth = max(span_x, span_y, 1.0) * HEAD_DEPTH_SPANS
    sin_x, cos_x = _sincos(degrees_x)
    sin_y, cos_y = _sincos(degrees_y)
    sin_z, cos_z = _sincos(degrees_z)
    moved = []
    for x, y in verts:
        dx, dy = x - px, y - py
        z = -(dx * sin_y + dy * sin_x)
        p = 1.0 + z / depth
        tx = px + dx * cos_y * p + shift_x * sin_y
        ty = py + dy * cos_x * p + shift_y * sin_x
        moved.append(_rotate([(tx, ty)], degrees_z, (px, py))[0])
    mesh = MeshSpec(
        mesh_id="part", vertices=moved,
        triangles=ensure_front_facing(moved, TRIANGLES), uvs=list(UVS))
    spec = RigSpec(
        meshes=[mesh],
        parameters=[ParameterSpec(p, minimum=-30.0, maximum=30.0, default=0.0)
                    for p in _HEAD_PARAMS],
        canvas_width=CANVAS, canvas_height=CANVAS, pixels_per_unit=PPU)
    return _package(tmp_path, compile_static_rig(spec), name)


@requires_live2d
@requires_pixels
def test_head_three_axis_composite_matches_a_pre_transformed_mesh(tmp_path):
    """头部三轴带：跨轴组合 (X=+30, Y=0, Z=−30) 必须与同款预变换逐像素相同。

    刻意让两个轴同时离开默认值：展平序（index = i_X + 3·i_Y + 9·i_Z）若搞错，
    内核就会选到别的关键形，sha 立刻不同。
    """
    atlas = {"part": {"u0": 0.0, "v0": 0.0, "u1": 1.0, "v1": 1.0}}
    spec = build_rig_spec(_head_builder_result(), atlas)
    assert [pid for pid, _ in spec.meshes[0].keyform_axes] == list(_HEAD_PARAMS)
    doc = compile_static_rig(spec)
    assert lint_document(doc) == [], [str(i) for i in lint_document(doc)]
    manifest = _package(tmp_path, doc, "head")

    got = render_probe(str(manifest), {"ParamAngleX": 30.0, "ParamAngleY": 0.0,
                                       "ParamAngleZ": -30.0})
    want = render_probe(str(_head_reference(
        tmp_path, "ref_combo", 30.0, 0.0, -30.0)))
    assert got["ok"] is True, got["blocker"]
    assert want["ok"] is True, want["blocker"]
    assert got["pixels_sha256"] == want["pixels_sha256"]

    rest = render_probe(str(manifest), {"ParamAngleX": 0.0, "ParamAngleY": 0.0,
                                        "ParamAngleZ": 0.0})
    rest_ref = render_probe(str(_head_reference(
        tmp_path, "ref_rest2", 0.0, 0.0, 0.0)))
    assert rest["pixels_sha256"] == rest_ref["pixels_sha256"]
