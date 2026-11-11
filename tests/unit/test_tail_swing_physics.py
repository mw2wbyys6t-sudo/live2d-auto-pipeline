"""尾巴物理链路回归测试。

修复前：``Tail`` 组写的是 ``ParamBodySway``，与 ``BodyBounce`` 抢同一个参数
（内核一个参数只保留一条输出），而且没有任何变形器读它 —— 尾巴既不摆，
还把躯干的摆动链顶掉一条。
"""

import pytest

from live2d_builder.blendshapes.parameters import STANDARD_PARAMETERS
from live2d_builder.bones.deformers import DeformerHierarchy
from live2d_builder.exporter.moc3_pipeline import (
    DEFORMER_PROFILES,
    TAIL_SWAY,
    _tail_sway_deform,
)
from live2d_builder.physics.config import PhysicsBuilder

PARAMS_BY_ID = {p["id"]: p for p in STANDARD_PARAMETERS}


def _full_physics() -> PhysicsBuilder:
    pb = PhysicsBuilder()
    pb.build_hair_physics(["hair_front", "hair_back"])
    pb.build_body_physics()
    pb.build_breathing_physics()
    pb.build_skirt_physics(["bottomwear"])
    pb.build_ear_tail_physics(has_animal_ears=True, has_tail=True)
    return pb


def _outputs(pb: PhysicsBuilder) -> list:
    settings = pb.to_physics3_json()["PhysicsSettings"]
    return [o["Destination"]["Id"] for s in settings for o in s["Output"]]


def test_tail_swing_parameter_is_declared():
    assert "ParamTailSwing" in PARAMS_BY_ID
    spec = PARAMS_BY_ID["ParamTailSwing"]
    assert spec["min"] < 0 < spec["max"]
    assert spec["default"] == 0.0


def test_tail_writes_its_own_parameter():
    pb = PhysicsBuilder()
    pb.build_ear_tail_physics(has_animal_ears=False, has_tail=True)
    assert _outputs(pb) == ["ParamTailSwing"]


def test_all_physics_outputs_are_unique():
    """六条链（前发/后发/躯干/呼吸/裙摆/兽耳/尾巴）输出互不覆盖。"""
    outputs = _outputs(_full_physics())
    assert len(outputs) == len(set(outputs)), f"输出参数重复：{outputs}"
    assert set(outputs) == {
        "ParamHairSwing", "ParamHairSwingBack", "ParamBodySway",
        "ParamShoulderL", "ParamShoulderR", "ParamSkirtSwing",
        "ParamEarSwing", "ParamTailSwing",
    }


@pytest.mark.parametrize("layer", ["tail", "tail_L", "尾巴", "000_tail"])
def test_tail_layer_gets_its_swing_deformer(layer):
    result = DeformerHierarchy().build({}, {}, layer_names=[layer])
    by_name = {item["name"]: item for item in result["deformers"]}
    assert "TailSwing" in by_name
    assert by_name["TailSwing"]["targets"] == [layer]
    assert by_name["TailSwing"]["parameters"] == ["ParamTailSwing"]


def test_tail_deformer_consumes_the_parameter_physics_writes():
    pb = PhysicsBuilder()
    pb.build_ear_tail_physics(has_tail=True)
    outputs = set(_outputs(pb))
    tail = {i["name"]: i for i in
            DeformerHierarchy().build({}, {}, layer_names=["tail"])["deformers"]}["TailSwing"]
    assert outputs <= set(tail["parameters"])


def test_tail_sway_profile_moves_the_tip_only():
    assert "tail_sway" in DEFORMER_PROFILES
    roles, _deform = DEFORMER_PROFILES["tail_sway"]
    assert roles == ("swing",)

    rect = (0.0, 0.0, 100.0, 100.0)
    out = _tail_sway_deform([(50.0, 0.0), (50.0, 100.0)], rect, rect,
                            {"swing": 1.0}, {})
    assert out[0][0] == pytest.approx(50.0)      # 根部钉住
    assert out[1][0] - 50.0 == pytest.approx(TAIL_SWAY * 100.0)
