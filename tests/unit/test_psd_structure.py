"""PSD 输入结构保真与变体识别（M3 / INV-M3-1、INV-M3-2）。

重点验证两件事：
1. 解析不再把图层树扁平化 —— 每个非根图层都有 group_path，父组都在输出里；
2. 变体只从**真实可见性互斥 + 组名语义**识别，PNG 输入显式 N/A，不伪造。
"""
from __future__ import annotations

import sys
import types

import pytest

from core.psd.parser import PSDParser, detect_variants, png_input_meta


# ---------------------------------------------------------------------------
# 假 psd-tools：只实现 parser 用到的最小接口
# ---------------------------------------------------------------------------

class _FakeLayer:
    def __init__(self, name, visible=True, children=None, offset=(0, 0), size=(10, 10)):
        self.name = name
        self.visible = visible
        self.offset = offset
        self.size = size
        self.kind = "group" if children is not None else "pixel"
        self.opacity = 255
        self._children = children

    def is_group(self) -> bool:
        return self._children is not None

    def __iter__(self):
        return iter(self._children or [])


class _FakePSD(_FakeLayer):
    def __init__(self, children, width=100, height=200):
        super().__init__("root", children=children)
        self.width = width
        self.height = height
        self.channels = 4
        self.depth = 8
        self.color_mode = "RGB"


@pytest.fixture()
def fake_psd_tools(monkeypatch: pytest.MonkeyPatch):
    """注入假 psd_tools，避免测试依赖二进制 PSD 文件。"""
    holder = {}

    class _PSDImage:
        @staticmethod
        def open(_path):
            return holder["psd"]

    module = types.SimpleNamespace(PSDImage=_PSDImage)
    monkeypatch.setitem(sys.modules, "psd_tools", module)
    return holder


def _parse(monkeypatch, holder, psd) -> dict:
    holder["psd"] = psd
    return PSDParser()._parse_with_psd_tools("fake.psd")


# ---------------------------------------------------------------------------
# 结构保真
# ---------------------------------------------------------------------------

def test_nested_groups_keep_parent_and_path(fake_psd_tools, monkeypatch) -> None:
    psd = _FakePSD([
        _FakeLayer("身体", children=[
            _FakeLayer("头发", children=[
                _FakeLayer("后发", visible=True),
            ]),
            _FakeLayer("脸", visible=True),
        ]),
    ])

    meta = _parse(monkeypatch, fake_psd_tools, psd)
    by_name = {layer["name"]: layer for layer in meta["layers"]}

    assert by_name["头发"]["parent"] == "身体"
    assert by_name["头发"]["group_path"] == "身体/头发"
    assert by_name["后发"]["group_path"] == "身体/头发/后发"
    assert by_name["后发"]["parent"] == "头发"
    assert by_name["后发"]["depth"] == 2
    assert by_name["后发"]["is_group"] is False
    # INV-M3-1：每个非根图层的父组都必须在输出里出现
    names = set(by_name)
    for layer in meta["layers"]:
        if layer["parent"] is not None:
            assert layer["parent"] in names


def test_visibility_and_offset_are_preserved(fake_psd_tools, monkeypatch) -> None:
    psd = _FakePSD([
        _FakeLayer("上装", visible=False, offset=(12, 34), size=(56, 78)),
    ])

    meta = _parse(monkeypatch, fake_psd_tools, psd)
    layer = meta["layers"][0]

    assert layer["visible"] is False
    assert layer["offset"] == (12, 34)
    assert layer["size"] == (56, 78)


def test_index_in_parent_reflects_draw_order(fake_psd_tools, monkeypatch) -> None:
    psd = _FakePSD([
        _FakeLayer("a"), _FakeLayer("b"), _FakeLayer("c"),
    ])

    meta = _parse(monkeypatch, fake_psd_tools, psd)

    assert [layer["index_in_parent"] for layer in meta["layers"]] == [0, 1, 2]


# ---------------------------------------------------------------------------
# 变体识别
# ---------------------------------------------------------------------------

def test_mutually_exclusive_groups_are_detected_as_variants(fake_psd_tools, monkeypatch) -> None:
    psd = _FakePSD([
        _FakeLayer("服装", visible=True, children=[
            _FakeLayer("服装_校服", visible=True, children=[_FakeLayer("裙")]),
            _FakeLayer("服装_礼服", visible=False, children=[_FakeLayer("长裙")]),
        ]),
    ])

    meta = _parse(monkeypatch, fake_psd_tools, psd)

    assert meta["variants_status"] == "ok"
    variants = meta["variants"]
    assert len(variants) == 2
    assert {v["kind"] for v in variants} == {"outfit"}
    active = [v for v in variants if v["visible"]]
    assert len(active) == 1
    assert active[0]["members"] == ["裙"]


def test_simultaneously_visible_groups_are_not_variants(fake_psd_tools, monkeypatch) -> None:
    """同时可见 = 可叠加部件，不是互斥状态，不得报成变体。"""
    psd = _FakePSD([
        _FakeLayer("服装", children=[
            _FakeLayer("服装_外套", visible=True, children=[_FakeLayer("a")]),
            _FakeLayer("服装_内搭", visible=True, children=[_FakeLayer("b")]),
        ]),
    ])

    meta = _parse(monkeypatch, fake_psd_tools, psd)

    assert meta["variants"] == []


def test_single_group_with_keyword_is_not_a_variant(fake_psd_tools, monkeypatch) -> None:
    psd = _FakePSD([
        _FakeLayer("服装", children=[_FakeLayer("主体")]),
    ])

    meta = _parse(monkeypatch, fake_psd_tools, psd)

    assert meta["variants"] == []


def test_expression_variants_use_their_own_kind(fake_psd_tools, monkeypatch) -> None:
    psd = _FakePSD([
        _FakeLayer("表情", children=[
            _FakeLayer("表情_微笑", visible=False, children=[_FakeLayer("嘴微笑")]),
            _FakeLayer("表情_生气", visible=True, children=[_FakeLayer("眉压低")]),
        ]),
    ])

    meta = _parse(monkeypatch, fake_psd_tools, psd)

    assert {v["kind"] for v in meta["variants"]} == {"expression"}
    assert {v["name"] for v in meta["variants"]} == {"表情_微笑", "表情_生气"}


# ---------------------------------------------------------------------------
# PNG 输入：显式 N/A，不伪造
# ---------------------------------------------------------------------------

def test_png_input_declares_not_applicable() -> None:
    meta = png_input_meta(1024, 1536)

    assert meta["variants"] == []
    assert meta["variants_status"] == "not_applicable"
    assert meta["width"] == 1024 and meta["height"] == 1536


def test_png_input_never_produces_variants_from_pixels() -> None:
    """即使尺寸像立绘，PNG 通道也不会凭空造出表情/服装变体（INV-M3-2）。"""
    meta = png_input_meta(3749, 7697)
    assert detect_variants(meta["layers"]) == []


# ---------------------------------------------------------------------------
# 安全校验：PSD 扩展名不能被图片白名单误杀，同时 magic 仍要生效
# ---------------------------------------------------------------------------

def test_validate_psd_file_accepts_psd_extension(tmp_path) -> None:
    """回归：曾因复用图片扩展名白名单，导致所有 .psd 被判「不支持的图片格式」。"""
    from PIL import Image
    from psd_tools import PSDImage

    from core.security import validate_psd_file

    psd = PSDImage.new("RGBA", (16, 16))
    psd.append(psd.create_pixel_layer(
        Image.new("RGBA", (4, 4), (1, 2, 3, 255)), name="layer"))
    path = tmp_path / "ok.psd"
    psd.save(str(path))

    valid, reason = validate_psd_file(str(path))

    assert valid is True, reason


def test_validate_psd_file_rejects_non_psd_extension(tmp_path) -> None:
    from core.security import validate_psd_file

    path = tmp_path / "fake.png"
    path.write_bytes(b"8BPS" + b"\x00" * 32)

    valid, reason = validate_psd_file(str(path))

    assert valid is False
    assert "extension" in reason.lower() or "扩展" in reason


def test_validate_psd_file_rejects_bad_magic(tmp_path) -> None:
    from core.security import validate_psd_file

    path = tmp_path / "bad.psd"
    path.write_bytes(b"NOTP" + b"\x00" * 64)

    valid, reason = validate_psd_file(str(path))

    assert valid is False
    assert "magic" in reason.lower()


# ---------------------------------------------------------------------------
# 真实 PSD 往返：不依赖假模块，验证递归遍历在真文件上成立
# ---------------------------------------------------------------------------

def test_real_psd_roundtrip_keeps_group_tree_and_variants(tmp_path) -> None:
    """真文件往返。层名用 ASCII：psd_tools 新建 PSD 时层名按 macroman 编码，
    中文写不进去（库的限制，与本模块逻辑无关）；中文语义名已由上面的假树测试覆盖。
    """
    from PIL import Image
    from psd_tools import PSDImage

    psd = PSDImage.new("RGBA", (32, 32))
    body = psd.create_group(name="body")
    outfit_a = psd.create_group([psd.create_pixel_layer(
        Image.new("RGBA", (8, 8), (255, 0, 0, 255)), name="skirt")], name="outfit_school")
    outfit_b = psd.create_group([psd.create_pixel_layer(
        Image.new("RGBA", (8, 8), (0, 255, 0, 255)), name="gown")], name="outfit_gown")
    outfit_b.visible = False
    body.append(outfit_a)
    body.append(outfit_b)
    psd.append(psd.create_pixel_layer(
        Image.new("RGBA", (8, 8), (0, 0, 255, 255)), name="face"))
    path = tmp_path / "variant.psd"
    psd.save(str(path))

    meta = PSDParser().parse(str(path))

    by_name = {layer["name"]: layer for layer in meta["layers"]}
    assert by_name["outfit_school"]["group_path"] == "body/outfit_school"
    assert by_name["outfit_school"]["parent"] == "body"
    assert by_name["outfit_gown"]["visible"] is False
    assert by_name["skirt"]["group_path"] == "body/outfit_school/skirt"
    variants = meta["variants"]
    assert {v["name"] for v in variants} == {"outfit_school", "outfit_gown"}
    assert all(v["kind"] == "outfit" for v in variants)
    assert [v["visible"] for v in variants].count(True) == 1