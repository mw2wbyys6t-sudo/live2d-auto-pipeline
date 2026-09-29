"""ROI 二次检测的触发、锚点、回填坐标与状态还原（不加载权重）。

`_roi_redetect` 里唯一昂贵的调用是 self.segment_detailed(crop)，全部换成桩件，
这里只判"该不该跑、跑几轮、补回来的东西落在哪、有没有污染外层状态"。
"""
import numpy as np
import pytest
from PIL import Image

from core.segment_engine.sam2_gd import Sam2GroundingDinoSegmenter

SHAPE = (400, 300)          # (H, W)
SIZE = (300, 400)           # PIL (W, H)


def _rect(box):
    m = np.zeros(SHAPE, dtype=bool)
    x0, y0, x1, y1 = box
    m[y0:y1, x0:x1] = True
    return m


FACE = _rect((100, 100, 160, 180))

ALL_PARTS = ("face", "eyebrows", "eyes_left", "eyes_right", "nose", "mouth",
             "hair_back", "hair_front", "neck", "clothes_top", "clothes_bottom",
             "arms", "hands", "legs", "accessories")


def _complete(**overrides):
    masks = {p: FACE.copy() for p in ALL_PARTS}
    masks.update(overrides)
    return masks


@pytest.fixture()
def calls(monkeypatch):
    """Patched segment_detailed that always 'finds' a mouth/nose pair."""
    seen = []

    def fake(self, image):
        seen.append({"size": image.size})
        m = np.zeros((image.size[1], image.size[0]), dtype=bool)
        m[60:70, 40:60] = True
        return {"mouth": m, "legs": m.copy()}, {}

    monkeypatch.setattr(Sam2GroundingDinoSegmenter, "segment_detailed", fake)
    return seen


def _seg():
    return Sam2GroundingDinoSegmenter(device="cpu")


def test_everything_present_means_no_second_pass(calls):
    seg = _seg()
    out, notes = seg._roi_redetect(Image.new("RGBA", SIZE), _complete())
    assert calls == [], "五官与身体齐全时不该再跑"
    assert notes == []


def test_reentrancy_guard_blocks_a_second_level(calls):
    seg = _seg()
    seg._in_roi_pass = True
    out, notes = seg._roi_redetect(Image.new("RGBA", SIZE), {"face": FACE})
    assert calls == [] and notes == []


def test_recovered_mouth_is_pasted_at_the_crop_offset(calls):
    seg = _seg()
    masks = _complete(mouth=None)
    masks.pop("mouth")
    out, notes = seg._roi_redetect(Image.new("RGBA", SIZE), masks)

    assert "mouth" in out, "缺失的嘴应被补回"
    ys, xs = np.nonzero(out["mouth"])
    # face bbox (100,100)-(159,179) 外扩 1.2 倍自身 -> crop 偏移 (29,5)；
    # 桩件把目标画在裁剪图的 [60:70, 40:60]，回填后应整体平移 (29,5)。
    assert (xs.min(), xs.max(), ys.min(), ys.max()) == (69, 88, 65, 74)
    assert any("补回 mouth" in n for n in notes)
    assert out["face"] is not None and out["face"].sum() == FACE.sum(), \
        "已有掩码不得被覆盖"


def test_roi_without_an_anchor_is_skipped_with_a_note(calls, monkeypatch):
    """没有任何锚点时只能跳过并说明原因——不猜位置。"""
    seg = _seg()
    assert seg._roi_box({"face": FACE}, ("clothes_bottom", "legs"),
                        1.2, SIZE) is None

    monkeypatch.setattr(Sam2GroundingDinoSegmenter, "ROI_SPECS", (
        {"name": "lower", "missing": ("clothes_bottom", "legs"),
         "prompts": ("clothes_bottom", "legs"),
         "anchors": ("clothes_bottom", "legs"), "grow": 1.2},
    ), raising=False)
    out, notes = seg._roi_redetect(Image.new("RGBA", SIZE), {"face": FACE})
    assert calls == [], "无锚点不该触发检测"
    assert any("没有任何锚点掩码可定位" in n for n in notes), notes
    assert "clothes_bottom" not in out


def test_mask_outside_the_silhouette_is_refused(calls):
    """剪影是真值：补回来的东西跑到角色外面就拒收，而不是当成一次成功检出。"""
    seg = _seg()
    img = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    px = img.load()
    for y in range(100, 180):
        for x in range(100, 160):
            px[x, y] = (200, 30, 30, 255)          # 剪影只有脸这么大
    masks = _complete()
    masks.pop("mouth")

    out, notes = seg._roi_redetect(img, masks)
    assert any("轮廓外" in n and "拒收" in n for n in notes), notes
    assert "mouth" not in out or not out["mouth"].any()


def test_prompt_table_and_report_state_are_restored(calls, monkeypatch):
    seg = _seg()
    monkeypatch.setattr(seg, "PART_PROMPTS",
                        {"mouth": "mouth . lips", "hair_back": "hair"},
                        raising=False)
    seg.dropped_boxes = ["outer drop"]
    seg.last_report = {"sentinel": True}
    before = dict(seg.PART_PROMPTS)

    seen_tables = []

    def fake(self, image):
        seen_tables.append(set(self.PART_PROMPTS))
        return {}, {}

    monkeypatch.setattr(Sam2GroundingDinoSegmenter, "segment_detailed", fake)
    out, notes = seg._roi_redetect(
        Image.new("RGBA", SIZE), {"face": FACE})

    assert seen_tables, "至少该跑过一轮"
    assert all(t != set(before) for t in seen_tables), "裁剪轮必须收窄提示词"
    assert dict(seg.PART_PROMPTS) == before, "提示词表必须还原"
    assert seg.dropped_boxes == ["outer drop"], "外层 dropped_boxes 不能被内层覆盖"
    assert seg.last_report == {"sentinel": True}, "内层不得覆盖外层 last_report"
    assert out["face"] is not None
