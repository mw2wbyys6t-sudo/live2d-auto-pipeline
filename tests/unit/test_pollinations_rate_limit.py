"""Pollinations 免费通道限流（HTTP 402 FREE_LIMIT）的处理。

实测（2026-11）：免费额度是「每 IP 每 45 秒 1 张」，超出直接 402 并给出
``Retry after Ns``。旧实现把 402 当永久失败立刻放弃，于是同一进程里的第二次
请求（多候选、重试、测试连接之后马上生成）必然失败，Go 侧再回退本地占位图 ——
用户看到的就是「生成成功但图是占位图」。这里钉住「该等的等、等不起的说清楚」。
"""

import time

import pytest
import requests

from core.image_gen import pollinations
from core.image_gen.base import GenerationError
from core.image_gen.pollinations import PollinationsProvider


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class FakeResponse:
    def __init__(self, status=200, body="", headers=None, chunks=None):
        self.status_code = status
        self.text = body
        self.headers = headers or {}
        self._chunks = chunks if chunks is not None else [PNG]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def iter_content(self, chunk_size=64 * 1024):
        return iter(self._chunks)


@pytest.fixture(autouse=True)
def _reset_limiter(monkeypatch):
    """每个测试都从「还没成功出过图」开始，避免用例之间互相限流。"""
    monkeypatch.setattr(PollinationsProvider, "_last_success_at", None)
    yield


def make_provider() -> PollinationsProvider:
    provider = PollinationsProvider.__new__(PollinationsProvider)
    provider._on_progress = None
    return provider


class TestFreeLimitParsing:
    def test_reads_retry_after_from_body(self):
        resp = FakeResponse(
            402,
            body='{"error":{"code":"FREE_LIMIT","message":"Free limit reached: 1 image every 45 seconds per IP. Retry after 33s"}}',
        )
        assert PollinationsProvider._free_limit_wait(resp) == 33.0

    def test_reads_retry_after_header(self):
        resp = FakeResponse(402, body='{"error":{"code":"FREE_LIMIT"}}', headers={"Retry-After": "12"})
        assert PollinationsProvider._free_limit_wait(resp) == 12.0

    def test_plain_402_is_not_a_free_limit(self):
        assert PollinationsProvider._free_limit_wait(FakeResponse(402, body="Payment Required")) is None

    def test_missing_response_is_not_a_free_limit(self):
        assert PollinationsProvider._free_limit_wait(None) is None


class TestGenerateUnderRateLimit:
    def test_waits_and_succeeds_when_body_says_retry_after(self, monkeypatch, tmp_path):
        responses = [
            FakeResponse(
                402,
                body='{"error":{"code":"FREE_LIMIT","message":"Retry after 5s"}}',
            ),
            FakeResponse(200, headers={"Content-Type": "image/png"}),
        ]
        monkeypatch.setattr(pollinations.requests, "get", lambda *a, **k: responses.pop(0))
        monkeypatch.setattr(pollinations.time, "sleep", lambda seconds: sleeps.append(seconds))
        monkeypatch.setattr(PollinationsProvider, "_save_image", lambda self, data, out: out)
        sleeps = []

        result = make_provider().generate("pink twintails", output_path=str(tmp_path / "a.png"))

        assert result.success is True
        assert result.metadata["limit_waits"] == 1
        # 402 的等待必须真的发生：等 5 秒 + 1 秒余量。
        assert sleeps == [6.0]

    def test_gives_up_with_actionable_error_after_waits(self, monkeypatch, tmp_path):
        body = '{"error":{"code":"FREE_LIMIT","message":"Retry after 45s"}}'
        monkeypatch.setattr(
            pollinations.requests, "get", lambda *a, **k: FakeResponse(402, body=body)
        )
        monkeypatch.setattr(pollinations.time, "sleep", lambda seconds: None)
        provider = make_provider()
        monkeypatch.setattr(provider, "max_retries", 2)

        with pytest.raises(GenerationError) as excinfo:
            provider.generate("pink twintails", output_path=str(tmp_path / "a.png"))

        assert excinfo.value.code == "http_402"
        assert "45 秒" in str(excinfo.value)
        assert excinfo.value.retryable is False

    def test_plain_402_fails_fast_without_sleeping(self, monkeypatch, tmp_path):
        sleeps = []
        monkeypatch.setattr(
            pollinations.requests, "get",
            lambda *a, **k: FakeResponse(402, body="Payment Required"),
        )
        monkeypatch.setattr(pollinations.time, "sleep", lambda seconds: sleeps.append(seconds))

        with pytest.raises(GenerationError) as excinfo:
            make_provider().generate("pink twintails", output_path=str(tmp_path / "a.png"))

        assert excinfo.value.code == "http_402"
        assert sleeps == []


class TestSizeHonesty:
    """免密通道会偷偷降尺寸：回传的必须是真实像素，不是请求像素。"""

    @staticmethod
    def _png_bytes(width: int, height: int) -> bytes:
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (width, height), (200, 30, 40)).save(buf, format="PNG")
        return buf.getvalue()

    def test_reports_actual_pixels_when_upstream_downgrades(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            pollinations.requests, "get",
            lambda *a, **k: FakeResponse(
                200,
                headers={"Content-Type": "image/png"},
                chunks=[self._png_bytes(64, 96)],
            ),
        )

        result = make_provider().generate(
            "pink twintails", output_path=str(tmp_path / "a.png"), width=1024, height=1536
        )

        assert (result.width, result.height) == (64, 96), "不能拿请求尺寸冒充产物尺寸"
        assert result.metadata["actual_size"] == [64, 96]
        assert result.metadata["requested_size"] == [1024, 1536]
        assert result.metadata["size_downgraded"] is True

    def test_equal_size_is_not_marked_downgraded(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            pollinations.requests, "get",
            lambda *a, **k: FakeResponse(
                200,
                headers={"Content-Type": "image/png"},
                chunks=[self._png_bytes(128, 192)],
            ),
        )

        result = make_provider().generate(
            "pink twintails", output_path=str(tmp_path / "a.png"), width=128, height=192
        )

        assert (result.width, result.height) == (128, 192)
        assert result.metadata["size_downgraded"] is False


class TestSlotWaiting:
    def test_recent_success_makes_generation_wait(self, monkeypatch, tmp_path):
        monkeypatch.setattr(PollinationsProvider, "_last_success_at", time.monotonic())
        sleeps = []
        monkeypatch.setattr(pollinations.time, "sleep", lambda seconds: sleeps.append(seconds))
        monkeypatch.setattr(
            pollinations.requests, "get",
            lambda *a, **k: FakeResponse(200, headers={"Content-Type": "image/png"}),
        )
        monkeypatch.setattr(PollinationsProvider, "_save_image", lambda self, data, out: out)

        result = make_provider().generate("pink twintails", output_path=str(tmp_path / "a.png"))

        assert result.success is True
        assert sleeps and 44 <= sleeps[0] <= 45

    def test_probe_does_not_hang_on_the_free_limit(self, tmp_path, monkeypatch):
        """测试连接要立刻返回 rate_limited，而不是把界面挂住 45 秒。"""
        from core.image_gen.probe import probe

        monkeypatch.setattr(PollinationsProvider, "_last_success_at", time.monotonic())

        result = probe("pollinations", str(tmp_path))

        assert result["ok"] is False
        assert result["error_code"] == "rate_limited"
        assert "秒" in result["message"]
