"""裙摆 / 头发物理链路回归测试。

一条物理链要真的让画面动起来，三件事必须同时成立：

1. physics3 里有组在**输出**这个部位自己的参数；
2. 变形器**读取**同一参数，且 targets 命中了真实层名；
3. 形变 profile 存在，能把参数值换算成位移。

只补其中一段，产物看起来「有物理」，画面却不动 —— 裙摆此前就是
physics3 里有 Skirt 组、网格却静止，而 19 类后端的 ``hair`` 一个变形器都没挂上。
"""

import pytest

from live2d_builder.blendshapes.parameters import STANDARD_PARAMETERS
from live2d_builder.bones.deformers import DeformerHierarchy
from live2d_builder.exporter.moc3_pipeline import (
    DEFORMER_PROFILES,
    SKIRT_SWAY,
    _skirt_sway_deform,
)
from live2d_builder.physics.config import (
    PhysicsBuilder,
    is_skirt_layer,
)

PARAMS_BY_ID = {p["id"]: p for p in STANDARD_PARAMETERS}


def _physics_outputs(**build_kwargs) -> set:
    pb = PhysicsBuilder()
    if build_kwargs.get("hair"):
        pb.build_hair_physics(build_kwargs["hair"])
    if build_kwargs.get("body"):
        pb.build_body_physics()
    if build_kwargs.get("breathing"):
        pb.build_breathing_physics()
    if build_kwargs.get("skirt"):
        pb.build_skirt_physics(build_kwargs["skirt"])
    settings = pb.to_physics3_json()["PhysicsSettings"]
    return {o["Destination"]["Id"] for s in settings for o in s["Output"]}


def _deformers_for(layer_names):
    result = DeformerHierarchy().build({}, {}, layer_names=layer_names)
    return {item["name"]: item for item in result["deformers"]}


def test_skirt_swing_parameter_is_declared():
    """新参数必须进模型参数表，否则 prune 会把整条裙摆链剪掉。"""
    assert "ParamSkirtSwing" in PARAMS_BY_ID
    spec = PARAMS_BY_ID["ParamSkirtSwing"]
    assert spec["min"] < 0 < spec["max"]
    assert spec["default"] == 0.0


def test_skirt_physics_outputs_its_own_parameter():
    outputs = _physics_outputs(skirt=["bottomwear"])
    assert outputs == {"ParamSkirtSwing"}
    # ParamBodySway 已被 BodyBounce 占用：两个组写同一参数，内核只留一条，
    # 裙摆那条会被整个顶掉。
    assert "ParamBodySway" not in outputs


def test_hair_front_and_back_write_different_parameters():
    """前发/后发是两条摆链，共用 ParamHairSwing 会被内核顶掉一条。"""
    pb = PhysicsBuilder()
    pb.build_hair_physics(["hair_front", "hair_back"])
    settings = pb.to_physics3_json()["PhysicsSettings"]
    by_group = {s["Id"]: {o["Destination"]["Id"] for o in s["Output"]}
                for s in settings}
    assert by_group["HairFront"] == {"ParamHairSwing"}
    assert by_group["HairBack"] == {"ParamHairSwingBack"}


def test_physics_output_parameters_are_unique_across_groups():
    """所有物理组的输出参数两两不同，避免互相覆盖。"""
    pb = PhysicsBuilder()
    pb.build_hair_physics(["hair_front", "hair_back"])
    pb.build_body_physics()
    pb.build_breathing_physics()
    pb.build_skirt_physics(["bottomwear"])
    settings = pb.to_physics3_json()["PhysicsSettings"]
    outputs = [o["Destination"]["Id"] for s in settings for o in s["Output"]]
    assert len(outputs) == len(set(outputs)), f"输出参数重复：{outputs}"
    assert set(outputs) == {"ParamHairSwing", "ParamHairSwingBack",
                            "ParamBodySway", "ParamShoulderL",
                            "ParamShoulderR", "ParamSkirtSwing"}


@pytest.mark.parametrize(
    "name", ["skirt", "skirt_front", "skirt_body", "bottomwear", "clothes_bottom"])
def test_is_skirt_layer_accepts_lower_body_names(name):
    assert is_skirt_layer(name)


@pytest.mark.parametrize(
    "name", ["topwear", "hair", "handwear", "legwear", "footwear", "objects"])
def test_is_skirt_layer_rejects_other_parts(name):
    assert not is_skirt_layer(name)


def test_19_class_layers_get_hair_and_skirt_deformers():
    """l2d_sam_iter2 只产出 hair / bottomwear，这两个名字必须挂上变形器。"""
    deformers = _deformers_for(["hair", "bottomwear", "face"])

    assert "HairFrontSwing" in deformers
    assert deformers["HairFrontSwing"]["targets"] == ["hair"]
    assert deformers["HairFrontSwing"]["parameters"] == ["ParamHairSwing"]

    assert "SkirtSway" in deformers
    assert deformers["SkirtSway"]["targets"] == ["bottomwear"]
    assert deformers["SkirtSway"]["parameters"] == ["ParamSkirtSwing"]
    assert deformers["SkirtSway"]["profile"] == "skirt_sway"


def test_skirt_deformer_consumes_the_parameter_physics_writes():
    """物理输出 ⊆ 变形器读取。断在这条上就是「有物理、画面不动」。"""
    outputs = _physics_outputs(skirt=["bottomwear"])
    skirt = _deformers_for(["bottomwear"])["SkirtSway"]
    assert outputs <= set(skirt["parameters"])


def test_hair_deformer_consumes_the_parameter_physics_writes():
    outputs = _physics_outputs(hair=["hair"])
    hair = _deformers_for(["hair"])["HairFrontSwing"]
    assert outputs <= set(hair["parameters"])


def test_back_hair_deformer_reads_the_back_parameter():
    outputs = _physics_outputs(hair=["hair_front", "hair_back"])
    deformers = _deformers_for(["hair_front", "hair_back"])
    back = set(deformers["HairBackSwing"]["parameters"])
    assert "ParamHairSwingBack" in back
    # 后发变形器必须消费物理写出的后发参数，且不再抢前发那条
    assert deformers["HairBackSwing"]["parameters"] == ["ParamHairSwingBack"]
    assert "ParamHairSwingBack" in outputs


SIDE_HAIR = ["hair_front", "hair_back", "hair_back_left", "hair_back_right"]


def test_side_hair_strands_write_their_own_parameters():
    """后发四束里的左右两束各有一条摆链，输出参数必须两两不同。

    三条链共用 ParamHairSwingBack 时内核只保留一条输出 —— 侧束会跟中束同相，
    四束拆分等于没拆。
    """
    pb = PhysicsBuilder()
    pb.build_hair_physics(SIDE_HAIR)
    settings = pb.to_physics3_json()["PhysicsSettings"]
    by_group = {s["Id"]: {o["Destination"]["Id"] for o in s["Output"]}
                for s in settings}

    assert by_group["HairSideL"] == {"ParamHairSwingSideL"}
    assert by_group["HairSideR"] == {"ParamHairSwingSideR"}
    assert by_group["HairBack"] == {"ParamHairSwingBack"}
    outputs = [o["Destination"]["Id"] for s in settings for o in s["Output"]]
    assert len(outputs) == len(set(outputs)), f"输出参数重复：{outputs}"


def test_side_hair_physics_is_only_built_when_the_strands_exist():
    """没有拆出侧束时不许凭空多出两个物理组（输出参数没人读就是噪音）。"""
    pb = PhysicsBuilder()
    pb.build_hair_physics(["hair_front", "hair_back"])
    ids = {s["Id"] for s in pb.to_physics3_json()["PhysicsSettings"]}
    assert ids == {"HairFront", "HairBack"}


def test_side_hair_deformers_consume_the_parameters_physics_writes():
    """物理输出 ⊆ 变形器读取：断在这条上就是「有物理、画面不动」。"""
    pb = PhysicsBuilder()
    pb.build_hair_physics(SIDE_HAIR)
    outputs = {o["Destination"]["Id"]
               for s in pb.to_physics3_json()["PhysicsSettings"] for o in s["Output"]}
    deformers = _deformers_for(SIDE_HAIR)

    assert deformers["HairSideSwing_L"]["parameters"] == ["ParamHairSwingSideL"]
    assert deformers["HairSideSwing_R"]["parameters"] == ["ParamHairSwingSideR"]
    consumed = set()
    for name in ("HairFrontSwing", "HairBackSwing",
                 "HairSideSwing_L", "HairSideSwing_R"):
        consumed |= set(deformers[name]["parameters"])
    assert outputs <= consumed, outputs - consumed


def test_side_hair_parameters_are_declared():
    for pid in ("ParamHairSwingSideL", "ParamHairSwingSideR"):
        spec = PARAMS_BY_ID[pid]
        assert spec["min"] < 0 < spec["max"]
        assert spec["default"] == 0.0


def test_skirt_sway_profile_exists_and_moves_the_hem():
    assert "skirt_sway" in DEFORMER_PROFILES
    roles, _deform = DEFORMER_PROFILES["skirt_sway"]
    assert roles == ("sway",)

    rect = (0.0, 0.0, 100.0, 100.0)
    points = [(50.0, 0.0), (50.0, 100.0)]
    out = _skirt_sway_deform(points, rect, rect, {"sway": 1.0}, {})

    assert out[0][0] == pytest.approx(50.0)          # 腰线钉住
    assert out[1][0] > 50.0                          # 下摆摆出去
    assert out[1][0] - 50.0 == pytest.approx(SKIRT_SWAY * 100.0)
