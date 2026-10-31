"""rotation 变形器的「建不出来」必须可查，不能静默消失。

背景（2026-10-21 实证）：``DeformerHierarchy.DEFAULT_ROTATION_DEFORMERS`` 声明的
targets 是 ``eyeball_l`` / ``eye_left``，而分层阶段实际产出的层名是
``eyewhite_left`` / ``irides_left`` / ``eyelid_left``。两套名字对不上，
真实流水线里 rotation 变形器**一个都不会创建**。

这件事必须显式记录：否则「模型里没有 rotation 变形器」会被误读成
「rotation 已支持、只是没绑参数」，而这两者的修法完全不同。
"""
from live2d_builder.bones.deformers import DeformerHierarchy

#: 取自 Work/base 的真实分层产出（rigged_1792572342/layers/，去掉序号前缀）。
REAL_LAYER_NAMES = [
    "hair_back", "face", "eyelid_left", "eyelid_right",
    "eyewhite_left", "eyewhite_right", "irides_left", "irides_right",
    "hair_front", "eyebrow_L", "eyebrow_R", "nose", "mouth_cavity", "mouth",
    "neck", "clothes_top", "waistband", "collar", "cuff_L", "cuff_R",
    "arm_L", "arm_R", "hand_L", "hand_R", "clothes_bottom",
    "skirt_hem_L", "skirt_hem_R", "leg_L", "leg_R", "sock_L",
    "sock_R", "shoes_L",
]


def _meshes(names):
    return {n: {"vertices": [[0, 0], [10, 0], [10, 10], [0, 10]]} for n in names}


def _build(names):
    return DeformerHierarchy().build({}, _meshes(names), layer_names=names)


def test_real_layer_names_produce_no_rotation_deformer():
    """真实层名下只有 warp 变形器 —— 这条锁住「rotation 变形器未被创建」的事实。"""
    out = _build(REAL_LAYER_NAMES)
    types = {d["name"]: d["type"] for d in out["deformers"]}
    assert types, "warp 变形器应当照常建出来"
    assert set(types.values()) == {"warp"}, types
    assert "EyeTrack_L" not in types and "EyeTrack_R" not in types


def test_numeric_index_prefix_does_not_lose_warp_deformers():
    """``000_hair_back`` 这类 PSD 导入名必须与 ``hair_back`` 等价。

    ``_normalise`` 早就会去掉序号前缀（骨骼查找一直在用），但 ``build`` 里的
    ``available_norm`` 忘了走同一套归一化：同样的图只是多带了个序号，warp 变形器
    就全部落空 —— 实测（2026-11-01，singer 34 张分层）11 个 → 0 个，
    可驱动参数 27 → 6，而构建与官方内核验收**都照样报通过**。
    """
    plain = _build(REAL_LAYER_NAMES)
    prefixed = _build([f"{i:03d}_{n}" for i, n in enumerate(REAL_LAYER_NAMES)])
    assert [d["name"] for d in prefixed["deformers"]] == [
        d["name"] for d in plain["deformers"]]


def test_skipped_rotation_deformers_are_reported_with_a_reason():
    """被跳过的 rotation 变形器必须给出名字、原因与它期望的层名。"""
    out = _build(REAL_LAYER_NAMES)
    skipped = out["rotation_skipped"]
    assert [d["name"] for d in skipped] == ["EyeTrack_L", "EyeTrack_R"]
    for entry in skipped:
        assert entry["reason"] == "目标层名未命中"
        assert entry["targets"], "必须能看出它原本想挂哪些层"
        assert entry["available_sample"], "必须附上真实层名样本，便于对照"


def test_rotation_deformer_is_still_created_when_targets_really_exist():
    """功能本身没被删掉：层名真的命中时，rotation 变形器照旧创建。

    这一条防止后人为了「消掉告警」把 DEFAULT_ROTATION_DEFORMERS 直接删空，
    从而连带废掉真实存在的 rotation 能力。
    """
    names = REAL_LAYER_NAMES + ["eyeball_l", "eyeball_r"]
    out = _build(names)
    rotation = [d for d in out["deformers"] if d["type"] == "rotation"]
    assert [d["name"] for d in rotation] == ["EyeTrack_L", "EyeTrack_R"]
    assert out["rotation_skipped"] == []


def test_no_rotation_skips_are_reported_when_all_targets_match():
    """全部命中时不留噪音：rotation_skipped 为空列表，不是缺键。"""
    out = _build(REAL_LAYER_NAMES + ["eyeball_l", "eyeball_r"])
    assert out["rotation_skipped"] == []
