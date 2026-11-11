"""``head_xy`` profile 必须真的让头动起来（X/Y 两轴）。

回归背景：``moc3_pipeline`` 里算头部变换选项的门控只列了
``("eye_head", "head", "neck_band")``，**漏了 ``"head_xy"``**。HeadRig 用的正是
``head_xy``（Z 归 HeadRotationZ 之后只剩 X/Y），于是 ``options`` 里没有 head 项，
``_apply_head`` 走「缺失时原样返回」→ HeadRig 的 9 张控制网格**完全相同** →
``ParamAngleX`` / ``ParamAngleY`` 在官方 Core 里**逐像素没有任何变化**：
角色只能绕 Z 转，点头与侧看整体失效。

判据：HeadRig 的控制网格必须随 X/Y 键变化（默认键与 X=30 那一格不同）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live2d_builder.bones.deformers import DeformerHierarchy
from live2d_builder.exporter.moc3_model import MeshSpec, ParameterSpec
from live2d_builder.exporter.moc3_pipeline import build_deformers


def _grid_at(deformer, wanted):
    """多轴带里挑出「各轴都取给定值」的那一格（展平序：第一个轴步长为 1）。"""
    index = 0
    stride = 1
    for (pid, keys), want in zip(deformer.axes_spec, wanted):
        slot = min(range(len(keys)), key=lambda i: abs(float(keys[i]) - want))
        index += slot * stride
        stride *= len(keys)
    return deformer.grids[index]


def _head_rig():
    specs = [MeshSpec(mesh_id="face",
                      vertices=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
                      triangles=[(0, 1, 2), (0, 2, 3)],
                      uvs=[(0.0, 0.0)] * 4)]
    entries = list(DeformerHierarchy.DEFAULT_WARP_DEFORMERS)
    params = {
        "ParamAngleX": ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0),
        "ParamAngleY": ParameterSpec("ParamAngleY", -30.0, 30.0, 0.0),
    }
    warps, _skipped, _static = build_deformers(
        specs, entries, 200.0, 200.0, params,
        {"Head": (100.0, 100.0)}, head_span=(120.0, 160.0))
    head = next((w for w in warps if w.deformer_id == "HeadRig"), None)
    assert head is not None, "HeadRig 没有编出来"
    return head


def test_the_head_xy_control_grid_changes_with_the_x_key():
    """默认键与 X=30 那一格的控制网格必须不同（否则头的 X 形变是空壳）。"""
    head = _head_rig()
    rest = _grid_at(head, [0.0, 0.0])
    tilted = _grid_at(head, [30.0, 0.0])
    assert rest.points != tilted.points, (
        "head_xy 的控制网格没随 ParamAngleX 变化 —— 头的 X 轴形变是空壳"
        "（options 里缺 head 项时 _apply_head 会原样返回）")


def test_the_head_xy_control_grid_changes_with_the_y_key():
    head = _head_rig()
    rest = _grid_at(head, [0.0, 0.0])
    turned = _grid_at(head, [0.0, 30.0])
    assert rest.points != turned.points, (
        "head_xy 的控制网格没随 ParamAngleY 变化 —— 头的 Y 轴形变是空壳")


def test_the_head_xy_control_grid_is_not_identical_for_every_key():
    """九张网格不能全等：全等就说明形变整体退化成恒等。"""
    head = _head_rig()
    grids = [tuple(tuple(p) for p in g.points) for g in head.grids]
    assert len(set(grids)) > 1, "HeadRig 的九张控制网格全等 → 头的 X/Y 形变完全失效"
