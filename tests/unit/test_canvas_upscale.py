"""4K 画布：``--canvas-height`` 的目标尺寸计算与规范化结果。

背景：生成侧受上游限制只有约 0.6MP，分层/PSD/moc3 的精细度却取决于画布。
``canvas_height`` 把规范化后的立绘等比放大到目标高度，画幅比例保持不变。
"""

from __future__ import annotations

from PIL import Image

from core.utils.image_utils import normalize_character_canvas
from core.workflow import WorkflowEngine


def _engine(tmp_path, **kwargs) -> WorkflowEngine:
    return WorkflowEngine(output_dir=str(tmp_path), **kwargs)


class TestTargetCanvasSize:
    def test_default_keeps_generation_size(self, tmp_path):
        engine = _engine(tmp_path, width=1024, height=1536)
        assert engine._target_canvas_size() == (1024, 1536)

    def test_zero_disables_upscale(self, tmp_path):
        engine = _engine(tmp_path, width=1024, height=1536, canvas_height=0)
        assert engine._target_canvas_size() == (1024, 1536)

    def test_four_k_height_keeps_aspect(self, tmp_path):
        engine = _engine(tmp_path, width=1024, height=1536, canvas_height=4096)
        width, height = engine._target_canvas_size()
        assert height == 4096
        # 2:3 画幅 → 宽度约 2731；允许 ±2px 取整误差
        assert abs(width - 2731) <= 2
        assert abs(width / height - 1024 / 1536) < 0.01

    def test_negative_height_is_clamped_to_zero(self, tmp_path):
        engine = _engine(tmp_path, width=1024, height=1536, canvas_height=-8)
        assert engine.canvas_height == 0
        assert engine._target_canvas_size() == (1024, 1536)


class TestNormalizeToLargeCanvas:
    def test_returns_exact_canvas_and_preserves_subject_aspect(self):
        canvas = Image.new("RGBA", (600, 900), (0, 0, 0, 0))
        subject = Image.new("RGBA", (300, 600), (255, 0, 0, 255))
        canvas.paste(subject, (150, 150))

        out = normalize_character_canvas(canvas, 2731, 4096)

        assert out.size == (2731, 4096)
        bbox = out.getbbox()
        assert bbox is not None
        box_w, box_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        # 人物本身 300x600（1:2），放大后比例不变
        assert abs(box_w / box_h - 0.5) < 0.05
        # 放大后的主体高度应明显大于原图
        assert box_h > 600
