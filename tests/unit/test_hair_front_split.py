"""前发按连通块分左右：只在真的分成两缕时才拆。"""
import numpy as np

from core.segment_engine.part_split import (
    HAIR_FRONT_LEFT,
    HAIR_FRONT_RIGHT,
    split_hair_front_sides,
)
from live2d_builder.physics.config import PhysicsBuilder


def _canvas(w=200, h=120):
    return np.zeros((h, w), dtype=bool)


def test_two_separate_strands_are_split_left_and_right():
    mask = _canvas()
    mask[20:100, 20:80] = True      # 左缕 4800 px
    mask[30:90, 130:190] = True     # 右缕 3600 px
    parts, warnings = split_hair_front_sides(mask, midline=100.0)
    assert set(parts) == {HAIR_FRONT_LEFT, HAIR_FRONT_RIGHT}
    assert parts[HAIR_FRONT_LEFT].sum() == 4800
    assert parts[HAIR_FRONT_RIGHT].sum() == 3600
    # 不重叠、不丢像素
    assert not (parts[HAIR_FRONT_LEFT] & parts[HAIR_FRONT_RIGHT]).any()
    assert int(parts[HAIR_FRONT_LEFT].sum() + parts[HAIR_FRONT_RIGHT].sum()) == 8400
    assert warnings


def test_single_connected_mass_is_not_split():
    """一整块连续前发硬拆会在摆动时裂开，必须原样保留。"""
    mask = _canvas()
    mask[20:100, 20:180] = True
    parts, warnings = split_hair_front_sides(mask, midline=100.0)
    assert parts == {}
    assert warnings == []


def test_tiny_side_strand_is_not_split():
    """一侧只有几像素的碎发不够成束，留给整块前发。"""
    mask = _canvas()
    mask[20:100, 20:100] = True
    mask[40:50, 180:190] = True     # 100 px，低于 300px 门槛
    parts, _ = split_hair_front_sides(mask, midline=100.0)
    assert parts == {}


def test_physics_adds_front_side_chains_and_drops_the_generic_one():
    pb = PhysicsBuilder()
    pb.build_hair_physics(["hair_front_left", "hair_front_right", "hair_back"])
    by_group = {s["Id"]: [o["Destination"]["Id"] for o in s["Output"]]
                for s in pb.to_physics3_json()["PhysicsSettings"]}
    assert by_group["HairFrontL"] == ["ParamHairSwingFrontL"]
    assert by_group["HairFrontR"] == ["ParamHairSwingFrontR"]
    # 前发已拆开：整体链的 ParamHairSwing 没有变形器消费，不能再留一条空转链。
    assert "HairFront" not in by_group


def test_monolithic_front_keeps_the_generic_chain():
    pb = PhysicsBuilder()
    pb.build_hair_physics(["hair_front", "hair_back"])
    ids = {s["Id"] for s in pb.to_physics3_json()["PhysicsSettings"]}
    assert "HairFront" in ids
    assert "HairFrontL" not in ids
