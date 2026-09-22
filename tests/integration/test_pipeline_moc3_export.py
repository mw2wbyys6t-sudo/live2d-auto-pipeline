"""端到端：真实导出目录里产出可被官方内核加载的 moc3。

覆盖三件事：
1. pipeline 桥接能从合成管线产物编译出 moc3，官方一致性通过；
2. 官方内核能加载 Model3Exporter 写出的**完整** model3.json
   （含 PreviewLayers 等非标准键，证明清单本身兼容）；
3. 输入含 warp 变形器时真的编译进 moc3 并通过官方内核；无法表达的变形器
   （rotation、无成员网格）如实标注 runtime_ready=False。
"""
from pathlib import Path

import pytest
from PIL import Image

from drivers.live2d_runtime.moc3_verify import (
    render_probe, verify_moc3_consistency, verify_moc3_load,
)
from live2d_builder.exporter.model3_exporter import Model3Exporter
from live2d_builder.exporter.moc3_pipeline import compile_export_moc3

import importlib.util
import os


def _has_live2d() -> bool:
    """live2d-py 是否可用。

    注意：`find_spec` 对点号名（如 "live2d.v3"）在父包缺失时会抛
    ModuleNotFoundError，而不是返回 None；必须捕获，否则本模块会在**收集期**
    报错，而不是被跳过。
    """
    try:
        return importlib.util.find_spec("live2d.v3") is not None
    except (ImportError, ValueError):
        return False


requires_live2d = pytest.mark.skipif(
    not _has_live2d(), reason="需要 live2d-py 运行时（pip install live2d-py）")
requires_pixels = pytest.mark.skipif(
    os.environ.get("LIVE2D_TEST_PIXELS") != "1",
    reason="需要 OpenGL：设 LIVE2D_TEST_PIXELS=1 开启真实渲染验收")

# 本文件全部用例都要经过官方 Cubism Core（compile_export_moc3 / verify_moc3_load /
# render_probe）；缺运行时的环境一律跳过，绝不在收集期报错。
pytestmark = requires_live2d


def _layer(width=128, height=128):
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for y in range(height // 3, 2 * height // 3):
        for x in range(width // 3, 2 * width // 3):
            img.putpixel((x, y), (200, 80, 80, 255))
    return img


def _mesh(name, width=128.0, height=128.0):
    # 十字两片三角，覆盖图层中心有内容的区域
    verts = [[32.0, 32.0], [96.0, 32.0], [96.0, 96.0], [32.0, 96.0]]
    norm = [[x / width, y / height] for x, y in verts]
    return {
        "vertices": verts,
        "vertices_norm": norm,
        "indices": [[0, 1, 2], [0, 2, 3]],
        "width": width,
        "height": height,
    }


def _builder_result(deformers=()):
    return {
        "layers": {"hair": _layer(), "face": _layer()},
        "meshes": {"hair": _mesh("hair"), "face": _mesh("face")},
        "deformer_tree": {"deformers": list(deformers)},
        "parameters": {"cubism_params": [
            {"Id": "ParamAngleX", "Min": -30, "Max": 30, "Value": 0},
            {"Id": "ParamMouthOpenY", "Min": 0, "Max": 1, "Value": 0},
        ]},
        "physics3": None,
    }


def _export(tmp_path: Path, builder_result):
    exporter = Model3Exporter(max_atlas_size=256)
    return exporter, exporter.export(
        builder_result=builder_result,
        output_dir=str(tmp_path / "export"),
        character_name="web_preview",
    )


def test_export_writes_official_accepted_moc3(tmp_path):
    builder_result = _builder_result()
    _, export_result = _export(tmp_path, builder_result)
    result = compile_export_moc3(
        builder_result=builder_result,
        atlas_uvs=export_result["atlas_uvs"],
        output_dir=export_result["output_dir"],
        character_name="web_preview",
    )
    assert result["moc3_written"] is True, result["blocker"]
    assert result["runtime_ready"] is True, result["blocker"]
    assert result["dropped"] == []
    assert result["consistency"]["ok"] is True, result["consistency"]
    assert Path(result["moc3_path"]).is_file()
    assert result["bytes"] > 0


def test_full_model3_manifest_loads_in_official_core(tmp_path):
    """model3.json 引用的 Moc 不再是占位符，且整份清单能被官方加载。"""
    builder_result = _builder_result()
    _, export_result = _export(tmp_path, builder_result)
    compile_export_moc3(
        builder_result=builder_result,
        atlas_uvs=export_result["atlas_uvs"],
        output_dir=export_result["output_dir"],
        character_name="web_preview",
    )
    result = verify_moc3_load(export_result["model3_json"])
    assert result["ok"] is True, (
        f"完整清单加载失败: {result['blocker']}\nstdout={result['stdout']}")
    assert set(result["parameter_ids"]) == {"ParamAngleX", "ParamMouthOpenY"}


def test_warp_deformers_compile_into_the_exported_moc3(tmp_path):
    """管线的 warp 变形器必须真的进 deformer 表，并被官方内核接受。"""
    _core = pytest.importorskip("moc3._core")

    deformer = {"name": "HairSwing", "type": "warp", "targets": ["hair"],
                "grid_rows": 3, "grid_cols": 2}
    builder_result = _builder_result(deformers=[deformer])
    _, export_result = _export(tmp_path, builder_result)
    result = compile_export_moc3(
        builder_result=builder_result,
        atlas_uvs=export_result["atlas_uvs"],
        output_dir=export_result["output_dir"],
        character_name="web_preview",
    )
    assert result["moc3_written"] is True, result["blocker"]
    assert result["deformers_compiled"] == ["HairSwing"]
    assert result["deformers_uncompiled"] == []
    assert result["dropped"] == []
    assert result["consistency"]["ok"] is True, result["consistency"]
    assert result["runtime_ready"] is True, result["blocker"]

    doc = _core.Moc3.from_file(result["moc3_path"])
    assert doc.counts[_core.CountIdx.DEFORMERS] == 1
    assert doc.get("deformer.ids") == ["HairSwing"]
    assert doc.get("deformer.types") == [0]
    assert doc.get("warp_deformer.rows") == [2]        # 格子数 = 顶点行数 - 1
    assert doc.get("warp_deformer.cols") == [1]
    assert doc.get("warp_deformer.vertex_counts") == [6]
    # 成员网格挂上变形器，且顶点被换算成网格内的 (s, t)
    parent = doc.get("art_mesh.parent_deformer_indices")
    assert parent == [0, -1] or parent == [-1, 0], parent
    hair = parent.index(0)
    begins = doc.get("art_mesh_keyform.keyform_position_begin_indices")
    count = doc.get("art_mesh.position_index_counts")[hair]
    pool = doc.get("keyform_position.xys")
    stored = pool[begins[hair]:begins[hair] + 2 * count]
    assert min(stored) >= 0.0 and max(stored) <= 1.0, stored


def test_rotation_deformer_compiles_as_static_and_is_labelled(tmp_path):
    """rotation 变形器现在会编译（静态姿态），但必须被如实标成「静态、不随参数动」。

    它不再产生 `dropped: ["deformers"]` —— 那件事曾经把整份真实导出压在
    runtime_ready=false 上。若哪天它又不见了或被当成已落地，这条会失败。
    """
    deformer = {"name": "eye_L", "type": "rotation", "targets": ["face"],
                "pivot": [0.0, 0.0], "angle": 0.0}
    builder_result = _builder_result(deformers=[deformer])
    _, export_result = _export(tmp_path, builder_result)
    result = compile_export_moc3(
        builder_result=builder_result,
        atlas_uvs=export_result["atlas_uvs"],
        output_dir=export_result["output_dir"],
        character_name="web_preview",
    )
    assert result["moc3_written"] is True, result["blocker"]
    assert result["deformers_compiled"] == ["eye_L"]
    assert result["deformers_static"] == ["eye_L"]
    assert result["deformers_uncompiled"] == []
    assert result["dropped"] == []
    assert result["runtime_ready"] is True, result["blocker"]

    Moc3 = pytest.importorskip("moc3").Moc3
    doc = Moc3.from_file(result["moc3_path"])
    assert doc.get("deformer.types") == [1]                  # 1 = rotation
    assert doc.get("rotation_deformer.keyform_counts") == [1]
    assert doc.get("rotation_deformer_keyform.angles") == [0.0]
    # 恒等姿态不得改变画面：见
    # tests/integration/test_moc3_rotation_acceptance.py 的逐像素比对
    assert doc.get("rotation_deformer_keyform.origin_xs") == [0.0]
    assert doc.get("rotation_deformer_keyform.origin_ys") == [0.0]


def test_unmappable_deformers_are_flagged_not_silently_dropped(tmp_path):
    """既认不出的类型、又没有成员网格的变形器：必须写清原因并压住 runtime_ready。"""
    builder_result = _builder_result(deformers=[
        {"name": "Glue", "type": "glue", "targets": ["face"]},
        {"name": "Orphan", "type": "warp", "targets": ["missing_layer"]}])
    _, export_result = _export(tmp_path, builder_result)
    result = compile_export_moc3(
        builder_result=builder_result,
        atlas_uvs=export_result["atlas_uvs"],
        output_dir=export_result["output_dir"],
        character_name="web_preview",
    )
    assert result["moc3_written"] is True
    assert result["deformers_compiled"] == []
    assert result["deformers_uncompiled"] == [
        "Glue(type=glue)", "Orphan(没有可挂的成员网格)"]
    assert result["dropped"] == ["deformers"]
    assert result["runtime_ready"] is False
    assert "变形器" in result["blocker"]


def test_package_without_motion_reports_motion_as_not_applicable(tmp_path):
    """没有可动参数 → 没有 motion → motion_verified 必须是 None，不算通过。"""
    builder_result = _builder_result()
    _, export_result = _export(tmp_path, builder_result)
    compiled = compile_export_moc3(
        builder_result=builder_result, atlas_uvs=export_result["atlas_uvs"],
        output_dir=export_result["output_dir"], character_name="web_preview")
    assert compiled["motion_verified"] is None, compiled


def test_unplayable_motion_blocks_runtime_ready(tmp_path, monkeypatch):
    """清单声明了 motion 但内核不播：必须报阻塞，不能只报「一致性通过」。"""
    import json

    from live2d_builder.exporter import moc3_pipeline

    builder_result = _builder_result()
    _, export_result = _export(tmp_path, builder_result)
    manifest = Path(export_result["model3_json"])
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["FileReferences"]["Motions"] = {"idle": [
        {"File": "motions/whatever.motion3.json"}]}
    manifest.write_text(json.dumps(document), encoding="utf-8")

    monkeypatch.setattr(
        moc3_pipeline, "verify_motion_playback",
        lambda *a, **k: {"ok": False, "blocker": "motion 未被官方内核播放：0/1",
                         "curves": {}})
    compiled = moc3_pipeline.compile_export_moc3(
        builder_result=builder_result, atlas_uvs=export_result["atlas_uvs"],
        output_dir=export_result["output_dir"], character_name="web_preview")
    assert compiled["moc3_written"] is True
    assert compiled["runtime_ready"] is False
    assert "motion" in compiled["blocker"], compiled["blocker"]
    assert compiled["motion_verified"] is None


def test_playable_motion_is_recorded_as_verified(tmp_path, monkeypatch):
    """内核确实在播 → motion_verified=True，且不会因此被判不可部署。"""
    import json

    from live2d_builder.exporter import moc3_pipeline

    builder_result = _builder_result()
    _, export_result = _export(tmp_path, builder_result)
    manifest = Path(export_result["model3_json"])
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["FileReferences"]["Motions"] = {"idle": [
        {"File": "motions/whatever.motion3.json"}]}
    manifest.write_text(json.dumps(document), encoding="utf-8")

    monkeypatch.setattr(
        moc3_pipeline, "verify_motion_playback",
        lambda *a, **k: {"ok": True, "blocker": None, "curves": {"ParamAngleX": {
            "moved": True, "observed_range": 60.0, "max_abs_error": 0.0}}})
    compiled = moc3_pipeline.compile_export_moc3(
        builder_result=builder_result, atlas_uvs=export_result["atlas_uvs"],
        output_dir=export_result["output_dir"], character_name="web_preview")
    assert compiled["motion_verified"] is True, compiled["blocker"]
    assert compiled["runtime_ready"] is True, compiled["blocker"]


def _declare_physics(tmp_path, export_result, builder_result):
    """让合成包真的声明一条两端都已配置的物理链（导出器会把引用缺失参数的链剪掉）。

    不注入的话 `_declares_physics` 会是 False，门禁根本不该触发 —— 那也是一种通过，
    但就测不到「有物理却不动」这条路了。
    """
    import json
    from pathlib import Path

    folder = Path(export_result["output_dir"])
    (folder / "p.physics3.json").write_text(json.dumps({
        "Version": 3, "Meta": {"PhysicsSettingCount": 1},
        "PhysicsSettings": [{
            "Id": "Hair", "Name": "Hair",
            "Input": [{"Source": {"Target": "Parameter", "Id": "ParamAngleX"},
                       "Weight": 60, "Type": "X", "Reflect": False}],
            "Output": [{"Destination": {"Target": "Parameter",
                                        "Id": "ParamMouthOpenY"},
                        "VertexIndex": 1, "Scale": 1.0, "Weight": 100,
                        "Type": "Angle", "Reflect": False}],
            "Vertices": [
                {"Position": {"X": 0, "Y": 0}, "Mobility": 1, "Delay": 1,
                 "Acceleration": 1, "Radius": 0},
                {"Position": {"X": 0, "Y": 8}, "Mobility": 0.95, "Delay": 0.8,
                 "Acceleration": 1.5, "Radius": 8}],
            "Normalization": {"Position": {"Minimum": -10, "Default": 0,
                                           "Maximum": 10},
                              "Angle": {"Minimum": -10, "Default": 0,
                                        "Maximum": 10}},
        }],
    }), encoding="utf-8")
    manifest = Path(export_result["model3_json"])
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    doc["FileReferences"]["Physics"] = "p.physics3.json"
    manifest.write_text(json.dumps(doc), encoding="utf-8")


def test_dead_physics_blocks_runtime_ready(tmp_path, monkeypatch):
    """physics3 存在且被加载，但内核里不产生运动：必须压住 runtime_ready。

    这不是假想：输出 Type 写成 "X" 时内核会静默丢弃整条链，
    参数表、加载、一致性全绿而物理一动不动。
    """
    from live2d_builder.exporter import moc3_pipeline

    builder_result = _builder_result()
    _, export_result = _export(tmp_path, builder_result)
    _declare_physics(tmp_path, export_result, builder_result)
    monkeypatch.setattr(
        moc3_pipeline, "verify_physics_playback",
        lambda *a, **k: {"ok": False, "chains": [],
                         "blocker": "physics 未产生带滞后的可回摆运动："
                                    "['ParamAngleX->ParamHairSwing']"})
    compiled = moc3_pipeline.compile_export_moc3(
        builder_result=builder_result, atlas_uvs=export_result["atlas_uvs"],
        output_dir=export_result["output_dir"], character_name="web_preview")
    assert compiled["moc3_written"] is True
    assert compiled["runtime_ready"] is False
    assert "physics" in compiled["blocker"], compiled["blocker"]


def test_working_physics_is_recorded_as_verified(tmp_path, monkeypatch):
    from live2d_builder.exporter import moc3_pipeline

    builder_result = _builder_result()
    _, export_result = _export(tmp_path, builder_result)
    _declare_physics(tmp_path, export_result, builder_result)
    monkeypatch.setattr(
        moc3_pipeline, "verify_physics_playback",
        lambda *a, **k: {"ok": True, "blocker": None,
                         "chains": [{"input": "ParamAngleX",
                                     "output": "ParamHairSwing",
                                     "moved": True, "lagged": True,
                                     "settled": True}]})
    compiled = moc3_pipeline.compile_export_moc3(
        builder_result=builder_result, atlas_uvs=export_result["atlas_uvs"],
        output_dir=export_result["output_dir"], character_name="web_preview")
    assert compiled["physics_verified"] is True, compiled["blocker"]
    assert compiled["runtime_ready"] is True, compiled["blocker"]


@requires_live2d
@requires_pixels
def test_exported_package_actually_renders(tmp_path):
    """导出包必须在官方运行时里真的画出像素。

    一致性、加载、参数表全绿也可能一个像素都画不出来 —— 单位坐标、三角形
    手性、图集 UV 三个 bug 都是这样躲过了之前所有测试。
    """
    builder_result = _builder_result()
    _, export_result = _export(tmp_path, builder_result)
    compile_export_moc3(
        builder_result=builder_result,
        atlas_uvs=export_result["atlas_uvs"],
        output_dir=export_result["output_dir"],
        character_name="web_preview",
    )
    result = render_probe(export_result["model3_json"],
                          png=str(tmp_path / "frame.png"))
    assert result["ok"] is True, f"渲染失败: {result['blocker']}"
    # 图层把画布中间 1/3 涂实，可见面积应约占视口的几个百分点
    assert result["opaque_pixels"] > 5000, (
        f"导出包几乎什么都没画出来：{result['opaque_pixels']} 像素，"
        f"bbox={result['alpha_bbox']}")
    assert result["alpha_bbox"] is not None
    x0, y0, x1, y1 = result["alpha_bbox"]
    assert 0 < x0 and x1 < 400 and 0 < y0 and y1 < 500, (
        f"内容超出视口，说明尺度/坐标约定不对：bbox={result['alpha_bbox']}")
    cx, cy = result["centroid"]
    assert abs(cx - 200) < 40 and abs(cy - 250) < 60, (
        f"内容没有落在画布中心，图层可能被翻转或错位：centroid={cx:.0f},{cy:.0f}")


def _weighted_mesh(name="hair", width=128.0, height=128.0, bone="Hair_Front",
                   weight=1.0):
    verts = [[32.0, 32.0], [96.0, 32.0], [96.0, 96.0], [32.0, 96.0]]
    return {
        "vertices": verts,
        "vertices_norm": [[x / width, y / height] for x, y in verts],
        "indices": [[0, 1, 2], [0, 2, 3]],
        "width": width, "height": height,
        "weights": {"bone_names": [bone], "weights": [[weight]] * len(verts)},
    }


def _z_rig_result():
    result = _builder_result()
    result["meshes"] = {"hair": _weighted_mesh("hair"),
                        "face": _weighted_mesh("face", bone="Head")}
    result["bone_positions"] = {"Head": (64.0, 40.0), "Body": (64.0, 100.0)}
    result["parameters"] = {"cubism_params": [
        {"Id": "ParamAngleZ", "Min": -30, "Max": 30, "Value": 0},
    ]}
    return result


def _compile_z_rig(tmp_path):
    """走完整导出 + 编译路径，返回 (export_result, moc3 编译结果)。"""
    builder_result = _z_rig_result()
    exporter = Model3Exporter(max_atlas_size=256)
    export_result = exporter.export(
        builder_result=builder_result,
        output_dir=str(tmp_path / "zr"), character_name="zr")
    compiled = compile_export_moc3(
        builder_result=builder_result,
        atlas_uvs=export_result["atlas_uvs"],
        output_dir=export_result["output_dir"], character_name="zr")
    return export_result, compiled


def test_pipeline_bakes_keyforms_into_moc3(tmp_path):
    """F-04：管线产物自己就该带参数键形，不再只能手搓 RigSpec。"""
    _, compiled = _compile_z_rig(tmp_path)
    assert compiled["lint_issues"] == [], compiled["lint_issues"]
    assert compiled["moc3_written"] is True, compiled["blocker"]
    assert compiled["consistency"]["ok"] is True, compiled["consistency"]

    Moc3 = pytest.importorskip("moc3").Moc3
    doc = Moc3.from_file(compiled["moc3_path"])
    # 每个受 Head/Hair 旋转影响的网格都应有 3 个键形（min/default/max）
    assert doc["art_mesh.keyform_counts"] == [3, 3]
    assert doc["keys.values"] == [-30.0, 0.0, 30.0]
    assert doc["art_mesh.keyform_binding_band_indices"] == [1, 1]
    assert doc["parameter.keyform_binding_counts"] == [1]
    # 三个键形里必须有一个逐字复现静止姿态（default 键），否则模型开箱就是歪的。
    # 顶点先按 (x - w/2, h/2 - y) 换算、再除以 ppu=100 存成单位坐标。
    begins = doc["art_mesh_keyform.keyform_position_begin_indices"]
    positions = doc["keyform_position.xys"]
    authored = [[(32.0 - 64.0) / 100.0, (64.0 - 32.0) / 100.0],
                [(96.0 - 64.0) / 100.0, (64.0 - 32.0) / 100.0],
                [(96.0 - 64.0) / 100.0, (64.0 - 96.0) / 100.0],
                [(32.0 - 64.0) / 100.0, (64.0 - 96.0) / 100.0]]
    expected = [v for point in authored for v in point]
    assert len(begins) == 6, f"2 个网格 x 3 个键形，实为 {begins}"
    for keyform_begin in begins:
        chunk = positions[keyform_begin: keyform_begin + len(expected)]
        if chunk == pytest.approx(expected, abs=1e-6):
            break
    else:
        pytest.fail("没有键形复现静止姿态（default 键丢失）")
    # 两端键形必须真的偏离静止姿态，否则参数不会改变画面
    moved = [b for b in begins
             if positions[b: b + len(expected)] != pytest.approx(expected, abs=1e-6)]
    assert len(moved) == 4, f"应恰有 4 个键形偏离静止姿态，实为 {len(moved)}"


@requires_live2d
@requires_pixels
def test_pipeline_keyforms_deform_in_official_runtime(tmp_path):
    """端到端：管线导出的包在官方运行时里被参数真的推动画面。"""
    from drivers.live2d_runtime.moc3_verify import render_probe

    export_result, compiled = _compile_z_rig(tmp_path)
    assert compiled["consistency"]["ok"] is True
    manifest = export_result["model3_json"]
    left = render_probe(manifest, {"ParamAngleZ": -30.0},
                        png=str(tmp_path / "left.png"))
    right = render_probe(manifest, {"ParamAngleZ": 30.0},
                         png=str(tmp_path / "right.png"))
    assert left["ok"] and right["ok"], (left["blocker"], right["blocker"])
    assert left["opaque_pixels"] > 1000 and right["opaque_pixels"] > 1000, \
        "两端都必须画得出内容，否则『画面变了』可能是噪声"
    assert left["pixels_sha256"] != right["pixels_sha256"], \
        f"参数改了但画面没变：bbox={left['alpha_bbox']}/{right['alpha_bbox']}"
    assert left["centroid"] != right["centroid"]
