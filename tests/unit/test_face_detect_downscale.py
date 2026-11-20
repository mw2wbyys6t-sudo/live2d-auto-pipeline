"""大画布人脸检测：检测输入降采样与坐标还原。

4K 立绘（2731×4096）直接喂 YuNet 检测不到脸，必须先压到 ≤1536 再按比例
还原坐标；这里锁住这层换算，避免回退。
"""

from __future__ import annotations

from core.segment_engine.face_detect import _DETECT_MAX_SIDE, plan_detect_size


class TestPlanDetectSize:
    def test_small_image_keeps_full_size(self):
        assert plan_detect_size(1024, 1536) == (1024, 1536, 1.0, 1.0)

    def test_four_k_image_is_downscaled(self):
        width, height, scale_x, scale_y = plan_detect_size(2731, 4096)
        assert max(width, height) <= _DETECT_MAX_SIDE
        # 还原后必须回到原画布坐标
        assert abs(width * scale_x - 2731) < 1.0
        assert abs(height * scale_y - 4096) < 1.0

    def test_extreme_aspect_keeps_minimum_size(self):
        width, height, _, _ = plan_detect_size(20000, 50)
        assert width >= 32 and height >= 32
