"""分层退化闸门：画面里没有角色时，整条链路必须失败，不能报成功。

实测依据（2026-10-29，本机 CPU，ISNet 权重）：
- 免密上游 pollinations 对中文提示词返回了一张**樱花河流风景图**（与提示词无关），
  分层退化到颜色启发式（method=semantic_fg_gated_color），在 1000×1000 画布上只
  抠出 1 个部件、45 个像素 —— 覆盖率 0.0045%。
- 同一套分层代码跑真正的人物图（512×768）得到 24 个部件，最大者 hair_back
  63757 像素 = 16.2% 覆盖率。

当时链路对这两种情况的反应是一样的：继续 PSD → rigging → 导出 model3.json →
写作品记录，用户在左栏看到一件"作品"，点开是一张空图。所以闸门要卡在分层之后、
任何下游产物之前。
"""
import pytest

import core.workflow as wf


class _StubLayerer:
    def __init__(self, model_type="auto"):
        self.model_type = model_type
        self.next_result = {}

    def layer(self, image, output_dir=None):
        result = dict(self.next_result)
        result.setdefault("output_dir", str(output_dir))
        return result


@pytest.fixture(autouse=True)
def stub_segmenters(monkeypatch):
    monkeypatch.setattr(wf, "SemanticLayerer", _StubLayerer)


def _engine(tmp_path):
    return wf.WorkflowEngine(output_dir=str(tmp_path))


def _layers(counts, canvas=(1000, 1000)):
    return [
        {"name": f"part_{i}", "pixel_count": count, "size": canvas, "path": f"p{i}.png"}
        for i, count in enumerate(counts)
    ]


def test_degenerate_segmentation_fails_the_run(tmp_path):
    """风景图实测值：1 个 45 像素部件 / 1000×1000 画布 → 必须判失败。"""
    engine = _engine(tmp_path)
    engine.semantic_layerer.next_result = {
        "success": True, "method": "semantic_fg_gated_color",
        "layers": _layers([45]), "layer_count": 1,
    }
    from PIL import Image
    image = Image.new("RGB", (1000, 1000))

    with pytest.raises(RuntimeError) as exc:
        engine._assert_layering_usable(engine.semantic_layerer.next_result, image.size)

    message = str(exc.value)
    assert "0.0045%" in message, f"要把实测覆盖率报给用户，实际：{message}"
    assert "提示词" in message, f"要给出可执行原因，实际：{message}"


def test_real_character_segmentation_passes_the_gate(tmp_path):
    """人物图实测值：24 个部件、最大 16.2% → 不得误伤。"""
    counts = [63757, 30451, 3056, 28421, 209, 214, 90, 274, 553, 265, 251, 31, 642, 170]
    counts += [10] * (24 - len(counts))
    engine = _engine(tmp_path)
    result = {"layers": _layers(counts, canvas=(512, 768)), "layer_count": 24}

    engine._assert_layering_usable(result, (512, 768))  # 不抛错即通过


def test_kmeans_fallback_is_gated_too(tmp_path):
    """兜底链路同样要过闸门：K-means 出一堆碎片不等于有角色。"""
    engine = _engine(tmp_path)
    result = {"layers": _layers([120, 80, 60]), "layer_count": 3}

    with pytest.raises(RuntimeError):
        engine._assert_layering_usable(result, (1000, 1000))


def test_empty_layer_list_is_rejected(tmp_path):
    """空清单不能因为「没有部件超过阈值」而误判为通过。"""
    engine = _engine(tmp_path)

    with pytest.raises(RuntimeError):
        engine._assert_layering_usable({"layers": []}, (1000, 1000))
