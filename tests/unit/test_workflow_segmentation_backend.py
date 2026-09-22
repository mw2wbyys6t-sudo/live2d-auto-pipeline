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
        return {"layers": [{"name": "layer_000", "path": "x.png"}],
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
