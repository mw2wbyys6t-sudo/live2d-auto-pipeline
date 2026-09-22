#!/usr/bin/env python3
"""从官方 Haru.moc3 里取 rotation 变形器的全部事实，为编译器定单位与朝向。

要回答的问题（都要有官方数字支撑，不靠猜规范）：
1. `deformer.types` 里 rotation 是几？哪些条目是 rotation？
2. `rotation_deformer.base_angles` 的量级 -> 角度制还是弧度？
3. `rotation_deformer_keyform.{angles,origin_xs,origin_ys,scales,reflect_*}` 的量级
   -> 原点在像素模型空间还是单位空间？scale 是 1 附近的倍率还是别的？
4. 键形的 angles 与其绑定参数的键值（`keys.values`）之间是什么关系？
运行：.venv/Scripts/python.exe tools/probe_rotation_deformer.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from moc3 import Moc3  # noqa: E402

HARU = ROOT / "Work/native-sample/Haru/Haru.moc3"


def main() -> int:
    doc = Moc3.from_file(str(HARU))
    get = doc.get
    types = get("deformer.types") or []
    ids = get("deformer.ids") or []
    print(f"变形器 {len(ids)} 个；types 取值分布: "
          f"{sorted(set(types))}，各计数: "
          f"{ {t: types.count(t) for t in sorted(set(types))} }")

    rotations = [i for i, t in enumerate(types) if t != 0]
    print(f"非 0 类型条目下标: {[(i, ids[i], types[i]) for i in rotations]}")

    for field in ("rotation_deformer.base_angles",
                  "rotation_deformer.keyform_counts",
                  "rotation_deformer.keyform_begin_indices",
                  "rotation_deformer.keyform_binding_band_indices"):
        print(f"{field} = {get(field)}")

    specific = get("deformer.specific_indices")
    print(f"deformer.specific_indices = {specific}")

    n_keyforms = len(get("rotation_deformer_keyform.angles") or [])
    print(f"\nrotation 键形共 {n_keyforms} 个")
    for name in ("angles", "origin_xs", "origin_ys", "scales",
                 "reflect_xs", "reflect_ys", "opacities"):
        values = get(f"rotation_deformer_keyform.{name}") or []
        shown = [round(v, 4) for v in values[:12]]
        print(f"{name:11s} n={len(values):3d} 前 12 项={shown} "
              f"范围=[{min(values):.4f}, {max(values):.4f}]" if values
              else f"{name}: 空")

    # 键形角度 vs 绑定参数的键值：找关系
    keys = get("keys.values") or []
    bands = get("keyform_binding.band_parameter_indices") or []
    print(f"\nkeys.values 前 24 项 = {keys[:24]}")
    print(f"bands 数量 = {len(bands)}；唯一参数下标 = "
          f"{sorted(set(b for b in bands if b >= 0))[:12]}")

    # 挂在 rotation 变形器下的网格：顶点在什么空间
    parents = get("art_mesh.parent_deformer_indices") or []
    mesh_ids = get("art_mesh.ids") or []
    begins = get("art_mesh.keyform.keyform_position_begin_indices") or []
    begins = begins or get("art_mesh_keyform.keyform_position_begin_indices") or []
    pool = get("keyform_position.xys") or []
    counts = get("art_mesh.position_index_counts") or []
    print(f"\n网格 parent_deformer_indices 取值: {sorted(set(parents))}")
    for mesh_index, parent in enumerate(parents):
        if parent not in rotations:
            continue
        count = counts[mesh_index] if mesh_index < len(counts) else 0
        begin = begins[mesh_index] if mesh_index < len(begins) else 0
        chunk = pool[begin:begin + 2 * count]
        if not chunk:
            continue
        print(f"  {mesh_ids[mesh_index]} <- 变形器 {parent} ({ids[parent]})："
              f"顶点数 {count}，x 范围 [{min(chunk[0::2]):.2f}, {max(chunk[0::2]):.2f}]，"
              f"y 范围 [{min(chunk[1::2]):.2f}, {max(chunk[1::2]):.2f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
