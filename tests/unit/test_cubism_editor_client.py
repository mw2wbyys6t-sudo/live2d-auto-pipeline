"""客户端端到端测试：对假编辑器跑完整注册/读写/订阅/错误路径。

假编辑器的行为对照 Cubism 5.3 字节码；这里验证客户端在线上格式上与之兼容。
"""

import asyncio
import json

import pytest
from websockets.asyncio.client import connect

from drivers.cubism_editor import (
    CubismConnectionError,
    CubismEditorClient,
    CubismProtocolError,
)
from drivers.cubism_editor.protocol import build_request

from tests.unit.cubism_editor_mock import MockCubismEditor


def test_register_delayed_approval_and_token_reuse(tmp_path):
    async def run():
        token_file = tmp_path / "editor.token"
        async with MockCubismEditor(approve_delay=0.1) as editor:
            async with CubismEditorClient(
                port=editor.port, token_file=token_file, approval_timeout=5.0
            ) as client:
                assert client.token
                assert await client.get_is_approval() is True
            assert token_file.exists()
            saved = token_file.read_text(encoding="utf-8").strip()
            assert saved == client.token
            # 第二次连接带 token：无需审批延迟，立即注册
            editor.received.clear()
            async with CubismEditorClient(
                port=editor.port, token_file=token_file
            ) as client2:
                assert client2.token == saved
                register = editor.received[0]
                assert register["Method"] == "RegisterPlugin"
                assert register["Data"]["Token"] == saved
                assert await client2.get_api_version() == "1.0.1"

    asyncio.run(run())


def test_manual_approval_gate(tmp_path):
    async def run():
        async with MockCubismEditor(auto_approve=False) as editor:
            client = CubismEditorClient(
                port=editor.port, approval_timeout=5.0, request_timeout=1.0
            )
            connect_task = asyncio.create_task(client.connect())
            await asyncio.sleep(0.1)
            assert not connect_task.done()  # 未审批前 RegisterPlugin 不应有响应
            editor.approve_pending()
            await connect_task
            try:
                assert await client.get_is_approval() is True
            finally:
                await client.close()

    asyncio.run(run())


def test_concurrent_request_correlation():
    async def run():
        async with MockCubismEditor() as editor:
            async with CubismEditorClient(port=editor.port) as client:
                uid = await client.get_current_model_uid()
                values, mode, version = await asyncio.gather(
                    client.get_parameter_values(uid),
                    client.get_current_edit_mode(),
                    client.get_api_version(),
                )
                assert values["ParamEyeLOpen"] == 1.0
                assert mode == "Modeling"
                assert version == "1.0.1"
                ids = [r["RequestId"] for r in editor.received]
                assert len(ids) == len(set(ids))

    asyncio.run(run())


def test_parameter_crud_roundtrip():
    async def run():
        async with MockCubismEditor() as editor:
            async with CubismEditorClient(port=editor.port) as client:
                uid = await client.get_current_model_uid()
                params = await client.get_parameters(uid)
                by_id = {p.id: p for p in params}
                assert by_id["ParamAngleX"].keyform == (-30.0, 0.0, 30.0)
                assert by_id["ParamAngleX"].minimum == -30.0
                assert "ParamMouthOpenY" in by_id

                await client.set_parameter_values(uid, {"ParamAngleX": 15.0})
                values = await client.get_parameter_values(uid, ["ParamAngleX"])
                assert values == {"ParamAngleX": 15.0}

                await client.clear_parameter_values(uid)
                values = await client.get_parameter_values(uid)
                assert values["ParamAngleX"] == 0.0

                groups = await client.get_parameter_groups(uid)
                assert {g["GroupUID"] for g in groups} == {"GroupFace", "GroupEye", "GroupMouth"}

    asyncio.run(run())


def test_unknown_parameter_id_is_invalid_parameter():
    async def run():
        async with MockCubismEditor() as editor:
            async with CubismEditorClient(port=editor.port) as client:
                uid = await client.get_current_model_uid()
                with pytest.raises(CubismProtocolError) as exc_info:
                    await client.set_parameter_values(uid, {"NoSuchParam": 1.0})
                assert exc_info.value.error_type == "InvalidParameter"

    asyncio.run(run())


def test_unknown_ids_silently_skipped_on_read():
    async def run():
        async with MockCubismEditor() as editor:
            async with CubismEditorClient(port=editor.port) as client:
                uid = await client.get_current_model_uid()
                values = await client.get_parameter_values(uid, ["ParamAngleX", "Nope"])
                assert values == {"ParamAngleX": 0.0}

    asyncio.run(run())


def test_error_response_raises_with_method():
    async def run():
        async with MockCubismEditor() as editor:
            async with CubismEditorClient(port=editor.port) as client:
                with pytest.raises(CubismProtocolError) as exc_info:
                    await client.get_parameters("no-such-model")
                assert exc_info.value.error_type == "InvalidModel"
                assert exc_info.value.method == "GetParameters"
                # 出错后连接仍可用
                assert await client.get_current_edit_mode() == "Modeling"

    asyncio.run(run())


def test_export_event_dispatched_by_method_without_request_id():
    async def run():
        async with MockCubismEditor() as editor:
            async with CubismEditorClient(port=editor.port) as client:
                received = []
                client.on_event("NotifyMocFileExported", received.append)
                assert await client.subscribe("NotifyMocFileExported") is True
                await editor.emit_export(path="C:/out/hero.moc3")
                for _ in range(100):
                    if received:
                        break
                    await asyncio.sleep(0.01)
                assert received == [{
                    "Path": "C:/out/hero.moc3",
                    "ModelFilePath": "C:/out/model.model3.json",
                }]
                events = [m for m in editor.sent if m["Type"] == "Event"]
                assert events and all("RequestId" not in m for m in events)
                # 取消订阅后不再推送
                assert await client.subscribe("NotifyMocFileExported", False) is True
                await editor.emit_export(path="C:/out/second.moc3")
                await asyncio.sleep(0.05)
                assert len(received) == 1

    asyncio.run(run())


def test_edit_mode_event_and_async_handler():
    async def run():
        async with MockCubismEditor() as editor:
            async with CubismEditorClient(port=editor.port) as client:
                received = []

                async def handler(data):
                    received.append(data)

                client.on_event("NotifyChangeEditMode", handler)
                await client.subscribe("NotifyChangeEditMode")
                await editor.set_edit_mode("Animation")
                for _ in range(100):
                    if received:
                        break
                    await asyncio.sleep(0.01)
                assert received == [{"EditMode": "Animation"}]
                assert await client.get_current_edit_mode() == "Animation"

    asyncio.run(run())


def test_event_handler_exception_does_not_kill_connection():
    async def run():
        async with MockCubismEditor() as editor:
            async with CubismEditorClient(port=editor.port) as client:
                def bad_handler(data):
                    raise RuntimeError("boom")

                client.on_event("NotifyMocFileExported", bad_handler)
                await client.subscribe("NotifyMocFileExported")
                await editor.emit_export()
                await asyncio.sleep(0.05)
                assert await client.get_current_edit_mode() == "Modeling"

    asyncio.run(run())


def test_pre_approval_business_method_rejected():
    async def run():
        async with MockCubismEditor() as editor:
            async with connect(f"ws://127.0.0.1:{editor.port}") as ws:
                await ws.send(json.dumps(build_request("GetParameters")))
                reply = json.loads(await ws.recv())
                assert reply["Type"] == "Error"
                assert reply["Data"]["ErrorType"] == "PluginNotRegistered"

    asyncio.run(run())


def test_register_requires_name():
    async def run():
        async with MockCubismEditor() as editor:
            async with connect(f"ws://127.0.0.1:{editor.port}") as ws:
                await ws.send(json.dumps(build_request("RegisterPlugin", {})))
                reply = json.loads(await ws.recv())
                assert reply["Type"] == "Error"
                assert reply["Data"]["ErrorType"] == "InvalidData"

    asyncio.run(run())


def test_unknown_method_is_method_not_found():
    async def run():
        async with MockCubismEditor() as editor:
            async with CubismEditorClient(port=editor.port) as client:
                with pytest.raises(CubismProtocolError) as exc_info:
                    await client.request("NoSuchMethod")
                assert exc_info.value.error_type == "MethodNotFound"

    asyncio.run(run())


def test_version_negotiation_omits_keyform_on_old_version():
    async def run():
        async with MockCubismEditor() as editor:
            async with CubismEditorClient(port=editor.port, version="0.9.0") as client:
                uid = await client.get_current_model_uid()
                params = await client.get_parameters(uid)
                assert all(p.keyform == () for p in params)

    asyncio.run(run())


def test_unsupported_version_raises():
    async def run():
        async with MockCubismEditor() as editor:
            client = CubismEditorClient(port=editor.port, version="1.0.1")
            await client.connect()
            try:
                client._version = "0.1.0"
                with pytest.raises(CubismProtocolError) as exc_info:
                    await client.request("GetAPIVersion")
                assert exc_info.value.error_type == "UnsupportedVersion"
            finally:
                await client.close()

    asyncio.run(run())


def test_set_global_version_and_send_log():
    async def run():
        async with MockCubismEditor() as editor:
            async with CubismEditorClient(port=editor.port) as client:
                await client.set_global_version("0.9.3")
                assert editor.plugin_version == "0.9.3"
                await client.send_cubism_log("pipeline 导出完成", display=True)
                assert editor.logs == [
                    {"Type": "info", "Message": "pipeline 导出完成", "Display": True}
                ]

    asyncio.run(run())


def test_request_timeout():
    async def run():
        async with MockCubismEditor(response_delay=0.5) as editor:
            async with CubismEditorClient(
                port=editor.port, request_timeout=0.05
            ) as client:
                with pytest.raises(asyncio.TimeoutError):
                    await client.get_current_edit_mode()

    asyncio.run(run())


def test_disconnect_fails_pending_request():
    async def run():
        async with MockCubismEditor(response_delay=1.0) as editor:
            client = CubismEditorClient(port=editor.port, request_timeout=5.0)
            await client.connect()
            pending = asyncio.create_task(client.request("GetAPIVersion"))
            await asyncio.sleep(0.05)
            await editor.stop()
            with pytest.raises(CubismConnectionError):
                await pending
            await client.close()

    asyncio.run(run())


def test_request_before_connect_raises():
    async def run():
        client = CubismEditorClient(port=1)
        with pytest.raises(CubismConnectionError):
            await client.request("GetAPIVersion")

    asyncio.run(run())