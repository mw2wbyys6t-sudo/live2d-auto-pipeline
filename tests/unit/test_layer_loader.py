"""导出前的图层装载：全仓唯一实现。

背景（实测，2026-10）：选层逻辑曾被内联复制到 3 个调用点 ——
``api/services/python_bridge.go``、``api/services/stage_progress.go``、
``api_server._load_layers_for_export``。其中**只有 1 处**做了正确的两项处理：
剔除整图预览、按 ``draw_index`` 排序。而生产导出实际走的是
``stage_progress.go``（``wsHub`` 恒非 nil），所以那份副本的两个缺陷至今仍在
产出「验收全绿但画面是一张静止的平图」。

这组用例锁住收敛后的单一实现必须具备的性质。
"""

from __future__ import annotations

import json

import pytest
from PIL import Image

from live2d_builder.layer_loader import (
    NON_LAYER_FILES,
    load_export_inputs,
    load_export_layer_inputs,
    load_layers_for_export,
)

_CANVAS = (32, 32)


def _write_layer(directory, name: str, alpha: int = 255):
    Image.new("RGBA", _CANVAS, (200, 150, 180, alpha)).save(directory / f"{name}.png")


def _write_guide(directory, entries):
    (directory / "layer_guide.json").write_text(
        json.dumps({"layers": [
            {"name": name, "draw_index": index} for name, index in entries
        ]}, ensure_ascii=False), encoding="utf-8")


def test_full_canvas_previews_are_never_layers(tmp_path):
    """满画布预览图不得进入图层集合 —— 否则模型会被压成静止的图。"""
    for name in ("hair_back", "face", "mouth", "mouth_cavity"):
        _write_layer(tmp_path, name)
    for name in sorted(NON_LAYER_FILES):
        _write_layer(tmp_path, name)      # 真实缺陷条件：同目录、同后缀

    inputs = load_export_inputs(str(tmp_path))

    assert sorted(inputs.paths) == ["face", "hair_back", "mouth", "mouth_cavity"]
    assert not (set(inputs.paths) & NON_LAYER_FILES)


def test_order_follows_draw_index_not_filename(tmp_path):
    """顺序以 draw_index 为权威；字母序会让 mouth_cavity 盖住 mouth。"""
    for name in ("hair_back", "face", "mouth", "mouth_cavity"):
        _write_layer(tmp_path, name)
    _write_guide(tmp_path, [
        ("hair_back", 0), ("face", 1), ("mouth_cavity", 14), ("mouth", 15)])

    inputs = load_export_inputs(str(tmp_path))

    assert inputs.order_source == "layer_guide"
    assert list(inputs.paths) == ["hair_back", "face", "mouth_cavity", "mouth"]
    assert list(inputs.paths).index("mouth_cavity") < list(inputs.paths).index("mouth")


def test_guide_rest_opacity_is_restored_for_export(tmp_path):
    for name in ("face", "cheek", "tear_left"):
        _write_layer(tmp_path, name)
    (tmp_path / "layer_guide.json").write_text(json.dumps({"layers": [
        {"name": "face", "draw_index": 0},
        {"name": "cheek", "draw_index": 1, "rest_opacity": 0.0},
        {"name": "tear_left", "draw_index": 2, "rest_opacity": 0.35},
    ]}), encoding="utf-8")

    inputs = load_export_inputs(str(tmp_path))

    assert inputs.layer_opacity == {"cheek": 0.0, "tear_left": 0.35}


def test_guide_without_rest_opacity_keeps_legacy_default(tmp_path):
    _write_layer(tmp_path, "face")
    _write_guide(tmp_path, [("face", 0)])

    assert load_export_inputs(str(tmp_path)).layer_opacity == {}


def test_missing_guide_falls_back_to_filename_and_says_so(tmp_path):
    """缺 guide 时如实报告降级；但层序仍以权威表为准，不按字母序硬排。

    字母序正是本模块记录过的历史缺陷（``mouth`` 排在 ``mouth_cavity`` 之前 →
    口腔盖住嘴唇）。既然权威表能给出正确次序，降级路径没有理由退回一个已知
    错误的顺序；表外的层仍在彼此之间保持字母序。
    """
    for name in ("face", "hair_back", "mouth"):
        _write_layer(tmp_path, name)

    inputs = load_export_inputs(str(tmp_path))

    assert inputs.order_source == "filename"
    assert list(inputs.paths) == ["hair_back", "face", "mouth"]
    assert inputs.unknown == sorted(inputs.paths)


def test_layers_absent_from_guide_are_reported(tmp_path):
    """词表漂移必须可见：未登记层被点名，而不是静默兜底。"""
    for name in ("face", "mystery_part"):
        _write_layer(tmp_path, name)
    _write_guide(tmp_path, [("face", 0)])

    inputs = load_export_inputs(str(tmp_path))

    assert inputs.unknown == ["mystery_part"]
    assert "mystery_part" in inputs.paths       # 仍参与构建，只是排在最前


def test_legacy_draw_index_is_normalized_to_the_authoritative_table(tmp_path):
    """层序权威表 2026-10-21 从 18 项扩到 65 项并改了 face 语义。

    更早的分层目录里 ``draw_index`` 是按旧表算的（脸/嘴被甩到刘海之后）。导出
    若照抄它，编译出的 moc3 层序与现行 FF-2 契约不符，下载闸门会**永远**拒绝
    这个产物（实测 issue_count=10，409 无解）。因此顺序的唯一真源是权威表，
    ``draw_index`` 只在表未覆盖时决定相对次序。重排对静止画面逐像素零差异，
    改的只是部件运动时的遮挡关系。
    """
    for name in ("hair_back", "hair_front", "eyebrows", "face", "mouth"):
        _write_layer(tmp_path, name)
    _write_guide(tmp_path, [
        ("hair_back", 0), ("hair_front", 1), ("eyebrows", 2), ("face", 3), ("mouth", 4)])

    inputs = load_export_inputs(str(tmp_path))

    assert list(inputs.paths) == ["hair_back", "face", "hair_front", "eyebrows", "mouth"]
    assert inputs.normalized is True


def test_guide_already_matching_the_table_is_left_alone(tmp_path):
    """本来就合规的分层目录不得被改动，也不得谎报「已重排」。"""
    for name in ("hair_back", "face", "mouth_cavity", "mouth"):
        _write_layer(tmp_path, name)
    _write_guide(tmp_path, [
        ("hair_back", 0), ("face", 1), ("mouth_cavity", 14), ("mouth", 15)])

    inputs = load_export_inputs(str(tmp_path))

    assert list(inputs.paths) == ["hair_back", "face", "mouth_cavity", "mouth"]
    assert inputs.normalized is False


def test_psd_index_prefix_is_stripped_from_layer_names(tmp_path):
    """PSD 导入的 ``NNN_`` 序号前缀不得进入层名。

    序号只表示画序，不带语义；而下游（变形器目标、网格键形、参数带）全部按语义名
    精确比对。实测（2026-11-01，singer 那批 34 张分层）：无前缀 = 11 个变形器 /
    27 个可驱动参数，加 ``000_`` 前缀 = 0 个变形器 / 6 个可驱动参数 —— 而构建照样
    报 valid，成片是「不闭嘴、不眨眼、不呼吸」却一句错都不报。
    """
    for index, name in enumerate(("hair_back", "face", "mouth")):
        _write_layer(tmp_path, f"{index:03d}_{name}")

    inputs = load_export_inputs(str(tmp_path))

    assert sorted(inputs.paths) == ["face", "hair_back", "mouth"]


def test_stripping_never_merges_two_layers(tmp_path):
    """去掉前缀后撞名时保留原名：宁可这一层匹配不上，也不能少画一层。"""
    _write_layer(tmp_path, "000_hair_back")
    _write_layer(tmp_path, "010_hair_back")

    inputs = load_export_inputs(str(tmp_path))

    assert list(inputs.paths) == ["000_hair_back", "010_hair_back"]


def test_empty_directory_raises_instead_of_silently_returning_nothing(tmp_path):
    """只有预览图 / 空目录时必须明确失败，不能返回空模型。"""
    _write_layer(tmp_path, "preview")
    _write_layer(tmp_path, "composite_preview")

    with pytest.raises(FileNotFoundError):
        load_export_inputs(str(tmp_path))


def test_compat_wrappers_agree_on_order(tmp_path):
    """两个兼容入口必须给出与主入口一致的顺序（Go 脚本用前者、api_server 用后者）。"""
    for name in ("hair_back", "face", "mouth", "mouth_cavity"):
        _write_layer(tmp_path, name)
    _write_guide(tmp_path, [
        ("hair_back", 0), ("face", 1), ("mouth_cavity", 14), ("mouth", 15)])

    main = load_export_inputs(str(tmp_path)).paths
    paths = load_export_layer_inputs(str(tmp_path))
    images = load_layers_for_export(str(tmp_path))

    assert list(paths) == list(main)
    assert list(images) == list(main)
    assert all(getattr(img, "mode", None) == "RGBA" for img in images.values())


def test_readme_of_behaviour_no_inline_glob_left_behind():
    """三处调用点都不得再内联选层逻辑（否则副本会再次漂移）。

    这是本次收敛的唯一防线：脚本以字符串嵌在 Go 里、无法 import 测试，
    只能靠源码级断言守住「不要再复制一份」。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders = []
    for rel in ("api/services/python_bridge.go", "api/services/stage_progress.go"):
        text = (root / rel).read_text(encoding="utf-8")
        if 'glob.glob(os.path.join(layers_dir, "*.png"))' in text:
            offenders.append(rel)

    # api_server 只查「把 glob 结果读成 PIL.Image」的真实图层装载逻辑
    # （用 AST 抽调用）。目录探测 `any(d.glob("*.png"))` 与注释、docstring
    # 都不算 —— 后者正是在解释为什么要收敛到这里。
    import ast

    api_server = (root / "api_server.py").read_text(encoding="utf-8")
    tree = ast.parse(api_server)
    glob_calls = {
        id(node) for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "glob"
        and '"*.png"' in (ast.get_source_segment(api_server, node) or "")
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else "")
        if name not in ("open", "convert"):
            continue
        if any(id(child) in glob_calls for child in ast.walk(node)):
            code = ast.get_source_segment(api_server, node) or ""
            offenders.append(f"api_server.py: {code.strip()[:70]}")

    assert offenders == [], f"这些调用点又内联了选层逻辑: {offenders}"


# ---------------------------------------------------------------------------
# 部件齐不齐：导出侧必须读分层清单自己的结论
#
# 实测（2026-10-30，已安装的 v0.10.10）：一张纯色无结构图分层后
# ``layer_manifest.json`` 写着 ``preview_ready=false`` + 缺 mouth/neck/... 与
# 警告「缺少必要部件，无法进入预览/分层检查」，而 ``/api/export/live2d`` 仍返回
# ``runtime_ready=true`` 并产出 moc3。两份结论同时成立 = 产品在自相矛盾地报成功。
# ---------------------------------------------------------------------------

def _write_manifest(directory, payload):
    (directory / "layer_manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_manifest_says_unusable_and_export_inputs_carry_it(tmp_path):
    """清单判定部件连预览都不够时，导出入参必须带上这个结论与缺项。"""
    for name in ("hair_back", "body"):
        _write_layer(tmp_path, name)
    _write_manifest(tmp_path, {
        "preview_ready": False,
        "missing_preview": ["face_base", "mouth", "neck", "clothes_top"],
    })

    inputs = load_export_inputs(str(tmp_path))

    assert inputs.parts_ready is False
    assert inputs.missing_preview == ["face_base", "mouth", "neck", "clothes_top"]
    # 但**不算空心**：hair_back 认出来了，这是"不完美"而不是"根本没认出人"。
    # 实测生产默认的 semantic 路径只缺 mouth 也落在这个区间（本机 14 个真实
    # 分层里 10 个 preview_ready=false），拿它拒绝出货会拦死正常作品。
    assert inputs.parts_hollow is False


def test_manifest_with_no_part_recognized_is_hollow(tmp_path):
    """5 个必需部件一个都没认出来 = 空心，这才是拒绝构建的那条线。"""
    _write_layer(tmp_path, "色块_1")
    _write_manifest(tmp_path, {
        "preview_ready": False,
        "source": "color_cluster",
        "missing_preview": ["hair_back", "face_base", "mouth", "neck", "clothes_top"],
    })

    inputs = load_export_inputs(str(tmp_path))

    assert inputs.parts_hollow is True
    assert inputs.parts_source == "color_cluster"


def test_blocker_message_names_parts_and_route():
    """拒绝理由必须说清缺什么、为什么、下一步做什么 —— 而且要分来源。"""
    from live2d_builder.layer_loader import parts_blocker_message
    from core.segment_engine.layer_manifest import REQUIRED_FOR_PREVIEW

    hollow = parts_blocker_message(list(REQUIRED_FOR_PREVIEW), "color_cluster")
    assert "头发_后" in hollow, "部件要用中文名说，不是把 id 甩给用户"
    assert "颜色聚类" in hollow, "颜色聚类的成因要说出口（换提示词没用）"
    assert "语义分层" in hollow and "导入" in hollow, "要给可执行的下一步"

    upstream = parts_blocker_message(list(REQUIRED_FOR_PREVIEW), "semantic")
    assert "颜色聚类" not in upstream, "语义分层空心不该怪到颜色聚类头上"
    assert "未按提示词" in upstream


def test_blocker_message_is_not_used_for_imperfect_layering(tmp_path):
    """只有空心才该被拒；"缺一个 mouth" 的分层仍然要能导出。

    这条是防误伤的边界：闸门一旦收紧到 preview_ready，用户已有的作品就全废了。
    """
    _write_layer(tmp_path, "hair_back")
    _write_manifest(tmp_path, {
        "preview_ready": False,
        "missing_preview": ["face_base", "mouth", "neck", "clothes_top"],
    })
    assert load_export_inputs(str(tmp_path)).parts_hollow is False



def test_manifest_says_usable_export_inputs_agree(tmp_path):
    """第三向：清单说齐了就得报齐，否则闸门只会永远红。"""
    _write_layer(tmp_path, "hair_back")
    _write_manifest(tmp_path, {"preview_ready": True, "missing_preview": []})

    inputs = load_export_inputs(str(tmp_path))

    assert inputs.parts_ready is True
    assert inputs.missing_preview == []


def test_no_manifest_is_not_judged(tmp_path):
    """没有清单（用户导入的 PSD/PNG）= 不判定，绝不能当成"不合格"。

    那 5 个部件 id 是对我们自己分层词表的约定，对用户起的图层名不成立；
    据此拒绝会把导入→导出这条路整个堵死。
    """
    _write_layer(tmp_path, "我的图层")

    inputs = load_export_inputs(str(tmp_path))

    assert inputs.parts_ready is None
    assert inputs.missing_preview == []


@pytest.mark.parametrize("payload", [
    {},                                     # 缺 preview_ready
    {"preview_ready": "false"},             # 类型不对（字符串不算布尔）
    {"preview_ready": None},
])
def test_unreadable_manifest_is_not_judged(tmp_path, payload):
    """清单在但读不出结论：按"不判定"处理，而不是猜一个方向。"""
    _write_layer(tmp_path, "hair_back")
    (tmp_path / "layer_manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert load_export_inputs(str(tmp_path)).parts_ready is None


def test_corrupt_manifest_json_is_not_judged(tmp_path):
    """清单是坏 JSON：不得因为读不出就判不合格，也不得抛出去打断导出。"""
    _write_layer(tmp_path, "hair_back")
    (tmp_path / "layer_manifest.json").write_text("{not json", encoding="utf-8")

    assert load_export_inputs(str(tmp_path)).parts_ready is None

