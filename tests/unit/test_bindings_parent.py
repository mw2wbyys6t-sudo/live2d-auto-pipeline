"""bindings.json 的变形器父级导出契约。

前端「绑骨线」按 ``deformers[].parent`` 画层级，所以这条字段必须：
1. 有父级时如实写出（否则可视化只剩孤立节点）；
2. 父级不存在时降级成根级并留痕（否则画出一条指向空气的断线）。
"""

from live2d_builder.exporter.bindings_export import build_bindings_document
from live2d_builder.exporter.moc3_model import (
    DeformerGrid,
    RigSpec,
    WarpDeformerSpec,
)


def _warp(name: str, parent: str = "") -> WarpDeformerSpec:
    points = [[0.0, 0.0], [10.0, 0.0], [0.0, 10.0], [10.0, 10.0]]
    return WarpDeformerSpec(
        deformer_id=name,
        rows=1,
        cols=1,
        grids=[DeformerGrid(0.0, points)],
        parent_deformer_id=parent,
    )


def _spec(*deformers) -> RigSpec:
    return RigSpec(
        meshes=[],
        parameters=[],
        canvas_width=100.0,
        canvas_height=100.0,
        deformers=list(deformers),
    )


def test_parent_is_exported_when_present():
    doc = build_bindings_document(_spec(_warp("BodySway"), _warp("HeadRig", "BodySway")))
    parents = {entry["id"]: entry["parent"] for entry in doc["deformers"]}
    assert parents == {"BodySway": "", "HeadRig": "BodySway"}


def test_parent_pointing_nowhere_falls_back_to_root(caplog):
    doc = build_bindings_document(_spec(_warp("HeadRig", "MissingParent")))
    assert doc["deformers"][0]["parent"] == ""
    assert "MissingParent" in caplog.text
