"""新旧尺子的跨接缝读数对比：格内插值从双线性改成三角形之后，读数动了多少。

为什么要重测：仓库 WIP 里那批跨接缝数值（27.1272px / 27.183px 等）是**用双线性
尺子**量出来的。2026-11-11 实测官方 Core 的 warp 格内插值是**三角形分片线性**
（quad_transforms=0 的语义），尺子与播放器已同步改成三角形 —— 于是那批读数的
口径变了，必须重测才能继续用。

本探针做同一件事的两遍：
  * 新尺子（现行：格内三角形）；
  * 旧尺子（把格内换回双线性，进程内替换，不动仓库文件）。
对同一个模型、同一个姿态，算每一对层的「配对距离变化」并给出增量。

用法：python tools/probe_seam_delta.py
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PIL import Image

import bindings_gap_probe as ruler
from live2d_builder.pipeline import Live2DBuilder

POSE = {"ParamAngleZ": 30.0, "ParamBodyAngleZ": 10.0}
RADIUS = 25.0


def _layer(w=300, h=420, color=(200, 120, 100, 255), box=None):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    arr = np.array(img)
    x0, y0, x1, y1 = box or (30, 30, w - 30, h - 30)
    arr[y0:y1, x0:x1] = color
    return Image.fromarray(arr, "RGBA")


def _layers():
    """层名对齐仓库变形器的 targets（face/hair_front/hair_back/nose/cheek…）。"""
    return {
        "face": _layer(box=(90, 40, 210, 200), color=(255, 220, 180, 255)),
        "hair_back": _layer(box=(70, 20, 230, 300), color=(80, 80, 200, 255)),
        "hair_front": _layer(box=(80, 30, 220, 160), color=(60, 60, 180, 255)),
        "nose": _layer(box=(140, 110, 160, 140), color=(250, 200, 160, 255)),
        "cheek": _layer(box=(100, 150, 130, 180), color=(250, 170, 170, 255)),
        "clothes_top": _layer(box=(70, 210, 230, 390), color=(50, 150, 50, 255)),
        "arm_L": _layer(box=(230, 180, 280, 360), color=(120, 90, 60, 255)),
    }


def _bilinear_sampler(grid, rows, cols, u, v):
    """旧尺子：格内用双线性，越界仍用边界格外推（与改动前逐字一致）。"""
    cx, cy = u * cols, v * rows
    c0 = min(max(int(math.floor(cx)), 0), max(cols - 1, 0))
    r0 = min(max(int(math.floor(cy)), 0), max(rows - 1, 0))
    c1 = min(c0 + 1, cols)
    r1 = min(r0 + 1, rows)
    fx, fy = cx - c0, cy - r0
    stride = (cols + 1) * 2
    i00 = r0 * stride + c0 * 2
    i10 = r0 * stride + c1 * 2
    i01 = r1 * stride + c0 * 2
    i11 = r1 * stride + c1 * 2
    top_x = grid[i00] + (grid[i10] - grid[i00]) * fx
    top_y = grid[i00 + 1] + (grid[i10 + 1] - grid[i00 + 1]) * fx
    bot_x = grid[i01] + (grid[i11] - grid[i01]) * fx
    bot_y = grid[i01 + 1] + (grid[i11 + 1] - grid[i01 + 1]) * fx
    return (top_x + (bot_x - top_x) * fy, top_y + (bot_y - top_y) * fy)


def _warp_only(doc):
    """只保留 warp 变形器的视图。

    离线尺子 `Field` 只认 warp（旋转变形器没有静止矩形）。旋转是刚体变换，
    新旧两把尺子下**完全相同**，所以把旋转摘掉不影响本次要量的增量，只影响
    绝对值（表里会标注）。
    """
    by_id = {d["id"]: d for d in doc.get("deformers", [])}

    def _nearest_warp(did):
        seen = set()
        while did and did in by_id and by_id[did].get("type") != "warp":
            if did in seen:
                return ""
            seen.add(did)
            did = by_id[did].get("parent") or ""
        return did if did in by_id else ""

    out = dict(doc)
    out["deformers"] = []
    for d in doc.get("deformers", []):
        if d.get("type") != "warp":
            continue
        entry = dict(d)
        entry["parent"] = _nearest_warp(d.get("parent") or "")
        out["deformers"].append(entry)
    meshes = []
    for m in doc.get("meshes", []):
        entry = dict(m)
        entry["deformer"] = _nearest_warp(m.get("deformer") or "")
        meshes.append(entry)
    out["meshes"] = meshes
    return out


def _measure(doc):
    meshes = doc["meshes"]
    base = ruler.Field(doc, {})
    pose = ruler.Field(doc, POSE)
    rest_tables, pose_tables = {}, {}
    for m in meshes:
        rest_tables[m["id"]], _ = base.deformed(m)
        _, pose_tables[m["id"]] = pose.deformed(m)
    pairs = ruler.cross_layer_pairs(rest_tables, RADIUS)
    rows = {}
    for (a_id, b_id), ij in pairs.items():
        worst = 0.0
        for i, j in ij:
            ra, da = rest_tables[a_id][i], pose_tables[a_id][i]
            rb, db = rest_tables[b_id][j], pose_tables[b_id][j]
            d0 = math.hypot(ra[0] - rb[0], ra[1] - rb[1])
            d1 = math.hypot(da[0] - db[0], da[1] - db[1])
            worst = max(worst, abs(d1 - d0))
        rows[f"{a_id}↔{b_id}"] = worst
    return rows


def _auto_pose(doc):
    """按 warp 变形器**实际绑定**的参数取极值（否则形变根本没触发）。"""
    pose = {}
    for d in doc.get("deformers", []):
        for pid, keys in d.get("axes", []) or []:
            if not keys:
                continue
            extreme = max(keys, key=lambda v: abs(float(v)))
            pose[str(pid)] = float(extreme)
    return pose


def _bend_one_lattice(doc):
    """正对照：把某个 warp 变形器的一列控制点掰弯（非仿射）。

    仓库当前的形变 profile（head_xy / hair_sway）产生的是**仿射**点阵形变，
    而仿射下三角形与双线性**恒等** —— 所以新旧尺子的读数必然一样，看不出探针
    有没有用。把点阵掰弯（非仿射）之后才量得出差异：这就是正对照。
    """
    out = json.loads(json.dumps(doc))
    for d in out.get("deformers", []):
        if d.get("type") != "warp" or not d.get("grids"):
            continue
        rows = int(d["rows"]) + 1
        cols = int(d["cols"]) + 1
        # 关键形带按参数组合展平（i_X + n_X*i_Y），姿态可能选中任意一张 ——
        # 所以**每一张都掰**，保证被选中的那张一定是非仿射的。
        for gi, grid in enumerate(d["grids"]):
            for r in range(rows):
                base = r * cols * 2
                t = (r + 0.5) / rows
                bump = 40.0 * (1.0 - abs(2.0 * t - 1.0))
                grid[base + (cols - 1) * 2 + 1] += bump
            d["grids"][gi] = grid
        return out, d["id"]
    return out, ""


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="seam_delta_") as tmp:
        builder = Live2DBuilder(output_dir=tmp, character_name="seam")
        builder.build(_layers())
        bindings = sorted(Path(tmp).glob("*.bindings.json"))
        if not bindings:
            print("没产出 bindings.json")
            return 2
        doc = json.loads(bindings[0].read_text(encoding="utf-8"))
        rotations = [d["id"] for d in doc.get("deformers", [])
                     if d.get("type") != "warp"]
        if rotations:
            print(f"（本次只量 warp 链：旋转变形器 {rotations} 被摘掉 —— "
                  "旋转是刚体，新旧尺子下读数相同，不影响增量）")
        doc = _warp_only(doc)

        global POSE
        POSE = _auto_pose(doc)
        print(f"姿态（按 warp 实际绑定的参数取极值）：{POSE}")
        new_rows = _measure(doc)

        original = ruler.sample_warp_grid
        ruler.sample_warp_grid = _bilinear_sampler
        try:
            old_rows = _measure(doc)
        finally:
            ruler.sample_warp_grid = original

        print(f"姿态 {POSE}；跨层配对半径 {RADIUS:g}px")
        print(f"{'层对':34s} {'旧尺子(双线性)':>16s} {'新尺子(三角形)':>16s} {'增量':>10s}")
        worst_delta = 0.0
        for key in sorted(set(old_rows) | set(new_rows)):
            old = old_rows.get(key, 0.0)
            new = new_rows.get(key, 0.0)
            delta = new - old
            worst_delta = max(worst_delta, abs(delta))
            print(f"{key:34s} {old:16.4f} {new:16.4f} {delta:+10.4f}")
        print(f"\n最大绝对增量 = {worst_delta:.4f}px")

        # ---- 正对照：把点阵掰弯（非仿射）后必须量得出差异 ----
        bent, bent_id = _bend_one_lattice(doc)
        if bent_id:
            new_bent = _measure(bent)
            ruler.sample_warp_grid = _bilinear_sampler
            try:
                old_bent = _measure(bent)
            finally:
                ruler.sample_warp_grid = original
            worst_bent = max(
                (abs(new_bent.get(k, 0.0) - old_bent.get(k, 0.0))
                 for k in set(new_bent) | set(old_bent)), default=0.0)
            print(f"\n正对照（把 {bent_id} 的点阵掰弯成非仿射）："
                  f"最大绝对增量 = {worst_bent:.4f}px")
            if worst_bent <= 0.001:
                print("  ❌ 掰弯后仍量不出差异 —— 探针对插值口径不敏感，"
                      "上面的 0 增量不能当证据")
            else:
                print("  ✅ 探针能分辨两种插值口径 → 上面的 0 增量是"
                      "「当前 profile 是仿射」的真实结论，不是探针失灵")

        print("（旧尺子的数字就是仓库 WIP 里那批读数的口径；"
              "若最大增量为 0，说明当前 profile 下两把尺子等价，基线仍然有效）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
