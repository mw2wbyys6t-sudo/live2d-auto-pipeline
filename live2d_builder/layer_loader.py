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
from typing import Any, Dict, List, Optional, Set

# 与真实图层同目录、但不是图层的整图预览文件。
NON_LAYER_FILES: Set[str] = {"preview", "composite_preview"}

__all__ = [
    "NON_LAYER_FILES",
    "ExportLayerInputs",
    "load_export_inputs",
    "load_export_layer_inputs",
    "load_layers_for_export",
    "parts_blocker_message",
    "read_parts_readiness",
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
    # 分层清单对本批部件的结论：False = 连 ``REQUIRED_FOR_PREVIEW`` 都不齐。
    # 这是**如实报告**，不是拒绝依据 —— 实测生产默认路径只缺一个 mouth 也算
    # False（本机 14 个真实分层里 10 个为 False），拿它当闸门会拦死正常作品。
    # None = 该目录没有 ``layer_manifest.json``（用户导入的 PSD/PNG 就没有），
    # 此时**不据此拒绝** —— 没有部件词表依据，判它缺什么等于凭空断言。
    parts_ready: Optional[bool] = None
    # ``parts_ready is False`` 时缺的那些部件 id（用于把原因说给用户听）。
    missing_preview: List[str] = field(default_factory=list)
    # 一个必需部件都没认出来 = 空心。这才是拒绝构建的硬线（空集边界，
    # 不依赖拍脑袋的阈值）；理由与判据见 ``layer_manifest.is_hollow``。
    parts_hollow: bool = False
    # 清单里的分层来源原样带出：``color_cluster`` 与「上游没画出人」的
    # 拒绝理由不一样，混成一句话用户就没法照着做。
    parts_source: str = ""


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
    readiness = read_parts_readiness(layers_dir)
    missing = list((readiness or {}).get("missing_preview") or [])
    # 与 _by_standard_order 同一做法：词表与判据都住在清单模块里，这里只调用。
    from core.segment_engine.layer_manifest import is_hollow
    return ExportLayerInputs(
        paths=OrderedDict((name, paths[name]) for name in ordered_names),
        order_source="layer_guide" if draw_index else "filename",
        unknown=unknown,
        draw_index=draw_index,
        normalized=ordered_names != guide_order,
        parts_ready=None if readiness is None else bool(readiness.get("preview_ready")),
        missing_preview=missing,
        parts_hollow=readiness is not None and is_hollow(missing),
        parts_source=str((readiness or {}).get("source") or ""),
    )


def parts_blocker_message(missing_preview: List[str], source: str = "") -> str:
    """把"一个部件都没认出来"翻译成用户能照着做的一句话。

    两条导出路径（Go 导出桥、Go 阶段事件桥）共用它：拒绝理由此前只写在
    ``layer_manifest.json`` 里，用户在界面上看到的是一句「导出成功」。
    分来源说：颜色聚类本来就没有部件身份（换提示词没用，得换分层方式），
    而语义分层交出空结果通常是上游图里根本没有人。
    """
    from core.segment_engine.layer_manifest import PART_SPECS

    labels = [str((PART_SPECS.get(pid) or {}).get("name_zh") or pid)
              for pid in missing_preview]
    head = (
        "分层没有认出任何角色部件"
        f"（{'、'.join(labels) if labels else '清单未列出'}），已拒绝构建模型。"
    )
    why = {
        "color_cluster": "这次用的是颜色聚类，它只按颜色分块、不认部位，"
                         "清单本身也标注「仅可预览，不可用于 Live2D 绑定」。",
        "semantic_hsv_fallback": "没拿到前景分割模型，已退化成纯颜色启发式，"
                                 "结果不认部位。",
    }.get(source, "立绘里可能根本没有可辨认的人物（上游未按提示词出图）。")
    return (
        head + why +
        "官方内核只会证明一个 moc3 能被加载，不证明它是有眼有嘴的角色，"
        "所以这里宁可不出货。请换一句更明确的人物描述重新生成并分层，"
        "换用「语义分层」，或在「分层」阶段导入自己的 PSD/PNG。"
    )


def read_parts_readiness(layers_dir: str) -> Optional[Dict[str, Any]]:
    """读 ``layer_manifest.json`` 里部件齐不齐的结论；没有清单返回 ``None``。

    为什么要有这一步：清单本来就是分层侧对自己产物的判定（``preview_ready`` =
    ``REQUIRED_FOR_PREVIEW`` 那 5 个部件是否都在），但导出侧此前完全没读它，
    于是同一个分层目录可以一边写着「缺少必要部件，无法进入预览」，一边在导出
    响应里拿到 ``runtime_ready=true``。``runtime_ready`` 只证明官方内核肯加载
    这个 moc3，不证明它是个有眼有嘴的角色。

    清单读不到 / 解析失败一律返回 ``None``（= 不判定），而不是返回"不合格"：
    用户导入的 PSD/PNG 目录没有清单，那 5 个部件 id 对我们的命名约定并不成立，
    判它缺东西是凭空断言。
    """
    from core.segment_engine.layer_manifest import MANIFEST_FILENAME

    path = os.path.join(layers_dir, MANIFEST_FILENAME)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("preview_ready"), bool):
        return None
    return {
        "preview_ready": data["preview_ready"],
        "missing_preview": list(data.get("missing_preview") or []),
        "source": str(data.get("source") or ""),
    }


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
