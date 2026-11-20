"""脖子带不能吞掉腿的肤色（2026-11-14 实图回归）。

现场：1024×1536 的偶像立绘，脸框高 ~721px。旧实现把脖子带的下界写成
``neck_top + face_h * 0.9``，于是 neck 一直伸到 1385px（大腿），把胸口与整条
腿的肤色都算进 neck：实测 neck 239×568、leg_L 只有 15×12、leg_R 20×71，
用户看到的就是「这张图没法分层」。

修复后脖子带取 ``min(face_h*0.28, body_h*0.10)``，且不越过腰线与前景底边。
"""

from core.segment_engine.semantic import SemanticSegmenter


def test_neck_span_is_bounded_by_face_and_body_height():
    # 脸 721px、身形 1500px → min(201, 150) = 150
    assert SemanticSegmenter._neck_span(face_h=721, body_h=1500) == 150


def test_neck_bottom_stops_at_waist_and_fg_bottom():
    assert SemanticSegmenter._neck_bottom(700, 200, waist_y=760, fg_bottom=1535) == 760
    assert SemanticSegmenter._neck_bottom(700, 200, waist_y=2000, fg_bottom=900) == 900


def test_neck_bottom_has_minimum_height():
    # face/body 都极小时也不能退化成零高度
    assert SemanticSegmenter._neck_span(face_h=10, body_h=10) == 1
    assert SemanticSegmenter._neck_bottom(100, 1, waist_y=2000, fg_bottom=2000) == 108


def test_new_neck_band_cannot_reach_the_thighs():
    """旧下界（736 + 0.9*721 ≈ 1384）会盖住大腿；新下界必须远小于它。"""
    old_bottom = 736 + int(721 * 0.9)
    span = SemanticSegmenter._neck_span(face_h=721, body_h=1500)
    new_bottom = SemanticSegmenter._neck_bottom(736, span, waist_y=1500, fg_bottom=1535)
    assert new_bottom < old_bottom - 300
