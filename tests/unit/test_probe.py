"""真实出图测试的失败路径：未知上游必须直接拒绝，不发起任何网络请求。"""

from pathlib import Path

from core.image_gen.probe import probe


class TestProbe:
    def test_unknown_provider_is_rejected(self, tmp_path):
        result = probe("not-a-provider", str(tmp_path))
        assert result["ok"] is False
        assert result["error_code"] == "unknown_provider"

    def test_local_placeholder_produces_a_real_file(self, tmp_path):
        result = probe("local_placeholder", str(tmp_path))
        assert result["ok"] is True, result
        assert Path(result["image_path"]).is_file()
        assert result["probe_size"] == [512, 512]
