#!/usr/bin/env python3
"""最小 write-back 实验：我们的 deformer 编码和参考实现差在哪里。

生产代码里 `RigSpec.validate()` 会拒绝变形器（内核一律判 invalid）。本脚本只在
**进程内**临时绕过这条拒绝来产出一个待检文档，不改动产品路径。

流程：
  A = 我们的容器写出的文档（预期被内核拒绝）
  B = 把 A 交给 py-moc3 读回后再写出的文档
  1) 对 A、B 分别跑官方一致性 —— B 通过而 A 不通过，就是我们的字节编码错
  2) 逐段比较 section 大小；再找第一个不同的字节偏移
运行：.venv/Scripts/python.exe tools/writeback_deformer_diff.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drivers.live2d_runtime.moc3_verify import verify_moc3_consistency  # noqa: E402
from live2d_builder.exporter import moc3_sections as ms  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    DeformerGrid, MeshSpec, ParameterSpec, RigSpec, WarpDeformerSpec,
    compile_static_rig,
)

OUT = ROOT / "Work" / "writeback"
CANVAS, PPU = 512.0, 100.0
HALF = 100.0


def build_spec() -> RigSpec:
    verts = [(-HALF, -HALF), (HALF, -HALF), (HALF, HALF), (-HALF, HALF)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    mesh = MeshSpec("ArtMesh1", verts, [(0, 1, 2), (0, 2, 3)], uvs,
                    deformer_id="Warp1")
    step = 2 * HALF / 2
    points = [(-HALF + col * step, -HALF + row * step)
              for row in range(3) for col in range(3)]
    deformer = WarpDeformerSpec(
        deformer_id="Warp1", rows=2, cols=2,
        grids=[DeformerGrid(0.0, points)],
        parent_part_id=mesh.effective_part_id)
    return RigSpec(
        meshes=[mesh],
        parameters=[ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0)],
        canvas_width=CANVAS, canvas_height=CANVAS, pixels_per_unit=PPU,
        deformers=[deformer], deformer_count=1)


def section_sizes(raw: bytes):
    layout = ms.build_layout(ms.MocVersion.V3_03)
    offs = [struct.unpack_from("<I", raw, ms.HEADER_SIZE + 4 * i)[0]
            for i in range(len(layout))]
    return {entry.name: (offs[i],
                         (offs[i + 1] if i + 1 < len(offs) else len(raw)) - offs[i])
            for i, entry in enumerate(layout)}


def main() -> int:
    from moc3 import _core
    OUT.mkdir(parents=True, exist_ok=True)

    spec = build_spec()
    keep = RigSpec.validate
    # 只在诊断进程内跳过「变形器未打通」这条拒绝，产品路径不变
    RigSpec.validate = lambda self: None if self is spec else keep(self)
    try:
        doc = compile_static_rig(spec)
    finally:
        RigSpec.validate = keep
    a = doc.to_bytes()
    path_a = OUT / "ours.moc3"
    path_a.write_bytes(a)

    back = _core.Moc3.from_bytes(a)
    b = back.to_bytes()
    path_b = OUT / "reserialized.moc3"
    path_b.write_bytes(b)

    print(f"A(我们) {len(a)} 字节  B(py-moc3 重写) {len(b)} 字节  "
          f"相同={a == b}")
    result_a = verify_moc3_consistency(str(path_a))
    result_b = verify_moc3_consistency(str(path_b))
    print(f"官方一致性:  A ok={result_a['ok']} ({result_a['blocker']})   "
          f"B ok={result_b['ok']} ({result_b['blocker']})")

    if a != b:
        first = next(i for i in range(min(len(a), len(b))) if a[i] != b[i])
        print(f"\n第一个不同的字节偏移: {first}")
        print(f"  A[{first}:{first+16}] = {a[first:first+16].hex()}")
        print(f"  B[{first}:{first+16}] = {b[first:first+16].hex()}")

    sizes_a, sizes_b = section_sizes(a), section_sizes(b)
    print("\n== 变形器相关 section 的大小差异（偏移, 字节数）==")
    for name in sizes_a:
        if not name.startswith(("deformer.", "warp_deformer", "part.",
                                "art_mesh.", "parameter.")):
            continue
        if sizes_a[name] != sizes_b[name]:
            print(f"  {name:48s} A={sizes_a[name]}  B={sizes_b[name]}")
    print("\n== A 里 deformer 段的实际布局 ==")
    for name in sorted(sizes_a):
        if name.startswith(("deformer.", "warp_deformer")):
            off, size = sizes_a[name]
            print(f"  {name:52s} off={off:6d} size={size:5d} 内容={a[off:off+min(size,24)].hex()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
