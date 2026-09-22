"""moc3 容器层：头部 + Section Offset Table + body 组装。

纯函数，无 IO、无外部进程，可独立单元测试。
容器布局见 moc3_sections 模块文档字符串。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List

from live2d_builder.exporter import moc3_sections as ms


class _Writer:
    """小端二进制累加器，支持对齐填充。"""

    def __init__(self) -> None:
        self._buf = bytearray()

    @property
    def pos(self) -> int:
        return len(self._buf)

    def write_bytes(self, data: bytes) -> None:
        self._buf += data

    def write_i32_array(self, values: List[int]) -> None:
        self._buf += struct.pack(f"<{len(values)}i", *values)

    def write_f32(self, value: float) -> None:
        self._buf += struct.pack("<f", value)

    def write_u1(self, value: int) -> None:
        self._buf += bytes([value & 0xFF])

    def fill(self, count: int) -> None:
        if count > 0:
            self._buf += bytes(count)

    def pad_to(self, alignment: int) -> None:
        rem = self.pos % alignment
        if rem:
            self.fill(alignment - rem)

    def get_bytes(self) -> bytes:
        return bytes(self._buf)


@dataclass
class CanvasInfo:
    """画布信息：5 个 float + 1 个 flag，占 CANVAS_BODY_SIZE 字节。"""
    pixels_per_unit: float = 1.0
    origin_x: float = 0.0
    origin_y: float = 0.0
    canvas_width: float = 0.0
    canvas_height: float = 0.0
    canvas_flag: int = 0


@dataclass
class Moc3Container:
    """可序列化的 moc3 文档。

    - ``counts``：count info 表（长度 = COUNT_INFO_MAX）
    - ``sections``：section 名 -> 值列表
    """
    version: int
    counts: List[int] = field(default_factory=list)
    canvas: CanvasInfo = field(default_factory=CanvasInfo)
    sections: Dict[str, list] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.counts:
            self.counts = [0] * ms.COUNT_INFO_MAX

    def get(self, name: str) -> list:
        return self.sections.get(name, [])

    def set(self, name: str, values: list) -> None:
        ms.get_section(name)  # 未知名称立即报错
        self.sections[name] = list(values)

    def to_bytes(self) -> bytes:
        layout = ms.build_layout(self.version)

        body = _Writer()
        sot: List[int] = []

        # SOT[0] = count info
        sot.append(ms.DEFAULT_OFFSET + body.pos)
        body.write_i32_array(self.counts)
        body.fill(ms.COUNT_INFO_SIZE - (ms.COUNT_INFO_MAX * 4))

        # SOT[1] = canvas info
        sot.append(ms.DEFAULT_OFFSET + body.pos)
        canvas_start = body.pos
        body.write_f32(self.canvas.pixels_per_unit)
        body.write_f32(self.canvas.origin_x)
        body.write_f32(self.canvas.origin_y)
        body.write_f32(self.canvas.canvas_width)
        body.write_f32(self.canvas.canvas_height)
        body.write_u1(self.canvas.canvas_flag)
        body.fill(ms.CANVAS_BODY_SIZE - (body.pos - canvas_start))

        # SOT[2..] = 各 section
        for entry in layout:
            if entry.align > 0:
                body.pad_to(entry.align)
            sot.append(ms.DEFAULT_OFFSET + body.pos)
            _write_section(body, entry, self.sections.get(entry.name, []), self.counts)

        out = _Writer()
        out.write_bytes(_header_bytes(self.version))
        while len(sot) < ms.SOT_COUNT:
            sot.append(0)
        out.write_bytes(struct.pack(f"<{ms.SOT_COUNT}I", *sot[: ms.SOT_COUNT]))
        out.fill(ms.DEFAULT_OFFSET - out.pos)
        assert out.pos == ms.DEFAULT_OFFSET, "SOT 布局与 DEFAULT_OFFSET 不符"
        out.write_bytes(body.get_bytes())
        out.pad_to(ms.ALIGN)
        return out.get_bytes()


def _header_bytes(version: int) -> bytes:
    w = _Writer()
    w.write_bytes(ms.MAGIC)
    w.write_u1(version)
    w.write_u1(0)  # 端序标志：0 = 小端
    w.fill(ms.HEADER_SIZE - w.pos)
    return w.get_bytes()


def _expected_count(entry, values: list, counts: List[int]) -> int:
    if entry.count_idx < 0:
        return len(values)
    return counts[entry.count_idx]


def _write_section(w: _Writer, entry, values: list, counts: List[int]) -> None:
    """按元素类型写出一个 section。长度不足 counts 期望时补零。"""
    et = entry.elem_type

    if et == "runtime":
        count = counts[entry.count_idx] if entry.count_idx >= 0 else 0
        w.write_bytes(bytes(count * ms.RUNTIME_UNIT_SIZE))
        return

    expect = _expected_count(entry, values, counts)
    padded = list(values) + [0] * max(0, expect - len(values))

    if et == "i32":
        w.write_bytes(struct.pack(f"<{len(padded)}i", *padded))
    elif et == "bool":
        # moc3 内 bool 按 i32 存储（与参考库 write_bool_array 一致）
        w.write_bytes(struct.pack(
            f"<{len(padded)}i", *(1 if v else 0 for v in padded)))
    elif et == "f32":
        w.write_bytes(struct.pack(f"<{len(padded)}f", *padded))
    elif et == "i16":
        w.write_bytes(struct.pack(f"<{len(padded)}h", *padded))
    elif et == "u8":
        w.write_bytes(bytes(v & 0xFF for v in padded))
    elif et == "str64":
        for value in values:
            raw = value.encode("utf-8")[:63]
            w.write_bytes(raw + bytes(64 - len(raw)))
    else:
        raise ValueError(f"未知元素类型: {et}")
