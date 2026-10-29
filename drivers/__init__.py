#!/usr/bin/env python3
"""
Pygmalion Studio - Drivers Module

Hardware and rendering drivers:
- face_tracker: MediaPipe-based real-time face tracking with ARKit blendshapes
- audio: Microphone capture and audio feature analysis
- desktop_pet: Transparent desktop pet window and animation system
- live2d_runtime: Software Live2D-like renderer (PNG layer compositing)

**这些子模块一律按「可选」处理，不在包初始化时硬导入。**

原因：本文件是 `import drivers.<任何子模块>` 的必经入口。早先这里无条件
`from drivers.face_tracker import ...`，等于把 mediapipe / OpenCV / 声卡 /
pygame 全部变成硬依赖 —— 任何一个缺失或初始化失败，连「只想用 moc3 导出」
的代码路径（`drivers.live2d_runtime`）都导入不了。实测后果：CI 的 Linux
runner 没有音频后端，`import mediapipe` 内部导入 sounddevice 时抛
PortAudioError，于是整批测试在**收集阶段**就失败。

需要这些类的调用方请直接从子模块导入（`from drivers.face_tracker import
FaceTracker`），那是显式的依赖声明；包级 `__getattr__` 只作为便利补全，
且同样在失败时返回 None 而不是抛异常。
"""

from typing import Any, Dict, Tuple

#: 文件名 → (子模块路径, 属性名)。按需解析，导入时不触碰任何硬件依赖。
_EXPORTS: Dict[str, Tuple[str, str]] = {
    "FaceTracker": ("drivers.face_tracker", "FaceTracker"),
    "BlendShapeMapper": ("drivers.face_tracker", "BlendShapeMapper"),
    "AudioCapture": ("drivers.audio", "AudioCapture"),
    "DesktopPetWindow": ("drivers.desktop_pet", "DesktopPetWindow"),
    "DesktopPetAnimator": ("drivers.desktop_pet", "DesktopPetAnimator"),
    "DesktopPet": ("drivers.desktop_pet", "DesktopPet"),
    "Live2DRenderer": ("drivers.live2d_runtime", "Live2DRenderer"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    """惰性解析包级导出；子模块不可用时返回 None 而不是让 import 失败。"""
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr = target
    try:
        import importlib

        module = importlib.import_module(module_path)
        value = getattr(module, attr)
    except Exception:  # noqa: BLE001 - 可选驱动，任何失败都只意味着「不可用」
        value = None
    globals()[name] = value
    return value
