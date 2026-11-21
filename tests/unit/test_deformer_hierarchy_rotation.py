"""rotation 变形器的「建不出来」必须可查，不能静默消失。

背景（2026-10-21 实证）：默认 rotation 表里曾声明 ``EyeTrack_L/R``，targets 写
``eyeball_l`` / ``eye_left``，而分层阶段实际产出的层名是 ``eyewhite_left`` /
``irides_left`` / ``eyelid_left``。两套名字对不上，真实流水线里这两条**一个都
不会创建**，于是每次构建都打一条「rotation 变形器未创建 2」——看起来像缺件。

2026-11-18 对照上游逆向资料确认：眼球跟随由 ParamEyeBallX/Y 的双轴网格键形
（``moc3_pipeline.eye_track_keyforms``）负责，不应再叠一层 rotation 变形器。
两条"不落地"的定义因此删除；下面保留的是**判定机制**的用例（目标没命中时
必须给出名字、原因与期望层名）。
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


def test_real_layer_names_produce_head_arm_and_leg_rotations():
    """真实层名下的 rotation：头 + 双臂 + 双腿（髋/膝各一节）。

    ``HeadRotationZ`` / ``ArmRotation_*`` 没有直接成员网格，靠 ``under`` 链转
    各自子树；``LegRotation_*`` / ``LegLowerRotation_*`` 直接带成员（腿部图层
    本来就不属于任何 warp）。它们都不依赖层名匹配的「空成员」技巧，所以真实层名
    下必须建出来。眼球跟随不是 rotation 变形器（由 ParamEyeBallX/Y 键形负责）。
    """
    out = _build(REAL_LAYER_NAMES)
    types = {d["name"]: d["type"] for d in out["deformers"]}
    assert types, "warp 变形器应当照常建出来"
    assert set(types.values()) == {"warp", "rotation"}, types
    assert [n for n, t in types.items() if t == "rotation"] == [
        "HeadRotationZ", "ArmRotation_L", "ArmRotation_R",
        "ForearmRotation_L", "ForearmRotation_R",
        "LegRotation_L", "LegLowerRotation_L", "LegRotation_R", "LegLowerRotation_R"]
    assert out["rotation_skipped"] == []


def test_leg_rotations_are_chained_hip_then_knee():
    """髋转带动整条腿，膝转只动小腿及以下 —— 走路/屈膝才能在一条链上成立。

    腿部图层此前不属于任何变形器，所以这里可以直接带成员（不像手臂要躲开
    肩部抬落 warp）。枢轴取 ``Leg_*`` / ``Calf_*``，绑定阶段据此命中
    ``ParamLegLA/LB`` 与 ``ParamLegRA/RB``。
    """
    out = _build(REAL_LAYER_NAMES)
    by_name = {d["name"]: d for d in out["deformers"]}
    for side in ("L", "R"):
        hip = by_name[f"LegRotation_{side}"]
        knee = by_name[f"LegLowerRotation_{side}"]
        assert hip["pivot_bone"] == f"Leg_{side}"
        assert knee["pivot_bone"] == f"Calf_{side}"
        assert knee["under"] == f"LegRotation_{side}"
        # 成员不重叠：大腿归髋、小腿及以下归膝
        assert set(hip["targets"]).isdisjoint(knee["targets"])


def test_leg_rotations_are_skipped_when_leg_layers_are_missing():
    """半身像（没有腿）不该产出四条空转的腿部 rotation。"""
    out = _build(["hair_back", "face", "clothes_top", "arm_L", "arm_R"])
    names = [d["name"] for d in out["deformers"]]
    assert not [n for n in names if n.startswith("Leg")]
    skipped = {d["name"] for d in out["rotation_skipped"]}
    assert {"LegRotation_L", "LegLowerRotation_L",
            "LegRotation_R", "LegLowerRotation_R"} <= skipped


def test_arm_rotation_is_the_parent_of_the_shoulder_lift():
    """手臂转动必须挂在肩部抬落**上面**当父级，而不是去抢手臂成员。

    手臂三层（上臂 / 袖口 / 手）刻意共用 ``ShoulderLift_*``（拆开会在腕口裂缝），
    所以 rotation 只能靠 ``under`` 链旋转整棵子树。枢轴用 ``ArmBack_*`` 骨骼 ——
    绑定阶段据此命中 ``ParamArmLA/RA``。
    """
    out = _build(REAL_LAYER_NAMES)
    by_name = {d["name"]: d for d in out["deformers"]}
    for side in ("L", "R"):
        rot = by_name[f"ArmRotation_{side}"]
        assert rot["type"] == "rotation"
        assert rot["pivot_bone"] == f"ArmBack_{side}"
        assert rot["targets"] == [], "抢成员会把腕口拉裂，成员仍归肩部抬落"
        lift = by_name[f"ShoulderLift_{side}"]
        assert lift["under"] == f"ArmRotation_{side}"


def test_arm_rotation_is_not_built_without_the_arm_layers():
    """没有手臂图层就不该建空转的 rotation：否则多一个「拨了没画面」的滑块。"""
    out = _build(["hair_back", "face", "clothes_top"])
    names = [d["name"] for d in out["deformers"]]
    assert "ArmRotation_L" not in names and "ArmRotation_R" not in names
    skipped = {d["name"]: d["reason"] for d in out["rotation_skipped"]}
    assert {"ArmRotation_L", "ArmRotation_R"} <= set(skipped)
    assert "ShoulderLift_L" in skipped["ArmRotation_L"]


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


def test_skipped_rotation_deformers_are_reported_with_a_reason(monkeypatch):
    """目标没命中的 rotation 变形器必须给出名字、原因与它期望的层名。"""
    spec = {"name": "ProbeRotation", "parent": "Eye_L", "pivot": (-35, -40),
            "type": "rotation", "targets": ["no_such_layer"]}
    monkeypatch.setattr(
        DeformerHierarchy, "DEFAULT_ROTATION_DEFORMERS",
        DeformerHierarchy.DEFAULT_ROTATION_DEFORMERS + [spec])

    out = _build(REAL_LAYER_NAMES)

    skipped = out["rotation_skipped"]
    assert [d["name"] for d in skipped] == ["ProbeRotation"]
    entry = skipped[0]
    assert entry["reason"] == "目标层名未命中"
    assert entry["targets"] == ["no_such_layer"], "必须能看出它原本想挂哪些层"
    assert entry["available_sample"], "必须附上真实层名样本，便于对照"


def test_rotation_deformer_is_still_created_when_targets_really_exist(monkeypatch):
    """功能本身没被删掉：层名真的命中时，rotation 变形器照旧创建。

    这一条防止后人为了「消掉告警」把判定逻辑一起删掉，从而连带废掉 rotation 能力。
    """
    spec = {"name": "ProbeRotation", "parent": "Eye_L", "pivot": (-35, -40),
            "type": "rotation", "targets": ["probe_layer"]}
    monkeypatch.setattr(
        DeformerHierarchy, "DEFAULT_ROTATION_DEFORMERS",
        DeformerHierarchy.DEFAULT_ROTATION_DEFORMERS + [spec])

    # 用一个没被任何默认变形器占用的层名：claimed 是先到先得，借用手臂/腿部
    # 现成层名会被上一条 rotation 抢走，从而误判成「rotation 不再创建」。
    out = _build(REAL_LAYER_NAMES + ["probe_layer"])

    rotation = [d for d in out["deformers"] if d["type"] == "rotation"]
    assert [d["name"] for d in rotation] == [
        "HeadRotationZ", "ArmRotation_L", "ArmRotation_R",
        "ForearmRotation_L", "ForearmRotation_R",
        "LegRotation_L", "LegLowerRotation_L", "LegRotation_R", "LegLowerRotation_R",
        "ProbeRotation"]
    assert out["rotation_skipped"] == []
    probe = next(d for d in rotation if d["name"] == "ProbeRotation")
    assert probe["targets"] == ["probe_layer"]


def test_no_rotation_skips_are_reported_for_the_shipped_defaults():
    """默认表全部能落地：rotation_skipped 为空列表，不是缺键。"""
    out = _build(REAL_LAYER_NAMES)
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
    out = _build(names)
    warp = {d["name"]: d for d in out["deformers"] if d["type"] == "warp"}
    assert "CheekFollow" not in warp and "TearFollow" not in warp
    # Z 归 HeadRotationZ（rotation），HeadRig 只剩 X/Y —— profile 也必须两轴。
    assert warp["HeadRig"]["profile"] == "head_xy"
    assert warp["HeadRig"]["parameters"] == ["ParamAngleX", "ParamAngleY"]
    rotation = {d["name"]: d for d in out["deformers"] if d["type"] == "rotation"}
    assert rotation["HeadRotationZ"]["under"] == "BodySway"
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


def test_shoulder_lift_carries_cuff_and_hand_through_the_forearm_rotation():
    """袖口与手必须在抬肩的**子树**里 —— 现在它们挂在肘关节上，靠 ``under`` 继承。

    实测（2026-11-01，singer 导出件）：``ParamShoulderL = 1`` 时 ``arm_L``
    抬 38.40px，而 ``cuff_L`` / ``hand_L`` 位移 **0px**；``ParamShoulderR = 1``
    时 ``arm_R`` 抬 56.88px，``cuff_R`` / ``hand_R`` 0px —— 腕口被拉开。

    2026-11-21 拆肘关节后，这条不变量从「同一条变形器的成员」升级成
    「同一条变形器的**子树**成员」：抬落仍是父级 warp，肘在它下面。
    父级静止矩形按子孙并集计算，所以腕口依然落在父网格之内、抬落幅度不变
    （``moc3_pipeline.rect_covering_descendants``）。
    """
    out = _build(REAL_LAYER_NAMES)
    by_name = {d["name"]: d for d in out["deformers"]}
    for side in ("L", "R"):
        lift = by_name[f"ShoulderLift_{side}"]
        elbow = by_name[f"ForearmRotation_{side}"]
        assert elbow["under"] == f"ShoulderLift_{side}"
        assert set(lift["targets"]) == {f"arm_{side}"}
        assert {f"cuff_{side}", f"hand_{side}"} <= set(elbow["targets"])


def test_shoulder_lift_recognises_arm_upper_naming():
    """SAM 分层产出的是 ``arm_upper_*``，抬肩 warp 必须认得这个写法。

    拆分前这条不变量是靠「袖口/手也在 targets 里」兜住的；袖口/手归肘关节后，
    若 targets 只写 ``arm_*``，本图一个都命中不了 → 抬落 warp、肩转动、
    肘关节三条一起消失（2026-11-21 实测：19 条变形器掉到 17 条）。
    """
    names = ["arm_upper_L", "arm_lower_L", "cuff_L", "hand_L",
             "arm_upper_R", "arm_lower_R", "cuff_R", "hand_R"]
    out = _build(names)
    by_name = {d["name"]: d for d in out["deformers"]}
    for side in ("L", "R"):
        assert by_name[f"ShoulderLift_{side}"]["targets"] == [f"arm_upper_{side}"]
        assert set(by_name[f"ForearmRotation_{side}"]["targets"]) == {
            f"arm_lower_{side}", f"cuff_{side}", f"hand_{side}"}


def test_forearm_rotation_pivots_on_the_elbow_bone():
    """肘关节绕 ForearmBack_* 转，绑定阶段据此命中 ParamArmLB/RB。"""
    out = _build(REAL_LAYER_NAMES)
    by_name = {d["name"]: d for d in out["deformers"]}
    for side in ("L", "R"):
        elbow = by_name[f"ForearmRotation_{side}"]
        assert elbow["type"] == "rotation"
        assert elbow["pivot_bone"] == f"ForearmBack_{side}"
        assert elbow["targets"]


def test_forearm_rotation_is_skipped_without_the_arm():
    """没有上臂就没有抬落 warp，肘关节也不该建。"""
    out = _build(["hair_back", "face", "clothes_top", "cuff_L", "hand_L"])
    names = [d["name"] for d in out["deformers"]]
    assert "ForearmRotation_L" not in names and "ForearmRotation_R" not in names


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
