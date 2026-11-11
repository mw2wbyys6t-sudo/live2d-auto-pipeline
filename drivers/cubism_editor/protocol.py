"""Cubism Editor 外部联动 WebSocket Plugin API 协议层（线格式核对过 5.3 字节码）。"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 22033
LATEST_VERSION = "1.0.1"
KNOWN_VERSIONS = ("0.9.0", "0.9.1", "0.9.2", "0.9.3", "1.0.0", "1.0.1")

MESSAGE_TYPES = ("Request", "Response", "Error", "Event")

NOTIFY_METHODS = (
    "NotifyMocFileExported",
    "NotifyMotionFileExported",
    "NotifyPhysicsFileExported",
    "NotifyMotionSyncFileExported",
    "NotifyChangeEditMode",
)

# CExternalAppLinkageDef.ErrorType 的线上文本
INVALID_JSON = "InvalidJson"
UNSUPPORTED_VERSION = "UnsupportedVersion"
METHOD_NOT_FOUND = "MethodNotFound"
INVALID_TYPE = "InvalidType"
INVALID_DATA = "InvalidData"
INVALID_PARAMETER = "InvalidParameter"
INVALID_MODEL = "InvalidModel"
INVALID_DOCUMENT = "InvalidDocument"
INVALID_VIEW = "InvalidView"
PLUGIN_NOT_REGISTERED = "PluginNotRegistered"


class CubismProtocolError(Exception):
    """编辑器返回 Type=Error 时抛出，error_type 为线上文本（如 InvalidModel）。"""

    def __init__(self, error_type: str, method: Optional[str] = None):
        self.error_type = error_type
        self.method = method
        super().__init__(f"{method or 'request'}: {error_type}")


class CubismConnectionError(Exception):
    """连接在请求完成前断开，或尚未连接。"""


def new_request_id() -> str:
    return uuid.uuid4().hex


def version_number(text: str) -> Optional[int]:
    """CExternalAppLinkageDef.Version 的解析规则：major*10000+(minor%100)*100+(patch%100)。"""
    parts = text.split(".")
    if len(parts) != 3:
        return None
    try:
        major, minor, patch = (int(p) for p in parts)
    except ValueError:
        return None
    if major < 0 or minor < 0 or patch < 0:
        return None
    return major * 10000 + (minor % 100) * 100 + (patch % 100)


def resolve_version(
    requested: Optional[str], plugin_version: Optional[str] = None
) -> Optional[str]:
    """服务端 getUseVersion：信封 Version > 插件全局版本 > 最新，映射到 ≤ 解析值的最高已知版本。"""
    if requested is not None:
        text = requested
    elif plugin_version is not None:
        text = plugin_version
    else:
        text = LATEST_VERSION
    number = version_number(text)
    if number is None:
        return None
    best: Optional[str] = None
    for known in KNOWN_VERSIONS:
        known_number = version_number(known)
        if known_number is not None and known_number <= number:
            best = known
    return best


def build_request(
    method: str,
    data: Optional[Dict[str, Any]] = None,
    *,
    version: str = LATEST_VERSION,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "Version": version,
        "Timestamp": int(time.time() * 1000),
        "RequestId": request_id or new_request_id(),
        "Type": "Request",
        "Method": method,
        "Data": {} if data is None else data,
    }


@dataclass(frozen=True)
class Message:
    type: str
    method: str
    data: Dict[str, Any]
    request_id: Optional[str] = None
    version: Optional[str] = None
    timestamp: Optional[int] = None


def parse_message(raw: Union[str, bytes]) -> Message:
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("envelope must be a JSON object")
    msg_type = obj.get("Type")
    if msg_type not in MESSAGE_TYPES:
        raise ValueError(f"invalid Type: {msg_type!r}")
    method = obj.get("Method")
    if not isinstance(method, str) or not method:
        raise ValueError("Method must be a non-empty string")
    data = obj.get("Data")
    if not isinstance(data, dict):
        raise ValueError("Data must be a JSON object")
    request_id = obj.get("RequestId")
    if request_id is not None and not isinstance(request_id, str):
        raise ValueError("RequestId must be a string")
    version = obj.get("Version")
    if version is not None and not isinstance(version, str):
        raise ValueError("Version must be a string")
    timestamp = obj.get("Timestamp")
    if timestamp is not None and not isinstance(timestamp, int):
        raise ValueError("Timestamp must be an integer")
    return Message(
        type=msg_type,
        method=method,
        data=data,
        request_id=request_id,
        version=version,
        timestamp=timestamp,
    )


@dataclass(frozen=True)
class Parameter:
    id: str
    name: str
    default: float
    minimum: float
    maximum: float
    group_uid: str = ""
    repeat: bool = False
    type: int = 0
    keyform: Tuple[float, ...] = ()

    @classmethod
    def from_json(cls, obj: Dict[str, Any]) -> "Parameter":
        return cls(
            id=obj["Id"],
            name=obj.get("Name", ""),
            default=float(obj["Default"]),
            minimum=float(obj["Min"]),
            maximum=float(obj["Max"]),
            group_uid=obj.get("GroupUID", ""),
            repeat=bool(obj.get("Repeat", False)),
            type=int(obj.get("Type", 0)),
            keyform=tuple(float(k["Value"]) for k in obj.get("Keyform", ())),
        )