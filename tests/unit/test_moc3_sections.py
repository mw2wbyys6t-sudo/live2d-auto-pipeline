"""断言我方 section 布局与 py-moc3 的权威布局完全一致。"""
import pytest

from live2d_builder.exporter import moc3_sections as ms


def test_constants_match_reference():
    _core = pytest.importorskip("moc3._core")
    assert ms.MAGIC == _core.MAGIC
    assert ms.HEADER_SIZE == _core.HEADER_SIZE
    assert ms.SOT_COUNT == _core.SOT_COUNT
    assert ms.COUNT_INFO_MAX == _core.COUNT_INFO_MAX
    assert ms.DEFAULT_OFFSET == _core.DEFAULT_OFFSET
    assert ms.ALIGN == _core.ALIGN


def test_section_layout_parity():
    _core = pytest.importorskip("moc3._core")
    assert len(ms.SECTION_LAYOUT) == len(_core.SECTION_LAYOUT)
    for mine, ref in zip(ms.SECTION_LAYOUT, _core.SECTION_LAYOUT):
        assert mine.name == ref.name
        assert mine.elem_type == ref.elem_type
        assert mine.count_idx == ref.count_idx
        assert mine.align == ref.align
        assert mine.group == ref.group


def test_moc_version_parity():
    _core = pytest.importorskip("moc3._core")
    for name, member in _core.MocVersion.__members__.items():
        assert getattr(ms.MocVersion, name) == int(member)


def _as_tuples(entries):
    return [(e.name, e.elem_type, e.count_idx, e.align, e.group) for e in entries]


def test_additional_v303_is_transcribed_not_dropped():
    """V3_03 追加的 section 决定 SOT 槽位数，漏抄会让官方内核判为结构非法。"""
    _core = pytest.importorskip("moc3._core")
    extra = _core.ADDITIONAL_V303
    expected = list(extra) if isinstance(extra, (list, tuple)) else [extra]
    assert _as_tuples(ms.ADDITIONAL_V303) == _as_tuples(expected)


def test_build_layout_grows_with_version():
    _core = pytest.importorskip("moc3._core")
    base = len(ms.build_layout(ms.MocVersion.V3_00))
    assert base == len(_core.SECTION_LAYOUT)
    assert len(ms.build_layout(ms.MocVersion.V3_03)) == base + len(ms.ADDITIONAL_V303)


def test_elem_sizes_parity():
    _core = pytest.importorskip("moc3._core")
    assert ms.ELEM_SIZES == dict(_core.ELEM_SIZES)


def test_count_idx_is_complete():
    assert len(ms.CountIdx.__dict__) - len(
        [k for k in ms.CountIdx.__dict__ if k.startswith("_")]
    ) == 23


def test_get_section_rejects_unknown_name():
    with pytest.raises(KeyError):
        ms.get_section("no.such.section")


def test_exporter_package_exposes_moc3_api():
    from live2d_builder import exporter
    assert hasattr(exporter, "build_minimal_model")
    assert hasattr(exporter, "lint_document")
    assert hasattr(exporter, "verify_moc3_consistency")
    assert hasattr(exporter, "verify_moc3_load")
