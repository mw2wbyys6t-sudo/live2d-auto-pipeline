"""Go 导出桥的选层与层序契约。

背景（实测，2026-10）：分层产物目录里除了真实图层，还躺着供人工查看的整图预览
``preview.png`` / ``composite_preview.png``。它们与图层同目录、同后缀，旧实现按
``glob("*.png")`` 全量收集，于是把**满画布 100% 不透明**的预览图当成一个网格烘进
moc3 —— 该网格覆盖全部像素且不随任何参数形变，模型表现为「官方内核验收全绿
（runtime_ready=True / motion_verified=True），但转头、张嘴画面完全不动」。

同时旧实现按文件名字母序喂层，而 moc3 的 draw_order 就是喂入顺序，导致
``mouth_cavity`` 排在 ``mouth`` 之前 —— 口腔盖住嘴唇。

这两个缺陷都不影响「验收」，只影响「画面」，所以必须由这里锁住：
断言的对象是**送进 ``Live2DBuilder`` 的层集合与顺序**，不是验收结论。

本测试直接执行 Go 源码里 ``bridgeExportLive2DScript`` 的原文（正则抽取），
测的是真实产品代码，不是复刻实现。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_GO = PROJECT_ROOT / "api" / "services" / "python_bridge.go"

_CANVAS = (64, 64)
_LAYERS = {
    "hair_back": (90, 120, 180, 255),
    "face": (245, 220, 200, 255),
    "mouth_cavity": (120, 40, 40, 255),
    "mouth": (200, 90, 90, 255),
}
# 小 = 先画 = 在底层。mouth_cavity 必须在 mouth 之下。
_DRAW_INDEX = {"hair_back": 0, "face": 1, "mouth_cavity": 14, "mouth": 15}


def _extract_bridge_script() -> str:
    """从 Go 源码抽出 bridgeExportLive2DScript 的原文。"""
    source = BRIDGE_GO.read_text(encoding="utf-8")
    match = re.search(r"const bridgeExportLive2DScript = `(.*?)`\n", source, re.S)
    if match is None:
        pytest.fail("api/services/python_bridge.go 里找不到 bridgeExportLive2DScript")
    return match.group(1)


def _build_layers_dir(root: Path, *, with_guide: bool) -> Path:
    """造一个与真实分层产物同构的层目录（含满画布不透明的预览图）。"""
    layers_dir = root / "layers_x"
    layers_dir.mkdir(parents=True, exist_ok=True)

    for name, colour in _LAYERS.items():
        image = Image.new("RGBA", _CANVAS, (0, 0, 0, 0))
        pixels = image.load()
        for y in range(16, 48):
            for x in range(16, 48):
                pixels[x, y] = colour
        image.save(layers_dir / f"{name}.png")

    # 真实缺陷条件：预览图是满画布、alpha 全 255 的不透明图。
    opaque = Image.new("RGBA", _CANVAS, (255, 255, 255, 255))
    opaque.save(layers_dir / "preview.png")
    opaque.save(layers_dir / "composite_preview.png")

    if with_guide:
        guide = {
            "standard_order": ["hair_back", "face", "mouth_cavity", "mouth"],
            "layer_count": len(_LAYERS),
            "layers": [
                {"name": name, "draw_index": index}
                for name, index in _DRAW_INDEX.items()
            ],
        }
        (layers_dir / "layer_guide.json").write_text(
            json.dumps(guide, ensure_ascii=False), encoding="utf-8")

    return layers_dir


def _run_bridge(tmp_path: Path, layers_dir: Path, script: str) -> dict:
    script_path = tmp_path / "bridge_export.py"
    script_path.write_text(script, encoding="utf-8")

    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps({
        "layers_dir": str(layers_dir),
        "out_dir": str(tmp_path / "out"),
        "character_id": "bridgetest",
    }), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(script_path), str(params_path)],
        cwd=str(PROJECT_ROOT),
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    assert completed.returncode == 0, (
        f"导出桥脚本退出码 {completed.returncode}\n"
        f"stdout={completed.stdout[-2000:]}\nstderr={completed.stderr[-2000:]}")

    payload = completed.stdout
    start = payload.find("{")
    assert start != -1, f"导出桥没有输出 JSON: {payload[-1000:]}"
    return json.loads(payload[start:])


def test_bridge_export_skips_full_canvas_previews(tmp_path):
    """满画布预览图不得进入模型 —— 否则画面会静止不动。"""
    layers_dir = _build_layers_dir(tmp_path, with_guide=True)
    result = _run_bridge(tmp_path, layers_dir, script=_extract_bridge_script())

    used = result["layers_used"]
    assert "preview" not in used, "preview.png 被当成图层烘进了模型"
    assert "composite_preview" not in used, "composite_preview.png 被当成图层烘进了模型"
    assert used == list(_LAYERS), f"送进构建的层集合不对: {used}"
    assert result["mesh_count"] == len(_LAYERS)


def test_bridge_export_orders_layers_by_layer_guide(tmp_path):
    """层序取自 layer_guide.json 的 draw_index，而不是文件名字母序。"""
    layers_dir = _build_layers_dir(tmp_path, with_guide=True)
    result = _run_bridge(tmp_path, layers_dir, script=_extract_bridge_script())

    used = result["layers_used"]
    assert result["layer_order_source"] == "layer_guide"
    assert used[0] == "hair_back", f"最底层应为 hair_back，实际 {used[0]}"
    assert used.index("mouth_cavity") < used.index("mouth"), (
        f"口腔必须画在嘴唇之下，实际层序 {used}")


def test_bridge_export_falls_back_to_standard_order_without_guide(tmp_path):
    """缺 layer_guide.json 时按权威层序表排位，并把「无 guide」如实标出。

    字母序正是本文件开头记录的历史缺陷：`sorted()` 得到 mouth 在 mouth_cavity
    之前 → 口腔盖住嘴唇。既然权威表能给出正确次序，降级路径没有理由退回一个
    已知错误的顺序。
    """
    layers_dir = _build_layers_dir(tmp_path, with_guide=False)
    result = _run_bridge(tmp_path, layers_dir, script=_extract_bridge_script())

    used = result["layers_used"]
    assert result["layer_order_source"] == "filename"
    assert used == ["hair_back", "face", "mouth_cavity", "mouth"], (
        f"无 guide 时应按权威表排位，实际 {used}")
    assert "preview" not in used
    # 退化路径也必须如实报告顺序来源，不能静默假装用的是 guide。
    assert result["layers_unknown"] == sorted(_LAYERS)


# ---------- FF-2：层序错位必须落盘，供下载闸门读取 ----------

def _swap_guide_entries(layers_dir: Path, first: str, second: str) -> None:
    """对调两层在 layer_guide.json 中的 draw_index，模拟层序错位。"""
    guide_path = layers_dir / "layer_guide.json"
    guide = json.loads(guide_path.read_text(encoding="utf-8"))
    index = {entry["name"]: entry["draw_index"] for entry in guide["layers"]}
    for entry in guide["layers"]:
        if entry["name"] == first:
            entry["draw_index"] = index[second]
        elif entry["name"] == second:
            entry["draw_index"] = index[first]
    guide_path.write_text(json.dumps(guide, ensure_ascii=False), encoding="utf-8")


def test_bridge_export_normalizes_conflicting_draw_index(tmp_path):
    """draw_index 与权威表冲突时：以表为准纠正，并如实报告「被纠正过」。

    旧契约是「点名错位然后由下载闸门拒绝」。层序权威表本身会演进
    （2026-10-21 从 18 项扩到 65 项并改了 face 语义），照旧契约意味着表一改，
    所有旧分层目录永久无法打包，用户手上没有任何补救入口 —— 实测
    issue_count=10 的 409 就是这么来的。现在导出以表为准归一化，
    静止画面逐像素零差异，改的只是部件运动时的遮挡关系。
    """
    layers_dir = _build_layers_dir(tmp_path, with_guide=True)
    _swap_guide_entries(layers_dir, "mouth_cavity", "mouth")

    result = _run_bridge(tmp_path, layers_dir, script=_extract_bridge_script())

    used = result["layers_used"]
    assert used.index("mouth_cavity") < used.index("mouth"), (
        f"口腔必须被纠正到嘴唇之下，实际层序 {used}")
    assert result["layer_order_normalized"] is True, "被纠正过必须可见，不能静默改序"
    assert result["layer_order_issues"] == [], "纠正后不得留下假阻塞"


def test_bridge_export_persists_normalization_into_build_meta(tmp_path):
    """纠正结论必须落进 build_meta.json —— 否则「本来就对」与「被改过」无法区分。"""
    layers_dir = _build_layers_dir(tmp_path, with_guide=True)
    _swap_guide_entries(layers_dir, "mouth_cavity", "mouth")

    result = _run_bridge(tmp_path, layers_dir, script=_extract_bridge_script())

    meta_path = Path(result["output_dir"]) / "build_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["layer_order"]["issue_count"] == len(result["layer_order_issues"]) == 0
    assert meta["layer_order"]["normalized"] is True


def test_bridge_export_writes_zero_issues_for_consistent_order(tmp_path):
    """成对断言：层序没问题时不得写入假阻塞（否则闸门等于永远关闭）。"""
    layers_dir = _build_layers_dir(tmp_path, with_guide=True)
    result = _run_bridge(tmp_path, layers_dir, script=_extract_bridge_script())

    meta_path = Path(result["output_dir"]) / "build_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert result["layer_order_issues"] == []
    assert meta["layer_order"]["issue_count"] == 0
    # 第三向：本来就合规时不得谎报「被纠正过」，否则 normalized 失去信息量。
    assert result["layer_order_normalized"] is False
    assert meta["layer_order"]["normalized"] is False
