"""warp 变形器的官方内核验收：恒等必须逐像素不变，参数驱动必须移动画面。

三条实测出来的约定在这里被钉住：
1. 挂在 warp 下的网格，顶点存的是网格内的 (s, t)（官方 Haru：warp 下 80 个网格
   顶点全在 ~[0,1]，rotation 下的 4 个在模型空间）；
2. V3_03 文档的 deformer 表非空时 additional 段必须非空，否则内核判
   "Header section is invalid"；
3. 静止形 = 参数默认值对应的那一形控制网格。
复现脚本：tools/probe_warp_st_identity.py、tools/probe_additional_rule.py。
"""
import json
import os
from pathlib import Path

import pytest

from drivers.live2d_runtime.moc3_verify import (
    render_probe,
    verify_moc3_consistency,
    verify_moc3_load,
)
from live2d_builder.exporter.moc3_lint import lint_document
from live2d_builder.exporter.moc3_model import (
    DeformerGrid,
    MeshSpec,
    ParameterSpec,
    RigSpec,
    WarpDeformerSpec,
    compile_static_rig,
)

_CANVAS = 512.0
_PPU = 100.0
_HALF_X, _HALF_Y = 100.0, 50.0     # 矩形半边长：两轴不等，才能暴露行列/朝向搞反
_GRID_X, _GRID_Y = 200.0, 160.0    # 控制网格半边长，比矩形大一圈
_NX, _NY = 3, 4                    # 网格顶点列数 / 行数（非方形）
_SHIFT = 80.0                      # 参数到极值时网格的水平位移
_PARAM = "ParamAngleZ"


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


def _rect():
    verts = [(-_HALF_X, -_HALF_Y), (_HALF_X, -_HALF_Y),
             (_HALF_X, _HALF_Y), (-_HALF_X, _HALF_Y)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    return verts, uvs, [(0, 1, 2), (0, 2, 3)]


def _lattice(dx=0.0):
    """行优先（x 变化最快）、y 自下而上 —— 与内核一致的点序。"""
    step_x, step_y = 2 * _GRID_X / (_NX - 1), 2 * _GRID_Y / (_NY - 1)
    return [(c * step_x - _GRID_X + dx, r * step_y - _GRID_Y)
            for r in range(_NY) for c in range(_NX)]


def _rig(keys=None):
    """keys=None 不挂变形器；否则按参数键值生成控制网格（随键值水平位移）。"""
    grids = [DeformerGrid(key, _lattice(dx=key / 30.0 * _SHIFT))
             for key in (keys or ())]
    verts, uvs, tris = _rect()
    mesh = MeshSpec(mesh_id="ArtMeshWarp", vertices=verts, triangles=tris,
                    uvs=uvs, deformer_id="WarpHead" if grids else "")
    deformers = ([WarpDeformerSpec(deformer_id="WarpHead", rows=_NY - 1,
                                   cols=_NX - 1, grids=grids,
                                   parameter_id=_PARAM if len(grids) > 1 else "")]
                 if grids else [])
    params = [ParameterSpec(_PARAM, minimum=-30.0, maximum=30.0, default=0.0)]
    return RigSpec(meshes=[mesh], parameters=params, deformers=deformers,
                   canvas_width=_CANVAS, canvas_height=_CANVAS,
                   pixels_per_unit=_PPU)


def _package(tmp_path: Path, doc, name: str) -> Path:
    from PIL import Image

    folder = tmp_path / name
    folder.mkdir()
    (folder / f"{name}.moc3").write_bytes(doc.to_bytes())
    Image.new("RGBA", (64, 64), (255, 0, 255, 255)).save(folder / "texture_00.png")
    manifest = folder / f"{name}.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3,
        "Meta": {"ArchiveName": name},
        "FileReferences": {"Moc": f"{name}.moc3",
                           "Textures": ["texture_00.png"]},
    }), encoding="utf-8")
    return manifest


@requires_live2d
def test_static_warp_is_accepted_by_official_core(tmp_path):
    doc = compile_static_rig(_rig((0.0,)))
    assert lint_document(doc) == []
    moc3 = tmp_path / "warp.moc3"
    moc3.write_bytes(doc.to_bytes())
    result = verify_moc3_consistency(str(moc3))
    assert result["ok"] is True, (
        f"带 warp 变形器的 moc3 被官方内核拒绝: {result['blocker']}\n"
        f"stdout={result['stdout']}")


@requires_live2d
def test_warp_package_loads_in_official_core(tmp_path):
    doc = compile_static_rig(_rig((0.0,)))
    result = verify_moc3_load(str(_package(tmp_path, doc, "warp")))
    assert result["ok"] is True, (
        f"加载失败: {result['blocker']}\n{result['stdout']}\n{result['stderr']}")


@requires_live2d
def test_empty_additional_section_with_warp_is_rejected(tmp_path):
    """负对照：删掉 additional 段，内核必须拒绝 —— 钉住我们实测到的那条约束。"""
    doc = compile_static_rig(_rig((0.0,)))
    doc.set("additional.quad_transforms", [])
    moc3 = tmp_path / "no_additional.moc3"
    moc3.write_bytes(doc.to_bytes())
    result = verify_moc3_consistency(str(moc3))
    assert result["ok"] is False, "additional 段为空却被内核接受了，该规则已不成立"


@requires_live2d
@requires_pixels
def test_identity_warp_renders_exactly_like_no_warp(tmp_path):
    """恒等控制网格 + (s, t) 顶点必须与「没有变形器」逐像素相同。"""
    plain = _package(tmp_path, compile_static_rig(_rig()), "plain")
    warp = _package(tmp_path, compile_static_rig(_rig((0.0,))), "warp")
    a = render_probe(str(plain), {_PARAM: 0.0})
    b = render_probe(str(warp), {_PARAM: 0.0})
    assert a["ok"] and b["ok"], (a["blocker"], b["blocker"])
    assert a["pixels_sha256"] == b["pixels_sha256"], (
        f"恒等变形器改变了画面：{a['alpha_bbox']} vs {b['alpha_bbox']}")
    assert a["opaque_pixels"] > 1000, a["alpha_bbox"]


@requires_live2d
@requires_pixels
def test_driven_warp_moves_the_mesh(tmp_path):
    """静止形取参数默认值那一形：默认值下画面不变，极值下整体平移。"""
    doc = compile_static_rig(_rig((-30.0, 0.0, 30.0)))
    manifest = _package(tmp_path, doc, "driven")
    rest = render_probe(str(manifest), {_PARAM: 0.0})
    moved = render_probe(str(manifest), {_PARAM: 30.0})
    assert rest["ok"] and moved["ok"], (rest["blocker"], moved["blocker"])

    plain = render_probe(str(_package(tmp_path, compile_static_rig(_rig()),
                                      "plain2")), {_PARAM: 0.0})
    assert rest["pixels_sha256"] == plain["pixels_sha256"], (
        "参数默认值下的画面与无变形器不同 —— 静止形不是默认值那一形")
    assert moved["pixels_sha256"] != rest["pixels_sha256"]
    # 网格右移 80 画布像素 = 0.8 单位，视口里应为正方向几十像素的质心位移
    assert moved["centroid"][0] > rest["centroid"][0] + 20, (
        rest["centroid"], moved["centroid"])
    assert abs(moved["centroid"][1] - rest["centroid"][1]) < 1.0
    # 纯平移不改变面积；只允许亚像素取边带来的少量差异
    assert abs(moved["opaque_pixels"] - rest["opaque_pixels"]) \
        < rest["opaque_pixels"] * 0.02
