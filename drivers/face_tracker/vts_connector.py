
#!/usr/bin/env python3
"""
VTube Studio WebSocket 面捕连接器

通过 WebSocket 连接 VTube Studio 的 API 端口（默认 8001），
实时获取面捕追踪参数并映射为 Live2D 模型参数，实现一键面捕驱动。

集成流程：
1. 在 VTube Studio 中启用「API 访问」，记下端口号（默认 8001）
2. 实例化 VTSConnector 并调用 connect()
3. 每秒轮询一次参数状态，驱动 Live2D 模型

基于 VTube Studio API v1.0 规范。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from core.logger import get_logger

log = get_logger("face_tracker.vts")


# ------------------------------------------------------------------
# 数据结构
# ------------------------------------------------------------------

class VTSConnectionState(Enum):
    """VTube Studio 连接状态。"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class VTSParameter:
    """VTube Studio 实时追踪参数。"""
    name: str                          # 参数名称，如 FaceAngleX
    value: float = 0.0                 # 当前值
    min_val: float = -30.0             # 最小值
    max_val: float = 30.0              # 最大值
    default_val: float = 0.0           # 默认值
    last_update: float = 0.0           # 上次更新时间戳（秒）


# ------------------------------------------------------------------
# VTS → Live2D 参数映射表
# ------------------------------------------------------------------

# VTube Studio 标准面捕参数 → Live2D 标准参数
# 参考 VTube Studio API 文档及 Cubism SDK 标准参数列表
VTS_TO_LIVE2D_MAPPING: Dict[str, str] = {
    # 头部角度
    "FaceAngleX":           "ParamAngleX",
    "FaceAngleY":           "ParamAngleY",
    "FaceAngleZ":           "ParamAngleZ",
    # 身体角度
    "BodyAngleX":           "ParamBodyAngleX",
    "BodyAngleY":           "ParamBodyAngleY",
    "BodyAngleZ":           "ParamBodyAngleZ",
    # 左眼
    "EyeLeftX":             "ParamEyeBallX",
    "EyeLeftY":             "ParamEyeBallY",
    "EyeLeftOpen":          "ParamEyeLOpen",
    "EyeLeftSmile":         "ParamEyeLSmile",
    # 右眼
    "EyeRightX":            "ParamEyeBallX",
    "EyeRightY":            "ParamEyeBallY",
    "EyeRightOpen":         "ParamEyeROpen",
    "EyeRightSmile":        "ParamEyeRSmile",
    # 眉毛
    "BrowLeftY":            "ParamBrowLY",
    "BrowRightY":           "ParamBrowRY",
    "BrowLeftAngle":        "ParamBrowLAngle",
    "BrowRightAngle":       "ParamBrowRAngle",
    # 嘴巴
    "MouthOpen":            "ParamMouthOpenY",
    "MouthSmile":           "ParamMouthForm",
    "MouthForm":            "ParamMouthForm",
    # 脸颊
    "CheekPuff":            "ParamCheek",
    # 吐舌
    "TongueOut":            "ParamTongue",
}

# VTS 参数值转换系数（VTS 范围 → Live2D 范围）
VTS_PARAM_SCALES: Dict[str, float] = {
    "FaceAngleX": 1.0,     # VTS: -30~30 → Live2D: -30~30
    "FaceAngleY": 1.0,
    "FaceAngleZ": 1.0,
    "EyeLeftOpen": 1.0,    # VTS: 0~1 → Live2D: 0~1
    "EyeRightOpen": 1.0,
    "MouthOpen": 1.0,      # VTS: 0~1 → Live2D: 0~1
}


# ------------------------------------------------------------------
# 主类
# ------------------------------------------------------------------

class VTSConnector:
    """VTube Studio WebSocket 连接器。

    提供与 VTube Studio 面捕软件的实时连接，支持：
    - 自动认证（Token 缓存）
    - 参数订阅与轮询
    - 自动重连（断线 2 秒后重试）
    - 回调驱动的参数更新通知

    使用方法::

        connector = VTSConnector(port=8001)
        connector.on_param_update = lambda params: update_model(params)
        await connector.connect()
        await connector.subscribe_tracking()
        # 主循环中调用 await connector.poll_parameters()
    """

    # VTube Studio API 端点
    DEFAULT_HOST = "127.0.0.1"
    DEFAULT_PORT = 8001
    API_ENDPOINT = f"ws://{DEFAULT_HOST}:{DEFAULT_PORT}"

    # 重连配置
    RECONNECT_DELAY_BASE = 2.0          # 初始重连延迟（秒）
    RECONNECT_DELAY_MAX = 30.0          # 最大重连延迟（秒）
    RECONNECT_DELAY_BACKOFF = 1.5       # 退避系数

    # 轮询间隔
    POLL_INTERVAL = 0.05                # 50ms（20fps）

    # API 消息类型
    MSG_TYPE_PARAM = "ParameterValueUpdate"

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        plugin_name: str = "AI Live2D Workbench",
        plugin_developer: str = "beacon",
    ) -> None:
        """
        Args:
            host: VTube Studio 主机地址
            port: VTube Studio API 端口（默认 8001）
            plugin_name: 插件名称（用于认证）
            plugin_developer: 插件开发者名称
        """
        self._host = host
        self._port = port
        self._url = f"ws://{host}:{port}"
        self._plugin_name = plugin_name
        self._plugin_developer = plugin_developer

        # 状态
        self._state = VTSConnectionState.DISCONNECTED
        self._ws: Optional[Any] = None      # WebSocket 连接
        self._token: str = ""               # 认证 Token
        self._request_id = 0                # 消息序列号
        self._last_request: Dict[str, Any] = {}

        # 参数数据
        self._parameters: Dict[str, VTSParameter] = {}
        self._param_lock = asyncio.Lock()

        # 回调
        self.on_param_update: Optional[
            Callable[[Dict[str, float]], None]
        ] = None                 # 每当参数更新时触发，传入 {Live2DParam: value}
        self.on_state_change: Optional[
            Callable[[VTSConnectionState], None]
        ] = None                 # 连接状态变化回调
        self.on_error: Optional[
            Callable[[str], None]
        ] = None                 # 错误回调

        # 任务控制
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """建立 WebSocket 连接并完成认证。

        Returns:
            True 表示连接成功并完成认证。
        """
        self._set_state(VTSConnectionState.CONNECTING)

        try:
            import websockets
        except ImportError:
            msg = "请安装 websockets 库: pip install websockets"
            log.error(msg)
            self._set_state(VTSConnectionState.ERROR)
            if self.on_error:
                self.on_error(msg)
            return False

        try:
            self._ws = await websockets.connect(self._url)
        except (OSError, ConnectionRefusedError, asyncio.TimeoutError) as exc:
            msg = f"无法连接到 VTube Studio ({self._url}): {exc}"
            log.warning(msg)
            self._set_state(VTSConnectionState.ERROR)
            if self.on_error:
                self.on_error(msg)
            return False

        # 认证
        self._set_state(VTSConnectionState.AUTHENTICATING)
        auth_ok = await self._authenticate()
        if not auth_ok:
            await self._ws.close()
            self._ws = None
            self._set_state(VTSConnectionState.ERROR)
            return False

        self._set_state(VTSConnectionState.CONNECTED)
        self._running = True
        log.info(f"已连接到 VTube Studio (端口 {self._port})")
        return True

    async def disconnect(self) -> None:
        """断开连接。"""
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._set_state(VTSConnectionState.DISCONNECTED)
        log.info("已断开 VTube Studio 连接")

    async def subscribe_tracking(self) -> bool:
        """订阅面捕追踪参数。

        订阅后 VTS 会持续推送面捕参数变化，可调用 poll_parameters()
        获取最新值。
        """
        if not self._ws or self._state != VTSConnectionState.CONNECTED:
            log.warning("未连接到 VTS，无法订阅参数")
            return False

        # 订阅标准面捕参数
        param_names = list(VTS_TO_LIVE2D_MAPPING.keys())
        result = await self._send_request("ParameterCreationRequest", {
            "Parameters": [
                {"Name": name} for name in param_names
            ]
        })
        return result is not None

    async def poll_parameters(self) -> Dict[str, float]:
        """轮询一次当前参数值，返回 {Live2D参数名: 值} 映射。

        通常在游戏主循环中调用（每帧一次）。
        """
        if not self._ws or self._state != VTSConnectionState.CONNECTED:
            return {}

        result = await self._send_request("InputParameterListRequest", {})
        if not result:
            return {}

        params = result.get("data", {}).get("parameters", [])
        live2d_params: Dict[str, float] = {}

        async with self._param_lock:
            for p in params:
                name = p.get("name", "")
                value = p.get("value", 0.0)
                if name in VTS_TO_LIVE2D_MAPPING:
                    l2d_name = VTS_TO_LIVE2D_MAPPING[name]
                    scale = VTS_PARAM_SCALES.get(name, 1.0)
                    live2d_params[l2d_name] = value * scale

        if self.on_param_update and live2d_params:
            self.on_param_update(live2d_params)

        return live2d_params

    async def run_poll_loop(self, callback: Optional[Callable] = None) -> None:
        """运行参数轮询主循环（阻塞，直到 disconnect() 被调用）。

        Args:
            callback: 每帧回调，接收 Dict[str, float]。
        """
        self._poll_task = asyncio.ensure_future(self._poll_loop(callback))
        try:
            await self._poll_task
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # 参数取值
    # ------------------------------------------------------------------

    def get_parameter(self, name: str) -> Optional[VTSParameter]:
        """获取单个追踪参数当前状态。"""
        return self._parameters.get(name)

    def get_all_parameters(self) -> Dict[str, float]:
        """获取所有 Live2D 参数当前值。"""
        return {
            vts_name: p.value
            for vts_name, p in self._parameters.items()
            if vts_name in VTS_TO_LIVE2D_MAPPING
        }

    @property
    def state(self) -> VTSConnectionState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == VTSConnectionState.CONNECTED

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    async def _authenticate(self) -> bool:
        """VTube Studio Token 认证流程。"""
        # 尝试使用已缓存 Token
        token_response = await self._send_request(
            "AuthenticationTokenRequest",
            {"pluginName": self._plugin_name, "pluginDeveloper": self._plugin_developer},
        )

        if token_response is None:
            return False

        token_data = token_response.get("data", {})
        self._token = token_data.get("authenticationToken", "")

        if not self._token:
            log.warning("VTS 认证失败：未收到 Token")
            return False

        # 使用 Token 认证
        auth_response = await self._send_request("AuthenticationRequest", {
            "pluginName": self._plugin_name,
            "pluginDeveloper": self._plugin_developer,
            "authenticationToken": self._token,
        })

        if auth_response is None:
            return False

        auth_data = auth_response.get("data", {})
        authenticated = auth_data.get("authenticated", False)

        if authenticated:
            log.info("VTS 认证成功")
        else:
            reason = auth_data.get("reason", "未知")
            log.warning(f"VTS 认证被拒绝: {reason}")

        return authenticated

    async def _send_request(
        self, message_type: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """发送 JSON-RPC 风格请求并等待响应。"""
        if not self._ws:
            return None

        self._request_id += 1
        request = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": str(self._request_id),
            "messageType": message_type,
            "data": data,
        }

        try:
            await self._ws.send(json.dumps(request, ensure_ascii=False))
            response_raw = await asyncio.wait_for(
                self._ws.recv(), timeout=5.0
            )
            response = json.loads(response_raw)
            return response
        except (asyncio.TimeoutError, json.JSONDecodeError) as exc:
            log.debug(f"VTS 请求 {message_type} 失败: {exc}")
            return None
        except Exception as exc:
            log.warning(f"VTS WebSocket 错误: {exc}")
            await self._on_disconnect()
            return None

    async def _poll_loop(self, callback: Optional[Callable] = None) -> None:
        """轮询主循环。"""
        while self._running:
            try:
                params = await self.poll_parameters()
                if callback and params:
                    callback(params)
            except Exception as exc:
                log.debug(f"轮询异常: {exc}")

            await asyncio.sleep(self.POLL_INTERVAL)

    async def _on_disconnect(self) -> None:
        """断线处理：尝试自动重连。"""
        if self._state == VTSConnectionState.RECONNECTING:
            return
        self._set_state(VTSConnectionState.RECONNECTING)

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        self._reconnect_task = asyncio.ensure_future(self._reconnect())

    async def _reconnect(self) -> None:
        """指数退避自动重连。"""
        retry = 0
        while self._running and self._state == VTSConnectionState.RECONNECTING:
            delay = min(
                self.RECONNECT_DELAY_BASE * (self.RECONNECT_DELAY_BACKOFF ** retry),
                self.RECONNECT_DELAY_MAX,
            )
            log.info(f"将在 {delay:.1f}s 后尝试重连 VTS (第 {retry + 1} 次)")
            await asyncio.sleep(delay)

            ok = await self.connect()
            if ok:
                await self.subscribe_tracking()
                log.info("VTS 重连成功")
                return
            retry += 1

    def _set_state(self, new_state: VTSConnectionState) -> None:
        old = self._state
        self._state = new_state
        if old != new_state and self.on_state_change:
            try:
                self.on_state_change(new_state)
            except Exception:
                pass


# ------------------------------------------------------------------
# 便捷函数
# ------------------------------------------------------------------

async def connect_vts(
    port: int = 8001,
    on_update: Optional[Callable] = None,
) -> Optional[VTSConnector]:
    """快速创建并连接 VTube Studio。

    Args:
        port: VTS API 端口
        on_update: 参数更新回调 Dict[str, float] → None

    Returns:
        VTSConnector 实例，连接失败返回 None。
    """
    connector = VTSConnector(port=port)
    connector.on_param_update = on_update
    ok = await connector.connect()
    if ok:
        await connector.subscribe_tracking()
        return connector
    return None
