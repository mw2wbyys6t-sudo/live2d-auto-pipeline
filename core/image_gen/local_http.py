#!/usr/bin/env python3
"""Local inference engine endpoint guard (ComfyUI / SD WebUI).

本地 provider 只允许连接回环地址。若确实需要连接局域网内的另一台机器，
显式设置 ``LIVE2D_LOCAL_ALLOW_LAN=1``。任何公网地址一律拒绝 —— 这条规则
保证「本地模式」不会悄悄退化成云端调用。
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

from core.image_gen.base import GenerationError


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def allow_lan() -> bool:
    return _truthy(os.environ.get("LIVE2D_LOCAL_ALLOW_LAN", "0"))


def _is_allowed_ip(ip: ipaddress._BaseAddress) -> bool:
    if ip.is_loopback:
        return True
    return allow_lan() and ip.is_private


def ensure_local_endpoint(raw_url: str) -> str:
    """校验并规范化本地引擎地址；不合法直接抛 GenerationError。"""
    raw = (raw_url or "").strip()
    if not raw:
        raise GenerationError("本地引擎地址为空")

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise GenerationError(f"本地引擎仅支持 http(s): {raw[:120]}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise GenerationError(f"本地引擎地址缺少主机名: {raw[:120]}")

    if host == "localhost":
        return raw.rstrip("/")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        if not _is_allowed_ip(literal):
            raise GenerationError(
                f"本地引擎拒绝连接非本机地址 {host}；"
                "如确需连局域网机器请设置 LIVE2D_LOCAL_ALLOW_LAN=1"
            )
        return raw.rstrip("/")

    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError) as e:
        raise GenerationError(f"本地引擎主机解析失败: {host} ({e})") from e
    if not infos:
        raise GenerationError(f"本地引擎主机解析为空: {host}")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError as e:
            raise GenerationError(f"本地引擎主机解析出非法地址: {host}") from e
        if not _is_allowed_ip(ip):
            raise GenerationError(
                f"本地引擎拒绝连接非本机地址 {host}；"
                "如确需连局域网机器请设置 LIVE2D_LOCAL_ALLOW_LAN=1"
            )
    return raw.rstrip("/")
