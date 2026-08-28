
#!/usr/bin/env python3
"""
OpenSeeFace 协议兼容层

为不直接支持 VTube Studio 的面捕方案提供通用接入能力。
所有兼容 OpenSeeFace 协议的面捕设备/软件均可通过本模块
将追踪参数映射为 Live2D 标准参数。

支持的输入来源：
- OpenSeeFace UDP 追踪数据（默认端口 11573）
- 兼容 OpenSeeFace 格式的自定义 JSON 流
- 文件回放（离线调试用）
"""

from __future__ import annotations

import asyncio
import json
import socket
import struct
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from core.logger import get_logger

log = get_logger("face_tracker.openseeface")


# ------------------------------------------------------------------
# 数据结构
# ------------------------------------------------------------------

@dataclass
class FaceTrackingFrame:
    """单帧面捕追踪数据。"""
    timestamp: float = 0.0
    # 头部旋转（度）
    rotation_x: float = 0.0     # pitch
    rotation_y: float = 0.0     # yaw
    rotation_z: float = 0.0     # roll
    # 头部平移（mm）
    translation_x: float = 0.0
    translation_y: float = 0.0
    translation_z: float = 0.0
    # ARKit blendshapes (52 个标准 blendshape)
    blendshapes: Dict[str, float] = None

    def __post_init__(self):
        if self.blendshapes is None:
            self.blendshapes = {}


# ------------------------------------------------------------------
# OpenSeeFace → Live2D 参数映射
# ------------------------------------------------------------------

# OpenSeeFace 头部旋转 → Live2D 角度参数
# OpenSeeFace 使用右手坐标系，需要做符号转换
OSF_TO_LIVE2D_ANGLE: Dict[str, tuple] = {
    # (osf_field, l2d_param, scale, invert)
    "rotation_x": ("ParamAngleX", 1.0, True),    # pitch 取反
    "rotation_y": ("ParamAngleY", 1.0, True),    # yaw 取反
    "rotation_z": ("ParamAngleZ", 1.0, False),   # roll 不变
}

# OpenSeeFace ARKit blendshape → Live2D 参数
# 使用与 blendshape_mapper 一致的映射逻辑
OSF_BLENDSHAPE_TO_LIVE2D: Dict[str, dict] = {
    # 眼部
    "eyeBlinkLeft":    {"param": "ParamEyeLOpen",  "scale": 1.0, "invert": True},
    "eyeBlinkRight":   {"param": "ParamEyeROpen",  "scale": 1.0, "invert": True},
    "eyeLookInLeft":   {"param": "ParamEyeBallX",  "scale": 1.0, "invert": False},
    "eyeLookOutLeft":  {"param": "ParamEyeBallX",  "scale": -1.0, "invert": False},
    "eyeLookInRight":  {"param": "ParamEyeBallX",  "scale": 1.0, "invert": False},
    "eyeLookOutRight": {"param": "ParamEyeBallX",  "scale": -1.0, "invert": False},
    "eyeLookUpLeft":   {"param": "ParamEyeBallY",  "scale": 1.0, "invert": False},
    "eyeLookDownLeft": {"param": "ParamEyeBallY",  "scale": -1.0, "invert": False},
    "eyeLookUpRight":  {"param": "ParamEyeBallY",  "scale": 1.0, "invert": False},
    "eyeLookDownRight":{"param": "ParamEyeBallY",  "scale": -1.0, "invert": False},
    # 嘴巴
    "jawOpen":         {"param": "ParamMouthOpenY","scale": 1.0, "invert": False},
    "mouthSmileLeft":  {"param": "ParamMouthForm", "scale": 0.5, "invert": False},
    "mouthSmileRight": {"param": "ParamMouthForm", "scale": 0.5, "invert": False},
    "mouthFrownLeft":  {"param": "ParamMouthForm", "scale": -0.5, "invert": False},
    "mouthFrownRight": {"param": "ParamMouthForm", "scale": -0.5, "invert": False},
    "cheekPuff":       {"param": "ParamCheek",     "scale": 1.0, "invert": False},
    # 眉毛
    "browInnerUp":     {"param": "ParamBrowLY",    "scale": 0.5, "invert": False},
    "browOuterUpLeft": {"param": "ParamBrowLY",    "scale": 0.5, "invert": False},
    "browDownLeft":    {"param": "ParamBrowLY",    "scale": -1.0, "invert": False},
    "browOuterUpRight":{"param": "ParamBrowRY",    "scale": 0.5, "invert": False},
    "browDownRight":   {"param": "ParamBrowRY",    "scale": -1.0, "invert": False},
    # 舌头
    "tongueOut":       {"param": "ParamTongue",    "scale": 1.0, "invert": False},
}


class OpenSeeFaceBridge:
    """OpenSeeFace 协议兼容层。

    使用方法::

        bridge = OpenSeeFaceBridge()
        bridge.set_callback(lambda params: update_model(params))
        bridge.parse_frame(raw_frame_data)
    """

    def __init__(self) -> None:
        self._last_frame: Optional[FaceTrackingFrame] = None
        self._callback: Optional[Callable[[Dict[str, float]], None]] = None
        self._smoothing: Dict[str, float] = {}
        self._smooth_factor = 0.3  # 平滑系数

    def set_callback(
        self, cb: Callable[[Dict[str, float]], None]
    ) -> None:
        """设置参数更新回调。

        Args:
            cb: 回调函数，接收 {Live2DParamName: value}。
        """
        self._callback = cb

    def parse_frame(self, raw_data: bytes) -> Optional[Dict[str, float]]:
        """解析一帧 OpenSeeFace 原始二进制数据。

        Args:
            raw_data: UDP 数据包原始字节

        Returns:
            {Live2D参数名: 值}，解析失败返回 None。
        """
        try:
            frame = self._parse_binary_frame(raw_data)
        except (struct.error, IndexError) as exc:
            log.debug(f"OpenSeeFace 帧解析失败: {exc}")
            return None

        self._last_frame = frame
        params = self._map_to_live2d(frame)

        if self._callback:
            self._callback(params)

        return params

    def parse_json_frame(self, json_str: str) -> Optional[Dict[str, float]]:
        """解析 JSON 格式的面捕数据（兼容格式）。

        Args:
            json_str: JSON 字符串，需包含 blendshapes 字段。

        Returns:
            {Live2D参数名: 值}
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return None

        frame = FaceTrackingFrame(
            timestamp=time.time(),
            rotation_x=data.get("rotation", {}).get("x", 0),
            rotation_y=data.get("rotation", {}).get("y", 0),
            rotation_z=data.get("rotation", {}).get("z", 0),
            blendshapes=data.get("blendshapes", {}),
        )

        self._last_frame = frame
        params = self._map_to_live2d(frame)

        if self._callback:
            self._callback(params)

        return params

    def _map_to_live2d(self, frame: FaceTrackingFrame) -> Dict[str, float]:
        """将 FaceTrackingFrame 映射为 Live2D 参数。"""
        import time

        params: Dict[str, float] = {}

        # 1. 头部角度
        for osf_field, (l2d_param, scale, invert) in OSF_TO_LIVE2D_ANGLE.items():
            raw_val = getattr(frame, osf_field, 0.0)
            val = raw_val * scale * (-1 if invert else 1)
            # 限制范围到 -30~30
            val = max(-30.0, min(30.0, val))
            params[l2d_param] = self._smooth_val(l2d_param, val)

        # 2. ARKit blendshapes
        accumulated: Dict[str, float] = {}
        for bs_name, mapping in OSF_BLENDSHAPE_TO_LIVE2D.items():
            bs_val = frame.blendshapes.get(bs_name, 0.0)
            l2d_param = mapping["param"]
            contribution = bs_val * mapping["scale"]
            if mapping["invert"]:
                contribution = 1.0 - bs_val * mapping["scale"]

            if l2d_param not in accumulated:
                accumulated[l2d_param] = 0.0
            accumulated[l2d_param] += contribution

        for l2d_param, val in accumulated.items():
            val = max(0.0, min(1.0, val))
            params[l2d_param] = self._smooth_val(l2d_param, val)

        return params

    def _parse_binary_frame(self, data: bytes) -> FaceTrackingFrame:
        """解析 OpenSeeFace 二进制帧格式。

        OpenSeeFace V2 二进制格式大致结构：
        - Header: 4 bytes length + 4 bytes msg_type
        - Body: rotation(3*float) + translation(3*float) + blendshapes(52*float)
        """
        offset = 0

        # 简化版：直接读取 float 数组
        # rotation (3 floats) + translation (3 floats) = 6 floats
        rotation = struct.unpack_from("<fff", data, offset)
        offset += 12
        translation = struct.unpack_from("<fff", data, offset)
        offset += 12

        # blendshapes (up to 52 floats) - 根据剩余数据量动态读取
        blendshape_count = (len(data) - offset) // 4
        blendshape_count = min(blendshape_count, 52)

        blendshapes: Dict[str, float] = {}
        blendshape_list = struct.unpack_from(
            f"<{blendshape_count}f", data, offset
        )

        # 按 OpenSeeFace ARKit blendshape 标准顺序命名
        bs_names = [
            "eyeBlinkLeft", "eyeLookDownLeft", "eyeLookInLeft",
            "eyeLookOutLeft", "eyeLookUpLeft", "eyeSquintLeft",
            "eyeWideLeft", "eyeBlinkRight", "eyeLookDownRight",
            "eyeLookInRight", "eyeLookOutRight", "eyeLookUpRight",
            "eyeSquintRight", "eyeWideRight", "jawForward",
            "jawLeft", "jawRight", "jawOpen", "mouthClose",
            "mouthFunnel", "mouthPucker", "mouthLeft",
            "mouthRight", "mouthSmileLeft", "mouthSmileRight",
            "mouthFrownLeft", "mouthFrownRight", "mouthDimpleLeft",
            "mouthDimpleRight", "mouthStretchLeft", "mouthStretchRight",
            "mouthRollLower", "mouthRollUpper", "mouthShrugLower",
            "mouthShrugUpper", "mouthPressLeft", "mouthPressRight",
            "mouthLowerDownLeft", "mouthLowerDownRight",
            "mouthUpperUpLeft", "mouthUpperUpRight", "browDownLeft",
            "browDownRight", "browInnerUp", "browOuterUpLeft",
            "browOuterUpRight", "cheekPuff", "cheekSquintLeft",
            "cheekSquintRight", "noseSneerLeft", "noseSneerRight",
            "tongueOut",
        ]

        for i in range(min(blendshape_count, len(bs_names))):
            blendshapes[bs_names[i]] = max(0.0, min(1.0, blendshape_list[i]))

        return FaceTrackingFrame(
            timestamp=time.time(),
            rotation_x=rotation[0],
            rotation_y=rotation[1],
            rotation_z=rotation[2],
            translation_x=translation[0],
            translation_y=translation[1],
            translation_z=translation[2],
            blendshapes=blendshapes,
        )

    def _smooth_val(self, key: str, val: float) -> float:
        """指数平滑滤波，减少抖动。"""
        if key not in self._smoothing:
            self._smoothing[key] = val
        else:
            self._smoothing[key] = (
                self._smooth_factor * val
                + (1 - self._smooth_factor) * self._smoothing[key]
            )
        return self._smoothing[key]
