"""脸层擦五官：墨线要擦掉，被后排画在脸层里的发丝要保留。

现场（2026-11-15）：眼定位修好后眉毛掩码压到了刘海上，脸层把发丝当五官
inpaint 成肤色，额头上出现一块 385px 白斑。
"""

import numpy as np

from core.segment_engine.composer import LayerComposer

SKIN = (246, 231, 230)
HAIR = (210, 202, 196)   # 浅米色发丝：饱和度低、且不满足暖肤色下限
LASH = (30, 25, 25)      # 睫毛/眼线
IRIS = (100, 180, 240)   # 亮蓝虹膜：亮但高饱和


def _canvas(size: int = 40) -> np.ndarray:
    arr = np.zeros((size, size, 4), dtype=np.uint8)
    arr[..., :3] = SKIN
    arr[..., 3] = 255
    return arr


def _paint(arr, box, colour):
    x0, y0, x1, y1 = box
    arr[y0:y1, x0:x1, :3] = colour
    return box


def test_hair_under_feature_mask_is_kept():
    arr = _canvas()
    hair_box = _paint(arr, (14, 14, 26, 26), HAIR)
    mask = np.ones(arr.shape[:2], dtype=bool)
    feature = np.zeros_like(mask)
    feature[hair_box[1] : hair_box[3], hair_box[0] : hair_box[2]] = True

    out = LayerComposer._erase_features(arr, mask, feature)

    assert tuple(out[20, 20, :3]) == HAIR, "发丝被当成五官擦掉了"


def test_lash_and_iris_are_still_erased():
    arr = _canvas()
    lash_box = _paint(arr, (4, 4, 14, 14), LASH)
    iris_box = _paint(arr, (24, 24, 34, 34), IRIS)
    mask = np.ones(arr.shape[:2], dtype=bool)
    feature = np.zeros_like(mask)
    for x0, y0, x1, y1 in (lash_box, iris_box):
        feature[y0:y1, x0:x1] = True

    out = LayerComposer._erase_features(arr, mask, feature)

    # 睫毛（暗）与虹膜（亮但高饱和）都必须被肤色覆盖，否则眨眼时会露出来
    assert tuple(out[9, 9, :3]) != LASH
    assert tuple(out[29, 29, :3]) != IRIS
