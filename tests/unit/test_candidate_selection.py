"""多候选择优评分：覆盖率的理想区间、居中惩罚与边界输入。"""

import numpy as np

from core.image_gen.selection import centering_score, silhouette_score


def _mask_with_box(height, width, box):
    mask = np.zeros((height, width), dtype=bool)
    left, top, right, bottom = box
    mask[top:bottom, left:right] = True
    return mask


class TestSilhouetteScore:
    def test_ideal_coverage_scores_full(self):
        mask = _mask_with_box(100, 100, (0, 0, 50, 60))  # 30%
        assert silhouette_score(mask) == 1.0

    def test_tiny_coverage_scores_low(self):
        mask = _mask_with_box(100, 100, (0, 0, 5, 5))  # 0.25%
        assert silhouette_score(mask) < 0.1

    def test_oversized_coverage_scores_low(self):
        mask = _mask_with_box(100, 100, (0, 0, 100, 100))  # 100%
        assert silhouette_score(mask) < 0.1

    def test_empty_mask_scores_zero(self):
        assert silhouette_score(np.zeros((10, 10), dtype=bool)) == 0.0


class TestCenteringScore:
    def test_centered_mask_scores_high(self):
        mask = _mask_with_box(100, 100, (25, 25, 75, 75))
        # 像素中心是 49.5/49.5，允许亚像素级偏移，不要求严格等于 1。
        assert centering_score(mask) > 0.95

    def test_corner_mask_scores_low(self):
        mask = _mask_with_box(100, 100, (0, 0, 10, 10))
        assert centering_score(mask) < 0.5

    def test_empty_mask_scores_zero(self):
        assert centering_score(np.zeros((10, 10), dtype=bool)) == 0.0
