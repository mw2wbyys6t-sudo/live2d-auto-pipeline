#!/usr/bin/env python3
"""Custom model manifest: how a user-trained model plugs into the pipeline.

A model is a directory containing ``model.json`` plus the weight file. The
manifest declares the task, tensor contract, and any label mapping, so the
pipeline can load it without code changes.

Supported tasks:
- ``depth``: single-channel relative depth, used for front/back splits and
  depth-based draw order.
- ``segmentation``: label map or per-part masks, used instead of the built-in
  segmenter.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = "1.0"
MANIFEST_FILENAME = "model.json"

TASK_DEPTH = "depth"
TASK_SEGMENTATION = "segmentation"
SUPPORTED_TASKS = (TASK_DEPTH, TASK_SEGMENTATION)
SUPPORTED_FORMATS = ("onnx",)
SUPPORTED_COLOR_ORDERS = ("RGB", "BGR")

DEFAULT_ROOTS = ("models/custom",)


@dataclass
class InputSpec:
    name: str = "input"
    shape: Tuple[int, int, int, int] = (1, 3, 512, 512)
    color_order: str = "RGB"
    scale: float = 1.0 / 255.0
    mean: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    std: Tuple[float, float, float] = (1.0, 1.0, 1.0)


@dataclass
class OutputSpec:
    name: str = "output"
    kind: str = "depth"
    depth_range: Tuple[float, float] = (0.0, 1.0)
    near_is_large: bool = True
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class CustomModelManifest:
    name: str
    task: str
    format: str
    file: str
    input: InputSpec
    output: OutputSpec
    schema_version: str = SCHEMA_VERSION
    notes: str = ""
    directory: str = ""

    @property
    def model_path(self) -> str:
        return str(Path(self.directory) / self.file) if self.directory else self.file

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "task": self.task,
            "format": self.format,
            "file": self.file,
            "input": {
                "name": self.input.name,
                "shape": list(self.input.shape),
                "color_order": self.input.color_order,
                "scale": self.input.scale,
                "mean": list(self.input.mean),
                "std": list(self.input.std),
            },
            "output": {
                "name": self.output.name,
                "kind": self.output.kind,
                "depth_range": list(self.output.depth_range),
                "near_is_large": self.output.near_is_large,
                "labels": dict(self.output.labels),
            },
            "notes": self.notes,
        }


def _as_float_list(value: Any, length: int) -> Optional[Tuple[float, ...]]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        return None
    try:
        return tuple(float(v) for v in value)
    except (TypeError, ValueError):
        return None


def parse_manifest(data: Dict[str, Any], directory: str = "") -> CustomModelManifest:
    """Parse a manifest dict. Raises ``ValueError`` with a readable reason."""
    errors = validate_manifest(data)
    if errors:
        raise ValueError("; ".join(errors))

    raw_input = data.get("input") or {}
    raw_output = data.get("output") or {}

    input_spec = InputSpec(
        name=str(raw_input.get("name") or "input"),
        shape=tuple(int(v) for v in raw_input.get("shape", (1, 3, 512, 512))),
        color_order=str(raw_input.get("color_order") or "RGB").upper(),
        scale=float(raw_input.get("scale", 1.0 / 255.0)),
        mean=_as_float_list(raw_input.get("mean", [0, 0, 0]), 3) or (0.0, 0.0, 0.0),
        std=_as_float_list(raw_input.get("std", [1, 1, 1]), 3) or (1.0, 1.0, 1.0),
    )

    labels = raw_output.get("labels") or {}
    output_spec = OutputSpec(
        name=str(raw_output.get("name") or "output"),
        kind=str(raw_output.get("kind") or "depth"),
        depth_range=_as_float_list(raw_output.get("depth_range", [0.0, 1.0]), 2) or (0.0, 1.0),
        near_is_large=bool(raw_output.get("near_is_large", True)),
        labels={str(k): str(v) for k, v in labels.items()},
    )

    return CustomModelManifest(
        name=str(data.get("name")),
        task=str(data.get("task")),
        format=str(data.get("format") or "onnx"),
        file=str(data.get("file") or "model.onnx"),
        input=input_spec,
        output=output_spec,
        schema_version=str(data.get("schema_version") or SCHEMA_VERSION),
        notes=str(data.get("notes") or ""),
        directory=directory,
    )


def validate_manifest(data: Dict[str, Any]) -> List[str]:
    """Return contract violations; empty list means valid."""
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["manifest 必须是 JSON 对象"]

    if str(data.get("schema_version") or "") != SCHEMA_VERSION:
        errors.append(f"schema_version 必须为 {SCHEMA_VERSION}")
    if not str(data.get("name") or "").strip():
        errors.append("name 不能为空")
    if str(data.get("task") or "") not in SUPPORTED_TASKS:
        errors.append(f"task 必须是 {', '.join(SUPPORTED_TASKS)}")
    if str(data.get("format") or "onnx") not in SUPPORTED_FORMATS:
        errors.append("format 目前只支持 onnx")
    if not str(data.get("file") or "").strip():
        errors.append("file 不能为空")

    raw_input = data.get("input") or {}
    shape = raw_input.get("shape")
    if not isinstance(shape, (list, tuple)) or len(shape) != 4:
        errors.append("input.shape 必须是 4 个整数 [N,C,H,W]")
    else:
        if any(not isinstance(v, int) or v <= 0 for v in shape):
            errors.append("input.shape 必须全为正整数")
        elif shape[1] not in (1, 3):
            errors.append("input.shape 的通道数必须是 1 或 3")

    order = str(raw_input.get("color_order") or "RGB").upper()
    if order not in SUPPORTED_COLOR_ORDERS:
        errors.append("input.color_order 必须是 RGB 或 BGR")

    raw_output = data.get("output") or {}
    kind = str(raw_output.get("kind") or "depth")
    if kind not in ("depth", "label_map", "masks"):
        errors.append("output.kind 必须是 depth / label_map / masks")

    task = str(data.get("task") or "")
    if task == TASK_DEPTH and kind != "depth":
        errors.append("depth 任务的 output.kind 必须是 depth")
    if task == TASK_SEGMENTATION and kind == "depth":
        errors.append("segmentation 任务的 output.kind 不能是 depth")
    if kind == "label_map":
        labels = raw_output.get("labels")
        if not isinstance(labels, dict) or not labels:
            errors.append("label_map 输出必须提供非空 labels 映射")
        else:
            for key, value in labels.items():
                if not str(key).isdigit():
                    errors.append(f"labels 的键必须是整数索引，实得 {key!r}")
                    break
                if not str(value).strip():
                    errors.append(f"labels[{key}] 不能为空")
                    break

    return errors


def load_manifest(path: str) -> CustomModelManifest:
    manifest_path = Path(path)
    if manifest_path.is_dir():
        manifest_path = manifest_path / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"找不到模型清单: {manifest_path}")

    with manifest_path.open(encoding="utf-8") as handle:
        data = json.load(handle)

    manifest = parse_manifest(data, directory=str(manifest_path.parent))
    if not Path(manifest.model_path).is_file():
        raise FileNotFoundError(f"清单声明的权重文件不存在: {manifest.model_path}")
    return manifest


def discover(roots: Iterable[str]) -> List[CustomModelManifest]:
    """Find every valid ``model.json`` under the given roots."""
    found: List[CustomModelManifest] = []
    for root in roots:
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        for manifest_path in sorted(base.glob(f"*/{MANIFEST_FILENAME}")):
            try:
                found.append(load_manifest(str(manifest_path)))
            except (ValueError, FileNotFoundError, json.JSONDecodeError):
                continue
    return found


def find_task(roots: Iterable[str], task: str) -> Optional[CustomModelManifest]:
    for manifest in discover(roots):
        if manifest.task == task:
            return manifest
    return None


def resolve_roots(base_dir: Optional[str] = None) -> List[str]:
    """Resolve search roots: env override, then per-user dir, then defaults."""
    roots: List[str] = []
    env = os.environ.get("LIVE2D_CUSTOM_MODELS", "")
    roots.extend(part for part in env.split(os.pathsep) if part)
    if base_dir:
        roots.append(str(Path(base_dir) / "models" / "custom"))
    roots.extend(DEFAULT_ROOTS)
    return roots


__all__ = [
    "SCHEMA_VERSION",
    "MANIFEST_FILENAME",
    "TASK_DEPTH",
    "TASK_SEGMENTATION",
    "SUPPORTED_TASKS",
    "DEFAULT_ROOTS",
    "InputSpec",
    "OutputSpec",
    "CustomModelManifest",
    "parse_manifest",
    "validate_manifest",
    "load_manifest",
    "discover",
    "find_task",
    "resolve_roots",
]
