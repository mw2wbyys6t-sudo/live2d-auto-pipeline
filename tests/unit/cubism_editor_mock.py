"""测试用假 Cubism Editor：行为逐条对照 5.3 字节码（CExternalAppManager 分发规则）。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Set

from websockets.asyncio.server import serve

from drivers.cubism_editor.protocol import (
    LATEST_VERSION,
    resolve_version,
    version_number,
)

_DEFAULT_PARAMETERS = [
    {
        "Id": "ParamAngleX", "Name": "角度 X", "GroupUID": "GroupFace",
        "Default": 0.0, "Max": 30.0, "Min": -30.0, "Repeat": False, "Type": 0,
        "Keyform": [{"Value": -30.0}, {"Value": 0.0}, {"Value": 30.0}],
    },
    {
        "Id": "ParamEyeLOpen", "Name": "左眼　開閉", "GroupUID": "GroupEye",
        "Default": 1.0, "Max": 1.0, "Min": 0.0, "Repeat": False, "Type": 0,
        "Keyform": [{"Value": 0.0}, {"Value": 1.0}],
    },
    {
        "Id": "ParamMouthOpenY", "Name": "口　開閉", "GroupUID": "GroupMouth",
        "Default": 0.0, "Max": 1.0, "Min": 0.0, "Repeat": False, "Type": 0,
    },
]


class _Plugin:
    def __init__(self, ws: Any):
        self.ws = ws
        self.approved = False
        self.token: Optional[str] = None
        self.approval_event = asyncio.Event()
        self.notify_versions: Dict[str, str] = {}


class MockCubismEditor:
    """asyncio WebSocket 假编辑器。start()/stop() 或 async with 使用。"""

    def __init__(
        self,
        *,
        model_uid: Optional[str] = "mock-model-uid",
        document_uid: str = "mock-doc-uid",
        parameters: Optional[List[Dict[str, Any]]] = None,
        auto_approve: bool = True,
        approve_delay: float = 0.05,
        response_delay: float = 0.0,
    ):
        self.model_uid = model_uid
        self.document_uid = document_uid
        self.parameters = json.loads(json.dumps(parameters or _DEFAULT_PARAMETERS))
        self.values = {p["Id"]: p["Default"] for p in self.parameters}
        self.groups = [
            {"GroupUID": gid, "GroupName": gid}
            for gid in dict.fromkeys(p.get("GroupUID", "") for p in self.parameters)
        ]
        self.auto_approve = auto_approve
        self.approve_delay = approve_delay
        self.response_delay = response_delay
        self.edit_mode = "Modeling"
        self.plugin_version: Optional[str] = None
        self.valid_tokens: Set[str] = set()
        self.received: List[Dict[str, Any]] = []
        self.sent: List[Dict[str, Any]] = []
        self.logs: List[Dict[str, Any]] = []
        self.port: int = 0
        self._server: Any = None
        self._plugins: Dict[Any, _Plugin] = {}

    async def start(self) -> "MockCubismEditor":
        self._server = await serve(
            self._handler, "127.0.0.1", 0, max_size=3 * 1024 * 1024
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        for plugin in list(self._plugins.values()):
            await plugin.ws.close()
        self._server.close()
        await self._server.wait_closed()

    async def __aenter__(self) -> "MockCubismEditor":
        return await self.start()

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()

    def approve_pending(self) -> None:
        for plugin in self._plugins.values():
            plugin.approval_event.set()

    async def emit_export(
        self,
        method: str = "NotifyMocFileExported",
        path: str = "C:/out/model.moc3",
        model_file_path: str = "C:/out/model.model3.json",
        files: Optional[List[str]] = None,
    ) -> None:
        data: Dict[str, Any] = {"Path": path, "ModelFilePath": model_file_path}
        if files is not None:
            data["Files"] = files
        await self._emit_event(method, data)

    async def set_edit_mode(self, mode: str) -> None:
        self.edit_mode = mode
        await self._emit_event("NotifyChangeEditMode", {"EditMode": mode})

    async def _emit_event(self, method: str, data: Dict[str, Any]) -> None:
        for plugin in list(self._plugins.values()):
            version = plugin.notify_versions.get(method)
            if version is None:
                continue
            # 字节码：Event 信封 RequestId=null（NotifyFlag 只存 enabled+version）
            envelope = {
                "Version": version,
                "Timestamp": int(time.time() * 1000),
                "Type": "Event",
                "Method": method,
                "Data": data,
            }
            await self._send(plugin.ws, envelope)

    async def _send(self, ws: Any, envelope: Dict[str, Any]) -> None:
        self.sent.append(envelope)
        try:
            await ws.send(json.dumps(envelope, ensure_ascii=False))
        except Exception:
            pass

    async def _send_error(
        self, ws: Any, error_type: str, request_id: Optional[str], method: Optional[str]
    ) -> None:
        envelope: Dict[str, Any] = {
            "Timestamp": int(time.time() * 1000),
            "Type": "Error",
            "Data": {"ErrorType": error_type},
        }
        if request_id is not None:
            envelope["RequestId"] = request_id
        if method is not None:
            envelope["Method"] = method
        await self._send(ws, envelope)

    async def _handler(self, ws: Any) -> None:
        plugin = _Plugin(ws)
        self._plugins[ws] = plugin
        try:
            async for raw in ws:
                await self._on_message(plugin, raw)
        except Exception:
            pass
        finally:
            self._plugins.pop(ws, None)

    async def _on_message(self, plugin: _Plugin, raw: Any) -> None:
        try:
            envelope = json.loads(raw)
        except (TypeError, ValueError):
            await self._send_error(plugin.ws, "InvalidJson", None, None)
            await plugin.ws.close()
            return
        if not isinstance(envelope, dict) or not isinstance(envelope.get("Data"), dict):
            await self._send_error(plugin.ws, "InvalidJson", None, None)
            await plugin.ws.close()
            return
        self.received.append(envelope)
        request_id = envelope.get("RequestId")
        method = envelope.get("Method")
        data = envelope["Data"]
        if not plugin.approved and method not in ("RegisterPlugin", "GetIsApproval"):
            await self._send_error(plugin.ws, "PluginNotRegistered", request_id, method)
            return
        if method == "RegisterPlugin":
            await self._register(plugin, envelope)
            return
        if envelope.get("Type") != "Request":
            await self._send_error(plugin.ws, "InvalidType", request_id, method)
            return
        requested_version = envelope.get("Version")
        if requested_version == "":
            await self._send_error(plugin.ws, "UnsupportedVersion", request_id, method)
            await plugin.ws.close()
            return
        version = resolve_version(requested_version, self.plugin_version)
        if version is None:
            await self._send_error(plugin.ws, "UnsupportedVersion", request_id, method)
            await plugin.ws.close()
            return
        if self.response_delay:
            await asyncio.sleep(self.response_delay)
        await self._dispatch(plugin, envelope, version)

    async def _register(self, plugin: _Plugin, envelope: Dict[str, Any]) -> None:
        data = envelope["Data"]
        request_id = envelope.get("RequestId")
        name = data.get("Name")
        if not isinstance(name, str):
            await self._send_error(plugin.ws, "InvalidData", request_id, "RegisterPlugin")
            await plugin.ws.close()
            return
        for key in ("Icon", "Path", "Token"):
            if key in data and data[key] is not None and not isinstance(data[key], str):
                await self._send_error(plugin.ws, "InvalidData", request_id, "RegisterPlugin")
                await plugin.ws.close()
                return
        provided = data.get("Token")
        token_valid = isinstance(provided, str) and provided in self.valid_tokens
        token = provided if token_valid else uuid.uuid4().hex
        self.valid_tokens.add(token)
        plugin.token = token
        if not token_valid and not self.auto_approve:
            await plugin.approval_event.wait()
        elif not token_valid:
            await asyncio.sleep(self.approve_delay)
        plugin.approved = True
        await self._respond(plugin.ws, envelope, {"Token": token})

    async def _respond(
        self, ws: Any, request: Dict[str, Any], data: Dict[str, Any]
    ) -> None:
        envelope = {
            "Version": request.get("Version") or LATEST_VERSION,
            "Timestamp": int(time.time() * 1000),
            "RequestId": request.get("RequestId"),
            "Type": "Response",
            "Method": request.get("Method"),
            "Data": data,
        }
        await self._send(ws, envelope)

    def _model_matches(self, data: Dict[str, Any]) -> bool:
        model_uid = data.get("ModelUID")
        document_uid = data.get("DocumentUID")
        if model_uid is not None:
            return model_uid == self.model_uid
        if document_uid is not None:
            return document_uid == self.document_uid
        return self.model_uid is not None

    async def _dispatch(
        self, plugin: _Plugin, envelope: Dict[str, Any], version: str
    ) -> None:
        ws = plugin.ws
        method = envelope["Method"]
        data = envelope["Data"]
        request_id = envelope.get("RequestId")

        async def error(error_type: str) -> None:
            await self._send_error(ws, error_type, request_id, method)

        async def respond(payload: Dict[str, Any]) -> None:
            await self._respond(ws, envelope, payload)

        if method == "GetAPIVersion":
            await respond({"Version": LATEST_VERSION})
        elif method == "GetIsApproval":
            await respond({"Result": plugin.approved})
        elif method == "SetGlobalVersion":
            new_version = data.get("Version")
            if not isinstance(new_version, str) or version_number(new_version) is None:
                await error("InvalidData")
                return
            self.plugin_version = new_version
            await respond({})
        elif method == "GetCurrentModelUID":
            if self.model_uid is None:
                await error("InvalidModel")
            else:
                await respond({"ModelUID": self.model_uid})
        elif method == "GetCurrentDocumentUID":
            if version_number(version) < version_number("0.9.3"):
                await error("UnsupportedVersion")
            else:
                await respond({"DocumentUID": self.document_uid})
        elif method == "GetCurrentEditMode":
            await respond({"EditMode": self.edit_mode})
        elif method in ("GetParameters", "GetParameterGroups", "GetParameterValues"):
            if not self._model_matches(data):
                await error("InvalidModel")
                return
            if method == "GetParameters":
                params = json.loads(json.dumps(self.parameters))
                if version_number(version) < version_number("1.0.1"):
                    for p in params:
                        p.pop("Keyform", None)
                await respond({"Parameters": params})
            elif method == "GetParameterGroups":
                await respond({"Groups": self.groups})
            else:
                ids = data.get("Ids")
                if ids is not None and not isinstance(ids, list):
                    await error("InvalidData")
                    return
                selected = ids if ids else list(self.values)
                # 字节码：显式 Ids 里不认识的参数被静默跳过
                items = [
                    {"Id": pid, "Value": self.values[pid]}
                    for pid in selected
                    if isinstance(pid, str) and pid in self.values
                ]
                await respond({"Parameters": items})
        elif method == "SetParameterValues":
            if not isinstance(data.get("ModelUID"), str) or not self._model_matches(data):
                await error("InvalidData" if not isinstance(data.get("ModelUID"), str) else "InvalidModel")
                return
            parameters = data.get("Parameters")
            if not isinstance(parameters, list):
                await error("InvalidData")
                return
            for item in parameters:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("Id"), str)
                    or not isinstance(item.get("Value"), (int, float))
                    or isinstance(item.get("Value"), bool)
                ):
                    await error("InvalidData")
                    return
                if item["Id"] not in self.values:
                    await error("InvalidParameter")
                    return
            for item in parameters:
                self.values[item["Id"]] = float(item["Value"])
            await respond({})
        elif method == "ClearParameterValues":
            if not isinstance(data.get("ModelUID"), str) or not self._model_matches(data):
                await error("InvalidModel")
                return
            self.values = {p["Id"]: p["Default"] for p in self.parameters}
            await respond({})
        elif method in (
            "NotifyMocFileExported",
            "NotifyMotionFileExported",
            "NotifyPhysicsFileExported",
            "NotifyMotionSyncFileExported",
            "NotifyChangeEditMode",
        ):
            enabled = data.get("Enabled")
            if not isinstance(enabled, bool):
                await error("InvalidData")
                return
            if enabled:
                plugin.notify_versions[method] = version
            else:
                plugin.notify_versions.pop(method, None)
            await respond({"Accepted": True})
        elif method == "SendCubismLog":
            if (
                data.get("Type") not in ("info", "warning")
                or not isinstance(data.get("Message"), str)
                or ("Display" in data and not isinstance(data["Display"], bool))
            ):
                await error("InvalidData")
                return
            self.logs.append(dict(data))
            await respond({})
        else:
            await error("MethodNotFound")