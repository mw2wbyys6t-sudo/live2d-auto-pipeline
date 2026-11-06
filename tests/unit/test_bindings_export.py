"""bindings.json：把编译后的绑定表落成前端能直接消费的形式。

前端需要的不只是「有哪些层」，而是「某个参数取某个值时，每个顶点在哪」。
这份文件是 moc3 里那套键形/变形器的可读投影，坐标统一换成图层图像坐标
（左上原点、y 向下），与 ``{name}.meshes.json`` 同一空间。
"""
import json

import pytest

from live2d_builder.exporter.bindings_export import write_bindings
from live2d_builder.exporter.moc3_model import (
    DeformerGrid,
    KeyformShape,
    MeshSpec,
    ParameterSpec,
    RigSpec,
    RotationDeformerSpec,
    RotationKeyform,
    WarpDeformerSpec,
)


def _mouth_mesh() -> MeshSpec:
    return MeshSpec(
        mesh_id="mouth",
        vertices=[[10.0, 20.0], [30.0, 20.0], [30.0, 40.0], [10.0, 40.0]],
        triangles=[[0, 1, 2], [0, 2, 3]],
        uvs=[[0.1, 0.2], [0.3, 0.2], [0.3, 0.4], [0.1, 0.4]],
        draw_order=2.0,
        texture_index=1,
        part_id="Part_mouth",
        deformer_id="MouthOpen",
        keyform_axes=[("ParamMouthOpenY", (0.0, 1.0))],
        keyform_shapes=[
            KeyformShape(0.0, [[10.0, 20.0], [30.0, 20.0],
                               [30.0, 40.0], [10.0, 40.0]]),
            KeyformShape(1.0, [[10.0, 20.0], [30.0, 20.0],
                               [30.0, 60.0], [10.0, 60.0]]),
        ],
    )


def _mouth_deformer() -> WarpDeformerSpec:
    return WarpDeformerSpec(
        deformer_id="MouthOpen",
        rows=1,
        cols=1,
        grids=[
            DeformerGrid(0.0, [[0.0, 10.0], [50.0, 10.0],
                               [0.0, 50.0], [50.0, 50.0]]),
            DeformerGrid(1.0, [[0.0, 10.0], [50.0, 10.0],
                               [0.0, 60.0], [50.0, 60.0]]),
        ],
        axes=(("ParamMouthOpenY", (0.0, 1.0)),),
    )


def _spec(*, deformers=None) -> RigSpec:
    return RigSpec(
        meshes=[_mouth_mesh()],
        parameters=[ParameterSpec("ParamMouthOpenY", 0.0, 1.0, 0.0)],
        canvas_width=200.0,
        canvas_height=100.0,
        deformers=deformers if deformers is not None else [_mouth_deformer()],
    )


def _manifest(tmp_path):
    path = tmp_path / "char.model3.json"
    path.write_text(json.dumps({"FileReferences": {"Moc": "char.moc3"}}),
                    encoding="utf-8")
    return path


def test_bindings_are_written_in_layer_image_coordinates(tmp_path):
    """模型坐标（原点居中）-> 图层图像坐标（左上原点）：只做平移，**不翻 y**。

    实测（to_model_xy 的探针结论）：内核把 y 当向下为正，模型坐标就是图像坐标
    减半画布；这里若按习惯写成 ``height/2 - y``，整份绑定会上下镜像，前端形变
    全反。
    """
    manifest = _manifest(tmp_path)
    path = write_bindings(_spec(), str(tmp_path), "char")

    assert path is not None and path.is_file()
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["version"] == 1
    assert doc["canvas"] == {"width": 200.0, "height": 100.0,
                             "pixelsPerUnit": 100.0}
    assert doc["parameters"] == [
        {"id": "ParamMouthOpenY", "min": 0.0, "max": 1.0, "default": 0.0}]

    mesh = doc["meshes"][0]
    assert mesh["id"] == "mouth"
    assert mesh["vertices"] == [110.0, 70.0, 130.0, 70.0, 130.0, 90.0, 110.0, 90.0]
    assert mesh["uvs"] == [0.1, 0.2, 0.3, 0.2, 0.3, 0.4, 0.1, 0.4]
    assert mesh["indices"] == [0, 1, 2, 0, 2, 3]
    assert mesh["drawOrder"] == 2.0
    # 缺了图集页号，前端只有 UV 不知道该把网格贴到哪张贴图上。
    assert mesh["texture"] == 1
    assert mesh["deformer"] == "MouthOpen"

    refs = json.loads(manifest.read_text(encoding="utf-8"))["FileReferences"]
    assert refs["Bindings"] == "char.bindings.json"


def test_keyform_vertices_are_exported_per_key_in_image_coordinates(tmp_path):
    _manifest(tmp_path)
    doc = json.loads(
        write_bindings(_spec(), str(tmp_path), "char").read_text(encoding="utf-8"))

    keyforms = doc["meshes"][0]["keyforms"]
    assert keyforms["axes"] == [["ParamMouthOpenY", [0.0, 1.0]]]
    # 展平序与 moc3 一致：第一个键形在前，顶点按网格顶点序两两展开
    assert keyforms["vertices"] == [
        [110.0, 70.0, 130.0, 70.0, 130.0, 90.0, 110.0, 90.0],
        [110.0, 70.0, 130.0, 70.0, 130.0, 110.0, 110.0, 110.0],
    ]


def test_deformer_rest_rect_comes_from_the_rest_grid(tmp_path):
    """静止矩形必须取静止网格的实际范围：前端按它把顶点换算成网格内 (u, v)。"""
    _manifest(tmp_path)
    doc = json.loads(
        write_bindings(_spec(), str(tmp_path), "char").read_text(encoding="utf-8"))

    deformer = doc["deformers"][0]
    assert deformer["id"] == "MouthOpen"
    assert deformer["rows"] == 1 and deformer["cols"] == 1
    assert deformer["rest"] == [100.0, 60.0, 150.0, 100.0]
    assert deformer["axes"] == [["ParamMouthOpenY", [0.0, 1.0]]]
    assert deformer["grids"] == [
        [100.0, 60.0, 150.0, 60.0, 100.0, 100.0, 150.0, 100.0],
        [100.0, 60.0, 150.0, 60.0, 100.0, 110.0, 150.0, 110.0],
    ]


def test_deformer_rest_comes_from_the_default_pose_not_the_first_key(tmp_path):
    """静止矩形必须按**默认参数值**插值：拿 grids[0] 会把默认姿态整体搬走。

    实测依据：singer 的 ShoulderLift_R 键值为 -1/0/1，用 grids[0]（-1 那个）当
    基准时，默认姿态下网格被平移约 57px，角色一站上去就是歪的。
    """
    _manifest(tmp_path)
    shifted = DeformerGrid(-1.0, [[0.0, 0.0], [50.0, 0.0],
                                  [0.0, 40.0], [50.0, 40.0]])
    rest = DeformerGrid(0.0, [[0.0, 10.0], [50.0, 10.0],
                              [0.0, 50.0], [50.0, 50.0]])
    lifted = DeformerGrid(1.0, [[0.0, 20.0], [50.0, 20.0],
                                [0.0, 60.0], [50.0, 60.0]])
    deformer = WarpDeformerSpec(
        deformer_id="ShoulderLift_R",
        rows=1, cols=1,
        grids=[shifted, rest, lifted],
        axes=(("ParamShoulderR", (-1.0, 0.0, 1.0)),),
    )
    spec = _spec(deformers=[deformer])
    spec = RigSpec(
        meshes=spec.meshes,
        parameters=[ParameterSpec("ParamMouthOpenY", 0.0, 1.0, 0.0),
                    ParameterSpec("ParamShoulderR", -1.0, 1.0, 0.0)],
        canvas_width=200.0,
        canvas_height=100.0,
        deformers=spec.deformers,
    )
    doc = json.loads(
        write_bindings(spec, str(tmp_path), "char").read_text(encoding="utf-8"))

    # 默认键是 0 那个网格：rest = (100,60)-(150,100)（加半画布平移）
    assert doc["deformers"][0]["rest"] == [100.0, 60.0, 150.0, 100.0]


def test_rotation_deformers_are_not_exported(tmp_path):
    """rotation 变形器当前全是静态姿态，前端不做刚体旋转；导出会造出假活动。

    它们不进 bindings，但必须留下计数，免得「文件里没有」被读成「模型没有」。
    """
    _manifest(tmp_path)
    rotation = RotationDeformerSpec(
        deformer_id="HeadTurn",
        keyforms=[RotationKeyform(0.0, angle=0.0, origin=(0.0, 0.0))])
    doc = json.loads(
        write_bindings(_spec(deformers=[_mouth_deformer(), rotation]),
                       str(tmp_path), "char").read_text(encoding="utf-8"))

    ids = [d["id"] for d in doc["deformers"]]
    assert ids == ["MouthOpen"]
    assert doc["skippedRotationDeformers"] == ["HeadTurn"]


def test_deformer_parent_is_exported_by_name(tmp_path):
    """父子链进 bindings 时写的是**父变形器名**，不是槽位下标。

    Task 2 把作者层的 ``under`` 接成 ``parent_deformer_id`` 之后，
    ``tools/bindings_gap_probe.py`` 的 ``chain()`` 就靠这份名字逐级求值配对距离；
    写成槽位的话变形器表顺序一变就读错。没有父级的变形器仍然写空串（今天 13/13
    的形状），这条同时钉住「不许把骨骼归属写进这一栏」。
    """
    _manifest(tmp_path)
    root = WarpDeformerSpec(
        deformer_id="HeadRig", rows=1, cols=1,
        grids=[DeformerGrid(0.0, [[-50.0, -50.0], [50.0, -50.0],
                                  [-50.0, 50.0], [50.0, 50.0]])])
    child = WarpDeformerSpec(
        deformer_id="MouthOpen", rows=1, cols=1,
        grids=[DeformerGrid(0.0, [[0.0, 10.0], [50.0, 10.0],
                                  [0.0, 50.0], [50.0, 50.0]]),
               DeformerGrid(1.0, [[0.0, 10.0], [50.0, 10.0],
                                  [0.0, 60.0], [50.0, 60.0]])],
        axes=(("ParamMouthOpenY", (0.0, 1.0)),),
        parent_deformer_id="HeadRig")
    doc = json.loads(
        write_bindings(_spec(deformers=[root, child]),
                       str(tmp_path), "char").read_text(encoding="utf-8"))

    by_id = {d["id"]: d for d in doc["deformers"]}
    assert by_id["HeadRig"]["parent"] == ""
    assert by_id["MouthOpen"]["parent"] == "HeadRig"


def test_write_bindings_needs_a_manifest_to_reference_it(tmp_path):
    """没有 model3.json 就无处挂引用 —— 不写一个没人引用的孤儿文件。"""
    assert write_bindings(_spec(), str(tmp_path), "char") is None
    assert not (tmp_path / "char.bindings.json").exists()
