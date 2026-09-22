#!/usr/bin/env python3
"""不变量体检：先要求在官方 Haru 上 100% 成立，再拿去跑我们被拒绝的文档。

哪条在 Haru 上成立、在我们的文档上失败，哪条就是内核真正要求、而我们没满足的约束。
运行：.venv/Scripts/python.exe tools/deformer_invariant_battery.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from moc3 import _core  # noqa: E402
from live2d_builder.exporter.moc3_model import RigSpec  # noqa: E402

HARU = ROOT / "Work" / "native-sample" / "Haru" / "Haru.moc3"


def align16(n: int) -> int:
    return -(-n // 16) * 16


def invariants(doc):
    """返回 (名字, 是否成立, 说明) 列表。任何取不到的数据都记为不成立。"""
    CI = _core.CountIdx
    out = []
    n_def = doc.counts[CI.DEFORMERS]
    n_warp = doc.counts[CI.WARP_DEFORMERS]
    n_rot = doc.counts[CI.ROTATION_DEFORMERS]
    rows = doc.get("warp_deformer.rows")
    cols = doc.get("warp_deformer.cols")
    verts = doc.get("warp_deformer.vertex_counts")
    w_kb = doc.get("warp_deformer.keyform_begin_indices")
    w_kc = doc.get("warp_deformer.keyform_counts")
    w_pos = doc.get("warp_deformer_keyform.keyform_position_begin_indices")
    types = doc.get("deformer.types")
    specific = doc.get("deformer.specific_indices")
    band = doc.get("deformer.keyform_binding_band_indices")
    bands_begin = doc.get("keyform_binding_band.begin_indices")
    bands_count = doc.get("keyform_binding_band.counts")
    bind_index = doc.get("keyform_binding_index.indices")
    keys_counts = doc.get("keyform_binding.keys_counts")
    mesh_parent = doc.get("art_mesh.parent_deformer_indices")
    mesh_part = doc.get("art_mesh.parent_part_indices")
    def_part = doc.get("deformer.parent_part_indices")
    total_keyforms_w = doc.counts[CI.WARP_DEFORMER_KEYFORMS]

    def add(name, ok, note=""):
        out.append((name, bool(ok), note))

    add("变形器总数 = warp + rotation",
        n_def == n_warp + n_rot, f"{n_def} vs {n_warp}+{n_rot}")

    # warp 索引按类型出现顺序必须稠密递增
    warp_seq = [specific[i] for i in range(n_def) if types[i] == 0]
    rot_seq = [specific[i] for i in range(n_def) if types[i] == 1]
    add("warp.specific_indices 稠密递增",
        warp_seq == list(range(len(warp_seq))), str(warp_seq[:6]))
    add("rotation.specific_indices 稠密递增",
        rot_seq == list(range(len(rot_seq))), str(rot_seq[:6]))

    add("vertex_counts == (rows+1)x(cols+1)",
        all(verts[i] == (rows[i] + 1) * (cols[i] + 1) for i in range(n_warp)),
        f"rows={rows[:4]} cols={cols[:4]} verts={verts[:4]}")

    add("warp keyform_begin 铺平",
        sum(w_kc) == total_keyforms_w and
        all(w_kb[i] == sum(w_kc[:i]) for i in range(n_warp)),
        f"sum={sum(w_kc)} count={total_keyforms_w}")

    spans_ok = True
    for i in range(n_warp):
        for k in range(w_kc[i]):
            idx = w_kb[i] + k
            if idx + 1 >= len(w_pos):
                continue
            if w_pos[idx + 1] - w_pos[idx] != align16(2 * verts[i]):
                spans_ok = False
    add("相邻 warp 控制网格起点间距 == align16(2*vertex_counts)",
        spans_ok)

    add("warp 控制网格起点均 16 对齐",
        all(v % 16 == 0 for v in w_pos),
        f"非对齐数 {sum(1 for v in w_pos if v % 16)}/{len(w_pos)}")

    # 乘积规则：变形器 keyform 数 == 其带内各 binding 键数之积
    prod_ok = True
    for i in range(n_def):
        b = band[i]
        if b >= len(bands_count):
            prod_ok = False
            continue
        product = 1
        for bi in bind_index[bands_begin[b]:bands_begin[b] + bands_count[b]]:
            product *= keys_counts[bi]
        kind = 0 if types[i] == 0 else 1
        counts = w_kc if kind == 0 else doc.get("rotation_deformer.keyform_counts")
        begins = w_kb if kind == 0 else doc.get("rotation_deformer.keyform_begin_indices")
        if product != counts[specific[i]]:
            prod_ok = False
    add("变形器乘积规则（keyform 数 == 带内键数之积）", prod_ok)

    add("挂在变形器下的网格，其 part == 变形器的 parent_part",
        all(mesh_part[m] == def_part[d]
            for m, d in enumerate(mesh_parent) if d >= 0),
        f"违反数 {sum(1 for m, d in enumerate(mesh_parent) if d >= 0 and mesh_part[m] != def_part[d])}"
        f"/{sum(1 for d in mesh_parent if d >= 0)}")

    add("parent_deformer 索引在范围内或 -1",
        all(d == -1 or 0 <= d < n_def for d in mesh_parent))
    add("deformer.parent_part 索引在范围内",
        all(0 <= v < doc.counts[CI.PARTS] for v in def_part))
    return out


def main() -> int:
    haru = _core.Moc3.from_file(str(HARU))
    print("== 官方 Haru（合法基准）==")
    baseline = {}
    for name, ok, note in invariants(haru):
        baseline[name] = ok
        print(f"  {'成立' if ok else '不成立'}  {name}  {note}")

    sys.path.insert(0, str(ROOT))
    from tools.mutate_deformer_doc import base_doc  # noqa: E402
    ours = base_doc()[0]
    print("\n== 我们的文档（被内核拒绝）==")
    diffs = []
    for name, ok, note in invariants(ours):
        mark = "成立" if ok else "不成立"
        print(f"  {mark}  {name}  {note}")
        if baseline.get(name) and not ok:
            diffs.append(name)
    print(f"\nHaru 成立但我们违反的约束：{diffs or '无（说明约束在别处）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
