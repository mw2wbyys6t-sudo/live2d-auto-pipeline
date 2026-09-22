#!/usr/bin/env python3
"""受控实验：我们的**多键形 warp** 与官方 Haru 的同类接线逐段对拍。

背景：`tools/verify_breath_end_to_end.py` 发现 ParamBreath 驱动的 2 键形 warp
在官方内核里**完全不改变画面**（键 0/1 像素相同），而结构自洽性(lint)与内核一致性
都通过 —— 说明是接线语义错，静态校验看不到。

做法：把「我们构造的 2 键形 warp」与「官方 Haru 里第一个含 ≥2 个关键形的 warp」
在**同一组字段**上并列打印，差异即根因。

运行：.venv/Scripts/python.exe tools/probe_warp_multi_keyform.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from moc3 import _core  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    MeshSpec,
    ParameterSpec,
    RigSpec,
    compile_static_rig,
)
from live2d_builder.exporter.moc3_pipeline import build_rig_spec  # noqa: E402

HARU = ROOT / "Work" / "native-sample" / "Haru" / "Haru.moc3"
CI = _core.CountIdx

_FIELDS = (
    "deformer.keyform_binding_band_indices",
    "warp_deformer.keyform_binding_band_indices",
    "warp_deformer.keyform_begin_indices",
    "warp_deformer.keyform_counts",
    "warp_deformer.vertex_counts",
    "warp_deformer.rows",
    "warp_deformer.cols",
    "warp_deformer_keyform.keyform_position_begin_indices",
    "warp_deformer_keyform.opacities",
    "keyform_binding_band.begin_indices",
    "keyform_binding_band.counts",
    "keyform_binding_index.indices",
    "keyform_binding.keys_begin_indices",
    "keyform_binding.keys_counts",
    "keys.values",
    "parameter.ids",
    "parameter.keyform_binding_begin_indices",
    "parameter.keyform_binding_counts",
)


def _our_doc():
    mesh = {"vertices": [[0.0, 0.0], [200.0, 0.0], [200.0, 300.0], [0.0, 300.0]],
            "vertices_norm": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            "indices": [[0, 1, 2], [0, 2, 3]], "width": 200.0, "height": 300.0}
    builder = {
        "meshes": {"body": mesh},
        "deformer_tree": {"deformers": [
            {"name": "Breath", "type": "warp", "targets": ["body"],
             "grid_rows": 2, "grid_cols": 2, "pivot": [0.0, 100.0]}]},
        "parameters": {"cubism_params": [
            {"Id": "ParamBreath", "Min": 0, "Max": 1, "Value": 0}]},
        "bone_positions": {"Chest": (0.0, 100.0)},
    }
    spec = build_rig_spec(builder, {"body": {"u0": 0.0, "v0": 0.0,
                                             "u1": 1.0, "v1": 1.0}})
    return spec, compile_static_rig(spec)


def _dump(title, doc, warp_index, band, binding):
    print(f"\n=== {title} ===")
    for field in _FIELDS:
        values = doc.get(field)
        if isinstance(values, (list, tuple)):
            shown = list(values)
            if len(shown) > 8:
                shown = shown[:8] + [f"...共{len(values)}项"]
        else:
            shown = values
        print(f"  {field:52s} = {shown}")
    print(f"  -- 该 warp（第 {warp_index} 个）--")
    print(f"     band = {band}  band.count = "
          f"{doc.get('keyform_binding_band.counts')[band]}")
    begins = doc.get("keyform_binding_band.begin_indices")
    idx = doc.get("keyform_binding_index.indices")
    count = doc.get("keyform_binding_band.counts")[band]
    print(f"     该带的 binding 引用 = {idx[begins[band]:begins[band] + count]}")
    print(f"     binding[{binding}].keys_count = "
          f"{doc.get('keyform_binding.keys_counts')[binding]}  "
          f"keys = {doc.get('keys.values')}")


def main() -> int:
    spec, ours = _our_doc()
    band = ours.get("warp_deformer.keyform_binding_band_indices")[0]
    binding = ours.get("keyform_binding_index.indices")[
        ours.get("keyform_binding_band.begin_indices")[band]]
    _dump("我们的 2 键形 warp", ours, 0, band, binding)

    # 诊断：成员网格 -> (s,t) 所用的 rect（reference_rect）与 warp 控制网格实际
    # 覆盖的 rect（_member_rect）必须**同一尺度**，否则网格会被映射到错误大小。
    from live2d_builder.exporter.moc3_model import reference_rect
    from live2d_builder.exporter.moc3_pipeline import _member_rect

    params_by_id = {p.parameter_id: p for p in spec.parameters}
    print("\n=== rect 一致性诊断 ===")
    members = [m for m in spec.meshes]
    print(f"  _member_rect(成员网格)      = {_member_rect(members)}")
    for d in spec.deformers:
        try:
            print(f"  reference_rect({d.deformer_id})    = "
                  f"{reference_rect(d, params_by_id)}")
        except Exception as exc:                       # noqa: BLE001
            print(f"  reference_rect({d.deformer_id}) 抛错: {exc}")
    print(f"  网格模型坐标范围            = "
          f"x{[v[0] for v in members[0].vertices]} "
          f"y{[v[1] for v in members[0].vertices]}")
    print("  （前两者若尺度/位置不同，成员网格的 (s,t) 就会偏离 [0,1]，"
          "渲染尺寸随之错掉）")

    if not HARU.is_file():
        print(f"\n（未找到官方样例 {HARU}，无法对照）")
        return 1
    haru = _core.Moc3.from_file(str(HARU))
    counts = haru.get("warp_deformer.keyform_counts")
    target = next((i for i, c in enumerate(counts) if c >= 2), None)
    print(f"\nHaru 的 warp 关键形数分布：≥2 键形的有 "
          f"{sum(1 for c in counts if c >= 2)} 个 / 共 {len(counts)} 个")
    if target is None:
        print("Haru 没有多键形 warp，无法对照")
        return 1
    hb = haru.get("warp_deformer.keyform_binding_band_indices")[target]
    hbegins = haru.get("keyform_binding_band.begin_indices")
    hidx = haru.get("keyform_binding_index.indices")
    hcount = haru.get("keyform_binding_band.counts")[hb]
    hbinding = hidx[hbegins[hb]] if hcount else 0
    _dump(f"官方 Haru 第 {target} 个 warp（{counts[target]} 个关键形）",
          haru, target, hb, hbinding)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
