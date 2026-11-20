"""裙下摆 / 头饰的独立摆链：物理输出 → 变形器 → 层归属，三段都要对上。

一条链只补一段就是「physics3 里有组、画面不动」，或者「变形器建了、没挂在
真实层上」。这两个部位本轮才拆出独立摆链，所以三段分别断言。
"""
from live2d_builder.blendshapes.parameters import STANDARD_PARAMETERS
from live2d_builder.bones.deformers import DeformerHierarchy
from live2d_builder.exporter.moc3_pipeline import DEFORMER_PROFILES, resolve_membership
from live2d_builder.physics.config import PhysicsBuilder

PARAMS_BY_ID = {p["id"]: p for p in STANDARD_PARAMETERS}


def _built(layer_names):
    result = DeformerHierarchy().build({}, {}, layer_names=layer_names)
    return {item["name"]: item for item in result["deformers"]}


def test_hem_and_accessory_parameters_are_declared():
    for pid in ("ParamSkirtHemSwing", "ParamAccessorySwing"):
        assert pid in PARAMS_BY_ID, pid
        spec = PARAMS_BY_ID[pid]
        assert spec["min"] < 0 < spec["max"]
        assert spec["default"] == 0.0


def test_skirt_hem_gets_its_own_chain_next_to_the_skirt_body():
    pb = PhysicsBuilder()
    pb.build_skirt_physics(["clothes_bottom", "skirt_hem"])
    by_group = {s["Id"]: {o["Destination"]["Id"] for o in s["Output"]}
                for s in pb.to_physics3_json()["PhysicsSettings"]}
    assert by_group["Skirt"] == {"ParamSkirtSwing"}
    assert by_group["SkirtHem"] == {"ParamSkirtHemSwing"}
    # 两个组写同一参数时内核只保留一条 —— 下摆等于白算。
    assert by_group["Skirt"] != by_group["SkirtHem"]


def test_skirt_without_hem_layer_keeps_a_single_chain():
    pb = PhysicsBuilder()
    pb.build_skirt_physics(["bottomwear"])
    ids = [s["Id"] for s in pb.to_physics3_json()["PhysicsSettings"]]
    assert ids == ["Skirt"]


def test_accessory_physics_outputs_its_own_parameter():
    pb = PhysicsBuilder()
    pb.build_accessory_physics(["accessories"])
    settings = pb.to_physics3_json()["PhysicsSettings"]
    assert [s["Id"] for s in settings] == ["Accessory"]
    assert [o["Destination"]["Id"] for o in settings[0]["Output"]] == [
        "ParamAccessorySwing"]


def test_hem_and_accessory_deformers_own_the_real_layers():
    names = ["clothes_bottom", "skirt_hem", "accessories", "face"]
    built = _built(names)
    owned = resolve_membership(list(built.values()), names)
    assert owned["clothes_bottom"] == "SkirtSway"
    assert owned["skirt_hem"] == "SkirtHemSway"
    assert owned["accessories"] == "AccessorySwing"
    # 头部转动靠继承：头饰的摆链必须挂在 HeadRig 之下，否则转头时饰件留在原地。
    assert built["AccessorySwing"]["under"] == "HeadRig"


def test_hem_and_accessory_profiles_exist():
    assert DEFORMER_PROFILES.get("skirt_sway") is not None
    assert DEFORMER_PROFILES.get("hair_sway") is not None
