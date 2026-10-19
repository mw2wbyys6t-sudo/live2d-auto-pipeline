#!/usr/bin/env python3
"""Runtime adapter for user-trained models declared by ``model.json``.

ONNX is the only supported format for now; ``onnxruntime`` is bundled with the
desktop build. The adapter owns preprocessing and postprocessing so a trained
model only has to honor the tensor contract in its manifest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
from PIL import Image

from core.custom_model.manifest import (
    TASK_DEPTH,
    TASK_SEGMENTATION,
    CustomModelManifest,
    InputSpec,
    OutputSpec,
)

_SESSIONS: Dict[str, Any] = {}


def preprocess(image: Image.Image, spec: InputSpec) -> np.ndarray:
    """Convert a PIL image into the NCHW float tensor declared by the manifest."""
    _, channels, height, width = spec.shape
    source = image.convert("L") if channels == 1 else image.convert("RGB")
    resized = source.resize((width, height), Image.BILINEAR)

    array = np.asarray(resized, dtype=np.float32)
    if channels == 1:
        array = array[..., None]
    elif spec.color_order == "BGR":
        array = array[..., ::-1]

    array = array * float(spec.scale)
    array = (array - np.asarray(spec.mean, dtype=np.float32)) / np.asarray(
        spec.std, dtype=np.float32
    )
    tensor = np.transpose(array, (2, 0, 1))[None, ...]
    return np.ascontiguousarray(tensor, dtype=np.float32)


def _squeeze_map(output: Any) -> np.ndarray:
    array = np.asarray(output, dtype=np.float32)
    while array.ndim > 2:
        array = array[0]
    return array


def postprocess_depth(output: Any, spec: OutputSpec, size: tuple) -> np.ndarray:
    """Return an HxW float32 map normalized to [0, 1], larger = nearer."""
    array = _squeeze_map(output)

    low, high = spec.depth_range
    if high > low:
        array = (array - low) / (high - low)
    array = np.clip(array, 0.0, 1.0)

    if not spec.near_is_large:
        array = 1.0 - array

    width, height = size
    if array.shape != (height, width):
        array = np.asarray(
            Image.fromarray(array, mode="F").resize((width, height), Image.BILINEAR),
            dtype=np.float32,
        )
    return array


def _resize_mask(mask: np.ndarray, size: tuple) -> np.ndarray:
    width, height = size
    if mask.shape == (height, width):
        return mask
    resized = Image.fromarray(mask.astype(np.uint8) * 255, mode="L").resize(
        (width, height), Image.NEAREST
    )
    return np.asarray(resized) > 0


def postprocess_segmentation(
    output: Any,
    spec: OutputSpec,
    size: tuple,
) -> Dict[str, np.ndarray]:
    """Convert a label map or mask stack into ``part -> boolean mask``."""
    array = np.asarray(output)
    masks: Dict[str, np.ndarray] = {}

    if spec.kind == "masks":
        if array.ndim == 4:
            array = array[0]
        if array.ndim != 3:
            raise ValueError("masks 输出的形状应为 [N,H,W] 或 [1,N,H,W]")
        for index, name in spec.labels.items():
            channel = int(index)
            if channel >= array.shape[0]:
                continue
            mask = array[channel] > 0.5
            if mask.any():
                masks[name] = _resize_mask(mask, size)
        return masks

    label_map = array
    while label_map.ndim > 2:
        label_map = label_map[0]
    for key, name in spec.labels.items():
        mask = label_map == int(key)
        if mask.any():
            masks[name] = _resize_mask(mask, size)
    return masks


def _session(manifest: CustomModelManifest):
    path = manifest.model_path
    if path in _SESSIONS:
        return _SESSIONS[path]

    try:
        import onnxruntime as ort  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "onnxruntime 未安装：桌面安装包已内置；源码运行请 pip install onnxruntime"
        ) from exc

    if not Path(path).is_file():
        raise FileNotFoundError(f"模型文件不存在: {path}")

    session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    _SESSIONS[path] = session
    return session


def _pick_output(session, outputs, preferred: str) -> Any:
    names = [item.name for item in session.get_outputs()]
    if preferred in names:
        return outputs[names.index(preferred)]
    return outputs[0]


def run(manifest: CustomModelManifest, image: Image.Image) -> Any:
    """Run the model and return task-specific output.

    - depth: ``HxW float32`` map, larger = nearer.
    - segmentation: ``part -> boolean mask`` dict.
    """
    session = _session(manifest)
    input_name = session.get_inputs()[0].name
    tensor = preprocess(image, manifest.input)
    outputs = session.run(None, {input_name: tensor})
    raw = _pick_output(session, outputs, manifest.output.name)

    size = image.size
    if manifest.task == TASK_DEPTH:
        return postprocess_depth(raw, manifest.output, size)
    if manifest.task == TASK_SEGMENTATION:
        return postprocess_segmentation(raw, manifest.output, size)
    raise ValueError(f"不支持的任务: {manifest.task}")


def probe(manifest: CustomModelManifest) -> Dict[str, Any]:
    """Load the model without inference and report its tensor contract."""
    session = _session(manifest)
    return {
        "name": manifest.name,
        "task": manifest.task,
        "path": manifest.model_path,
        "inputs": [
            {"name": item.name, "shape": list(item.shape), "type": item.type}
            for item in session.get_inputs()
        ],
        "outputs": [
            {"name": item.name, "shape": list(item.shape), "type": item.type}
            for item in session.get_outputs()
        ],
    }


__all__ = [
    "preprocess",
    "postprocess_depth",
    "postprocess_segmentation",
    "run",
    "probe",
]
