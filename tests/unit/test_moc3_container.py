"""容器层：头部、偏移表、对齐与总长度。"""
import struct

import pytest

from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_container import Moc3Container


def _minimal() -> Moc3Container:
    c = Moc3Container(version=ms.MocVersion.V3_03)
    c.canvas.pixels_per_unit = 1.0
    c.canvas.canvas_width = 512.0
    c.canvas.canvas_height = 512.0
    return c


def test_header_magic_and_version():
    data = _minimal().to_bytes()
    assert data[:4] == b"MOC3"
    assert struct.unpack_from("<i", data, 4)[0] == ms.MocVersion.V3_03


def test_body_starts_at_default_offset():
    data = _minimal().to_bytes()
    assert len(data) >= ms.DEFAULT_OFFSET
    assert len(data) % ms.ALIGN == 0


def test_sot_slot0_points_at_default_offset():
    data = _minimal().to_bytes()
    slot0 = struct.unpack_from("<I", data, ms.HEADER_SIZE)[0]
    assert slot0 == ms.DEFAULT_OFFSET


def test_count_info_size_is_128():
    c = _minimal()
    assert ms.COUNT_INFO_SIZE == 128
    assert len(c.counts) == ms.COUNT_INFO_MAX


def test_empty_document_roundtrips_through_reference_reader(tmp_path):
    Moc3 = pytest.importorskip("moc3").Moc3
    p = tmp_path / "empty.moc3"
    p.write_bytes(_minimal().to_bytes())
    back = Moc3.from_file(str(p))
    assert back.canvas.canvas_width == 512.0
    assert back["part.ids"] == []
