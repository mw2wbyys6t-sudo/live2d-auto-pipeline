"""19 类部位（l2d_sam_iter2）→ 52 层的映射契约测试。

覆盖三件事：

1. 映射表必须完整覆盖 19 个规范类名，且状态合法；
2. 52 层没有槽位的部件必须被登记为 unmapped，而不是被塞进耳朵/配饰；
3. 旧的子串匹配缺陷（``topwear`` 含 ``ear``）不得复现。
"""

import pytest

from core.segment_engine.layers52 import (
    BODY_PARTS_19_TO_52,
    LIVE2D_52_LAYERS,
    MAP_DIRECT,
    MAP_NEEDS_SPLIT,
    MAP_UNMAPPED,
    VALID_BODY_PARTS_V2,
    Layer52Generator,
    _matches_as_word,
    resolve_body_part_19,
)

VALID_STATUS = {MAP_DIRECT, MAP_NEEDS_SPLIT, MAP_UNMAPPED}
EAR_LAYERS = {25, 26}


def test_map_covers_all_19_body_parts():
    assert len(VALID_BODY_PARTS_V2) == 19
    assert set(BODY_PARTS_19_TO_52) == set(VALID_BODY_PARTS_V2)


@pytest.mark.parametrize("name", VALID_BODY_PARTS_V2)
def test_every_entry_has_valid_status_and_media(name):
    spec = BODY_PARTS_19_TO_52[name]
    assert spec["status"] in VALID_STATUS
    assert spec["note"], f"{name} 缺少说明"
    assert isinstance(spec["standard_ids"], tuple)
    if spec["status"] == MAP_UNMAPPED:
        assert spec["primary"] is None
        assert spec["standard_ids"] == ()
    else:
        assert isinstance(spec["primary"], int)
        assert spec["primary"] in spec["standard_ids"]


@pytest.mark.parametrize("name", VALID_BODY_PARTS_V2)
def test_primary_points_at_a_real_52_layer(name):
    primary = BODY_PARTS_19_TO_52[name]["primary"]
    if primary is None:
        return
    assert primary in {layer["id"] for layer in LIVE2D_52_LAYERS}


@pytest.mark.parametrize("name", VALID_BODY_PARTS_V2)
def test_split_targets_exist_in_52_layers(name):
    ids = set(BODY_PARTS_19_TO_52[name]["standard_ids"])
    assert ids <= {layer["id"] for layer in LIVE2D_52_LAYERS}


def test_resolve_is_case_insensitive_and_rejects_suffixed_names():
    assert resolve_body_part_19("TopWear") is BODY_PARTS_19_TO_52["topwear"]
    # 带左右后缀的名字交给既有映射，不能被 19 类表吞掉
    assert resolve_body_part_19("eyes_left") is None


@pytest.mark.parametrize(
    "word",
    ["topwear", "bottomwear", "legwear", "footwear", "handwear",
     "headwear", "neckwear", "earwear"],
)
def test_wear_suffix_is_not_matched_as_ear(word):
    assert _matches_as_word(word, "ear") is False


def test_word_boundary_still_allows_real_tokens():
    assert _matches_as_word("ear_left", "ear") is True
    assert _matches_as_word("sidehair_right", "sidehair") is True
    assert _matches_as_word("hair_back", "hair") is True


def test_topwear_requires_split_without_claiming_clothes_outer():
    result = Layer52Generator().map_layers_to_standard(
        [{"part_name": "topwear", "path": "topwear.png"}]
    )
    mapped = [m for m in result["mappings"] if m["mapped"]]
    assert mapped == []
    assert result["split_required"][0]["part_name"] == "topwear"
    assert not ({m["standard_id"] for m in mapped} & EAR_LAYERS)


def test_unmapped_parts_keep_original_label_and_consume_no_layer():
    layers = [
        {"part_name": "bottomwear", "path": "skirt.png"},
        {"part_name": "headwear", "path": "hat.png"},
        {"part_name": "neck", "path": "neck.png"},
    ]
    result = Layer52Generator().map_layers_to_standard(layers)

    unmapped = {item["part_name"] for item in result["unmapped_parts"]}
    assert unmapped == {"bottomwear", "headwear"}
    assert all(item["path"] for item in result["unmapped_parts"])

    # 只有 neck 真正占用 52 层
    mapped = [m for m in result["mappings"] if m["mapped"]]
    assert [m["standard_id"] for m in mapped] == [5]


def test_needs_split_parts_do_not_claim_fine_slots():
    layers = [
        {"part_name": "hair", "path": "hair.png"},
        {"part_name": "eyes", "path": "eyes.png"},
        {"part_name": "ears", "path": "ears.png"},
        {"part_name": "mouth", "path": "mouth.png"},
    ]
    result = Layer52Generator().map_layers_to_standard(layers)

    primary = {item["part_name"]: item["primary"]
               for item in result["split_required"]}
    assert primary == {"hair": 1, "eyes": 33, "ears": 25, "mouth": 28}

    # 每个 mask 只落一层，不重复写同一个 mask 到多个层
    mapped_ids = [m["standard_id"] for m in result["mappings"] if m["mapped"]]
    assert mapped_ids == []
    assert len(mapped_ids) == len(set(mapped_ids))


def test_direct_parts_map_without_split_bookkeeping():
    result = Layer52Generator().map_layers_to_standard(
        [
            {"part_name": "face", "path": "face.png"},
            {"part_name": "nose", "path": "nose.png"},
            {"part_name": "neck", "path": "neck.png"},
        ]
    )
    mapped = sorted(m["standard_id"] for m in result["mappings"] if m["mapped"])
    assert mapped == [5, 23, 27]
    assert result["split_required"] == []


def test_result_embeds_the_19_map_for_auditing():
    result = Layer52Generator().map_layers_to_standard([])
    embedded = result["body_parts_19_map"]
    assert set(embedded) == set(VALID_BODY_PARTS_V2)
    assert embedded["topwear"]["primary"] == 21
    assert embedded["headwear"]["status"] == MAP_UNMAPPED


# --- 合成器真实层名（eyewhite_*/irides_*/tear_*/skirt_hem_*） ---------------

COMPOSER_LAYERS = [
    "hair_back", "cheek", "irides_left", "waistband", "collar",
    "clothes_top", "skirt_hem_L", "skirt_hem_R", "face", "tear_left",
    "tear_right", "nose", "mouth_cavity", "eyes_right", "eyewhite_left",
    "eyebrow_L", "eyebrow_R", "hair_front", "neck", "clothes_bottom",
    "sock_L", "sock_R", "accessories",
]


def _map_names(names):
    return Layer52Generator().map_layers_to_standard(
        [{"part_name": name, "path": f"{name}.png"} for name in names]
    )


def test_composer_layer_names_map_to_the_correct_slot_and_side():
    by_id = {
        m["standard_id"]: m["source_file"].split("/")[-1].removesuffix(".png")
        for m in _map_names(COMPOSER_LAYERS)["mappings"]
        if m["mapped"]
    }
    # 语义不能被「候选列表第一个」抢走
    assert by_id[5] == "neck"          # 不是 cheek
    assert by_id[24] == "cheek"        # 腮红
    assert by_id[36] == "irides_left"  # 虹膜左，不是胸腔
    assert 7 not in by_id
    assert 20 not in by_id
    assert 49 not in by_id
    # 左右必须同侧
    assert by_id[45] == "eyebrow_R"
    assert by_id[46] == "eyebrow_L"
    assert 33 not in by_id
    assert by_id[34] == "eyewhite_left"


def test_layers_without_a_52_slot_are_not_forced_into_ears_or_accessories():
    result = _map_names(COMPOSER_LAYERS)
    unmapped = {item["part_name"] for item in result["unmapped_parts"]}
    assert {"tear_left", "tear_right", "skirt_hem_L", "skirt_hem_R",
            "sock_L", "sock_R", "clothes_bottom"} <= unmapped

    mapped_sources = {m["source_file"].split("/")[-1].removesuffix(".png")
                      for m in result["mappings"] if m["mapped"]}
    assert not ({"tear_left", "tear_right"} & mapped_sources)
    # 耳朵层只允许真正的耳部件占用
    assert not ({m["standard_id"] for m in result["mappings"]
                 if m["mapped"]} & EAR_LAYERS)
    assert "skirt_hem_L" not in mapped_sources


def test_unrecognized_names_do_not_consume_arbitrary_layers():
    result = _map_names(["mystery_part"])
    assert result["mapped_layers"] == 0
    assert [item["part_name"] for item in result["unmapped_parts"]] == ["mystery_part"]


def test_whole_eye_and_front_hair_are_recorded_as_split_targets():
    result = _map_names(["eyes_left", "hair_front"])
    split = {item["part_name"]: item for item in result["split_required"]}
    assert split["eyes_left"]["primary"] == 34
    assert split["hair_front"]["primary"] == 49
    assert split["hair_front"]["standard_ids"] == [47, 48, 49, 50, 51]


@pytest.mark.parametrize("name", ["waistband", "waistband_L", "Waistband_Right"])
def test_waistband_never_claims_a_body_slot(name):
    result = _map_names([name])
    assert result["mapped_layers"] == 0
    assert result["unmapped_parts"][0]["part_name"] == name
    assert 7 in {m["standard_id"] for m in result["missing_required"]}


@pytest.mark.parametrize("name", [
    "body", "skin", "eyes_left", "eye_right", "eyebrow", "ear",
    "clothes", "clothes_top", "collar", "hair_front", "leg_L", "arm_R",
    "skin_chest", "unknown_eye_white_left", "脖子_身体",
])
def test_coarse_or_unrecognized_parts_do_not_claim_fine_slots(name):
    result = _map_names([name])
    assert result["mapped_layers"] == 0
    assert result["split_required"] or result["unmapped_parts"]


def test_repeated_left_part_does_not_fill_missing_right_slot():
    result = _map_names(["eyebrow_L", "eyebrow_left"])
    assert [m["standard_id"] for m in result["mappings"] if m["mapped"]] == [46]
    assert 45 in {m["standard_id"] for m in result["missing_required"]}
    assert result["unmapped_parts"][0]["part_name"] == "eyebrow_left"


@pytest.mark.parametrize("name, standard_id", [
    ("Chest", 6), ("Waist_Hips", 7), ("Thigh_Left", 8),
    ("Calf_Right", 11), ("Foot_Left", 12), ("UpperArm_Back_Left", 14),
    ("Forearm_Back_Right", 17), ("Hand_Back_Left", 18),
    ("clothes_inner", 20), ("eye_white_left", 34),
    # 肘/膝分段产出的实际层名：必须落到同侧的单层槽位。
    ("thigh_L", 8), ("calf_R", 11),
    ("arm_upper_L", 14), ("arm_lower_R", 17), ("hand_L", 18),
])
def test_explicit_fine_parts_map_to_their_exact_standard_slot(name, standard_id):
    result = _map_names([name])
    assert [m["standard_id"] for m in result["mappings"] if m["mapped"]] == [standard_id]
    assert result["split_required"] == []

