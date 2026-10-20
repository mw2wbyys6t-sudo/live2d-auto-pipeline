#!/usr/bin/env python3
"""
Live2D Master Agent - 本地 ONNX 前景（抠图）模型运行时

为什么不用 rembg 直接跑：
``rembg.new_session()`` 在本地找不到权重时会**静默联网下载**（ISNet 约 176MB）。
桌面安装包在离线环境或国内网络下必然下载失败，于是分层悄悄退化成颜色启发式
（实测：头发占 46%、脸仅 0.7%、眼睛完全缺失），用户看到的分层是坏的。

这里改为直接用 onnxruntime 跑 DIS/ISNet 系权重：
- 模型路径完全由调用方决定（桌面安装包内置 ``models/rembg/isnet-anime.onnx``）；
- 找不到就明确报错，绝不偷偷联网；
- 预处理与 rembg 的 ``DisSession`` 严格一致（除以图像最大值、ImageNet 均值）。

支持两种归一化约定：
- ISNet / DIS（1024×1024，mean=ImageNet，std=1.0）
- U²-Net / Silueta（320×320，mean=ImageNet，std=ImageNet）
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

try:  # pragma: no cover - onnxruntime 是桌面必装依赖
    import onnxruntime as ort  # type: ignore
    HAS_ORT = True
except ImportError:  # pragma: no cover
    HAS_ORT = False

#: 候选权重文件名，按偏好排序（动漫专用模型最优先）。
MODEL_FILENAMES: Tuple[str, ...] = (
    "isnet-anime.onnx",
    "isnet-general-use.onnx",
    "dis-anime.onnx",
    "dis-general-use.onnx",
    "silueta.onnx",
    "u2net.onnx",
    "u2netp.onnx",
)

#: ISNet / DIS 系：1024×1024，std=1.0
_ISNET_STEMS = ("isnet", "dis-")

#: 环境变量：额外的模型搜索目录（os.pathsep 分隔）
MODELS_DIR_ENV = "LIVE2D_MODELS_DIR"


def _split_env_dirs(raw: str) -> List[Path]:
    out: List[Path] = []
    for part in (raw or "").split(os.pathsep):
        part = part.strip()
        if part:
            out.append(Path(part))
    return out


def default_search_dirs() -> List[Path]:
    """返回模型搜索目录（按优先级）。

    1. ``LIVE2D_MODELS_DIR`` 环境变量（Go 桥从桌面安装包注入）
    2. 工程根 / 资源根下的 ``models/rembg`` 与 ``models``
    3. rembg 自己的缓存目录（用户此前用 rembg 下载过）
    4. 用户数据目录下的 Live2D 模型缓存
    """
    dirs: List[Path] = []
    dirs.extend(_split_env_dirs(os.environ.get(MODELS_DIR_ENV, "")))

    here = Path(__file__).resolve()
    # core/segment_engine/isnet_onnx.py -> 逐级上溯，覆盖
    #   <app>/core/segment_engine -> <app>（源码树）
    #   <res>/app/core/segment_engine -> <res>（桌面安装包，模型在 <res>/models）
    for level in (2, 3):
        base = here.parents[level] if len(here.parents) > level else None
        if base is None:
            continue
        dirs.append(base / "models" / "rembg")
        dirs.append(base / "models")

    home = Path.home()
    dirs.append(home / ".rembg" / "models" / "isnet-anime")
    dirs.append(home / ".rembg" / "models" / "isnet-general-use")
    dirs.append(home / ".rembg" / "models")
    dirs.append(home / ".u2net")
    dirs.append(home / ".cache" / "live2d" / "models" / "rembg")
    return dirs


def find_model(dirs: Optional[Sequence[Path]] = None) -> Optional[Path]:
    """在候选目录中查找前景模型权重，返回首个命中的路径。"""
    search = list(dirs) if dirs is not None else default_search_dirs()
    seen: set[str] = set()
    for directory in search:
        try:
            key = str(Path(directory).resolve())
        except OSError:  # pragma: no cover - 路径不可解析时跳过
            continue
        if key in seen:
            continue
        seen.add(key)
        for name in MODEL_FILENAMES:
            candidate = Path(directory) / name
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return candidate
            except OSError:  # pragma: no cover
                continue
    return None


def _is_isnet_family(name: str) -> bool:
    stem = Path(name).stem.lower()
    return any(stem.startswith(prefix) for prefix in _ISNET_STEMS)


class OnnxForegroundModel:
    """DIS/ISNet 系前景模型的最小 ONNX 运行时。"""

    def __init__(self, path: os.PathLike | str, providers: Optional[List[str]] = None) -> None:
        if not HAS_ORT:  # pragma: no cover
            raise RuntimeError("onnxruntime 未安装，无法运行本地前景模型")
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"前景模型不存在: {self.path}")
        if providers is None:
            available = ort.get_available_providers()
            providers = (
                ["CUDAExecutionProvider", "CPUExecutionProvider"]
                if "CUDAExecutionProvider" in available
                else ["CPUExecutionProvider"]
            )
        self.session = ort.InferenceSession(str(self.path), providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        # 动态维度按家族默认值兜底
        if isinstance(shape[-2], int) and isinstance(shape[-1], int):
            self.input_size = (int(shape[-2]), int(shape[-1]))
        elif _is_isnet_family(self.path.name):
            self.input_size = (1024, 1024)
        else:
            self.input_size = (320, 320)
        self.is_isnet_family = _is_isnet_family(self.path.name)

    # ------------------------------------------------------------------ 内部

    def _normalize(self, image: Image.Image) -> np.ndarray:
        """预处理：缩放到模型输入尺寸并按家族约定归一化。"""
        im = image.convert("RGB").resize(self.input_size, Image.Resampling.LANCZOS)
        ary = np.array(im).astype(np.float32)
        # DIS 系列的官方约定：除以图像最大值（而非 255）
        ary = ary / max(float(np.max(ary)), 1e-6)
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        if self.is_isnet_family:
            std = np.array([1.0, 1.0, 1.0], dtype=np.float32)
        else:
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        ary = (ary - mean) / std
        ary = np.transpose(ary, (2, 0, 1))
        return np.expand_dims(ary, 0).astype(np.float32)

    # ------------------------------------------------------------------ 公开

    def predict(self, image: Image.Image) -> np.ndarray:
        """返回与原图同尺寸的前景布尔掩码。"""
        outputs = self.session.run(None, {self.input_name: self._normalize(image)})
        pred = np.asarray(outputs[0])
        if pred.ndim == 4:
            pred = pred[:, 0, :, :]
        pred = np.squeeze(pred)
        if pred.ndim != 2:
            raise RuntimeError(f"前景模型输出维度异常: {pred.shape}")
        ma = float(np.max(pred))
        mi = float(np.min(pred))
        if ma - mi > 1e-8:
            pred = (pred - mi) / (ma - mi)
        else:
            pred = (pred > 0.5).astype(np.float32)
        mask = Image.fromarray((pred * 255).astype("uint8"), mode="L")
        mask = mask.resize(image.size, Image.Resampling.LANCZOS)
        return np.array(mask) > 127

    def probe(self) -> dict:
        """返回模型信息，供自检页展示。"""
        return {
            "path": str(self.path),
            "input_size": list(self.input_size),
            "family": "isnet" if self.is_isnet_family else "u2net",
            "providers": self.session.get_providers(),
        }
