"""导出前的图层装载：全仓唯一实现。

为什么只能有一份：选层逻辑曾被内联复制到 3 个调用点（Go 导出桥、Go 阶段事件桥、
``api_server._load_layers_for_export``），且**只有 1 处**做了正确的两项处理 —
— 剔除整图预览（``preview.png`` / ``composite_preview.png``，满画布 100% 不透明，
当成图层烘进 moc3 会把模型压成一张静止的图），并按 ``layer_guide.json`` 的
``draw_index`` 排序（退回文件名字母序会让 ``mouth_cavity`` 排到 ``mouth`` 之前，
口腔盖住嘴唇）。另 2 处副本没有这两项，其中一份就是**生产导出走的路**。

调用方只传路径；本模块负责「读哪些 PNG、按什么顺序、顺序取自哪」。
"""

from __future__ import annotations

import glob
import json
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

# 与真实图层同目录、但不是图层的整图预览文件。
NON_LAYER_FILES: Set[str] = {"preview", "composite_preview"}

__all__ = [
    "NON_LAYER_FILES",
    "ExportLayerInputs",
    "load_export_inputs",
    "load_export_layer_inputs",
    "load_layers_for_export",
]


@dataclass(frozen=True)
class ExportLayerInputs:
    """一次导出装载的完整结果（全部 JSON 可序列化）。"""

    # 按权威层序排好序的 {层名: PNG 绝对路径}；顺序即 moc3 的 draw_order。
    paths: "OrderedDict[str, str]"
    # 顺序取自哪："layer_guide"（有 guide）或 "filename"（降级，无 guide 时）。
    order_source: str
    # 不在 layer_guide.json 里的层名（排在最后、按字母序；非空说明词表漂移）。
    unknown: List[str] = field(default_factory=list)
    # 实际读到的 draw_index 表；fallback 路径为空 dict。
    draw_index: Dict[str, float] = field(default_factory=dict)
    # draw_index 与权威层序表冲突、被表纠正过：如实报告，不静默改用户的顺序。
    normalized: bool = False


def load_export_inputs(layers_dir: str) -> ExportLayerInputs:
    """按权威层序表排序返回 {name: path}，并报告顺序来源、未知层与是否被纠正。

    Args:
        layers_dir: 分层产物目录（PNG + 可选 ``layer_guide.json``）。

    Note:
        层序的唯一真源是 ``LayerComposer.STANDARD_LAYER_ORDER``（表内越靠后=画得
        越靠前）。``draw_index`` 只是分层当时的产物顺序，用作表内层之间的初始
        次序；与权威表冲突时被表纠正，并在 ``normalized`` 里如实报告。
        ``layer_guide.json`` 缺失或不可读时退回文件名字母序（降级路径）。
        不在权威表里的层排在最后并保持相对次序，同时打印一次警告，
        不再静默兜底（历史上 ``mouth_cavity`` 正是被字母序排到 ``mouth`` 之后、
        口腔盖住嘴唇）。

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
        _warn("图层不在 layer_guide.json 中，按权威层序表排位（表外者排最后）: "
              + json.dumps(unknown, ensure_ascii=False))

    tail = float(len(draw_index) + 1)
    guide_order = sorted(paths, key=lambda n: (draw_index.get(n, tail), n))
    ordered_names = _by_standard_order(guide_order)
    return ExportLayerInputs(
        paths=OrderedDict((name, paths[name]) for name in ordered_names),
        order_source="layer_guide" if draw_index else "filename",
        unknown=unknown,
        draw_index=draw_index,
        normalized=ordered_names != guide_order,
    )


def _by_standard_order(names: List[str]) -> List[str]:
    """按 ``STANDARD_LAYER_ORDER`` 稳定重排，表外的层保持原相对位置。

    权威表是层序的唯一真源：``draw_index`` 只是分层当时的产物，而表本身会演进
    （2026-10-21 从 18 项扩到 65 项并改了 face 语义）。照抄旧 ``draw_index`` 会
    让旧分层目录永远过不了 FF-2 下载闸门，用户手上没有任何补救入口。
    """
    from core.segment_engine.composer import LayerComposer

    rank = {name: index for index, name in enumerate(LayerComposer.STANDARD_LAYER_ORDER)}
    outside = float(len(rank) + 1)
    # 表内按表排位；表外一律落到最后、并保持原有相对次序（sorted 是稳定排序）。
    return sorted(names, key=lambda n: rank.get(n, outside))


def load_export_layer_inputs(layers_dir: str) -> "OrderedDict[str, str]":
    """{层名: PNG 路径} 的兼容包装，顺序与 :func:`load_export_inputs` 一致。"""
    return load_export_inputs(layers_dir).paths


def load_layers_for_export(layers_dir: str) -> "OrderedDict[str, Any]":
    """按 draw_index 排序把 PNG 读成 ``{name: PIL.Image}``。"""
    from PIL import Image

    inputs = load_export_inputs(layers_dir)
    return OrderedDict((name, Image.open(path).convert("RGBA"))
                       for name, path in inputs.paths.items())


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
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        idx = entry.get("draw_index")
        if name is not None and isinstance(idx, (int, float)):
            draw_index[str(name)] = float(idx)
    return draw_index


def _warn(message: str) -> None:
    import sys
    sys.stderr.write(f"[warn] {message}\n")
