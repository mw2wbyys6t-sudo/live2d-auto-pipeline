"""立绘规范化：输出画布必须精确等于目标尺寸，且主体居中留边。"""

from PIL import Image

from core.utils.image_utils import normalize_character_canvas


def _subject_image(width=400, height=400, box=(350, 40, 390, 360)):
    """透明底 + 一个偏右的竖直长条，模拟"构图随缘"的生成图。"""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for x in range(box[0], box[2]):
        for y in range(box[1], box[3]):
            img.putpixel((x, y), (200, 60, 80, 255))
    return img


class TestNormalizeCharacterCanvas:
    def test_output_size_is_exact(self):
        out = normalize_character_canvas(_subject_image(), 100, 150)
        assert out.size == (100, 150)

    def test_corners_are_transparent(self):
        out = normalize_character_canvas(_subject_image(), 100, 150)
        alpha = out.getchannel("A")
        assert alpha.getpixel((0, 0)) == 0
        assert alpha.getpixel((99, 149)) == 0

    def test_subject_is_centered(self):
        out = normalize_character_canvas(_subject_image(), 100, 150)
        bbox = out.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
        assert bbox is not None
        left, top, right, bottom = bbox
        center_x = (left + right) / 2
        center_y = (top + bottom) / 2
        assert abs(center_x - 50) <= 2
        assert abs(center_y - 75) <= 2

    def test_subject_is_inside_margins(self):
        out = normalize_character_canvas(_subject_image(), 100, 150, margin=0.1)
        bbox = out.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
        left, top, right, bottom = bbox
        assert left >= 8 and right <= 92
        assert top >= 13 and bottom <= 137

    def test_blank_image_falls_back_to_plain_resize(self):
        blank = Image.new("RGBA", (30, 30), (255, 255, 255, 255))
        out = normalize_character_canvas(blank, 64, 96)
        assert out.size == (64, 96)
