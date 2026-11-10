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


def test_topwear_maps_to_clothes_outer_not_ear():
    result = Layer52Generator().map_layers_to_standard(
        [{"part_name": "topwear", "path": "topwear.png"}]
    )
    mapped = [m for m in result["mappings"] if m["mapped"]]
    assert len(mapped) == 1
    assert mapped[0]["standard_id"] == 21
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


def test_needs_split_parts_fall_to_a_single_primary_layer():
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
    assert sorted(mapped_ids) == [1, 25, 28, 33]
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
