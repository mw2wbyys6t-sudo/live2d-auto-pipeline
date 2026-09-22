#!/usr/bin/env python3
"""从 py-moc3 提取权威 moc3 section 布局，生成 live2d_builder/exporter/moc3_sections.py。

py-moc3 采用 MIT 许可，其 SECTION_LAYOUT 源自对官方 Cubism SDK 导出器的反编译。
本脚本只做「数据转录」，不复制其实现代码；生成物入库并带一致性测试。
运行：python tools/extract_moc3_layout.py
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from moc3 import _core
except ImportError:  # pragma: no cover
    print("需要先安装 py-moc3: python -m pip install py-moc3", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "live2d_builder" / "exporter" / "moc3_sections.py"


def fmt_counts() -> str:
    items = sorted(
        ((v, k) for k, v in vars(_core.CountIdx).items()
         if not k.startswith("_") and isinstance(v, int)),
    )
    lines = ["class CountIdx:"]
    lines.append('    """count info 表索引（共 %d 项，仅 %d 项在用）。"""'
                 % (_core.COUNT_INFO_MAX, len(items)))
    seen: set[int] = set()
    for value, name in items:
        if value in seen:
            continue
        seen.add(value)
        lines.append(f"    {name} = {value}")
    return "\n".join(lines)


def fmt_versions() -> str:
    lines = ["class MocVersion:"]
    lines.append('    """moc3 格式版本（值与参考库一致，勿手改）。"""')
    for name, member in _core.MocVersion.__members__.items():
        lines.append(f"    {name} = {int(member)}")
    return "\n".join(lines)


def fmt_sections() -> str:
    lines = ["SECTION_LAYOUT: List[SectionEntry] = ["]
    for e in _core.SECTION_LAYOUT:
        lines.append(
            f'    SectionEntry(name="{e.name}", elem_type="{e.elem_type}", '
            f"count_idx={e.count_idx}, align={e.align}, group=\"{e.group}\"),"
        )
    lines.append("]")
    return "\n".join(lines)


def fmt_additional() -> str:
    """V3_03 起追加的 section。参考库里是单个 SectionEntry，这里统一转录为列表。"""
    extra = getattr(_core, "ADDITIONAL_V303", None)
    if extra is None:
        entries = []
    elif isinstance(extra, (list, tuple)):
        entries = list(extra)
    else:
        entries = [extra]
    lines = [
        "# 版本 >= V3_03 时追加的 section；SOT 槽位数随之增加，写出时必须计入。",
        "ADDITIONAL_V303: List[SectionEntry] = [",
    ]
    for e in entries:
        lines.append(
            f'    SectionEntry(name="{e.name}", elem_type="{e.elem_type}", '
            f"count_idx={e.count_idx}, align={e.align}, group=\"{e.group}\"),"
        )
    lines.append("]")
    return "\n".join(lines)



def main() -> int:
    body = f'''"""moc3 二进制格式的 section 布局定义（自动生成，请勿手改）。

生成脚本: tools/extract_moc3_layout.py
数据来源: py-moc3 (MIT) 的 SECTION_LAYOUT，源自官方 Cubism SDK 导出器反编译。

容器布局:
    [0:64]      头部（magic b"MOC3" + 版本 + 端序）
    [64:704]    Section Offset Table，160 x uint32
    [704:1984]  零填充
    [1984:]     body = countInfo(128) + canvasInfo(64) + 各 section（逐个 64 字节对齐）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

MAGIC = b"MOC3"
HEADER_SIZE = {_core.HEADER_SIZE}
SOT_SIZE = {_core.SOT_SIZE}
SOT_COUNT = {_core.SOT_COUNT}
COUNT_INFO_SIZE = {_core.COUNT_INFO_SIZE}
COUNT_INFO_MAX = {_core.COUNT_INFO_MAX}
DEFAULT_OFFSET = {_core.DEFAULT_OFFSET}
ALIGN = {_core.ALIGN}
RUNTIME_UNIT_SIZE = {_core.RUNTIME_UNIT_SIZE}
CANVAS_BODY_SIZE = {_core.CanvasInfo.BODY_SIZE}


{fmt_versions()}


ELEM_SIZES: Dict[str, int] = {{
{chr(10).join(f'    "{k}": {v},' for k, v in _core.ELEM_SIZES.items())}
}}


@dataclass(frozen=True)
class SectionEntry:
    """一个 section 的描述：名称、元素类型、计数来源、对齐、所属分组。"""
    name: str
    elem_type: str
    count_idx: int
    align: int
    group: str

    @property
    def elem_size(self) -> int:
        return ELEM_SIZES[self.elem_type]


{fmt_counts()}


{fmt_sections()}


{fmt_additional()}

_SECTION_BY_NAME: Dict[str, SectionEntry] = {{e.name: e for e in SECTION_LAYOUT}}


def get_section(name: str) -> SectionEntry:
    """按名称取 section 定义；不存在则抛 KeyError。"""
    return _SECTION_BY_NAME[name]


def build_layout(version: int) -> List[SectionEntry]:
    """按格式版本返回实际使用的 section 序列。"""
    if version >= MocVersion.V3_03:
        return list(SECTION_LAYOUT) + list(ADDITIONAL_V303)
    return list(SECTION_LAYOUT)
'''
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(body, encoding="utf-8")
    print(f"已生成 {TARGET}  （{len(_core.SECTION_LAYOUT)} 个 section）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
