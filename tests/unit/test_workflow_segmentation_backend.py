"""workflow 的分层后端选择与「降级必须写进结果」两条不变量。

纯逻辑，用桩件跑 —— 不需要 GPU，也不许去下载权重。
"""
import pytest
from PIL import Image

import core.workflow as wf


class _StubLayerer:
    """记下被要求的后端，并按用例指定方式回应 layer()。"""

    def __init__(self, model_type="auto"):
        self.model_type = model_type
        self.next_result = {}

    def layer(self, image, output_dir=None):
        return dict(self.next_result)


@pytest.fixture(autouse=True)
def stub_segmenter(monkeypatch):
    monkeypatch.setattr(wf, "SemanticLayerer", _StubLayerer)


def _engine(tmp_path, backend=None):
    if backend is None:
        return wf.WorkflowEngine(output_dir=str(tmp_path))
    return wf.WorkflowEngine(output_dir=str(tmp_path), segmentation_backend=backend)


def _kmeans_returns(engine, monkeypatch):
    called = {}

    def fake(image, output_dir=None):
        called["yes"] = True
        # 与 core/segment_engine/kmeans.py:175 的真实返回同构（含 layer_count）。
        return {"layers": [{"name": "layer_000", "path": "x.png"}],
                "layer_count": 1,
                "method": "kmeans", "output_dir": str(output_dir)}

    monkeypatch.setattr(engine.kmeans_layerer, "layer", fake)
    return called


def test_requested_backend_reaches_the_segmenter(tmp_path):
    assert _engine(tmp_path, "sam2_gd").semantic_layerer.model_type == "sam2_gd"


def test_default_backend_is_still_auto(tmp_path):
    """接线不能顺手改掉现网默认行为。"""
    assert _engine(tmp_path).semantic_layerer.model_type == "auto"


def test_named_backend_falling_back_is_recorded(tmp_path, monkeypatch):
    """点名 sam2_gd 却退回 K-means：结果里必须写着降级，不能只进日志。"""
    engine = _engine(tmp_path, "sam2_gd")
    engine.semantic_layerer.next_result = {
        "layers": [], "method": "sam2_gd", "missing_parts": ["face"],
        "error": "权重不可用"}
    called = _kmeans_returns(engine, monkeypatch)

    result = {"steps": {}}
    engine._step_layering(Image.new("RGBA", (8, 8)), result, 1)

    assert called.get("yes"), "确实回落到了 K-means"
    layering = result["steps"]["layering"]
    assert layering["degraded"] is True
    assert layering["requested_backend"] == "sam2_gd"
    assert layering["used_backend"] == "kmeans"
    assert layering["missing_parts"] == ["face"]
    assert "权重不可用" in layering["reason"]


def test_recording_the_step_does_not_erase_the_degradation(tmp_path, monkeypatch):
    """降级记录必须活过 run() 里那步「写 output_dir/layer_count」。

    曾经 ``result["steps"]["layering"] = {...}`` 是普通赋值，把
    ``_step_layering`` 刚写的 degraded/requested_backend/reason 整份覆盖掉；
    上面的单测直调 ``_step_layering`` 就断言，所以从没经过那一行。
    同时这里写入真图层 PNG，验证质量度量确实挂在这一步上。
    """
    engine = _engine(tmp_path, "sam2_gd")
    engine.semantic_layerer.next_result = {
        "layers": [], "method": "sam2_gd", "missing_parts": ["face"],
        "error": "权重不可用"}
    _kmeans_returns(engine, monkeypatch)

    layers_dir = tmp_path / "layers"
    layers_dir.mkdir()
    for name in ("hair_back", "hair_front", "face"):
        img = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        px = img.load()
        for x in range(2, 6):
            for y in range(2, 6):
                px[x, y] = (200, 30, 30, 255)
        img.save(layers_dir / f"{name}.png")

    result = {"steps": {}}
    engine._step_layering(Image.new("RGBA", (8, 8)), result, 1)
    engine._record_layering_step(result, str(layers_dir), layer_result={"layer_count": 1})

    layering = result["steps"]["layering"]
    assert layering["degraded"] is True, "降级记录被覆盖掉了"
    assert layering["requested_backend"] == "sam2_gd"
    assert "权重不可用" in layering["reason"]
    assert layering["method"] == "kmeans"      # 新字段照常写入
    assert layering["layer_count"] == 1

    quality = layering["quality"]
    assert quality["defect_count"] >= 1, "只出了 3 个部件，必须被度量抓到"
    assert quality["ok"] is False
    assert any("必需部件" in m
               for m in quality["defects"].get("required_parts", []))


def test_auto_backend_fallback_is_not_labelled_degraded(tmp_path, monkeypatch):
    """auto 的常规回落不属于「点名的后端没做到」。"""
    engine = _engine(tmp_path)
    engine.semantic_layerer.next_result = {"layers": [], "method": "isnet"}
    _kmeans_returns(engine, monkeypatch)

    result = {"steps": {}}
    engine._step_layering(Image.new("RGBA", (8, 8)), result, 1)
    assert result["steps"].get("layering", {}).get("degraded") is None


def test_semantic_layers_are_kept_even_when_hsv_degraded(tmp_path, monkeypatch):
    """带部位名的 HSV 结果要保留：换成 K-means 会让 PSD 失去可编辑语义。"""
    engine = _engine(tmp_path)
    engine.semantic_layerer.next_result = {
        "layers": [{"name": "hair_front", "path": "h.png"}],
        "method": "semantic_hsv_fallback", "output_dir": "out/layers"}
    called = _kmeans_returns(engine, monkeypatch)

    result = {"steps": {}}
    out, layer_result = engine._step_layering(Image.new("RGBA", (8, 8)), result, 1)
    assert not called.get("yes"), "有语义图层就不该再跑 K-means"
    assert layer_result["method"] == "semantic_hsv_fallback"
    assert out == "out/layers"


def test_kmeans_only_path_skips_the_semantic_segmenter(tmp_path, monkeypatch):
    engine = wf.WorkflowEngine(output_dir=str(tmp_path),
                               use_semantic_segmentation=False)
    monkeypatch.setattr(engine.kmeans_layerer, "layer", lambda image, output_dir=None: {
        "layers": [{"name": "layer_000", "path": "x.png"}],
        "method": "kmeans", "output_dir": "km"})
    result = {"steps": {}}
    out, layer_result = engine._step_layering(Image.new("RGBA", (8, 8)), result, 1)
    assert layer_result["method"] == "kmeans"
    assert out == "km"
