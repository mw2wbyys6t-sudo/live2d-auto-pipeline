"""E001（画布高度不足）的自动补救：放大 + 显式记录，且必须真的让 QA 闭嘴。

纯 PIL + QA 引擎，不碰模型权重。
"""
from pathlib import Path

import pytest
from PIL import Image

import core.workflow as wf


@pytest.fixture()
def engine(tmp_path):
    return wf.WorkflowEngine(output_dir=str(tmp_path))


def test_tall_enough_input_is_left_alone(engine):
    img = Image.new("RGBA", (600, 1500), (10, 20, 30, 255))
    out, record = engine._ensure_min_height(img)
    assert record is None, "不该改图却记了一笔"
    assert out.size == (600, 1500)


def test_short_input_is_scaled_up_and_recorded(engine):
    img = Image.new("RGBA", (600, 900), (10, 20, 30, 255))
    out, record = engine._ensure_min_height(img)

    # 只补到硬门槛 1000，不去 2000-4000 的"最佳区间"：
    # 实测放大到 2000 会让同一张图的分层缺陷 9 -> 11。
    assert out.size == (667, 1000)
    assert record["from"] == [600, 900]
    assert record["to"] == [667, 1000]
    assert record["method"] == "LANCZOS"
    assert "不增加画面细节" in record["note"], "记录必须说明这是插值不是细节"


def test_extreme_small_input_is_refused_not_blown_up(engine):
    """放大 10 倍是凭空造细节，所以拒绝补救并让 E001 继续可见。"""
    img = Image.new("RGBA", (100, 100), (10, 20, 30, 255))
    out, record = engine._ensure_min_height(img)
    assert out.size == (100, 100), "不该偷偷改图"
    assert record["method"] == "none"
    assert record["to"] == [100, 100]
    assert "拒绝插值" in record["note"]


def test_upscaled_demo_input_no_longer_reports_E001(engine):
    """补救的意义：同一张图，E001 从有到无。"""
    demo = (Path(__file__).resolve().parents[2]
            / "docs" / "assets" / "demo_input.png")
    src = Image.open(demo).convert("RGBA")
    before = engine.qa_engine.assess_image(src)
    assert any(i.code == "E001" for i in before.issues), "基线必须真的报 E001"

    after_img, record = engine._ensure_min_height(src)
    assert record is not None
    after = engine.qa_engine.assess_image(after_img)
    assert not any(i.code == "E001" for i in after.issues)
    assert after.valid is True
