"""绑定层键的语义回写（core/workflow.py 的 ``_rigging_layer_key``）。

背景：绑定（骨骼绑定 / 变形器 / 参数键形）完全按**层名**解析 ——
``live2d_builder`` 的 ``LAYER_BONE_MAP`` 认 ``hair_back`` / ``eye_left``，
``DeformerHierarchy`` 的 targets 认 ``hair_front`` / ``eyeball_l``。

语义分层路径的层名本来就是这些名字，所以绑定本来就是对的；但 **K-means 回退路径**
的层名是 ``layer_000`` 这类通用序号名 —— 语义识别算出来的部件名没被回写到
正式绑定输入，于是头发不绑 Hair_Back、眼睛不绑 Eye_L，参数键形整片失效，
骨骼解析只能退到默认值或几何猜测。

这些断言锁住的是「层键来自哪里」这条数据契约，不是某个具体图片的结果。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.workflow import _rigging_layer_key  # noqa: E402


class TestRiggingLayerKey:
    def test_semantic_path_keeps_its_own_name(self):
        """语义分层路径：name 与 part_name_en 一致，层键不变（不制造回归）。"""
        info = {"name": "hair_back", "part_name": "hair_back", "part_name_en": "hair_back"}
        assert _rigging_layer_key(info) == "hair_back"

    def test_kmeans_fallback_uses_identified_part_name(self):
        """K-means 回退路径：通用序号名必须被识别出的部件名替换。"""
        info = {"name": "layer_000", "part_name": "头发_后", "part_name_en": "hair_back"}
        assert _rigging_layer_key(info) == "hair_back"

    def test_unidentified_part_keeps_original_name(self):
        """识别不出部件时保持原名 —— 质心推断仍可兜底，绝不凭空编造。"""
        for unknown in ("unknown", "unclassified", "未分类", "", None):
            info = {"name": "layer_003", "part_name_en": unknown}
            assert _rigging_layer_key(info) == "layer_003"

    def test_missing_semantic_field_keeps_original_name(self):
        assert _rigging_layer_key({"name": "layer_007"}) == "layer_007"

    def test_blank_name_returns_blank(self):
        """空名字由调用方回落到文件 stem，这里必须如实返回空串而不是 None。"""
        assert _rigging_layer_key({"name": "", "path": "/x/layer_001.png"}) == ""
