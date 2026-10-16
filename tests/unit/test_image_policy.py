"""core.image_policy 入口尺寸规范化测试：8K/超宽/alpha/合规图不变。"""
from PIL import Image

from core.image_policy import normalize_image, MAX_SIDE_PX, MAX_MEGAPIXELS


def test_small_image_unchanged():
    img = Image.new("RGBA", (512, 768), (10, 20, 30, 255))
    out, rec = normalize_image(img)
    assert rec["resized"] is False
    assert out.size == (512, 768)
    assert out is img


def test_8000x8000_downscaled_to_safety_bounds():
    img = Image.new("RGBA", (8000, 8000), (1, 2, 3, 128))
    out, rec = normalize_image(img)
    assert rec["resized"] is True
    assert max(out.size) <= MAX_SIDE_PX
    assert (out.size[0] * out.size[1]) / 1_000_000 <= MAX_MEGAPIXELS + 0.1
    # 等比正方形：两边相等，不拉伸
    assert out.size[0] == out.size[1]
    # alpha 保留
    assert out.mode == "RGBA"
    assert out.getpixel((10, 10))[3] == 128


def test_ultrawide_clamped_by_side_not_stretched():
    # 12000×100 超宽幅：长边被钳到 4096，短边同比缩小，不拉伸
    img = Image.new("RGBA", (12000, 100), (5, 5, 5, 200))
    out, rec = normalize_image(img)
    assert rec["resized"] is True
    assert out.size[0] <= MAX_SIDE_PX
    # 宽高比保持 120:1
    ratio = out.size[0] / out.size[1]
    assert abs(ratio - 120.0) < 0.5


def test_megapixel_cap_independent_of_side():
    # 6000×6000（单边超 4096，面积 36MP > 16MP）：两边约束都生效，结果不超限
    img = Image.new("RGBA", (6000, 6000), (0, 0, 0, 255))
    out, rec = normalize_image(img)
    assert rec["resized"] is True
    assert max(out.size) <= MAX_SIDE_PX
    assert (out.size[0] * out.size[1]) / 1_000_000 <= MAX_MEGAPIXELS + 0.1


def test_in_bounds_preserves_mode_and_object():
    img = Image.new("RGB", (1024, 1024), (200, 100, 50))
    out, rec = normalize_image(img)
    assert rec["resized"] is False
    assert out.mode == "RGB"
    assert out is img
