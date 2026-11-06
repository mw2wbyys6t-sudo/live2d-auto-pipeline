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


def test_expression_fade_layers_follow_the_single_head_field():
    """腮红 / 眼泪不再各拟合一份头部三轴，而是落进**唯一**那条 HeadRig。

    契约的来处（2026-11-01 实测）：这两类层的网格键形带被**不透明度**
    （ParamCheek / ParamTears）占满，头部三轴进不去，当时没有 warp 就把它们留在原地
    —— ``ParamAngleX/Y/Z = ±30`` 下 ``cheek`` / ``tear_left`` / ``tear_right`` 位移
    **0px**，同姿势脸移 13.15px，腮红与泪痕滑出脸外。所以「必须随头转」这条**没变**，
    变的是由谁负责：Task 3 把 ``CheekFollow`` / ``TearFollow`` 两条各自拟合三轴的
    4×4 网格删掉（它们是头内部 27.1272px 漂移的六个独立场之二），层名改由
    ``HeadRig.targets`` 显式接住。断言因此从「这两个条目存在且 profile=head」
    改成「这三层真的挂在 HeadRig 上」，语义不变、持有者唯一化。
    """
    names = REAL_LAYER_NAMES + ["cheek", "tear_left", "tear_right"]
    warp = {d["name"]: d for d in _build(names)["deformers"] if d["type"] == "warp"}
    assert "CheekFollow" not in warp and "TearFollow" not in warp
    assert warp["HeadRig"]["profile"] == "head"
    assert warp["HeadRig"]["parameters"] == [
        "ParamAngleX", "ParamAngleY", "ParamAngleZ"]
    assert {"cheek", "tear_left", "tear_right"} <= set(warp["HeadRig"]["targets"])


def test_absent_expression_layers_do_not_create_their_deformers():
    """层不存在就别建空壳变形器：否则会白占一个名额并出现在统计里。"""
    names = [d["name"] for d in _build(REAL_LAYER_NAMES)["deformers"]]
    assert "CheekFollow" not in names and "TearFollow" not in names


def test_the_fade_follow_deformers_are_gone_even_with_the_layers_present():
    """正常场景里这两个名字也**恒不出现** —— 它们是被设计删掉的，不是被过滤掉的。

    上一条走的是「层不存在 → 不建」的降级路径；只看那条会把「条目还在表里、
    只是这次没命中」误读成已删干净。删条目这件事本身要在层齐全时也可观察。
    """
    from live2d_builder.bones.deformers import DeformerHierarchy
    authored = [e["name"] for e in DeformerHierarchy.DEFAULT_WARP_DEFORMERS]
    assert "CheekFollow" not in authored and "TearFollow" not in authored
    names = REAL_LAYER_NAMES + ["cheek", "tear_left", "tear_right"]
    assert all(d["name"] not in ("CheekFollow", "TearFollow")
               for d in _build(names)["deformers"])


def test_shoulder_lift_carries_cuff_and_hand_with_the_arm():
    """袖口与手是小臂末端的独立图层，必须挂在同一条肩部变形器上。

    实测（2026-11-01，singer 导出件）：``ParamShoulderL = 1`` 时 ``arm_L``
    抬 38.40px，而 ``cuff_L`` / ``hand_L`` 位移 **0px**；``ParamShoulderR = 1``
    时 ``arm_R`` 抬 56.88px，``cuff_R`` / ``hand_R`` 0px —— 腕口被拉开。
    """
    out = _build(REAL_LAYER_NAMES)
    warp = {d["name"]: d for d in out["deformers"] if d["type"] == "warp"}
    assert warp["ShoulderLift_L"]["targets"] == ["arm_L", "cuff_L", "hand_L"]
    assert warp["ShoulderLift_R"]["targets"] == ["arm_R", "cuff_R", "hand_R"]


def test_shoulder_lift_targets_shrink_to_what_really_exists():
    """分层里没有袖口 / 手时不该凭空多出目标（命中过滤照常生效）。"""
    out = _build(["arm_L", "arm_R"])
    warp = {d["name"]: d for d in out["deformers"] if d["type"] == "warp"}
    assert warp["ShoulderLift_L"]["targets"] == ["arm_L"]
    assert warp["ShoulderLift_R"]["targets"] == ["arm_R"]


def test_neck_leans_with_the_torso():
    """领口画在脖子上：脖子进入分级颈场后，仍须通过 NeckBand→BodySway 继承躯干倾摆。"""
    out = _build(REAL_LAYER_NAMES)
    warp = {d["name"]: d for d in out["deformers"] if d["type"] == "warp"}
    assert "neck" in warp["NeckBand"]["targets"]
    assert warp["NeckBand"]["under"] == "BodySway"


def test_shoulder_lift_also_carries_the_body_lean():
    """抬肩那条变形器要同时接住躯干的四个倾摆轴，手臂才会跟着身子一起摆。"""
    out = _build(REAL_LAYER_NAMES)
    warp = {d["name"]: d for d in out["deformers"] if d["type"] == "warp"}
    assert warp["ShoulderLift_L"]["profile"] == "lift_lean"
    assert warp["ShoulderLift_L"]["parameters"] == [
        "ParamShoulderL", "ParamBodyAngleX", "ParamBodyAngleY",
        "ParamBodySway", "ParamBreath"]
