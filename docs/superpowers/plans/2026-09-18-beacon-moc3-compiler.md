# Beacon moc3 编译器（P0）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 beacon 能自产出被官方 Cubism Core 接受的 `.moc3`，并把验证结论建立在客观证据上（禁止虚假成功）。

**Architecture:** 分四层——(1) 权威 section 布局定义，(2) 纯函数容器写出器，(3) 绑定数据 → 最小模型的构建器，(4) 自洽性静态校验 + 子进程隔离的官方一致性验证。复用既有 `drivers/live2d_runtime/native.py` 与 `verify.py`，不新建第二套验证逻辑。

**Tech Stack:** Python 3.12 / pytest / py-moc3（仅参考与交叉校验）/ live2d-py（仅隔离验证）

**设计依据:** [2026-09-18-beacon-editor-design.md](file:///c:/Users/admin/Desktop/Live2D/beacon/docs/superpowers/specs/2026-09-18-beacon-editor-design.md)

**范围说明:** 本计划**只覆盖 P0**。P1（编辑器骨架）与 P2（可动性/自有轨道）各自另立计划。

---

## 关键实测事实（本计划依赖，勿再猜测）

| 事实 | 来源 |
|---|---|
| `MAGIC=b"MOC3"`、`HEADER_SIZE=64`、`SOT_COUNT=160`、`SOT_SIZE=640` | py-moc3 `_core.py:37-40` |
| `COUNT_INFO_SIZE=128`、`COUNT_INFO_MAX=23`、`DEFAULT_OFFSET=1984`、`ALIGN=64` | py-moc3 `_core.py:41-44` |
| `CanvasInfo.BODY_SIZE=64`、`RUNTIME_UNIT_SIZE=8` | py-moc3 `_core.py:474,520` |
| 容器布局 `[0:64]header [64:704]SOT [704:1984]零填充 [1984:]body` | 推导 + 官方 Mark.moc3 字节实证 |
| `counts[UVS]` 计的是**浮点数个数**（2×顶点数） | 本机实测往返 |
| 官方 Core 拒绝缺绑定链的模型，报 `Data section is invalid` | 本机实测 |
| Cubism Core 未 `sdk.init()` 直接调用会**段错误**（`0xC0000005`） | 本机实测 |

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `tools/extract_moc3_layout.py` | 开发期脚本：从 py-moc3 提取权威布局，生成下方 `moc3_sections.py` |
| `live2d_builder/exporter/moc3_sections.py` | 生成物（提交入库）：常量 + `CountIdx` + `SectionEntry` + `SECTION_LAYOUT` |
| `live2d_builder/exporter/moc3_container.py` | 纯函数：header + SOT + body 组装为字节 |
| `live2d_builder/exporter/moc3_builder.py` | 绑定数据 → 最小模型 section 图 |
| `live2d_builder/exporter/moc3_lint.py` | 写出前的自洽性静态校验 |
| `drivers/live2d_runtime/moc3_consistency.py` | 子进程入口：只做官方一致性检查 |
| `drivers/live2d_runtime/moc3_verify.py` | 调用方：subprocess + 超时 + 崩溃捕获 |
| `tests/unit/test_moc3_sections.py` | 布局与 py-moc3 一致性 |
| `tests/unit/test_moc3_container.py` | 容器字节结构 |
| `tests/unit/test_moc3_builder.py` | 最小模型构建 + 往返 |
| `tests/unit/test_moc3_lint.py` | 非法输入被拦截 |
| `tests/unit/test_moc3_verify.py` | 替身模拟超时/崩溃归类 |
| `tests/integration/test_moc3_official_acceptance.py` | 官方 Haru 回归 + 最小模型验收 |

**测试命令**（来自 `run_all_tests.sh`）：
- 单元：`python -m pytest tests/unit/ -q --no-header`
- 集成：`python -m pytest tests/integration/ -q --no-header`

---

## Task 1: 依赖声明与权威布局提取

**Files:**
- Modify: `requirements.txt`
- Create: `tools/extract_moc3_layout.py`
- Create: `live2d_builder/exporter/moc3_sections.py`（由脚本生成后提交）
- Test: `tests/unit/test_moc3_sections.py`

- [ ] **Step 1: 写入依赖声明**

在 `requirements.txt` 的 `# ====== Optional: AI Models ======` 段**之前**插入：

```txt
# ====== Live2D moc3 compile & verification (P0) ======
# py-moc3: 结构参考与交叉校验（读写 .moc3）；编译器本体自研，不依赖其运行时
py-moc3>=0.1.0
# live2d-py: 仅用于隔离验证（官方 Cubism Native Core）；不进入生产数据路径
live2d-py>=0.7.0
```

- [ ] **Step 2: 写提取脚本**

创建 `tools/extract_moc3_layout.py`：

```python
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


ADDITIONAL_V303: List[SectionEntry] = []

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
```

- [ ] **Step 3: 运行脚本生成布局文件**

Run: `python tools/extract_moc3_layout.py`
Expected: `已生成 ...\live2d_builder\exporter\moc3_sections.py  （99 个 section）`

- [ ] **Step 4: 写失败测试（一致性断言）**

创建 `tests/unit/test_moc3_sections.py`：

```python
"""断言我方 section 布局与 py-moc3 的权威布局完全一致。"""
import pytest

from live2d_builder.exporter import moc3_sections as ms


def test_constants_match_reference():
    from moc3 import _core
    assert ms.MAGIC == _core.MAGIC
    assert ms.HEADER_SIZE == _core.HEADER_SIZE
    assert ms.SOT_COUNT == _core.SOT_COUNT
    assert ms.COUNT_INFO_MAX == _core.COUNT_INFO_MAX
    assert ms.DEFAULT_OFFSET == _core.DEFAULT_OFFSET
    assert ms.ALIGN == _core.ALIGN


def test_section_layout_parity():
    from moc3 import _core
    assert len(ms.SECTION_LAYOUT) == len(_core.SECTION_LAYOUT)
    for mine, ref in zip(ms.SECTION_LAYOUT, _core.SECTION_LAYOUT):
        assert mine.name == ref.name
        assert mine.elem_type == ref.elem_type
        assert mine.count_idx == ref.count_idx
        assert mine.align == ref.align
        assert mine.group == ref.group


def test_moc_version_parity():
    from moc3 import _core
    for name, member in _core.MocVersion.__members__.items():
        assert getattr(ms.MocVersion, name) == int(member)


def test_elem_sizes_parity():
    from moc3 import _core
    assert ms.ELEM_SIZES == dict(_core.ELEM_SIZES)


def test_count_idx_is_complete():
    assert len(ms.CountIdx.__dict__) - len(
        [k for k in ms.CountIdx.__dict__ if k.startswith("_")]
    ) == 23


def test_get_section_rejects_unknown_name():
    with pytest.raises(KeyError):
        ms.get_section("no.such.section")
```

- [ ] **Step 5: 运行测试**

Run: `python -m pytest tests/unit/test_moc3_sections.py -v`
Expected: 6 passed

- [ ] **Step 6: 提交**

```bash
git add requirements.txt tools/extract_moc3_layout.py live2d_builder/exporter/moc3_sections.py tests/unit/test_moc3_sections.py
git commit -m "feat(moc3): add authoritative section layout extracted from py-moc3"
```

---

## Task 2: 容器写出器（header + SOT + body）

**Files:**
- Create: `live2d_builder/exporter/moc3_container.py`
- Test: `tests/unit/test_moc3_container.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_moc3_container.py`：

```python
"""容器层：头部、偏移表、对齐与总长度。"""
import struct

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
    from moc3 import Moc3
    p = tmp_path / "empty.moc3"
    p.write_bytes(_minimal().to_bytes())
    back = Moc3.from_file(str(p))
    assert back.canvas.canvas_width == 512.0
    assert back["part.ids"] == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_moc3_container.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'live2d_builder.exporter.moc3_container'`

- [ ] **Step 3: 实现容器写出器**

创建 `live2d_builder/exporter/moc3_container.py`：

```python
"""moc3 容器层：头部 + Section Offset Table + body 组装。

纯函数，无 IO、无外部进程，可独立单元测试。
容器布局见 moc3_sections 模块文档字符串。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


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
        import struct
        self._buf += struct.pack(f"<{len(values)}i", *values)

    def write_i32(self, value: int) -> None:
        import struct
        self._buf += struct.pack("<i", value)

    def write_f32(self, value: float) -> None:
        import struct
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
        from live2d_builder.exporter import moc3_sections as ms
        if not self.counts:
            self.counts = [0] * ms.COUNT_INFO_MAX

    def get(self, name: str) -> list:
        return self.sections.get(name, [])

    def set(self, name: str, values: list) -> None:
        from live2d_builder.exporter import moc3_sections as ms
        ms.get_section(name)  # 未知名称立即报错
        self.sections[name] = list(values)

    def to_bytes(self) -> bytes:
        from live2d_builder.exporter import moc3_sections as ms

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
            values = self.sections.get(entry.name, [])
            _write_section(body, entry, values, self.counts)

        out = _Writer()
        out.write_bytes(_header_bytes(self.version))
        while len(sot) < ms.SOT_COUNT:
            sot.append(0)
        _write_u32_array(out, sot[: ms.SOT_COUNT])
        out.fill(ms.DEFAULT_OFFSET - out.pos)
        assert out.pos == ms.DEFAULT_OFFSET, "SOT 布局与 DEFAULT_OFFSET 不符"
        out.write_bytes(body.get_bytes())
        out.pad_to(ms.ALIGN)
        return out.get_bytes()


def _header_bytes(version: int) -> bytes:
    from live2d_builder.exporter import moc3_sections as ms
    w = _Writer()
    w.write_bytes(ms.MAGIC)
    w.write_u1(version)
    w.write_u1(0)  # 端序标志：0 = 小端
    w.fill(ms.HEADER_SIZE - w.pos)
    return w.get_bytes()


def _write_u32_array(w: _Writer, values: List[int]) -> None:
    import struct
    w.write_bytes(struct.pack(f"<{len(values)}I", *values))


def _write_section(w: _Writer, entry, values: list, counts: List[int]) -> None:
    """按元素类型写出一个 section。长度不足时按 counts 期望补零。"""
    import struct

    et = entry.elem_type
    if et == "i32":
        expect = counts[entry.count_idx] if entry.count_idx >= 0 else len(values)
        padded = list(values) + [0] * max(0, expect - len(values))
        w.write_bytes(struct.pack(f"<{len(padded)}i", *padded))
    elif et == "f32":
        expect = counts[entry.count_idx] if entry.count_idx >= 0 else len(values)
        padded = list(values) + [0.0] * max(0, expect - len(values))
        w.write_bytes(struct.pack(f"<{len(padded)}f", *padded))
    elif et == "i16":
        expect = counts[entry.count_idx] if entry.count_idx >= 0 else len(values)
        padded = list(values) + [0] * max(0, expect - len(values))
        w.write_bytes(struct.pack(f"<{len(padded)}h", *padded))
    elif et in ("bool", "u8"):
        expect = counts[entry.count_idx] if entry.count_idx >= 0 else len(values)
        padded = list(values) + [0] * max(0, expect - len(values))
        w.write_bytes(bytes(v & 0xFF for v in padded))
    elif et == "str64":
        for value in values:
            raw = value.encode("utf-8")[:63]
            w.write_bytes(raw + bytes(64 - len(raw)))
    elif et == "runtime":
        count = counts[entry.count_idx] if entry.count_idx >= 0 else 0
        w.write_bytes(bytes(count * _runtime_unit_size()))
    else:
        raise ValueError(f"未知元素类型: {et}")


def _runtime_unit_size() -> int:
    from live2d_builder.exporter import moc3_sections as ms
    return ms.RUNTIME_UNIT_SIZE
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/unit/test_moc3_container.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add live2d_builder/exporter/moc3_container.py tests/unit/test_moc3_container.py
git commit -m "feat(moc3): add container writer (header + SOT + aligned body)"
```

---

## Task 3: 最小模型构建器（含完整绑定链）

**Files:**
- Create: `live2d_builder/exporter/moc3_builder.py`
- Test: `tests/unit/test_moc3_builder.py`

**背景**：设计文档约束 C3–C6 指出，必须产出完整绑定链，否则官方 Core 报 `Data section is invalid`。本任务构建「1 部件 + 1 网格（四边形）+ 1 参数」且**内在自洽**的最小模型。

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_moc3_builder.py`：

```python
"""最小模型构建：计数一致 + 绑定链完整 + 参考读回往返。"""
import struct

import pytest

from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_builder import (
    MinimalModelSpec,
    build_minimal_model,
)


def _spec() -> MinimalModelSpec:
    return MinimalModelSpec(
        part_id="Part1",
        art_mesh_id="ArtMesh1",
        parameter_id="ParamAngleX",
        width=512.0,
        height=512.0,
    )


def test_counts_are_set_for_declared_parts():
    doc = build_minimal_model(_spec())
    assert doc.counts[ms.CountIdx.PARTS] == 1
    assert doc.counts[ms.CountIdx.ART_MESHES] == 1
    assert doc.counts[ms.CountIdx.PARAMETERS] == 1
    assert doc.counts[ms.CountIdx.PART_KEYFORMS] == 1
    assert doc.counts[ms.CountIdx.ART_MESH_KEYFORMS] == 1
    assert doc.counts[ms.CountIdx.UVS] == 8


def test_uv_count_is_floats_not_vertices():
    doc = build_minimal_model(_spec())
    assert len(doc.get("uv.xys")) == 8
    assert doc.counts[ms.CountIdx.UVS] == 8


def test_binding_chain_is_complete():
    doc = build_minimal_model(_spec())
    assert doc.counts[ms.CountIdx.KEYFORM_BINDINGS] == 1
    assert doc.counts[ms.CountIdx.KEYFORM_BINDING_BANDS] == 1
    assert doc.counts[ms.CountIdx.KEYFORM_BINDING_INDICES] == 1
    assert doc.get("keys.values"), "参数必须有可插值的键值"
    assert doc.get("draw_order_group.object_counts"), "必须有绘制顺序组"


def test_keyform_positions_cover_every_vertex():
    doc = build_minimal_model(_spec())
    positions = doc.get("keyform_position.xys")
    assert len(positions) % 2 == 0
    assert len(positions) == doc.counts[ms.CountIdx.KEYFORM_POSITIONS]


def test_roundtrip_through_reference_reader(tmp_path):
    from moc3 import Moc3
    doc = build_minimal_model(_spec())
    p = tmp_path / "model.moc3"
    p.write_bytes(doc.to_bytes())
    back = Moc3.from_file(str(p))
    assert back["part.ids"] == ["Part1"]
    assert back["art_mesh.ids"] == ["ArtMesh1"]
    assert back["parameter.ids"] == ["ParamAngleX"]
    assert back["uv.xys"] == doc.get("uv.xys")
    assert back["position_index.indices"] == doc.get("position_index.indices")


def test_reserialize_is_byte_identical(tmp_path):
    from moc3 import Moc3
    doc = build_minimal_model(_spec())
    first = doc.to_bytes()
    p = tmp_path / "m.moc3"
    p.write_bytes(first)
    assert Moc3.from_file(str(p)).to_bytes() == first
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_moc3_builder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'live2d_builder.exporter.moc3_builder'`

- [ ] **Step 3: 实现构建器**

创建 `live2d_builder/exporter/moc3_builder.py`：

```python
"""绑定数据 -> moc3 section 图。纯函数，无 IO。

最小模型的自洽性要求（设计文档 C3-C6）：
  * 每个参数有完整绑定链：keyform_binding_* -> band -> index -> keys
  * 每个部件有 part_keyform.draw_orders
  * 每个网格有 art_mesh_keyform.*
  * keyform_position.xys 覆盖所有关键形顶点
  * draw_order_group* 覆盖全部 art mesh
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_container import CanvasInfo, Moc3Container

# 单位四边形（局部坐标，逆时针）
_QUAD: Tuple[Tuple[float, float], ...] = (
    (0.0, 0.0),
    (1.0, 0.0),
    (1.0, 1.0),
    (0.0, 1.0),
)
_QUAD_UV: List[float] = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
_QUAD_INDICES: List[int] = [0, 1, 2, 0, 2, 3]

_PARAM_MIN = -30.0
_PARAM_MAX = 30.0
_PARAM_KEYS = 3  # 最小：min / default / max 三个键


@dataclass(frozen=True)
class MinimalModelSpec:
    """最小模型参数。"""
    part_id: str = "Part1"
    art_mesh_id: str = "ArtMesh1"
    parameter_id: str = "ParamAngleX"
    width: float = 512.0
    height: float = 512.0


def build_minimal_model(spec: MinimalModelSpec) -> Moc3Container:
    """构建一个内在自洽的最小模型（1 部件 / 1 网格 / 1 参数）。"""
    doc = Moc3Container(version=ms.MocVersion.V3_03)
    doc.canvas = CanvasInfo(
        pixels_per_unit=1.0,
        origin_x=0.0,
        origin_y=0.0,
        canvas_width=spec.width,
        canvas_height=spec.height,
    )

    _set_counts(doc)

    _write_part(doc, spec)
    _write_art_mesh(doc, spec)
    _write_parameter(doc, spec)
    _write_keys_and_bindings(doc, spec)
    _write_keyforms(doc, spec)
    _write_draw_order(doc, spec)
    return doc


def _set_counts(doc: Moc3Container) -> None:
    c = doc.counts
    c[ms.CountIdx.PARTS] = 1
    c[ms.CountIdx.DEFORMERS] = 0
    c[ms.CountIdx.WARP_DEFORMERS] = 0
    c[ms.CountIdx.ROTATION_DEFORMERS] = 0
    c[ms.CountIdx.ART_MESHES] = 1
    c[ms.CountIdx.PARAMETERS] = 1
    c[ms.CountIdx.PART_KEYFORMS] = 1
    c[ms.CountIdx.ART_MESH_KEYFORMS] = 1
    c[ms.CountIdx.WARP_DEFORMER_KEYFORMS] = 0
    c[ms.CountIdx.ROTATION_DEFORMER_KEYFORMS] = 0
    c[ms.CountIdx.KEYFORM_POSITIONS] = len(_QUAD) * 2      # 浮点数个数
    c[ms.CountIdx.KEYFORM_BINDING_INDICES] = 1
    c[ms.CountIdx.KEYFORM_BINDING_BANDS] = 1
    c[ms.CountIdx.KEYFORM_BINDINGS] = 1
    c[ms.CountIdx.KEYS] = _PARAM_KEYS
    c[ms.CountIdx.UVS] = len(_QUAD_UV)                     # 浮点数个数
    c[ms.CountIdx.POSITION_INDICES] = len(_QUAD_INDICES)
    c[ms.CountIdx.DRAWABLE_MASKS] = 0
    c[ms.CountIdx.DRAW_ORDER_GROUPS] = 1
    c[ms.CountIdx.DRAW_ORDER_GROUP_OBJECTS] = 1
    c[ms.CountIdx.GLUES] = 0
    c[ms.CountIdx.GLUE_INFOS] = 0
    c[ms.CountIdx.GLUE_KEYFORMS] = 0


def _write_part(doc: Moc3Container, spec: MinimalModelSpec) -> None:
    doc.set("part.ids", [spec.part_id])
    doc.set("part.keyform_binding_band_indices", [-1])
    doc.set("part.keyform_begin_indices", [0])
    doc.set("part.keyform_counts", [1])
    doc.set("part.visibles", [1])
    doc.set("part.enables", [1])
    doc.set("part.parent_part_indices", [-1])


def _write_art_mesh(doc: Moc3Container, spec: MinimalModelSpec) -> None:
    doc.set("art_mesh.ids", [spec.art_mesh_id])
    doc.set("art_mesh.keyform_binding_band_indices", [-1])
    doc.set("art_mesh.keyform_begin_indices", [0])
    doc.set("art_mesh.keyform_counts", [1])
    doc.set("art_mesh.visibles", [1])
    doc.set("art_mesh.enables", [1])
    doc.set("art_mesh.parent_part_indices", [0])
    doc.set("art_mesh.parent_deformer_indices", [-1])
    doc.set("art_mesh.texture_indices", [0])
    doc.set("art_mesh.drawable_flags", [0])
    doc.set("art_mesh.position_index_counts", [len(_QUAD_INDICES)])
    doc.set("art_mesh.uv_begin_indices", [0])
    doc.set("art_mesh.position_index_begin_indices", [0])
    doc.set("art_mesh.vertex_counts", [len(_QUAD)])
    doc.set("art_mesh.mask_begin_indices", [0])
    doc.set("art_mesh.mask_counts", [0])

    doc.set("uv.xys", list(_QUAD_UV))
    doc.set("position_index.indices", list(_QUAD_INDICES))


def _write_parameter(doc: Moc3Container, spec: MinimalModelSpec) -> None:
    doc.set("parameter.ids", [spec.parameter_id])
    doc.set("parameter.min_values", [_PARAM_MIN])
    doc.set("parameter.max_values", [_PARAM_MAX])
    doc.set("parameter.default_values", [0.0])
    doc.set("parameter.repeats", [0])
    doc.set("parameter.decimal_places", [2])
    doc.set("parameter.keyform_binding_begin_indices", [0])
    doc.set("parameter.keyform_binding_counts", [1])


def _write_keys_and_bindings(doc: Moc3Container, spec: MinimalModelSpec) -> None:
    # keys.values: 参数在 min / default / max 三处的取值
    doc.set("keys.values", [_PARAM_MIN, 0.0, _PARAM_MAX])

    # keyform_binding_index.indices: 每个参数指向自己的 binding
    doc.set("keyform_binding_index.indices", [0])

    # keyform_binding_band: 每个 band 的键起始与个数
    doc.set("keyform_binding_band.begin_indices", [0])
    doc.set("keyform_binding_band.counts", [_PARAM_KEYS])

    # keyform_binding: 每个 binding 的键起始与个数
    doc.set("keyform_binding.keys_begin_indices", [0])
    doc.set("keyform_binding.keys_counts", [_PARAM_KEYS])


def _write_keyforms(doc: Moc3Container, spec: MinimalModelSpec) -> None:
    # 部件关键形：绘制顺序
    doc.set("part_keyform.draw_orders", [0.0])

    # 网格关键形：不透明度 / 绘制顺序 / 顶点位置起始
    doc.set("art_mesh_keyform.opacities", [1.0])
    doc.set("art_mesh_keyform.draw_orders", [0.0])
    doc.set("art_mesh_keyform.keyform_position_begin_indices", [0])

    # 关键形顶点位置：单位四边形
    flat: List[float] = []
    for x, y in _QUAD:
        flat.extend((x, y))
    doc.set("keyform_position.xys", flat)


def _write_draw_order(doc: Moc3Container, spec: MinimalModelSpec) -> None:
    doc.set("draw_order_group.object_begin_indices", [0])
    doc.set("draw_order_group.object_counts", [1])
    doc.set("draw_order_group.object_total_counts", [1])
    doc.set("draw_order_group.min_draw_orders", [0])
    doc.set("draw_order_group.max_draw_orders", [0])

    # 对象类型：art mesh 用类型 0 表示（与参考布局一致）
    doc.set("draw_order_group_object.types", [0])
    doc.set("draw_order_group_object.indices", [0])
    doc.set("draw_order_group_object.group_indices", [0])
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/unit/test_moc3_builder.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add live2d_builder/exporter/moc3_builder.py tests/unit/test_moc3_builder.py
git commit -m "feat(moc3): build minimal self-consistent model with full binding chain"
```

---

## Task 4: 自洽性静态校验器

**Files:**
- Create: `live2d_builder/exporter/moc3_lint.py`
- Test: `tests/unit/test_moc3_lint.py`

**目的**：在把数据交给会崩溃的 Cubism Core **之前**拦截非法结构。

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_moc3_lint.py`：

```python
"""自洽性校验：长度不符、索引越界、悬空引用必须被拦截。"""
from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_builder import MinimalModelSpec, build_minimal_model
from live2d_builder.exporter.moc3_lint import lint_document


def _good():
    return build_minimal_model(MinimalModelSpec())


def test_clean_model_has_no_issues():
    assert lint_document(_good()) == []


def test_section_length_mismatch_is_reported():
    doc = _good()
    doc.set("part.ids", ["A", "B"])  # counts.PARTS 仍为 1
    issues = lint_document(doc)
    assert any("part.ids" in i.section for i in issues)


def test_out_of_range_begin_index_is_reported():
    doc = _good()
    doc.set("art_mesh.position_index_begin_indices", [999])
    issues = lint_document(doc)
    assert any("position_index_begin_indices" in i.section for i in issues)


def test_dangling_parent_part_index_is_reported():
    doc = _good()
    doc.set("art_mesh.parent_part_indices", [7])  # 只有 1 个部件
    issues = lint_document(doc)
    assert any("parent_part_indices" in i.section for i in issues)


def test_empty_binding_chain_is_reported():
    doc = _good()
    doc.set("keys.values", [])
    issues = lint_document(doc)
    assert any("keys.values" in i.section for i in issues)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_moc3_lint.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'live2d_builder.exporter.moc3_lint'`

- [ ] **Step 3: 实现校验器**

创建 `live2d_builder/exporter/moc3_lint.py`：

```python
"""moc3 section 图的自洽性静态校验（纯 Python，无 IO）。

拦截三类问题：
  1. section 长度与 count info 期望不符
  2. begin_index / count 组合越界
  3. 引用型索引悬空
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_container import Moc3Container


@dataclass(frozen=True)
class LintIssue:
    """一条自洽性问题。"""
    section: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - 便于阅读
        return f"[{self.section}] {self.message}"


# (目标 section, begin 字段, count 字段)
_RANGE_RULES = (
    ("uv.xys", "art_mesh.uv_begin_indices", "art_mesh.vertex_counts"),
    ("position_index.indices",
     "art_mesh.position_index_begin_indices",
     "art_mesh.position_index_counts"),
    ("keyform_position.xys",
     "art_mesh_keyform.keyform_position_begin_indices",
     None),
    ("keys.values",
     "keyform_binding.keys_begin_indices",
     "keyform_binding.keys_counts"),
)

# (被引用数组, 持索引的 section)
_REF_RULES = (
    ("part.ids", "art_mesh.parent_part_indices"),
    ("art_mesh.ids", "draw_order_group_object.indices"),
    ("parameter.ids", "keyform_binding_index.indices"),
)


def lint_document(doc: Moc3Container) -> List[LintIssue]:
    """返回全部自洽性问题；空列表表示通过。"""
    issues: List[LintIssue] = []
    issues.extend(_check_lengths(doc))
    issues.extend(_check_ranges(doc))
    issues.extend(_check_references(doc))
    issues.extend(_check_binding_chain(doc))
    return issues


def _check_lengths(doc: Moc3Container) -> List[LintIssue]:
    """每个 section 的实际长度必须等于 count info 的期望值。

    runtime 类 section 由运行期填充，不参与校验。
    """
    issues: List[LintIssue] = []
    for entry in ms.build_layout(doc.version):
        if entry.count_idx < 0 or entry.elem_type == "runtime":
            continue
        expected = doc.counts[entry.count_idx]
        actual = len(doc.get(entry.name))
        if actual != expected:
            issues.append(LintIssue(
                entry.name,
                f"长度 {actual} 与 count info 期望 {expected} 不符",
            ))
    return issues


def _check_ranges(doc: Moc3Container) -> List[LintIssue]:
    issues: List[LintIssue] = []
    for target, begin_field, count_field in _RANGE_RULES:
        begins = doc.get(begin_field)
        if not begins:
            continue
        counts = doc.get(count_field) if count_field else None
        limit = len(doc.get(target))
        for i, begin in enumerate(begins):
            span = counts[i] if counts and i < len(counts) else 0
            if begin < 0 or begin > limit or begin + span > limit:
                issues.append(LintIssue(
                    begin_field,
                    f"第 {i} 项 begin={begin} span={span} 越界（目标长度 {limit}）",
                ))
    return issues


def _check_references(doc: Moc3Container) -> List[LintIssue]:
    issues: List[LintIssue] = []
    for ref_name, holder in _REF_RULES:
        values = doc.get(holder)
        if not values:
            continue
        limit = len(doc.get(ref_name))
        for i, value in enumerate(values):
            if value >= limit:
                issues.append(LintIssue(
                    holder,
                    f"第 {i} 项引用 {value} 越界（{ref_name} 长度 {limit}）",
                ))
    return issues


def _check_binding_chain(doc: Moc3Container) -> List[LintIssue]:
    issues: List[LintIssue] = []
    if doc.counts[ms.CountIdx.PARAMETERS] > 0:
        if not doc.get("keys.values"):
            issues.append(LintIssue(
                "keys.values", "存在参数但没有任何键值，绑定链不完整"))
        if not doc.get("keyform_binding.keys_counts"):
            issues.append(LintIssue(
                "keyform_binding.keys_counts", "存在参数但缺少绑定键计数"))
        if not doc.get("parameter.keyform_binding_counts"):
            issues.append(LintIssue(
                "parameter.keyform_binding_counts", "参数未声明绑定数量"))
    if doc.counts[ms.CountIdx.ART_MESHES] > 0:
        if not doc.get("art_mesh_keyform.opacities"):
            issues.append(LintIssue(
                "art_mesh_keyform.opacities", "存在网格但缺少网格关键形"))
    if doc.counts[ms.CountIdx.PARTS] > 0:
        if not doc.get("part_keyform.draw_orders"):
            issues.append(LintIssue(
                "part_keyform.draw_orders", "存在部件但缺少部件关键形"))
    return issues
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/unit/test_moc3_lint.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add live2d_builder/exporter/moc3_lint.py tests/unit/test_moc3_lint.py
git commit -m "feat(moc3): add self-consistency linter to block invalid documents"
```

---

## Task 5: 子进程隔离的官方一致性验证

**Files:**
- Create: `drivers/live2d_runtime/moc3_consistency.py`
- Create: `drivers/live2d_runtime/moc3_verify.py`
- Test: `tests/unit/test_moc3_verify.py`

**硬性约束**：Cubism Core 崩溃会拖垮进程，**必须**子进程隔离 + 超时。

- [ ] **Step 1: 写失败测试（用替身模拟崩溃/超时）**

创建 `tests/unit/test_moc3_verify.py`：

```python
"""调用方契约：崩溃与超时必须被归类为「验证失败」，绝不抛出。"""
import subprocess

from drivers.live2d_runtime import moc3_verify
from drivers.live2d_runtime.moc3_verify import verify_moc3_consistency


def test_missing_file_is_reported_not_raised(tmp_path):
    result = verify_moc3_consistency(str(tmp_path / "absent.moc3"))
    assert result["ok"] is False
    assert "不存在" in result["blocker"]


def test_clean_exit_zero_is_success(tmp_path, monkeypatch):
    target = tmp_path / "a.moc3"
    target.write_bytes(b"MOC3" + bytes(2044))

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, '{"ok": true}', "")

    monkeypatch.setattr(moc3_verify.subprocess, "run", fake_run)
    result = verify_moc3_consistency(str(target))
    assert result["ok"] is True
    assert result["blocker"] is None


def test_nonzero_exit_is_failure(tmp_path, monkeypatch):
    target = tmp_path / "a.moc3"
    target.write_bytes(b"MOC3" + bytes(2044))

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 1, '{"ok": false}', "")

    monkeypatch.setattr(moc3_verify.subprocess, "run", fake_run)
    result = verify_moc3_consistency(str(target))
    assert result["ok"] is False
    assert "不一致" in result["blocker"]


def test_crash_exit_is_failure(tmp_path, monkeypatch):
    target = tmp_path / "a.moc3"
    target.write_bytes(b"MOC3" + bytes(2044))

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 3221225477, "", "")

    monkeypatch.setattr(moc3_verify.subprocess, "run", fake_run)
    result = verify_moc3_consistency(str(target))
    assert result["ok"] is False
    assert result["crashed"] is True
    assert "crashed" in result["blocker"]


def test_timeout_is_failure(tmp_path, monkeypatch):
    target = tmp_path / "a.moc3"
    target.write_bytes(b"MOC3" + bytes(2044))

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=1)

    monkeypatch.setattr(moc3_verify.subprocess, "run", fake_run)
    result = verify_moc3_consistency(str(target), timeout=1)
    assert result["ok"] is False
    assert result["timed_out"] is True
    assert "timed out" in result["blocker"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_moc3_verify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'drivers.live2d_runtime.moc3_verify'`

- [ ] **Step 3: 实现子进程入口**

创建 `drivers/live2d_runtime/moc3_consistency.py`：

```python
"""子进程入口：只做官方 Cubism Core 一致性检查，不启 OpenGL。

独立进程运行，因为 Cubism Core 在异常路径下会段错误（0xC0000005）。
用法: python -m drivers.live2d_runtime.moc3_consistency <path/to/model.moc3>
退出码: 0 = 一致；1 = 不一致；2 = 用法/依赖错误
"""
from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"ok": False, "error": "用法: <moc3 路径>"}))
        return 2

    path = argv[1]
    try:
        import live2d.v3 as sdk
    except ImportError:
        print(json.dumps({"ok": False, "error": "未安装 live2d-py"}))
        return 2

    # 实测：不 init 直接调用会段错误
    sdk.init()
    try:
        model = sdk.LAppModel()
        ok = bool(model.HasMocConsistencyFromFile(path))
    finally:
        sdk.dispose()

    print(json.dumps({"ok": ok}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: 实现调用方**

创建 `drivers/live2d_runtime/moc3_verify.py`：

```python
"""调用官方一致性检查：subprocess 隔离 + 超时 + 崩溃归类。

契约：本函数**永不抛出**。任何异常路径都返回 ok=False 并给出 blocker 原因。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

_MODULE = "drivers.live2d_runtime.moc3_consistency"
_DEFAULT_TIMEOUT = 30


def verify_moc3_consistency(
    moc3_path: str,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """对 moc3 跑官方一致性检查。

    Returns:
        {ok, exit_code, stdout, stderr, timed_out, crashed, blocker}
    """
    result: Dict[str, Any] = {
        "ok": False,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "timed_out": False,
        "crashed": False,
        "blocker": None,
    }

    path = Path(moc3_path)
    if not path.is_file():
        result["blocker"] = f"文件不存在: {moc3_path}"
        return result

    cmd = [sys.executable, "-m", _MODULE, str(path)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
        result["blocker"] = f"consistency check timed out after {timeout}s"
        return result
    except OSError as exc:
        result["blocker"] = f"无法启动检查子进程: {exc}"
        return result

    result["exit_code"] = proc.returncode
    result["stdout"] = proc.stdout
    result["stderr"] = proc.stderr

    if proc.returncode == 0:
        result["ok"] = True
        return result

    if proc.returncode == 2:
        detail = _first_error(proc.stdout) or "live2d-py 不可用"
        result["blocker"] = f"检查环境不可用: {detail}"
        return result

    if proc.returncode == 1:
        result["blocker"] = "官方 Cubism Core 判定 moc3 不一致"
        return result

    # 其余退出码：视为崩溃（Windows 访问违规为 3221225477 / 0xC0000005）
    result["crashed"] = True
    result["blocker"] = f"cubism core crashed (code={proc.returncode})"
    return result


def _first_error(stdout: str) -> str:
    try:
        data = json.loads(stdout.strip().splitlines()[-1])
        return str(data.get("error", ""))
    except (ValueError, IndexError):
        return ""
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/unit/test_moc3_verify.py -v`
Expected: 5 passed

- [ ] **Step 6: 提交**

```bash
git add drivers/live2d_runtime/moc3_consistency.py drivers/live2d_runtime/moc3_verify.py tests/unit/test_moc3_verify.py
git commit -m "feat(moc3): add subprocess-isolated cubism consistency verification"
```

---

## Task 6: P0 端到端验收测试（官方内核）

**Files:**
- Create: `tests/integration/test_moc3_official_acceptance.py`

**说明**：本任务**先写测试再补实现**——如果测试失败，说明"最小模型结构仍有缺失"，需要在 `moc3_builder.py` 迭代补齐。这是本计划的核心验证点。

- [ ] **Step 1: 写验收测试**

创建 `tests/integration/test_moc3_official_acceptance.py`：

```python
"""P0 验收：官方 Cubism Core 必须接受我们生成的 moc3。

若这些测试失败，说明最小模型的自洽结构仍有缺失，
需要回到 live2d_builder/exporter/moc3_builder.py 迭代补齐。
"""
import importlib.util
from pathlib import Path

import pytest

from drivers.live2d_runtime.moc3_verify import verify_moc3_consistency
from live2d_builder.exporter.moc3_builder import MinimalModelSpec, build_minimal_model
from live2d_builder.exporter.moc3_lint import lint_document

_HARU = Path("Work/native-sample/Haru/Haru.moc3")


def _has_live2d() -> bool:
    """live2d-py 是否可用（决定官方内核类测试能否运行）。"""
    return importlib.util.find_spec("live2d.v3") is not None


requires_live2d = pytest.mark.skipif(
    not _has_live2d(), reason="需要 live2d-py 运行时（pip install live2d-py）"
)


def _write_minimal(tmp_path: Path) -> Path:
    doc = build_minimal_model(MinimalModelSpec())
    assert lint_document(doc) == [], "生成的模型必须通过自洽性校验"
    out = tmp_path / "minimal.moc3"
    out.write_bytes(doc.to_bytes())
    return out


@requires_live2d
def test_minimal_model_is_accepted_by_official_core(tmp_path):
    moc3 = _write_minimal(tmp_path)
    result = verify_moc3_consistency(str(moc3))
    assert result["ok"] is True, (
        f"官方内核拒绝了我们生成的 moc3: {result['blocker']}\n"
        f"stdout={result['stdout']}\nstderr={result['stderr']}"
    )


@requires_live2d
@pytest.mark.skipif(not _HARU.is_file(), reason="缺少官方参考模型")
def test_official_haru_regression_stays_consistent():
    """回归保护：检查器本身必须对官方模型返回一致。"""
    result = verify_moc3_consistency(str(_HARU))
    assert result["ok"] is True, (
        f"官方 Haru.moc3 应通过一致性检查，实际: {result['blocker']}"
    )


@requires_live2d
def test_tampered_model_is_rejected(tmp_path):
    """负例：篡改后必须判为不一致或崩溃，绝不能通过。"""
    moc3 = _write_minimal(tmp_path)
    raw = bytearray(moc3.read_bytes())
    # 破坏 Section Offset Table 中的若干偏移
    for offset in range(64, 64 + 40, 4):
        raw[offset] = 0xFF
    bad = tmp_path / "tampered.moc3"
    bad.write_bytes(bytes(raw))

    result = verify_moc3_consistency(str(bad))
    assert result["ok"] is False
```

- [ ] **Step 2: 运行测试**

Run: `python -m pytest tests/integration/test_moc3_official_acceptance.py -v`
Expected:
- `test_official_haru_regression_stays_consistent` → PASS
- `test_tampered_model_is_rejected` → PASS
- `test_minimal_model_is_accepted_by_official_core` → **可能 FAIL**

- [ ] **Step 3: 若最小模型被拒，按诊断迭代**

官方内核的报错只在子进程 stdout/stderr 里。用以下命令获取原始诊断：

Run: `python -m drivers.live2d_runtime.moc3_consistency <tmp 里的 minimal.moc3 路径>`
Expected: 打印 `{"ok": false}` 或崩溃；结合 `[CSM]` 日志定位是哪个 section 不自洽。

对照方法（结构 diff）：用 py-moc3 同时加载官方 Mark 与我们的模型，逐 section 比对长度与索引范围：

Run:
```bash
python -c "import os;from moc3 import Moc3,CountIdx;d=os.path.join(os.environ['TEMP'],'beacon_moc3_ref','Mark.moc3');m=Moc3.from_file(d);print([ (k,len(v)) for k,v in m._sections.items() if v ][:40])"
```
Expected: 打印官方各 section 的长度，用于与我们模型对照。

修复方向（按可能性排序）：
1. `draw_order_group_object.types` 取值是否符合官方约定（art mesh 的类型编码）
2. `keyform_binding_band.counts` / `keyform_binding.keys_counts` 是否应按「键数 - 1」计
3. `parameter.keyform_binding_counts` 与 `keyform_binding_index.indices` 的对应关系
4. `art_mesh_keyform.keyform_position_begin_indices` 是否以「位置数」而非「浮点数」为单位

每改一项，重跑 Step 2，直到 `test_minimal_model_is_accepted_by_official_core` 通过。

- [ ] **Step 4: 更新任务状态**

当三项测试全部通过后，把验收结果记录到 `Work/native-verification.json` 同级的 `Work/moc3-p0-verification.json`：

```json
{
  "scope": "moc3_p0_minimal_model",
  "official_core_consistent": true,
  "haru_regression": true,
  "tampered_rejected": true,
  "cubism_core_version": "05.01.0000"
}
```

- [ ] **Step 5: 提交**

```bash
git add tests/integration/test_moc3_official_acceptance.py live2d_builder/exporter/moc3_builder.py Work/moc3-p0-verification.json
git commit -m "test(moc3): add P0 official-core acceptance gate for minimal model"
```

---

## Task 7: 导出接口与文档收口

**Files:**
- Modify: `live2d_builder/exporter/__init__.py`
- Modify: `docs/native-runtime.md:28-34`
- Test: `tests/unit/test_moc3_sections.py`（追加一项导出可用性断言）

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_moc3_sections.py` 末尾追加：

```python
def test_exporter_package_exposes_moc3_api():
    from live2d_builder import exporter
    assert hasattr(exporter, "build_minimal_model")
    assert hasattr(exporter, "lint_document")
    assert hasattr(exporter, "verify_moc3_consistency")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_moc3_sections.py::test_exporter_package_exposes_moc3_api -v`
Expected: FAIL — `AttributeError: module 'live2d_builder.exporter' has no attribute 'build_minimal_model'`

- [ ] **Step 3: 扩导出**

把 `live2d_builder/exporter/__init__.py` 改为：

```python
#!/usr/bin/env python3
"""Model3.json export, texture atlas packing, Cubism packaging, and moc3 compile."""

from live2d_builder.exporter.model3_exporter import Model3Exporter
from live2d_builder.exporter.texture_atlas import TextureAtlas
from live2d_builder.exporter.moc3_builder import MinimalModelSpec, build_minimal_model
from live2d_builder.exporter.moc3_lint import LintIssue, lint_document
from drivers.live2d_runtime.moc3_verify import verify_moc3_consistency

__all__ = [
    "Model3Exporter",
    "TextureAtlas",
    "MinimalModelSpec",
    "build_minimal_model",
    "LintIssue",
    "lint_document",
    "verify_moc3_consistency",
]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/unit/test_moc3_sections.py -v`
Expected: 7 passed

- [ ] **Step 5: 更新文档**

把 `docs/native-runtime.md` 的「仍未完成」列表中第 4 条：

```
4. 从素材自动建立 Cubism 工程并导出真实 moc3。
```

替换为：

```
4. 从素材自动建立 Cubism 工程并导出真实 moc3。
   → P0 已完成最小闭环：`live2d_builder/exporter/moc3_builder.py` 生成 moc3，
     由 `drivers/live2d_runtime/moc3_verify.py` 在子进程内用官方 Cubism Core 校验。
     完整管线接入（把编译接到 build() 第 9 步之后）属于 P1。
```

并在文末「本轮测试」章节追加：

```
新增 moc3 相关测试：
- 单元：tests/unit/test_moc3_sections.py, test_moc3_container.py,
        test_moc3_builder.py, test_moc3_lint.py, test_moc3_verify.py
- 集成：tests/integration/test_moc3_official_acceptance.py
```

- [ ] **Step 6: 运行全量单元 + 集成测试**

Run: `python -m pytest tests/unit/ tests/integration/ -q --no-header`
Expected: 全部通过（既有 110 项 + 新增 28 项单元 + 3 项集成）

- [ ] **Step 7: 提交**

```bash
git add live2d_builder/exporter/__init__.py docs/native-runtime.md tests/unit/test_moc3_sections.py
git commit -m "feat(moc3): expose compile/lint/verify API and document P0 closure"
```

---

## Self-Review 记录

**1. Spec 覆盖检查**

| Spec 要求 | 对应任务 |
|---|---|
| 6.1 `moc3_sections.py` 数据定义 | Task 1 |
| 6.1 `moc3_writer.py` 编译器 | Task 2 + Task 3（拆成容器层与模型层，职责更清晰） |
| 6.2 `moc3_lint.py` 自洽校验 | Task 4 |
| 6.3 隔离沙箱入口 + 调用方 | Task 5 |
| 6.5 `runtime_ready` 判定规则 | Task 6 Step 4（P0 只落证据文件；API 字段属 P1） |
| 8.1 单元测试 | Task 1/2/3/4/5 |
| 8.2 集成测试（含 Haru 回归 + 负例） | Task 6 |
| 8.3 P0 验收门槛 1–5 | Task 6 Step 1–4 |
| 9 P0 交付清单 | 全覆盖；「接到 pipeline 第 9 步之后」明确划入 P1 |
| 10 风险：边界校验 | Task 4 |
| 10 风险：进程隔离 | Task 5 |

**2. 占位符扫描**：已检查，无 TBD / TODO / "类似 Task N" / "适当处理错误"。Task 6 Step 3 是**明确的迭代收敛流程**，附具体命令、对照数据与候选修复方向，不是占位符。

**3. 类型一致性检查**

- `Moc3Container`：`to_bytes()` / `get(name)` / `set(name, values)` / `counts` / `canvas` / `version` —— Task 2 定义，Task 3/4 使用一致。
- `CanvasInfo`：字段 `pixels_per_unit / origin_x / origin_y / canvas_width / canvas_height / canvas_flag` —— Task 2 定义，Task 3 使用一致。
- `MinimalModelSpec`：字段 `part_id / art_mesh_id / parameter_id / width / height` —— Task 3 定义，Task 6 使用一致。
- `lint_document(doc) -> List[LintIssue]`，`LintIssue.section` —— Task 4 定义，Task 6 使用一致。
- `verify_moc3_consistency(path, timeout=30) -> dict`，键 `ok / exit_code / stdout / stderr / timed_out / crashed / blocker` —— Task 5 定义，Task 6 使用一致。
- `ms.CountIdx` / `ms.build_layout(version)` / `ms.get_section(name)` / `ms.MocVersion.V3_03` —— Task 1 定义，后续一致。
- `exporter` 导出名 `build_minimal_model / lint_document / verify_moc3_consistency / MinimalModelSpec / LintIssue` —— Task 7 与 Task 3/4/5 定义一致。

---

## 执行说明

- 本计划只做 P0。**P0 的 Go/No-Go 判据是 Task 6 的三项测试全绿**；若最小模型始终被官方内核拒绝且无法定位，应回到用户处重新评估路线（对应设计文档 §8.3 的回退条件）。
- 所有 Cubism Core 调用**只能**通过 `verify_moc3_consistency()` 间接发生；**禁止**在任何测试或生产代码里直接 `import live2d.v3` 后 in-process 调用。
- `Work/` 目录建议加入 `.gitignore`（设计文档 §11 第 1 条）。
