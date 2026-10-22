"""导出前的图层装载：全仓唯一实现。

为什么只能有一份：选层逻辑曾被内联复制到 3 个调用点（Go 导出桥、Go 阶段事件桥、
``api_server._load_layers_for_export``），且**只有 1 处**做了正确的两项处理 —
— 剔除整图预览（``preview.png`` / ``composite_preview.png``，满画布 100% 不透明，
当成图层烘进 moc3 会把模型压成一张静止的图），并按 ``layer_guide.json`` 的
``draw_index`` 排序（退回文件名字母序会让 ``mouth_cavity`` 排到 ``mouth`` 之前，
口腔盖住嘴唇）。另 2 处副本没有这两项，其中一份就是**生产导出走的路**。

调用方只传路径；本模块负责「读哪些 PNG、按什么顺序」。
"""

from __future__ import annotations

import glob
import json
import os
from collections import OrderedDict
from typing import Any, Dict, List, Set

# 与真实图层同目录、但不是图层的整图预览文件。
NON_LAYER_FILES: Set[str] = {"preview", "composite_preview"}

__all__ = [
    "NON_LAYER_FILES",
    "load_layers_for_export",
    "load_export_layer_inputs",
]


class _Log:
    @staticmethod
    def warn(message: str) -> None:
        import sys
        sys.stderr.write(f"[warn] {message}\n")


def load_export_layer_inputs(layers_dir: str) -> Dict[str, Any]:
    """按 draw_index 排序返回 ``{name: path}``，并报告顺序来源与未知层名。

    Args:
        layers_dir: 分层产物目录（PNG + 可选 ``layer_guide.json``）。

    Returns:
        OrderedDict ``{layer_name: absolute_png_path}``，顺序即 moc3 的 draw_order
        （小的先画、在底层）。

    Note:
        - ``draw_index`` 是顺序的唯一权威（小的先画、在底层）；
          ``layer_guide.json`` 缺失或不可读时退回文件名字母序（降级路径）。
        - 不在 ``draw_index`` 里的层按字母序追加到最前，并打印一次警告，
          不再静默兜底。

    Raises:
        FileNotFoundError: 目录里没有任何可用 PNG。
    """
    draw_index = _read_draw_index(layers_dir)

    paths: Dict[str, str] = {}
    for p in sorted(glob.glob(os.path.join(layers_dir, "*.png"))):
        name = os.path.splitext(os.path.basename(p))[0]
        if name in NON_LAYER_FILES:
            continue
        paths[name] = p

    if not paths:
        raise FileNotFoundError(f"图层目录内没有可用 PNG: {layers_dir}")

    unknown = sorted(n for n in paths if n not in draw_index)
    if unknown:
        _Log.warn("图层不在 layer_guide.json 中，按字母序追加到最前: "
                  + json.dumps(unknown, ensure_ascii=False))

    tail = float(len(draw_index) + 1)
    ordered_names = sorted(paths, key=lambda n: (draw_index.get(n, tail), n))
    return OrderedDict((name, paths[name]) for name in ordered_names)


def load_layers_for_export(layers_dir: str) -> "OrderedDict[str, Any]":
    """按 draw_index 排序把 PNG 读成 ``{name: PIL.Image}``。"""
    from PIL import Image

    inputs = load_export_layer_inputs(layers_dir)
    return OrderedDict((name, Image.open(path).convert("RGBA"))
                       for name, path in inputs.items())


def _read_draw_index(layers_dir: str) -> Dict[str, float]:
    """读 ``layer_guide.json`` 的 ``draw_index``；读不到就返回空表（字母序兜底）。"""
    guide_path = os.path.join(layers_dir, "layer_guide.json")
    try:
        with open(guide_path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return {}

    draw_index: Dict[str, float] = {}
    for entry in payload.get("layers") or []:
        name = entry.get("name") if isinstance(entry, dict) else None
        idx = entry.get("draw_index") if isinstance(entry, dict) else None
        if name is not None and isinstance(idx, (int, float)):
            draw_index[str(name)] = float(idx)
    return draw_index
