"""Cubism Editor 外部联动客户端：驱动真实编辑器读写参数、订阅导出事件。"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Union

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .protocol import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    LATEST_VERSION,
    NOTIFY_METHODS,
    CubismConnectionError,
    CubismProtocolError,
    Message,
    Parameter,
    build_request,
    parse_message,
)

logger = logging.getLogger(__name__)

EventHandler = Callable[[Dict[str, Any]], Any]


class CubismEditorClient:
    """Cubism Editor WebSocket Plugin API 的 asyncio 客户端。

    用法::

        async with CubismEditorClient(token_file=Path("editor.token")) as client:
            uid = await client.get_current_model_uid()
            await client.set_parameter_values(uid, {"ParamAngleX": 15.0})
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        name: str = "Pygmalion Studio",
        version: str = LATEST_VERSION,
        token: Optional[str] = None,
        token_file: Optional[Union[str, Path]] = None,
        request_timeout: float = 10.0,
        approval_timeout: float = 120.0,
        connect_timeout: float = 10.0,
    ):
        self._uri = f"ws://{host}:{port}"
        self._name = name
        self._version = version
        self._token = token
        self._token_file = Path(token_file) if token_file is not None else None
        self._request_timeout = request_timeout
        self._approval_timeout = approval_timeout
        self._connect_timeout = connect_timeout
        self._ws: Any = None
        self._reader: Optional[asyncio.Task] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._handlers: Dict[str, List[EventHandler]] = {}
        self._send_lock = asyncio.Lock()

    @property
    def token(self) -> Optional[str]:
        return self._token

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def __aenter__(self) -> "CubismEditorClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._ws is not None:
            raise RuntimeError("already connected")
        token = self._token
        if token is None and self._token_file is not None and self._token_file.exists():
            token = self._token_file.read_text(encoding="utf-8").strip() or None
        self._ws = await connect(
            self._uri, open_timeout=self._connect_timeout, max_size=4 * 1024 * 1024
        )
        self._reader = asyncio.create_task(self._read_loop())
        data: Dict[str, Any] = {"Name": self._name}
        if token is not None:
            data["Token"] = token
        try:
            resp = await self.request(
                "RegisterPlugin", data, timeout=self._approval_timeout
            )
        except BaseException:
            await self.close()
            raise
        self._token = resp["Token"]
        if self._token_file is not None:
            self._write_token_file(self._token)

    def _write_token_file(self, token: str) -> None:
        # 编辑器 token 只是 localhost 配对凭证，按明文单行保存；POSIX 上收紧到 0600
        fd = os.open(str(self._token_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token)

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        reader, self._reader = self._reader, None
        if ws is not None:
            await ws.close()
        if reader is not None:
            try:
                await reader
            except (ConnectionClosed, CubismConnectionError):
                pass
        self._fail_pending(CubismConnectionError("client closed"))

    async def request(
        self,
        method: str,
        data: Optional[Dict[str, Any]] = None,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        ws = self._ws
        if ws is None:
            raise CubismConnectionError("not connected")
        envelope = build_request(method, data, version=self._version)
        request_id = envelope["RequestId"]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            async with self._send_lock:
                await ws.send(json.dumps(envelope, ensure_ascii=False))
            return await asyncio.wait_for(
                future, timeout if timeout is not None else self._request_timeout
            )
        except ConnectionClosed as exc:
            raise CubismConnectionError(f"connection closed during {method}") from exc
        finally:
            self._pending.pop(request_id, None)

    def on_event(self, method: str, handler: EventHandler) -> Callable[[], None]:
        """注册事件回调（同步或 async），返回取消函数。Event 不带 RequestId，按 Method 分发。"""
        self._handlers.setdefault(method, []).append(handler)

        def remove() -> None:
            handlers = self._handlers.get(method, [])
            if handler in handlers:
                handlers.remove(handler)

        return remove

    async def _read_loop(self) -> None:
        ws = self._ws
        assert ws is not None
        try:
            async for raw in ws:
                try:
                    msg = parse_message(raw)
                except ValueError:
                    logger.warning("ignoring malformed editor message: %r", raw[:200])
                    continue
                if msg.type in ("Response", "Error"):
                    self._resolve_pending(msg)
                elif msg.type == "Event":
                    self._dispatch_event(msg)
        except ConnectionClosed:
            pass
        finally:
            self._fail_pending(CubismConnectionError("editor connection closed"))

    def _resolve_pending(self, msg: Message) -> None:
        if msg.request_id is None:
            return
        future = self._pending.get(msg.request_id)
        if future is None or future.done():
            return
        if msg.type == "Error":
            error_type = msg.data.get("ErrorType", "Unknown")
            future.set_exception(CubismProtocolError(str(error_type), msg.method))
        else:
            future.set_result(msg.data)

    def _dispatch_event(self, msg: Message) -> None:
        for handler in list(self._handlers.get(msg.method, ())):
            try:
                result = handler(msg.data)
                if inspect.isawaitable(result):
                    asyncio.create_task(result)
            except Exception:
                logger.exception("event handler for %s failed", msg.method)

    def _fail_pending(self, exc: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)

    async def get_api_version(self) -> str:
        resp = await self.request("GetAPIVersion")
        return str(resp["Version"])

    async def get_is_approval(self) -> bool:
        resp = await self.request("GetIsApproval")
        return bool(resp["Result"])

    async def set_global_version(self, version: str) -> None:
        await self.request("SetGlobalVersion", {"Version": version})

    async def get_current_model_uid(self) -> str:
        resp = await self.request("GetCurrentModelUID")
        return str(resp["ModelUID"])

    async def get_current_document_uid(self) -> str:
        resp = await self.request("GetCurrentDocumentUID")
        return str(resp["DocumentUID"])

    async def get_current_edit_mode(self) -> str:
        resp = await self.request("GetCurrentEditMode")
        return str(resp["EditMode"])

    async def get_parameters(
        self,
        model_uid: Optional[str] = None,
        *,
        document_uid: Optional[str] = None,
    ) -> List[Parameter]:
        data: Dict[str, Any] = {}
        if model_uid is not None:
            data["ModelUID"] = model_uid
        elif document_uid is not None:
            data["DocumentUID"] = document_uid
        resp = await self.request("GetParameters", data)
        return [Parameter.from_json(p) for p in resp["Parameters"]]

    async def get_parameter_groups(self, model_uid: str) -> List[Dict[str, str]]:
        resp = await self.request("GetParameterGroups", {"ModelUID": model_uid})
        return list(resp["Groups"])

    async def get_parameter_values(
        self, model_uid: str, ids: Optional[List[str]] = None
    ) -> Dict[str, float]:
        data: Dict[str, Any] = {"ModelUID": model_uid}
        if ids is not None:
            data["Ids"] = list(ids)
        resp = await self.request("GetParameterValues", data)
        return {p["Id"]: float(p["Value"]) for p in resp["Parameters"]}

    async def set_parameter_values(
        self, model_uid: str, values: Mapping[str, float]
    ) -> None:
        parameters = [{"Id": pid, "Value": float(v)} for pid, v in values.items()]
        await self.request(
            "SetParameterValues", {"ModelUID": model_uid, "Parameters": parameters}
        )

    async def clear_parameter_values(self, model_uid: str) -> None:
        await self.request("ClearParameterValues", {"ModelUID": model_uid})

    async def subscribe(self, method: str, enabled: bool = True) -> bool:
        if method not in NOTIFY_METHODS:
            raise ValueError(f"not a notify method: {method}")
        resp = await self.request(method, {"Enabled": enabled})
        return resp.get("Accepted") is True

    async def send_cubism_log(
        self, message: str, level: str = "info", display: Optional[bool] = None
    ) -> None:
        if level not in ("info", "warning"):
            raise ValueError("level must be 'info' or 'warning'")
        data: Dict[str, Any] = {"Type": level, "Message": message}
        if display is not None:
            data["Display"] = display
        await self.request("SendCubismLog", data)