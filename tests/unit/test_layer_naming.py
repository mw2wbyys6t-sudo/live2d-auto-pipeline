"""层名分类的通用性测试。

产品要面向任意立绘、任意分层器，层名可能是 ``hair_back``（SAM2）、
``000_HairBack``（PSD）、``头发_后``（K-means）、``hair``（19 类模型）。
这里锁定「整词匹配不误判、中文/驼峰/序号前缀都能命中」两条底线。
"""

import pytest

from live2d_builder.bones.deformers import (
    BoneHierarchy,
    DeformerHierarchy,
    normalise_layer_name,
)
from live2d_builder.layer_naming import (
    ANIMAL_EAR_KEYWORDS,
    HAIR_BACK_KEYWORDS,
    HAIR_KEYWORDS,
    SKIRT_KEYWORDS,
    TAIL_KEYWORDS,
    layer_tokens,
    matches_keywords,
)


def test_layer_tokens_handles_snake_camel_prefix_and_chinese():
    assert layer_tokens("000_Hair-Back") == {"000", "hair", "back"}
    assert layer_tokens("HairFront") == {"hair", "front"}
    assert layer_tokens("hairBack") == {"hair", "back"}
    assert layer_tokens("头发_后") == {"头发", "后"}


@pytest.mark.parametrize("name", ["tail", "tail_L", "尾巴", "000_tail", "Tail_02"])
def test_tail_keywords_hit_real_tail_names(name):
    assert matches_keywords(name, TAIL_KEYWORDS)


@pytest.mark.parametrize("name", ["detail", "face_detail", "tailor", "retail"])
def test_tail_keywords_do_not_hit_lookalikes(name):
    """``tail`` 必须是整词：``detail`` 里也含 tail，按子串匹配会凭空长出尾巴。"""
    assert not matches_keywords(name, TAIL_KEYWORDS)


@pytest.mark.parametrize("name", ["hair", "hair_front", "000_hair", "HairFront",
                                  "头发", "头发_后", "毛发"])
def test_hair_keywords_hit_all_backends(name):
    assert matches_keywords(name, HAIR_KEYWORDS)


@pytest.mark.parametrize("name", ["hair_back", "HairBack", "hairback",
                                  "back_hair", "头发_后", "后发"])
def test_hair_back_keywords_hit_all_backends(name):
    assert matches_keywords(name, HAIR_BACK_KEYWORDS)


@pytest.mark.parametrize("name", ["animal_ear", "animal_ear_L", "cat_ear",
                                  "fox_ears", "kemomimi", "兽耳", "猫耳"])
def test_animal_ear_keywords_hit(name):
    assert matches_keywords(name, ANIMAL_EAR_KEYWORDS)


@pytest.mark.parametrize("name", ["ear_L", "ear_right", "earwear", "earring",
                                  "耳朵", "耳环"])
def test_animal_ear_keywords_do_not_hit_human_ears_or_earrings(name):
    """人类的 ``ear`` 与 19 类的 ``earwear`` 不能被当成会摆的兽耳。"""
    assert not matches_keywords(name, ANIMAL_EAR_KEYWORDS)


@pytest.mark.parametrize("name", ["skirt", "skirt_front", "bottomwear",
                                  "clothes_bottom", "裙子", "短裙", "裤子"])
def test_skirt_keywords_hit_all_backends(name):
    assert matches_keywords(name, SKIRT_KEYWORDS)


@pytest.mark.parametrize("name", ["topwear", "shirt", "上衣", "coat"])
def test_skirt_keywords_do_not_hit_upper_body(name):
    assert not matches_keywords(name, SKIRT_KEYWORDS)


def test_deformers_match_arbitrary_layer_naming():
    """一套混合命名（PSD 前缀 / 中文 / 驼峰 / 兽耳 / 尾巴 / 干扰项）都要挂上。"""
    layers = ["000_HairBack", "裙子", "cat_ear_L", "尾巴", "detail", "face"]
    result = DeformerHierarchy().build({}, {}, layer_names=layers)
    by_name = {item["name"]: item for item in result["deformers"]}

    # 精确 targets 返回的是目标写法、关键词命中返回的是原始层名，下游按归一
    # 后的名字找网格，所以这里按归一结果比较（000_HairBack 与 hair_back 等价）。
    def norm_targets(name: str):
        return [normalise_layer_name(t) for t in by_name[name]["targets"]]

    assert norm_targets("HairBackSwing") == ["hair_back"]
    assert norm_targets("SkirtSway") == ["裙子"]
    assert norm_targets("EarSwing") == ["cat_ear_l"]
    assert norm_targets("TailSwing") == ["尾巴"]

    # 干扰项不得被任何摆动链抢走
    all_targets = {t for item in result["deformers"] for t in item["targets"]}
    assert "detail" not in all_targets


def test_bones_resolve_across_naming_styles():
    """骨骼归属也不能依赖某一种命名：驼峰 / PSD 前缀 / 中文 / 兽耳 / 尾巴。"""
    layers = ["HairFront", "000_HairBack", "尾巴", "animal_ear_L", "裙子"]
    tree = BoneHierarchy().build(layers)
    assigned = tree["layer_assignments"]

    assert assigned["HairFront"] == "Hair_Front"
    assert assigned["000_HairBack"] == "Hair_Back"
    assert assigned["尾巴"] == "Tail"
    assert assigned["animal_ear_L"] == "Ear_L"
    assert assigned["裙子"] == "Skirt"
    # 骨骼树里必须真的有 Tail，否则 TailSwing 变形器没有归属
    assert "Tail" in {b["name"] for b in tree["flat"]}


def test_one_layer_only_belongs_to_one_deformer():
    """一块网格只能挂一个变形器：关键词扩展后也不能出现重复归属。"""
    layers = ["hair", "hair_back", "hair_back_left", "hair_front"]
    result = DeformerHierarchy().build({}, {}, layer_names=layers)
    seen = [t for item in result["deformers"] for t in item["targets"]]
    assert len(seen) == len(set(seen)), f"层被多个变形器声明：{seen}"
