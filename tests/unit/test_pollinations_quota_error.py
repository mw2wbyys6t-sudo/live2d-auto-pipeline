"""免密上游 pollinations 的配额/鉴权错误必须当场说清，而不是重试三轮后报通用错。

实测（2026-10-29）：`https://image.pollinations.ai/prompt/...` 对同一句中文提示词
返回 **402 Payment Required** —— 免费额度已进付费墙。旧实现把 4xx 一并交给
`requests.RequestException` 分支处理：退避重试 3 次（白等 2+4 秒），最后抛
「Pollinations generation failed after 3 attempts: 402 Client Error…」，
用户看不出这是「需要配置图像服务」而不是「网络抖了一下」。
"""
from pathlib import Path

import pytest
import requests

from core.image_gen import pollinations as poll
from core.image_gen.base import GenerationError


class _Response:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.headers = {"Content-Type": "text/plain"}
        self.content = b""
        self.text = "Payment Required"

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"{self.status_code} Client Error")
            error.response = self
            raise error

    def iter_content(self, chunk_size=65536):
        yield self.content

    def close(self):
        pass


@pytest.fixture
def stub_get(monkeypatch):
    calls = []

    def _install(status_code: int):
        def fake_get(url, params=None, timeout=None, stream=False, **kwargs):
            calls.append(url)
            return _Response(status_code)

        monkeypatch.setattr(poll.requests, "get", fake_get)
        return calls

    return _install


@pytest.mark.parametrize("status_code,keyword", [
    (402, "配额"),
    (401, "密钥"),
    (403, "权限"),
])
def test_permanent_auth_or_quota_errors_fail_once_with_actionable_chinese(
        tmp_path: Path, stub_get, status_code, keyword):
    calls = stub_get(status_code)
    provider = poll.PollinationsProvider()

    with pytest.raises(GenerationError) as exc:
        provider.generate("一位少女", str(tmp_path / "out.png"))

    assert len(calls) == 1, f"{status_code} 是永久性错误，不该重试 {len(calls)} 次"
    assert exc.value.retryable is False, "重试不会让付费墙消失，别把它标成可重试"
    assert keyword in str(exc.value), f"错误要能指向下一步动作，实际：{exc.value}"
    assert "402" in str(exc.value) or str(status_code) in str(exc.value)


def test_transient_5xx_still_retries(tmp_path: Path, stub_get):
    """只把鉴权/配额类 4xx 短路；5xx 与 429 仍按原策略重试，别把抖动当死症。"""
    calls = stub_get(503)
    provider = poll.PollinationsProvider()

    with pytest.raises(GenerationError):
        provider.generate("一位少女", str(tmp_path / "out.png"))

    assert len(calls) == provider.max_retries
