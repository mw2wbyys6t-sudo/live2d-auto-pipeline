#!/usr/bin/env python3
"""探针（证据脚本）：官方 Haru.moc3 里 keyform / 绑定带 / 键的真实关系。

只读分析，用于设计参数形变键型编译；不改任何产物。
运行：.venv/Scripts/python.exe tools/probe_haru_keyforms.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HARU = Path(__file__).resolve().parents[1] / "Work" / "native-sample" / "Haru" / "Haru.moc3"


def main() -> int:
    from moc3 import _core

    doc = _core.Moc3.from_file(HARU)
    c = doc.counts

    def g(name):
        return doc.get(name)

    CI = _core.CountIdx
    print("== counts（关键项）==")
    for key in ("parts", "deformers", "warp_deformers", "rotation_deformers",
                "art_meshes", "parameters", "part_keyforms", "art_mesh_keyforms",
                "keyform_positions", "keyform_binding_indices",
                "keyform_binding_bands", "keyform_bindings", "keys",
                "position_indices", "uvs"):
        print(f"  {key} = {c[getattr(CI, key.upper())]}")

    kf_count = g("art_mesh.keyform_counts")
    kf_begin = g("art_mesh.keyform_begin_indices")
    band_idx = g("art_mesh.keyform_binding_band_indices")
    band_begin = g("keyform_binding_band.begin_indices")
    band_count = g("keyform_binding_band.counts")
    bind_index = g("keyform_binding_index.indices")
    bind_keys_begin = g("keyform_binding.keys_begin_indices")
    bind_keys_count = g("keyform_binding.keys_counts")
    keys_values = g("keys.values")
    kf_pos_begin = g("art_mesh_keyform.keyform_position_begin_indices")
    pos_counts = g("art_mesh.position_index_counts")
    param_bind_begin = g("parameter.keyform_binding_begin_indices")
    param_bind_count = g("parameter.keyform_binding_counts")

    n_meshes = c[CI.ART_MESHES]
    print(f"\n== art mesh 数：{n_meshes} ==")

    def describe(mesh_i):
        kf = kf_count[mesh_i]
        b = band_idx[mesh_i]
        binds = bind_index[band_begin[b]: band_begin[b] + band_count[b]]
        key_lists = []
        for bi in binds:
            kb, kc = bind_keys_begin[bi], bind_keys_count[bi]
            key_lists.append(list(keys_values[kb: kb + kc]))
        verts = pos_counts[mesh_i]
        pos_span = []
        for k in range(kf):
            begin = kf_pos_begin[kf_begin[mesh_i] + k]
            pos_span.append(begin)
        return kf, b, binds, key_lists, verts, pos_span

    from collections import Counter
    print("\n== 前 12 个网格的绑定结构 ==")
    for i in range(min(12, n_meshes)):
        kf, b, binds, key_lists, verts, pos_span = describe(i)
        print(f"mesh[{i}] keyforms={kf} verts={verts} band={b} binds={binds} "
              f"keys={key_lists} pos_begin={pos_span[:6]}")

    print("\n== keyform_count 直方图（全部网格）==")
    hist = Counter(kf_count[i] for i in range(n_meshes))
    print(dict(sorted(hist.items())))

    print("\n== 被绑定参数统计 ==")
    n_params = c[CI.PARAMETERS]
    bound = [(i, param_bind_count[i]) for i in range(n_params) if param_bind_count[i] > 0]
    print(f"有绑定的参数数：{len(bound)} / {n_params}")
    for i, cnt in bound[:10]:
        pid = doc.parameter_ids[i]
        print(f"  param[{i}] {pid}: bindings={cnt}")

    print("\n== 绑定带占用 ==")
    used_bands = Counter(band_idx[i] for i in range(n_meshes))
    print(f"mesh 引用的 band 分布（前 10）：{dict(list(used_bands.most_common()[:10]))}")
    print(f"band 总数：{c[CI.KEYFORM_BINDING_BANDS]}，binding 总数：{c[CI.KEYFORM_BINDINGS]}，"
          f"binding_index 条目：{c[CI.KEYFORM_BINDING_INDICES]}，keys 总数：{c[CI.KEYS]}")

    print("\n== binding <-> parameter 映射 ==")
    for p in range(n_params):
        bb, bc = param_bind_begin[p], param_bind_count[p]
        if bc == 0:
            continue
        keysets = []
        for b in range(bb, bb + bc):
            kb, kc = bind_keys_begin[b], bind_keys_count[b]
            keysets.append(list(keys_values[kb: kb + kc]))
        print(f"  param[{p}] {doc.parameter_ids[p]}: bindings[{bb},{bb+bc}) "
              f"keys={keysets}")

    print("\n== keyform 数 == band 各 binding 键数之积？（全网格验证）==")
    import math
    ok = bad = 0
    for i in range(n_meshes):
        b = band_idx[i]
        binds = bind_index[band_begin[b]: band_begin[b] + band_count[b]]
        prod = 1
        for bi in binds:
            prod *= bind_keys_count[bi]
        if prod == kf_count[i]:
            ok += 1
        else:
            bad += 1
            if bad <= 5:
                print(f"  不匹配 mesh[{i}]: keyforms={kf_count[i]} band={b} "
                      f"binds={binds} keys_counts="
                      f"{[bind_keys_count[bi] for bi in binds]}")
    print(f"  乘积规则匹配 {ok} / {ok + bad}")

    print("\n== keyform_position_begin 是否都是 16 的倍数 ==")
    begins = kf_pos_begin
    non16 = [v for v in begins if v % 16 != 0]
    print(f"  非对齐数：{len(non16)} / {len(begins)}")

    print("\n== 每个 keyform 占用的浮点数 == verts*2 且补齐到 16 倍数 ==")
    mismatch = 0
    for i in range(n_meshes):
        verts = pos_counts[i]
        base = kf_begin[i]
        for k in range(kf_count[i]):
            cur = kf_pos_begin[base + k]
            nxt = kf_pos_begin[base + k + 1] if base + k + 1 < len(kf_pos_begin) else None
            if nxt is None:
                break
            span = nxt - cur
            need = verts * 2
            padded = (need + 15) // 16 * 16
            if span != padded and span != need:
                mismatch += 1
                if mismatch <= 5:
                    print(f"  mesh[{i}] kf[{k}]: span={span} verts={verts} need={need}")
    print(f"  不符条目：{mismatch}")

    print("\n== part 的 band ==")
    part_band = g("part.keyform_binding_band_indices")
    print(f"  {part_band}")

    print("\n== band 拥有的 binding 数分布 ==")
    band_size = Counter(band_count[b] for b in range(c[CI.KEYFORM_BINDING_BANDS]))
    print(f"  {dict(sorted(band_size.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
