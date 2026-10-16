"""可选重型能力探测与安装计划。

基础包（requirements.desktop.txt）只带零前提主链路所需的轻依赖；SAM2/torch、
rembg、mediapipe、ASR、TTS、diffusers 等重能力不进包，由应用内按需下载器落
site-packages / 用户缓存。本模块只做「可导入性」探测 + 给出 pip 安装计划，
不自行联网安装（安装由 Go 后端以受控子进程执行，便于进度/失败跟踪）。
"""

from __future__ import annotations

import importlib.util
from typing import Dict, List

# 能力组 → 探测模块 + pip 包名（用于按需下载）。
# 探测用 importlib 模块名；pip spec 与模块名不同的（segment-anything→segment_anything）
# 在 spec 里单独给。
GROUPS: Dict[str, Dict[str, object]] = {
    "sam2_gd": {
        "label": "SAM2 学习式分割（torch/transformers，约 1.7GB）",
        "probe": ["torch", "transformers", "segment_anything"],
        "pip": ["torch", "torchvision", "transformers", "segment-anything"],
        "optional": True,
    },
    "rembg": {
        "label": "rembg 背景移除（u2net 模型）",
        "probe": ["rembg"],
        "pip": ["rembg[cpu]"],
        "optional": True,
    },
    "mediapipe": {
        "label": "MediaPipe Python 面捕备选（生产面捕走 Rust 子进程）",
        "probe": ["mediapipe"],
        "pip": ["mediapipe"],
        "optional": True,
    },
    "asr": {
        "label": "离线语音识别（faster-whisper）",
        "probe": ["faster_whisper"],
        "pip": ["faster-whisper"],
        "optional": True,
    },
    "tts": {
        "label": "Edge 在线 TTS（edge-tts，需联网）",
        "probe": ["edge_tts"],
        "pip": ["edge-tts"],
        "optional": True,
    },
    "diffusion": {
        "label": "高级扩散生成管线（diffusers/accelerate，未接线）",
        "probe": ["diffusers"],
        "pip": ["diffusers", "accelerate"],
        "optional": True,
    },
}


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def group_status(name: str) -> Dict[str, object]:
    spec = GROUPS[name]
    probes: List[str] = spec["probe"]  # type: ignore[assignment]
    present = {m: _importable(m) for m in probes}
    installed = all(present.values())
    partial = any(present.values()) and not installed
    return {
        "group": name,
        "label": spec["label"],
        "installed": installed,
        "partial": partial,
        "modules": present,
        "pip_spec": spec["pip"],
        "optional": True,
    }


def report() -> Dict[str, object]:
    """返回全部能力组状态，供 /api/capabilities 使用。"""
    return {
        "capabilities": [group_status(g) for g in GROUPS],
        "note": "重型能力按需下载，不进基础包；主链路（KMeans/HSV 颜色分层）无需这些依赖。",
    }


if __name__ == "__main__":
    import json
    print(json.dumps(report(), ensure_ascii=False, indent=2))
