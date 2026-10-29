#!/usr/bin/env python3
"""
Pygmalion Studio - Image Provider Base Class (Provider Registry Pattern)

Abstract base for all image generation providers. The ProviderRouter
automatically discovers and routes to registered providers.
"""

import abc
import ipaddress
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable, FrozenSet
from urllib.parse import urlparse

import requests


class GenerationError(Exception):
    """Raised when image generation fails."""
    def __init__(self, message: str, provider: str = "", retryable: bool = True):
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


@dataclass
class GenerationResult:
    """Result of an image generation request."""
    success: bool
    image_path: Optional[str] = None
    image_data: Optional[bytes] = None
    provider: str = ""
    model: str = ""
    prompt: str = ""
    width: int = 0
    height: int = 0
    seed: int = 0
    elapsed_seconds: float = 0.0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.success and self.image_path is not None


# 单张生成图的下载体积上限：正常 4K PNG 不超过 50MB；超出即视为异常响应，
# 防止上游（可能被污染/为自建网关）用超大响应体打爆内存。
MAX_IMAGE_DOWNLOAD_BYTES = 50 << 20


# ---------- DNS pin：关闭 CheckedURL.parse 与 requests.get 之间的 TOCTOU 窗口 ----------
#
# 攻击向量（DNS rebinding）：CheckedURL.parse 在校验阶段解析 DNS 并验证 IP 是
# 公网地址，随后 requests.get 再次解析 DNS 连接。两次解析之间攻击者可改变
# DNS 记录指向内网/云元数据地址，绕过第一阶段的边界校验。
#
# 防御：parse 阶段缓存已验证的 addr_infos，连接阶段经 DNSPin 上下文管理器
# 把同一组 addr_infos 重新喂给 socket.getaddrinfo，使两次解析返回同一结果。
# 使用 threading.local 保证线程安全——只有显式进入 DNSPin 的线程受影响。

_dns_local = threading.local()
_original_getaddrinfo = socket.getaddrinfo


def _pinned_getaddrinfo(host, port, *args, **kwargs):
    """DNS 解调解器：若当前线程对 host 设置了 pin，返回 pin 的缓存结果。"""
    pins = getattr(_dns_local, "pins", None)
    if pins and host in pins:
        return pins[host]
    return _original_getaddrinfo(host, port, *args, **kwargs)


# 安装调解器（进程级替换，但对未 pin 的线程完全透明）
socket.getaddrinfo = _pinned_getaddrinfo


class DNSPin:
    """线程局部 DNS pin 上下文管理器。

    with DNSPin(host, addr_infos):
        requests.get(url, ...)
    # 连接阶段使用 parse 阶段验证过的 IP，攻击者无法在两次解析间做 rebinding。
    """

    __slots__ = ("_host", "_addr_infos")

    def __init__(self, host: str, addr_infos: list):
        self._host = host
        self._addr_infos = addr_infos

    def __enter__(self):
        if not hasattr(_dns_local, "pins"):
            _dns_local.pins = {}
        _dns_local.pins[self._host] = self._addr_infos
        return self

    def __exit__(self, *exc):
        pins = getattr(_dns_local, "pins", None)
        if pins:
            pins.pop(self._host, None)
        return False


class CheckedURL:
    """经过协议与解析 IP 边界校验的出站 URL（防 SSRF）。

    所有「下载上游返回的图片 URL」的 provider 都必须经此校验：仅允许
    http(s)；可信主机（操作员显式配置的网关）放行；字面量 IP 必须是
    公网地址；主机名解析出的每个地址也都必须是公网地址（防 DNS
    rebinding 到内网/云元数据服务）。
    """

    __slots__ = ("url", "host", "addr_infos")

    def __init__(self, url: str, host: str, addr_infos: list = None):
        self.url = url
        self.host = host
        self.addr_infos = addr_infos

    @classmethod
    def parse(cls, raw: str, trusted_hosts: FrozenSet[str]) -> "CheckedURL":
        parsed = urlparse(raw)
        if parsed.scheme not in ("http", "https"):
            raise GenerationError(f"出站 URL 仅允许 http(s): {raw[:120]}")
        host = (parsed.hostname or "").lower()
        if not host:
            raise GenerationError(f"出站 URL 缺少主机名: {raw[:120]}")
        if host in trusted_hosts:
            return cls(raw, host)
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if not literal.is_global:
                raise GenerationError("出站 URL 指向非公网地址，已拒绝")
            return cls(raw, host)
        # 主机名：解析出的所有地址都必须是公网地址（防 rebinding 到内网）
        try:
            infos = socket.getaddrinfo(host, None)
        except (OSError, UnicodeError) as e:
            raise GenerationError(f"出站 URL 主机解析失败: {host} ({e})") from e
        if not infos:
            raise GenerationError(f"出站 URL 主机解析为空: {host}")
        for info in infos:
            try:
                ip = ipaddress.ip_address(info[4][0])
            except ValueError as e:
                raise GenerationError(f"出站 URL 解析出非法地址: {host}") from e
            if not ip.is_global:
                raise GenerationError("出站 URL 解析到非公网地址，已拒绝")
        return cls(raw, host, infos)


def read_bounded(resp: "requests.Response", max_bytes: int = MAX_IMAGE_DOWNLOAD_BYTES) -> bytes:
    """流式读取响应体并强制总字节上限，超出即抛 GenerationError。"""
    buf = bytearray()
    for chunk in resp.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise GenerationError(f"下载响应体超过上限（{max_bytes} 字节），已拒绝")
    return bytes(buf)


def checked_image_download(
    raw_url: str,
    trusted_hosts: FrozenSet[str] = frozenset(),
    timeout: int = 60,
    max_bytes: int = MAX_IMAGE_DOWNLOAD_BYTES,
) -> bytes:
    """下载上游返回的图片 URL 的唯一安全出口。

    - URL 经 CheckedURL 校验（协议白名单 + 公网 IP 边界）；
    - DNS pin：连接阶段复用 parse 阶段验证过的 IP（防 DNS rebinding TOCTOU）；
    - 禁止自动重定向（302 到内网/元数据地址直接抛错，不跟随）；
    - 响应体流式读取并受 max_bytes 限制。
    """
    checked = CheckedURL.parse(raw_url, trusted_hosts)
    try:
        if checked.addr_infos:
            with DNSPin(checked.host, checked.addr_infos):
                resp = requests.get(checked.url, timeout=timeout, stream=True, allow_redirects=False)
        else:
            resp = requests.get(checked.url, timeout=timeout, stream=True, allow_redirects=False)
    except requests.RequestException as e:
        raise GenerationError(f"下载生成图像失败: {e}") from e
    try:
        if resp.status_code != 200:
            raise GenerationError(f"下载生成图像返回 {resp.status_code}（不跟随重定向）")
        return read_bounded(resp, max_bytes)
    finally:
        resp.close()


class ImageProvider(abc.ABC):
    """Abstract base class for image generation providers."""

    # Subclasses must set these
    name: str = "base"
    display_name: str = "Base Provider"
    requires_api_key: bool = False
    # 是否为「只连本机」的本地引擎（本地模式下只会尝试这些 provider）
    is_local: bool = False
    max_retries: int = 2
    timeout: int = 60

    def __init__(self, config=None):
        from core.config import config as global_config
        self.config = config or global_config
        self._on_progress: Optional[Callable[[str, float], None]] = None

    def set_progress_callback(self, callback: Callable[[str, float], None]):
        """Set callback for progress updates: callback(phase, percent_0_100)."""
        self._on_progress = callback

    def _report_progress(self, phase: str, percent: float):
        if self._on_progress:
            self._on_progress(phase, min(max(percent, 0), 100))

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is available (dependencies installed, keys configured)."""
        ...

    @abc.abstractmethod
    def generate(
        self,
        prompt: str,
        output_path: str,
        width: int = 1024,
        height: int = 1024,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
        **kwargs
    ) -> GenerationResult:
        """Generate an image and save to output_path."""
        ...

    def get_setup_guide(self) -> str:
        """Return setup instructions for this provider."""
        return f"{self.display_name}: No setup required."

    def _save_bytes(self, data: bytes, output_path: str) -> str:
        """Save image bytes to file, creating parent dirs."""
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return str(p)

    def _timed_generate(self, generate_fn, *args, **kwargs) -> GenerationResult:
        """Wrap generate with timing and error handling."""
        start = time.time()
        try:
            result = generate_fn(*args, **kwargs)
            result.elapsed_seconds = time.time() - start
            return result
        except GenerationError:
            raise
        except Exception as e:
            elapsed = time.time() - start
            return GenerationResult(
                success=False,
                provider=self.name,
                error=str(e),
                elapsed_seconds=elapsed,
            )
