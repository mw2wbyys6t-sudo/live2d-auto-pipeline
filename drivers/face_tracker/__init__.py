
#!/usr/bin/env python3
"""Face tracking with MediaPipe, ARKit blendshape mapping, VTube Studio WebSocket,
and OpenSeeFace protocol compatibility.

新增模块（基于 Live2D 资料报告优化）:
- VTSConnector: VTube Studio WebSocket 面捕连接器（端口 8001）
- OpenSeeFaceBridge: OpenSeeFace 协议兼容层，支持通用面捕方案接入
"""

from drivers.face_tracker.mediapipe_tracker import FaceTracker
from drivers.face_tracker.blendshape_mapper import BlendShapeMapper
from drivers.face_tracker.vts_connector import VTSConnector, connect_vts
from drivers.face_tracker.openseeface_bridge import OpenSeeFaceBridge

__all__ = [
    "FaceTracker",
    "BlendShapeMapper",
    "VTSConnector",
    "connect_vts",
    "OpenSeeFaceBridge",
]
