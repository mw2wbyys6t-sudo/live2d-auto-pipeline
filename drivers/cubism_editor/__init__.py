"""Cubism Editor 外部联动驱动（WebSocket Plugin API 客户端）。"""

from .client import CubismEditorClient
from .protocol import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    LATEST_VERSION,
    NOTIFY_METHODS,
    CubismConnectionError,
    CubismProtocolError,
    Parameter,
)

__all__ = [
    "CubismEditorClient",
    "CubismConnectionError",
    "CubismProtocolError",
    "Parameter",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "LATEST_VERSION",
    "NOTIFY_METHODS",
]