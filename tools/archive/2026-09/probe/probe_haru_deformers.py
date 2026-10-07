#!/usr/bin/env python3
"""探针：读出官方 Haru.moc3 里变形器（warp / rotation）的字段语义。

只读分析，用于设计变形器编译；不改任何产物。
运行：.venv/Scripts/python.exe tools/probe_haru_deformers.py
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

HARU = Path(__file__).resolve().parents[1] / "Work" / "native-sample" / "Haru" / "Haru.moc3"


def main() -> int:
    from moc3 import _core

    doc = _core.Moc3.from_file(HARU)
    c = doc.counts
    CI = _core.CountIdx
    g = doc.get

    n_def = c[CI.DEFORMERS]
    n_warp = c[CI.WARP_DEFORMERS]
    n_rot = c[CI.ROTATION_DEFORMERS]
    print(f"deformers={n_def} warp={n_warp} rotation={n_rot} "
          f"warp_keyforms={c[CI.WARP_DEFORMER_KEYFORMS]} "
          f"rot_keyforms={c[CI.ROTATION_DEFORMER_KEYFORMS]}")

    types = g("deformer.types")
    specific = g("deformer.specific_indices")
    parent_part = g("deformer.parent_part_indices")
    parent_def = g("deformer.parent_deformer_indices")
    bands = g("deformer.keyform_binding_band_indices")
    ids = doc.deformer_ids
    print(f"\ntypes 取值分布: {dict(Counter(types))}")
    print(f"前 12 个 deformer: ")
    for i in range(min(12, n_def)):
        print(f"  [{i}] {ids[i]:24s} type={types[i]} specific={specific[i]} "
              f"parent_part={parent_part[i]} parent_def={parent_def[i]} band={bands[i]}")

    # type -> specific 数组的对应关系
    warp_specific = [specific[i] for i in range(n_def) if types[i] == 0]
    rot_specific = [specific[i] for i in range(n_def) if types[i] != 0]
    print(f"\ntype==0 的数量 {len(warp_specific)}（warp={n_warp}），"
          f"specific 序列前 8: {warp_specific[:8]}")
    print(f"type!=0 的数量 {len(rot_specific)}（rot={n_rot}），"
          f"specific 序列前 8: {rot_specific[:8]}")
    print(f"warp specific 是否为 0..n-1 递增: "
          f"{warp_specific == list(range(len(warp_specific)))}")
    print(f"rotation specific 是否为 0..n-1 递增: "
          f"{rot_specific == list(range(len(rot_specific)))}")
    print(f"types 非零值集合: {sorted({t for t in types if t != 0})}")

    rows = g("warp_deformer.rows")
    cols = g("warp_deformer.cols")
    vcounts = g("warp_deformer.vertex_counts")
    wkb = g("warp_deformer.keyform_begin_indices")
    wkc = g("warp_deformer.keyform_counts")
    print("\n== warp_deformer 网格 ==")
    print(f"vertex_counts == rows*cols 的个数: "
          f"{sum(1 for i in range(n_warp) if vcounts[i] == rows[i] * cols[i])} / {n_warp}")
    for i in range(min(8, n_warp)):
        print(f"  warp[{i}] rows={rows[i]} cols={cols[i]} verts={vcounts[i]} "
              f"keyforms={wkc[i]} kf_begin={wkb[i]} band={g('warp_deformer.keyform_binding_band_indices')[i]}")
    print(f"rows 分布 {dict(Counter(rows))}  cols 分布 {dict(Counter(cols))}")
    print(f"warp keyform 总数 {sum(wkc)} vs count {c[CI.WARP_DEFORMER_KEYFORMS]}")
    print(f"kf_begin 连续铺平: "
          f"{[wkb[0]] + [wkb[i] - (wkb[i-1] + wkc[i-1]) for i in range(1, n_warp)] == [wkb[0]] + [0]*(n_warp-1)}")

    # 关键形位置：与 art mesh 共用 keyform_position.xys 池
    pos = g("keyform_position.xys")
    wpos = g("warp_deformer_keyform.keyform_position_begin_indices")
    print(f"\nkeyform_position.xys 总浮点数 {len(pos)} (count {c[CI.KEYFORM_POSITIONS]})")
    print(f"warp_deformer_keyform 起点数 {len(wpos)}；是否为 16 的倍数："
          f"{all(v % 16 == 0 for v in wpos)}；非倍数个数 {sum(1 for v in wpos if v % 16)}")
    gaps = [wpos[i + 1] - wpos[i] for i in range(len(wpos) - 1)]
    print(f"相邻起点的跨度样例: {sorted(set(gaps))[:8]} ...")
    # 跨度应等于 2*rows*cols（按出现次数配对）
    expect = []
    for i in range(n_warp):
        for _ in range(wkc[i]):
            expect.append(2 * vcounts[i])
    print(f"span==2*vertex_counts 的比例: "
          f"{sum(1 for gp, ex in zip(gaps, expect) if gp == ex)} / {len(gaps)}"
          f"（若低于此值说明有对齐补白）")

    # art mesh -> deformer 引用
    a_parent_def = g("art_mesh.parent_deformer_indices")
    used = Counter(a_parent_def)
    print(f"\nart_mesh.parent_deformer_indices: -1 的个数 {used[-1]}，"
          f"引用真实变形器的网格数 {sum(v for k, v in used.items() if k >= 0)}")
    print(f"被引用的 deformer 索引数 {len({k for k in used if k >= 0})} / {n_def}")
    print(f"引用的索引是否都 < {n_warp}（即 warp 段前部）: "
          f"{all(k < n_warp for k in used if k >= 0)}")

    # 变形器引用的 part 与网格引用的 part 是否一致
    print(f"\ndeformer.parent_part_indices 取值样例: {parent_part[:12]}")
    print(f"rotation_deformer.base_angles 样例: "
          f"{[round(v, 3) for v in g('rotation_deformer.base_angles')[:8]]}")
    rk = g("rotation_deformer.keyform_counts")
    print(f"rotation keyform 总数 {sum(rk)} vs count {c[CI.ROTATION_DEFORMER_KEYFORMS]}")
    rang = g("rotation_deformer_keyform.angles")
    print(f"rotation keyform angles 样例: {[round(v, 3) for v in rang[:8]]}")
    print(f"rotation origins 非零比例: "
          f"{sum(1 for v in g('rotation_deformer_keyform.origin_xs') if abs(v) > 1e-6)} "
          f"/ {len(rang)}")
    print(f"rotation scales 分布样例: {sorted(set(round(v, 3) for v in g('rotation_deformer_keyform.scales')))[:6]}")
    print(f"rotation reflect 取值: x={sorted(set(g('rotation_deformer_keyform.reflect_xs')))} "
          f"y={sorted(set(g('rotation_deformer_keyform.reflect_ys')))}")

    # 位置坐标范围：判断变形器网格是否也使用画布像素坐标
    warp_spans = []
    for i, begin in enumerate(wpos[:40]):
        idx = i // max(1, 1)
        warp_spans.append((begin, pos[begin:begin + 8]))
    flat = [v for _, chunk in warp_spans for v in chunk[:4]]
    print(f"\nwarp 网格坐标样例（前若干个 float）: {[round(v, 1) for v in flat[:16]]}")
    amf = []
    for begin in g("art_mesh_keyform.keyform_position_begin_indices")[:20]:
        amf.extend(pos[begin:begin + 2])
    print(f"art_mesh 关键形坐标样例: {[round(v, 1) for v in amf[:12]]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
