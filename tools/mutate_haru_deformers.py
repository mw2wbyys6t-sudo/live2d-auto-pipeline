#!/usr/bin/env python3
"""变异搜索：从**已知合法**的 Haru 出发，一次只改一处，看内核何时翻脸。

翻脸 = 内核确实校验这条约束；不翻脸 = 这条不是我们失败的原因。
这样枚举出的是内核实际执行的规则，而不是我们猜测的规则。
运行：.venv/Scripts/python.exe tools/mutate_haru_deformers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from moc3 import _core  # noqa: E402
from drivers.live2d_runtime.moc3_verify import verify_moc3_consistency  # noqa: E402

HARU = ROOT / "Work" / "native-sample" / "Haru" / "Haru.moc3"
OUT = ROOT / "Work" / "haru-mutations"
DEFORMERS = _core.CountIdx.DEFORMERS


def load():
    return _core.Moc3.from_file(str(HARU))


def check(tag: str, doc) -> bool:
    path = OUT / f"{tag}.moc3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(doc.to_bytes())
    result = verify_moc3_consistency(str(path))
    verdict = "仍然一致" if result["ok"] else "被拒绝了"
    note = "" if result["ok"] else f"  ({str(result['blocker'])[:70]})"
    print(f"  {tag:34s} {verdict}{note}")
    return result["ok"]


def patched(doc, sections: dict):
    for name, values in sections.items():
        doc.set(name, list(values))
    return doc


def replace(seq, index, value):
    out = list(seq)
    out[index] = value
    return out


def main() -> int:
    print("控制组：不改任何东西")
    if not check("control_none", load()):
        print("官方 Haru 本身不过一致性 —— 实验前提失效")
        return 1

    doc0 = load()
    n = doc0.counts[DEFORMERS]
    types = doc0.get("deformer.types")
    warp0 = next(i for i in range(n) if types[i] == 0)
    specific = doc0.get("deformer.specific_indices")[warp0]
    w_rows = doc0.get("warp_deformer.rows")
    w_cols = doc0.get("warp_deformer.cols")
    w_verts = doc0.get("warp_deformer.vertex_counts")
    w_kc = doc0.get("warp_deformer.keyform_counts")
    w_kb = doc0.get("warp_deformer.keyform_begin_indices")
    d_band = doc0.get("deformer.keyform_binding_band_indices")
    a_parent = doc0.get("art_mesh.parent_deformer_indices")
    kp_begin = doc0.get("warp_deformer_keyform.keyform_position_begin_indices")
    print(f"\n取样 deformer #{warp0}（warp，specific={specific}）："
          f"rows={w_rows[specific]} cols={w_cols[specific]} "
          f"verts={w_verts[specific]} keyforms={w_kc[specific]} "
          f"kf_begin={w_kb[specific]} band={d_band[warp0]}")

    print("\n== 单点变异 ==")
    # parent 测试要挑一个真的挂着网格的变形器（网格挂在链的末端，不是 #0）
    mesh_i = next(i for i, v in enumerate(a_parent) if v >= 0)
    d_i = a_parent[mesh_i]
    print(f"  取样：mesh #{mesh_i} 挂在 deformer #{d_i}（type={types[d_i]}）")
    patched_tests = [
        ("rows+cols 与顶点数不再自洽", {
            "warp_deformer.rows": replace(w_rows, specific, 1),
            "warp_deformer.cols": replace(w_cols, specific, 1)}),
        ("vertex_counts +1", {
            "warp_deformer.vertex_counts": replace(
                w_verts, specific, w_verts[specific] + 1)}),
        ("keyform_counts 改成 1", {
            "warp_deformer.keyform_counts": replace(w_kc, specific, 1)}),
        ("keyform_begin +1（破坏铺平）", {
            "warp_deformer.keyform_begin_indices": replace(
                w_kb, specific, w_kb[specific] + 1)}),
        ("deformer 改用空带 0", {
            "deformer.keyform_binding_band_indices": replace(d_band, warp0, 0)}),
        (f"mesh #{mesh_i} 的 parent_deformer 设 -1", {
            "art_mesh.parent_deformer_indices": replace(a_parent, mesh_i, -1)}),
        ("控制网格位置起点 +1（破坏对齐）", {
            "warp_deformer_keyform.keyform_position_begin_indices": replace(
                kp_begin, w_kb[specific], kp_begin[w_kb[specific]] + 1)}),
        ("把该 warp 的 type 改成 rotation", {
            "deformer.types": replace(types, warp0, 1)}),
    ]
    for tag, sections in patched_tests:
        check(tag, patched(load(), sections))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
