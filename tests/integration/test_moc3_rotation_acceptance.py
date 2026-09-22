"""rotation 变形器的官方内核验收：与「预先转好的等价网格」必须逐像素相同。

判据刻意不用 bbox/质心近似（纹理遮罩会把可见区裁掉，几何量的比例不可信），
而是拿一份**没有变形器**、顶点已按待验证的变换转好的网格当参照：

* 若 angles 是角度制、正值=模型空间逆时针、origin 在换算后的模型坐标里，
  那么变形器渲染与参照渲染的 sha 必须完全相同；
* 任何一处约定错（弧度、方向翻转、原点当成像素、base 不参与求和），
  sha 立刻不同 —— 不给自己留「看起来差不多」的余地。

实测依据：``tools/probe_rotation_pixels.py``（含 scale=0/负值、reflect=1 的
观察），字段语义写进 ``docs/native-runtime.md``。
"""
import json
import math
import os
from pathlib import Path

import pytest

from drivers.live2d_runtime.moc3_verify import (
    render_probe,
    verify_moc3_consistency,
)
from live2d_builder.exporter.moc3_lint import lint_document
from live2d_builder.exporter.moc3_model import (
    MeshSpec,
    ParameterSpec,
    RigSpec,
    RotationDeformerSpec,
    RotationKeyform,
    compile_static_rig,
)

_CANVAS = 1024.0
_PPU = 100.0
_PARAM = "ParamEyeRotX"
# 不对称直角三角形：只有不对称图形才能暴露旋转方向与镜像
_VERTS = [(0.0, 0.0), (200.0, 0.0), (0.0, 150.0)]
_UVS = [(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)]


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
    reason="需要 OpenGL：设 LIVE2D_TEST_PIXELS=1 开启像素验证")


def _rotate(points, degrees, about=(0.0, 0.0)):
    """模型坐标（y 向上）下的 CCW 旋转 —— 待验证的假设本身。"""
    theta = math.radians(degrees)
    cos, sin = math.cos(theta), math.sin(theta)
    ox, oy = about
    out = []
    for x, y in points:
        dx, dy = x - ox, y - oy
        out.append((ox + dx * cos - dy * sin, oy + dx * sin + dy * cos))
    return out


def _rig(*, deformers=(), meshes=None, parameters=True):
    if meshes is None:
        # 变形器只有挂上网格才起作用；忘了设 deformer_id 会得到「什么都没转」的假负例
        meshes = [_mesh(_VERTS, parent=deformers[0].deformer_id if deformers
                        else "")]
    return RigSpec(
        meshes=meshes,
        parameters=([ParameterSpec(_PARAM, minimum=-30.0, maximum=30.0,
                                   default=0.0)] if parameters else []),
        canvas_width=_CANVAS, canvas_height=_CANVAS, pixels_per_unit=_PPU,
        deformers=list(deformers))


def _mesh(vertices, parent=""):
    return MeshSpec(mesh_id="ArtMeshEye", vertices=list(vertices),
                    triangles=[(0, 1, 2)], uvs=list(_UVS), deformer_id=parent)


def _rotation(deformer_id, keyforms, *, base_angle=0.0, parameter=""):
    return RotationDeformerSpec(deformer_id=deformer_id, keyforms=keyforms,
                                base_angle=base_angle, parameter_id=parameter)


def _package(tmp_path: Path, doc, name: str) -> Path:
    from PIL import Image

    folder = tmp_path / name
    folder.mkdir()
    (folder / f"{name}.moc3").write_bytes(doc.to_bytes())
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for y in range(size):
        for x in range(size):
            if x + y < size:                 # 对角遮罩：让镜像/翻转无法蒙混
                img.putpixel((x, y), (255, 0, 255, 255))
    img.save(folder / "t.png")
    manifest = folder / f"{name}.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3, "Meta": {"ArchiveName": name},
        "FileReferences": {"Moc": f"{name}.moc3", "Textures": ["t.png"]},
    }), encoding="utf-8")
    return manifest


def _render(tmp_path, spec, name, params=None):
    doc = compile_static_rig(spec)
    assert lint_document(doc) == [], [str(i) for i in lint_document(doc)]
    manifest = _package(tmp_path, doc, name)
    result = render_probe(str(manifest), params)
    assert result["ok"] is True, result["blocker"]
    assert result["opaque_pixels"] > 500, (
        f"参照本身要能画出来，否则 sha 相同没有意义：{result['alpha_bbox']}")
    return result


@requires_live2d
@requires_pixels
def test_identity_rotation_is_pixel_identical_to_no_deformer(tmp_path):
    """angle=0 / origin=0 / scale=1 的中性键形必须完全不动画面。"""
    plain = _render(tmp_path, _rig(), "plain")
    identity = _render(tmp_path, _rig(deformers=[_rotation(
        "RotEye", [RotationKeyform(0.0)], )]), "identity")
    # 顶点空间：挂在 rotation 下的网格仍是模型坐标（不像 warp 那样换成 (s,t)）
    assert identity["pixels_sha256"] == plain["pixels_sha256"], (
        f"中性 rotation 改变了画面：{plain['alpha_bbox']} vs "
        f"{identity['alpha_bbox']}")


@requires_live2d
@requires_pixels
@pytest.mark.parametrize("degrees", [90.0, 30.0, -30.0, -120.0])
def test_rotation_matches_pre_rotated_geometry(tmp_path, degrees):
    """变形器转 N 度必须与「顶点预先按 CCW 转 N 度」逐像素相同。

    这一条同时钉死：角度制（不是弧度）、正方向（模型空间逆时针）、
    以及 rotation 子网格的顶点空间。
    """
    tag = str(degrees).replace("-", "m").replace(".", "p")
    reference = _render(tmp_path, _rig(meshes=[_mesh(
        _rotate(_VERTS, degrees, about=(0.0, 0.0)))]), f"ref{tag}")
    via_deformer = _render(tmp_path, _rig(deformers=[_rotation(
        "RotEye", [RotationKeyform(0.0, angle=degrees)])]), f"rot{tag}")
    assert via_deformer["pixels_sha256"] == reference["pixels_sha256"], (
        f"{degrees}° 的两种画法不同 —— 单位/朝向/顶点空间有一处不对："
        f"参照 bbox={reference['alpha_bbox']} 变形器 bbox={via_deformer['alpha_bbox']}")
    # 刚体旋转不改变面积
    assert abs(via_deformer["opaque_pixels"] - reference["opaque_pixels"]) <= 2


@requires_live2d
@requires_pixels
@pytest.mark.parametrize("degrees", [30.0, 90.0, -45.0, 120.0])
def test_rotation_about_an_offset_pivot_matches_pre_rotated_geometry(tmp_path,
                                                                    degrees):
    """绕偏移枢轴转：必须与「顶点预先绕同一点转好」逐像素相同。

    这条钉住 origin 的真实语义 —— 内核做的是 p' = R·p + offset，
    所以编译器要把枢轴换算成 (I − R)·p（``tools/probe_rotation_offset.py``）。
    角度特意覆盖 60° 以外：``I − R(A)`` 与 ``R(−A)`` 只在 cos A = 1/2 时相同，
    只测 60° 会让「写成 R(−A)」这个错误公式蒙混过关。
    """
    about = (60.0, -25.0)                     # 模型像素，两轴都非零
    tag = str(degrees).replace("-", "m").replace(".", "p")
    reference = _render(tmp_path, _rig(meshes=[_mesh(
        _rotate(_VERTS, degrees, about=about))]), f"ref_pivot{tag}")
    via_deformer = _render(tmp_path, _rig(deformers=[_rotation(
        "RotEye", [RotationKeyform(0.0, angle=degrees, origin=about)])]),
        f"rot_pivot{tag}")
    assert via_deformer["pixels_sha256"] == reference["pixels_sha256"], (
        f"绕 {about} 转 {degrees}° 不吻合：参照 bbox={reference['alpha_bbox']} "
        f"变形器 bbox={via_deformer['alpha_bbox']}")


@requires_live2d
@requires_pixels
def test_origin_is_irrelevant_at_zero_angle(tmp_path):
    """角度为 0 时不需要平移：任意枢轴都必须等于没挂变形器。"""
    plain = _render(tmp_path, _rig(), "plain")
    for origin in ((0.0, 0.0), (60.0, -25.0), (-120.0, 80.0)):
        got = _render(tmp_path, _rig(deformers=[_rotation(
            "RotEye", [RotationKeyform(0.0, angle=0.0, origin=origin)])]),
            f"zero_{abs(hash(origin))}")
        assert got["pixels_sha256"] == plain["pixels_sha256"], (
            f"origin={origin} 在中性角度下改变了画面")


@requires_live2d
@requires_pixels
def test_base_angle_shares_the_same_pivot_translation(tmp_path):
    """base_angle 参与总角度：base=30/angle=0 绕枢轴 p 必须等于 angle=30 绕 p。"""
    about = (60.0, -25.0)
    with_base = _render(tmp_path, _rig(deformers=[_rotation(
        "RotEye", [RotationKeyform(0.0, angle=0.0, origin=about)],
        base_angle=30.0)]), "with_base")
    with_angle = _render(tmp_path, _rig(deformers=[_rotation(
        "RotEye", [RotationKeyform(0.0, angle=30.0, origin=about)])]),
        "with_angle")
    assert with_base["pixels_sha256"] == with_angle["pixels_sha256"], (
        "base_angle 没进总角度：枢轴平移对不上")


@requires_live2d
@requires_pixels
def test_base_angle_and_keyform_angle_sum(tmp_path):
    """base_angle 与键形角度相加：base=30/angle=0 必须等于 base=0/angle=30。"""
    with_base = _render(tmp_path, _rig(deformers=[_rotation(
        "RotEye", [RotationKeyform(0.0, angle=0.0)], base_angle=30.0)]),
        "with_base")
    with_angle = _render(tmp_path, _rig(deformers=[_rotation(
        "RotEye", [RotationKeyform(0.0, angle=30.0)])]), "with_angle")
    assert with_base["pixels_sha256"] == with_angle["pixels_sha256"], (
        f"base_angle 不参与求和：{with_base['alpha_bbox']} vs "
        f"{with_angle['alpha_bbox']}")


@requires_live2d
@requires_pixels
def test_scale_is_a_multiplier_around_the_origin(tmp_path):
    """scale 是倍率：2.0 必须等于把顶点按同一原点放大 2 倍的参照。"""
    reference = _render(tmp_path, _rig(meshes=[_mesh(
        [(x * 2.0, y * 2.0) for x, y in _VERTS])]), "ref_scale")
    via_deformer = _render(tmp_path, _rig(deformers=[_rotation(
        "RotEye", [RotationKeyform(0.0, scale=2.0)])]), "rot_scale")
    assert via_deformer["pixels_sha256"] == reference["pixels_sha256"], (
        f"scale 语义不是倍率：{reference['alpha_bbox']} vs "
        f"{via_deformer['alpha_bbox']}")


@requires_live2d
@requires_pixels
def test_driven_rotation_rests_at_default_and_moves_both_ways(tmp_path):
    """多键形：默认键必须复现静止姿态，两端各自转到对应角度。"""
    spec = _rig(deformers=[_rotation("RotEye", [
        RotationKeyform(-30.0, angle=-30.0),
        RotationKeyform(0.0, angle=0.0),
        RotationKeyform(30.0, angle=30.0)], parameter=_PARAM)])
    plain = _render(tmp_path, _rig(), "plain_rest")
    at_default = _render(tmp_path, spec, "driven", {_PARAM: 0.0})
    at_plus = _render(tmp_path, spec, "driven_p", {_PARAM: 30.0})
    at_minus = _render(tmp_path, spec, "driven_m", {_PARAM: -30.0})
    assert at_default["pixels_sha256"] == plain["pixels_sha256"], (
        "参数取默认值时画面变了 —— 静止形不是默认键")
    ref_plus = _render(tmp_path, _rig(meshes=[_mesh(
        _rotate(_VERTS, 30.0))]), "ref_p")
    ref_minus = _render(tmp_path, _rig(meshes=[_mesh(
        _rotate(_VERTS, -30.0))]), "ref_m")
    assert at_plus["pixels_sha256"] == ref_plus["pixels_sha256"]
    assert at_minus["pixels_sha256"] == ref_minus["pixels_sha256"]


@requires_live2d
def test_rotation_moc3_is_accepted_by_official_core(tmp_path):
    doc = compile_static_rig(_rig(deformers=[_rotation(
        "RotEye", [RotationKeyform(0.0, angle=30.0)])]))
    assert lint_document(doc) == []
    moc3 = tmp_path / "rot.moc3"
    moc3.write_bytes(doc.to_bytes())
    result = verify_moc3_consistency(str(moc3))
    assert result["ok"] is True, (
        f"带 rotation 变形器的 moc3 被官方内核拒绝: {result['blocker']}\n"
        f"stdout={result['stdout']}")
