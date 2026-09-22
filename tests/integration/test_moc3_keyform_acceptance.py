"""参数形变键型的官方内核验收：编译产物必须真的让画面动起来。

判据 1-3（一致性 / 加载 / 参数表）只需 live2d-py；判据 4 需要可用的
OpenGL 上下文，按本仓库惯例用 LIVE2D_TEST_PIXELS=1 显式开启。

负对照同样重要：只声明参数、不带键型的包必须**通不过**像素验证，
否则说明该测试无法区分「真的形变」与「什么都没发生」。
"""
import json
import os
from pathlib import Path

import pytest

from drivers.live2d_runtime.moc3_verify import (
    render_probe,
    verify_moc3_consistency,
    verify_moc3_load,
    verify_moc3_runtime,
)
from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_lint import lint_document
from live2d_builder.exporter.moc3_model import (
    KeyformShape,
    MeshSpec,
    ParameterSpec,
    RigSpec,
    compile_static_rig,
)

_CANVAS = 512.0
_PPU = 100.0
_HALF = 100.0          # 方块半边长（画布像素）
_SHIFT = 100.0         # 参数到极值时的水平位移（画布像素）


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


def _square(cx: float, cy: float, half: float = _HALF):
    verts = [(cx - half, cy - half), (cx + half, cy - half),
             (cx + half, cy + half), (cx - half, cy + half)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    return verts, uvs, [(0, 1, 2), (0, 2, 3)]


def _rig(deform: bool):
    """deform=False 时只声明参数（静态路径），用作负对照。"""
    verts, uvs, tris = _square(0.0, 0.0)
    shapes = ()
    if deform:
        shapes = tuple(
            KeyformShape(key_value=key,
                         vertices=[(x + key / 30.0 * _SHIFT, y)
                                   for x, y in verts])
            for key in (-30.0, 0.0, 30.0)
        )
    mesh = MeshSpec(
        mesh_id="ArtMeshTest", vertices=verts, triangles=tris, uvs=uvs,
        keyform_parameter_id="ParamAngleX" if deform else "",
        keyform_shapes=shapes,
    )
    return RigSpec(
        meshes=[mesh],
        parameters=[ParameterSpec("ParamAngleX", minimum=-30.0, maximum=30.0,
                                  default=0.0)],
        canvas_width=_CANVAS, canvas_height=_CANVAS, pixels_per_unit=_PPU)


def _package(tmp_path: Path, doc, name: str) -> Path:
    """不透明纯色贴图的完整 model3 包，供官方内核加载并绘制。"""
    from PIL import Image

    folder = tmp_path / name
    folder.mkdir()
    (folder / f"{name}.moc3").write_bytes(doc.to_bytes())
    Image.new("RGBA", (64, 64), (255, 0, 255, 255)).save(
        folder / "texture_00.png")
    manifest = folder / f"{name}.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3,
        "Meta": {"ArchiveName": name},
        "FileReferences": {
            "Moc": f"{name}.moc3",
            "Textures": ["texture_00.png"],
        },
    }), encoding="utf-8")
    return manifest


_EYE_KEYS = (-30.0, 0.0, 30.0)
_STEP_X = 50.0         # 每上一档 X 键的水平位移（画布像素）
_STEP_Y = 40.0         # 每上一档 Y 键的垂直位移


def _two_axis_rig():
    """一个网格由 ParamEyeBallX / ParamEyeBallY **两轴**驱动（各 3 键 → 9 个键形）。

    键形按**展平序** ``index = i_X + 3*i_Y`` 排列 —— 即带的 binding 列表里
    ``ParamEyeBallX`` 在前、步长为 1（F-06 实测定论）。第 (i_X, i_Y) 个键形把方块
    平移 (i_X*50, i_Y*40)，于是"内核选了哪个键形"可以从画面上读出来。
    """
    verts, uvs, tris = _square(0.0, 0.0)
    shapes = []
    for i_y in range(len(_EYE_KEYS)):
        for i_x in range(len(_EYE_KEYS)):
            shapes.append(KeyformShape(
                key_value=float(i_x + len(_EYE_KEYS) * i_y),   # 展平序号（仅备注）
                vertices=[(x + i_x * _STEP_X, y + i_y * _STEP_Y)
                          for x, y in verts]))
    mesh = MeshSpec(
        mesh_id="ArtMeshEye", vertices=verts, triangles=tris, uvs=uvs,
        keyform_shapes=shapes,
        keyform_axes=(("ParamEyeBallX", _EYE_KEYS), ("ParamEyeBallY", _EYE_KEYS)),
    )
    return RigSpec(
        meshes=[mesh],
        parameters=[
            ParameterSpec("ParamEyeBallX", minimum=-30.0, maximum=30.0,
                          default=0.0),
            ParameterSpec("ParamEyeBallY", minimum=-30.0, maximum=30.0,
                          default=0.0),
        ],
        canvas_width=_CANVAS, canvas_height=_CANVAS, pixels_per_unit=_PPU)


def _shifted_static_rig(shift_x: float, shift_y: float):
    """参照：**不带任何键形**、顶点已按期望姿态平移好的网格。

    仍须声明参数（本模型没有绑定）：``render_probe`` 对"没有任何原生参数"的模型会
    直接拒绝验收（"No native parameters loaded"）。
    """
    verts, uvs, tris = _square(0.0, 0.0)
    mesh = MeshSpec(
        mesh_id="ArtMeshTest", triangles=tris, uvs=uvs,
        vertices=[(x + shift_x, y + shift_y) for x, y in verts])
    return RigSpec(meshes=[mesh], parameters=[
        ParameterSpec("ParamEyeBallX", minimum=-30.0, maximum=30.0, default=0.0),
        ParameterSpec("ParamEyeBallY", minimum=-30.0, maximum=30.0, default=0.0),
    ], canvas_width=_CANVAS, canvas_height=_CANVAS, pixels_per_unit=_PPU)


@requires_live2d
def test_two_axis_band_is_one_band_with_two_bindings(tmp_path):
    """两轴（眼球 X+Y）必须写成一个**引用 2 个 binding 的带**，且内核接受。

    单参数带是只含一个 binding 的带，所以旧的单轴路径字节不变；这里验证新增的多轴。
    """
    doc = compile_static_rig(_two_axis_rig())
    assert lint_document(doc) == [], [str(i) for i in lint_document(doc)]
    assert doc.get("keyform_binding_band.counts") == [0, 2]
    assert doc.get("keyform_binding_index.indices") == [0, 1]
    assert doc.get("keyform_binding.keys_counts") == [3, 3]
    assert doc.counts[ms.CountIdx.KEYFORM_BINDINGS] == 2
    assert doc.get("art_mesh.keyform_counts") == [9]      # 3 x 3
    moc3 = tmp_path / "twoaxis.moc3"
    moc3.write_bytes(doc.to_bytes())
    result = verify_moc3_consistency(str(moc3))
    assert result["ok"] is True, result["blocker"]


@requires_live2d
@requires_pixels
def test_two_axis_band_follows_the_flattened_axis_order(tmp_path):
    """官方内核按 ``index = i_X + 3*i_Y`` 选键形 —— 轴序搞反则 sha 立刻不同。

    判据与旋转验收同源：与「顶点已按期望姿态平移好的无键形网格」逐像素比对。
    """
    doc = compile_static_rig(_two_axis_rig())
    manifest = _package(tmp_path, doc, "twoaxis")

    def reference(name, shift_x, shift_y):
        ref_doc = compile_static_rig(_shifted_static_rig(shift_x, shift_y))
        ref = render_probe(str(_package(tmp_path, ref_doc, name)))
        assert ref["ok"] is True, ref["blocker"]
        return ref["pixels_sha256"]

    # (i_X=2, i_Y=1) -> (100, 40)：X 取最大键、Y 取中间键
    got = render_probe(str(manifest),
                       {"ParamEyeBallX": 30.0, "ParamEyeBallY": 0.0})
    assert got["ok"] is True, got["blocker"]
    want = reference("ref_xy", 2 * _STEP_X, 1 * _STEP_Y)
    assert got["pixels_sha256"] == want

    # (i_X=1, i_Y=2) -> (50, 80)：X 取中间键、Y 取最大键
    got2 = render_probe(str(manifest),
                        {"ParamEyeBallX": 0.0, "ParamEyeBallY": 30.0})
    assert got2["ok"] is True, got2["blocker"]
    want2 = reference("ref_yx", 1 * _STEP_X, 2 * _STEP_Y)
    assert got2["pixels_sha256"] == want2

    # 两个期望姿态本身必须可区分，否则该测试无法证伪轴序
    assert want != want2


@requires_live2d
def test_keyform_moc3_is_accepted_by_official_core(tmp_path):
    doc = compile_static_rig(_rig(deform=True))
    assert lint_document(doc) == []
    moc3 = tmp_path / "keyform.moc3"
    moc3.write_bytes(doc.to_bytes())
    result = verify_moc3_consistency(str(moc3))
    assert result["ok"] is True, (
        f"带键型的 moc3 被官方内核拒绝: {result['blocker']}\n"
        f"stdout={result['stdout']}")


@requires_live2d
def test_keyform_package_loads_and_exposes_parameter(tmp_path):
    doc = compile_static_rig(_rig(deform=True))
    manifest = _package(tmp_path, doc, "keyform")
    result = verify_moc3_load(str(manifest))
    assert result["ok"] is True, (
        f"加载失败: {result['blocker']}\n{result['stdout']}\n{result['stderr']}")
    assert result["parameter_ids"] == ["ParamAngleX"]


@requires_live2d
@requires_pixels
def test_keyforms_actually_move_pixels_in_official_core(tmp_path):
    """判据 4：ParamAngleX 到两端时画面必须发生大范围、可复现的变化。"""
    doc = compile_static_rig(_rig(deform=True))
    manifest = _package(tmp_path, doc, "keyform")
    evidence = tmp_path / "evidence"
    result = verify_moc3_runtime(str(manifest), evidence_dir=str(evidence))
    assert result["ok"] is True, (
        f"受控像素验证未通过: {result['blocker']}\n"
        f"checks={result['checks']}\nstdout={result['stdout']}\n"
        f"stderr={result['stderr']}")
    checks = {c["parameter"]: c for c in result["checks"]}
    angle = checks["ParamAngleX"]
    assert angle["passed"] is True, angle
    # 位移 200 画布像素在 400x500 视口下应有上万像素级的差异
    assert angle["changed_pixels"] > 5000, angle
    assert angle["control_drift"] == 0, angle
    assert angle["restore_error"] == 0, angle
    assert (evidence / "ParamAngleX-min.png").is_file()
    assert (evidence / "ParamAngleX-max.png").is_file()


@requires_live2d
@requires_pixels
def test_static_package_fails_pixel_verification(tmp_path):
    """负对照：没有键形的包即使声明了同一参数，也不能通过像素验证。"""
    doc = compile_static_rig(_rig(deform=False))
    assert lint_document(doc) == []
    manifest = _package(tmp_path, doc, "static")
    result = verify_moc3_runtime(str(manifest))
    assert result["ok"] is False, (
        f"静态包竟然通过了像素验证，说明测试无效: {result['checks']}")
    checks = {c["parameter"]: c for c in result["checks"]}
    assert checks["ParamAngleX"]["passed"] is False, checks["ParamAngleX"]
    assert checks["ParamAngleX"]["changed_pixels"] < 5000, checks


def _multi_binding_rig(static_control: bool = False):
    """同一参数、两种键值布局各驱动一个网格（多 binding），外加一个静态网格。

    static_control=True 时同样的网格不带键形，用作像素负对照。
    """
    outer, uvs_o, tris_o = _square(0.0, 0.0, half=120.0)
    inner, uvs_i, tris_i = _square(0.0, 0.0, half=60.0)
    plain, uvs_p, tris_p = _square(0.0, 0.0, half=30.0)
    if static_control:
        meshes = [MeshSpec("ArtMeshOuter", outer, tris_o, uvs_o),
                  MeshSpec("ArtMeshInner", inner, tris_i, uvs_i),
                  MeshSpec("ArtMeshPlain", plain, tris_p, uvs_p)]
    else:
        meshes = [
            MeshSpec("ArtMeshOuter", outer, tris_o, uvs_o,
                     keyform_parameter_id="ParamAngleX",
                     keyform_shapes=tuple(
                         KeyformShape(k, [(x + k / 30.0 * 100.0, y)
                                          for x, y in outer])
                         for k in (-30.0, 0.0, 30.0))),
            MeshSpec("ArtMeshInner", inner, tris_i, uvs_i,
                     keyform_parameter_id="ParamAngleX",
                     keyform_shapes=tuple(
                         KeyformShape(k, [(x + k * 40.0, y) for x, y in inner])
                         for k in (0.0, 1.0))),
            MeshSpec("ArtMeshPlain", plain, tris_p, uvs_p),
        ]
    return RigSpec(meshes=meshes,
                   parameters=[ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0)],
                   canvas_width=_CANVAS, canvas_height=_CANVAS,
                   pixels_per_unit=_PPU)


@requires_live2d
def test_multi_binding_rig_is_accepted_by_official_core(tmp_path):
    doc = compile_static_rig(_multi_binding_rig())
    assert doc.counts[ms.CountIdx.KEYFORM_BINDINGS] == 2
    assert doc.get("parameter.keyform_binding_counts") == [2]
    assert lint_document(doc) == []
    moc3 = tmp_path / "multi.moc3"
    moc3.write_bytes(doc.to_bytes())
    result = verify_moc3_consistency(str(moc3))
    assert result["ok"] is True, (
        f"同参数多 binding 的结构被官方内核拒绝: {result['blocker']}\n"
        f"stdout={result['stdout']}")


@requires_live2d
def test_multi_binding_rig_loads_in_official_core(tmp_path):
    doc = compile_static_rig(_multi_binding_rig())
    manifest = _package(tmp_path, doc, "multi")
    result = verify_moc3_load(str(manifest))
    assert result["ok"] is True, f"加载失败: {result['blocker']}\n{result['stderr']}"
    assert result["parameter_ids"] == ["ParamAngleX"]


@requires_live2d
@requires_pixels
def test_multi_binding_rig_deforms_pixels(tmp_path):
    """多 binding 结构在官方内核里同样要能改变画面。

    几何按单位坐标（画布像素 / pixels_per_unit）书写，因此在运行时里
    是正常尺寸；负对照用同样的网格去掉键形。
    """
    doc = compile_static_rig(_multi_binding_rig())
    manifest = _package(tmp_path, doc, "multi")
    result = verify_moc3_runtime(str(manifest))
    assert result["ok"] is True, (
        f"多 binding 模型未通过受控像素验证: {result['blocker']}\n"
        f"checks={result['checks']}")


@requires_live2d
@requires_pixels
def test_multi_binding_static_control_fails_pixels(tmp_path):
    """同样的网格去掉键形后不得通过 —— 证明响应来自键形而非模型本身。"""
    doc = compile_static_rig(_multi_binding_rig(static_control=True))
    manifest = _package(tmp_path, doc, "multi_static")
    result = verify_moc3_runtime(str(manifest))
    assert result["ok"] is False, result["checks"]
