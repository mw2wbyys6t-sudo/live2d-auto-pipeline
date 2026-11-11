"""兽耳物理链路回归测试。

修复前：``AnimalEars`` 组写的是 ``ParamHairSwing``，与前发抢同一个参数
（内核一个参数只保留一条输出），而且没有任何变形器读它 —— 兽耳既不摆，
还顺手把前发的摆动链顶掉一条。
"""

import pytest

from live2d_builder.blendshapes.parameters import STANDARD_PARAMETERS
from live2d_builder.bones.deformers import DeformerHierarchy
from live2d_builder.physics.config import PhysicsBuilder

PARAMS_BY_ID = {p["id"]: p for p in STANDARD_PARAMETERS}


def _physics(**kwargs) -> PhysicsBuilder:
    pb = PhysicsBuilder()
    if kwargs.get("hair"):
        pb.build_hair_physics(kwargs["hair"])
    pb.build_ear_tail_physics(
        has_animal_ears=bool(kwargs.get("ears")),
        has_tail=bool(kwargs.get("tail")),
    )
    return pb


def _outputs(pb: PhysicsBuilder) -> list:
    settings = pb.to_physics3_json()["PhysicsSettings"]
    return [o["Destination"]["Id"] for s in settings for o in s["Output"]]


def _deformers_for(layer_names):
    result = DeformerHierarchy().build({}, {}, layer_names=layer_names)
    return {item["name"]: item for item in result["deformers"]}


def test_ear_swing_parameter_is_declared():
    """新参数必须进模型参数表，否则 prune 会把兽耳链整条剪掉。"""
    assert "ParamEarSwing" in PARAMS_BY_ID
    spec = PARAMS_BY_ID["ParamEarSwing"]
    assert spec["min"] < 0 < spec["max"]
    assert spec["default"] == 0.0


def test_animal_ears_write_their_own_parameter():
    outputs = _outputs(_physics(ears=True))
    assert outputs == ["ParamEarSwing"]
    # ParamHairSwing 归前发，兽耳不得再写它
    assert "ParamHairSwing" not in outputs


def test_ear_and_hair_outputs_do_not_collide():
    outputs = _outputs(_physics(hair=["hair_front"], ears=True))
    assert len(outputs) == len(set(outputs)), f"输出参数重复：{outputs}"
    assert set(outputs) == {"ParamHairSwing", "ParamEarSwing"}


@pytest.mark.parametrize("layer", ["animal_ear", "animal_ear_L", "animal_ear_R",
                                   "kemomimi", "kemomimi_L", "kemomimi_R"])
def test_animal_ear_layer_gets_its_swing_deformer(layer):
    deformers = _deformers_for([layer])
    assert "EarSwing" in deformers
    assert deformers["EarSwing"]["targets"] == [layer]
    assert deformers["EarSwing"]["parameters"] == ["ParamEarSwing"]


def test_ear_deformer_consumes_the_parameter_physics_writes():
    """物理输出 ⊆ 变形器读取；断在这条上就是「有物理、耳朵不动」。"""
    outputs = set(_outputs(_physics(ears=True)))
    ear = _deformers_for(["animal_ear_L"])["EarSwing"]
    assert outputs <= set(ear["parameters"])


def test_human_ears_stay_on_head_rig_and_follow_head_rotation():
    """人类耳朵不归兽耳摆动管，但必须被 HeadRig 接住（19 类用 ear_L/ear_R）。"""
    deformers = _deformers_for(["ear_L", "ear_R"])
    assert "EarSwing" not in deformers
    assert deformers["HeadRig"]["targets"] == ["ear_L", "ear_R"]
