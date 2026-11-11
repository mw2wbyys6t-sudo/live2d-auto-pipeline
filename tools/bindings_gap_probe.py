#!/usr/bin/env python3
"""离线复测：按运行时的真实求值顺序重放形变，再量跨层相对位移与夹缝。

上一轮的浏览器探针因为下标写错（拿 A 层的形变位置去减 B 层的静止位置）造出过
假证据。这一版把度量整个搬到离线，并强制两条自检，任何一条不过就直接退出：

  自检 1（求值正确性）  默认参数下，逐顶点重放的结果必须等于静止值。
                        这条挡住"移植 TS 时算错序 / 坐标偏移"这类整体性错误。
  自检 2（度量正确性）  同一刚体场里的两个网格，在只驱动该场的姿态下，
                        跨层顶点对的**距离变化**必须是 0。
                        上一轮的假证据正是缺了这条：它量的是「位移之差」，
                        而同一刚体场下位移之差天生等于 2·sin(θ/2)·间距
                        （θ=10°、间距 25px 就是 4.36px），根本不是错动。
                        缝开没开只看配对距离的变化。

求值顺序与 web/lib/live2d-player.ts:updateArtMeshes 一致：
  变形器键形混合 → 网格键形混合 → 顶点穿变形器链（先自身网格，再逐级父级）。

顶点落在变形器静止矩形**之外**时按官方 Core 那样线性外推，不夹取（#72）：
旧版把 (u,v) 夹在 [0,1]，于是"挂在父级网格外的画"被整段塌到父级边界上，
自检 1 会报出 27~130px 的静止偏差，而同一批产物的 Core 静止 SHA 一个像素都没动。
越界的量现在单独成列（`art outside parent grid`），永不并进偏差读数。

用法：
    python tools/bindings_gap_probe.py <name>.bindings.json [选项]
    --pose "ParamAngleZ=30,ParamBodyAngleZ=10"   显式姿态（可重复，取并集）
    --band 1056 1076                              只在该 y 带内做夹缝扫描
    --radius 25                                   跨层配对的静止距离阈值
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

# 与 live2d-player.ts 的 _fitLayers 一致：捕获画布 → 立绘坐标的换算就靠它，
# 上一轮这里是手算的，所以读数没法回读到立绘像素。
CAPTURE_W = 512
CAPTURE_H = 768


# --------------------------------------------------------------------------
# 求值原语（逐条对应 web/lib/live2d-bindings.ts）
# --------------------------------------------------------------------------

def bracket_keys(keys, value):
    """升序键值表里 value 的夹取区间，返回 (低下标, 高下标, 高键权重)。"""
    n = len(keys)
    if n == 0:
        return 0, 0, 0.0
    if n == 1 or value <= keys[0]:
        return 0, 0, 0.0
    if value >= keys[n - 1]:
        return n - 1, n - 1, 0.0
    lo, hi = 0, n - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if keys[mid] <= value:
            lo = mid
        else:
            hi = mid
    span = keys[hi] - keys[lo]
    return lo, hi, ((value - keys[lo]) / span if span else 0.0)


def keyform_terms(axes, resolve):
    """多轴键形的插值项：[(展平下标, 权重)]，展平序 i0 + n0*i1 + n0*n1*i2 + …"""
    if not axes:
        return []
    brackets = [bracket_keys(keys, resolve(pid)) for pid, keys in axes]
    strides = []
    stride = 1
    for _pid, keys in axes:
        strides.append(stride)
        stride *= len(keys)
    terms = []

    def walk(axis, index, weight):
        if weight <= 0:
            return
        if axis == len(axes):
            terms.append((index, weight))
            return
        lo, hi, w = brackets[axis]
        step = strides[axis]
        walk(axis + 1, index + lo * step, weight * (1.0 - w))
        if hi != lo:
            walk(axis + 1, index + hi * step, weight * w)

    walk(0, 0, 1.0)
    return terms


def blend_flat(terms, tables, width):
    out = [0.0] * width
    for index, weight in terms:
        src = tables[index] if index < len(tables) else None
        if src is None:
            continue
        n = min(width, len(src))
        for i in range(n):
            out[i] += src[i] * weight
    return out


def sample_warp_grid(grid, rows, cols, u, v):
    """变形器网格采样（行优先、x 最快）。

    **格内是三角形分片线性**，不是双线性：官方 Core 的「网格形变」
    （moc3 ``additional.quad_transforms=0``）沿 P10-P01 对角线把每格切成两半，
    各用三个角点插值。实测见 ``tools/probe_warp_interpolation.py``（官方 Core
    5-r.5）：非仿射控制网格下 Core 渲染质心与三角形预测差 0.43%、与双线性差
    9.49%。仿射网格下两者相同，所以仓库原有的仿射对照用例看不出差别。
    双线性是「quad 形变」（``quad_transforms=1``）的语义。

    (u,v) **不夹取**，越界的那一段按边界那一格线性外推 —— 这是官方 Core 的行为
    （#72）。旧版这里写的是 `min(max(u,0),1)`，那是移植时自己加的假设：把挂在
    变形器静止矩形之外的画整段塌到矩形边界上。实测（诊断 `step3_core_arbiter.py`
    / `step4_ruler_vs_core.py`，判据已钉进
    ``tests/unit/test_bindings_gap_probe.py::test_art_outside_the_parent_grid_is_extrapolated_not_clamped``）：
    挂在父矩形外 160px 的边，Core 画在 452（与「根本没有变形器」的对照逐字节相同），
    夹取版尺子却读 328 —— 124 屏幕像素的幻影正好等于越界量。嵌套主干第一次让这件事
    变得可观测（hair_back 的 27/140 个顶点在 HeadRig 矩形之外），于是这把尺子报出
    「静止帧偏了 27.183px」，而同一批产物的 Core 静止 SHA 一直没动。

    **越界这一段的规则尚未钉死**（2026-11-11 实测）：上面那条 #72 判据用的是**恒等
    点阵**——恒等网格下「外推」「仿射平面」「三角形平面外推」全都退化成同一条映射，
    所以它分不出差别。用**变形过**的网格实测（``tools/probe_warp_interpolation.py``
    的 ``measure_out_of_grid``）：官方 Core 把挂在矩形外 160px 的顶点放在模型
    x≈562，而本函数读 530（UR 三角形平面外推是 650、全局平面拟合是 465~510）——
    候选规则一个都没命中，差约 32 个模型单位。**别把这里的读数当内核语义用**；
    静止/仿射网格上的读数仍然与内核逐像素一致（那正是 #72 覆盖的场景）。
    """
    cx = u * cols
    cy = v * rows
    # int() 向零取整，越界到负方向必须用 floor，否则第一格会被当成第 0 格重复用
    c0 = min(max(int(math.floor(cx)), 0), max(cols - 1, 0))
    r0 = min(max(int(math.floor(cy)), 0), max(rows - 1, 0))
    c1 = min(c0 + 1, cols)
    r1 = min(r0 + 1, rows)
    fx = cx - c0
    fy = cy - r0
    stride = (cols + 1) * 2
    i00 = r0 * stride + c0 * 2
    i10 = r0 * stride + c1 * 2
    i01 = r1 * stride + c0 * 2
    i11 = r1 * stride + c1 * 2

    if 0.0 <= cx <= cols and 0.0 <= cy <= rows:
        # 格内：官方 Core 的「网格形变」（additional.quad_transforms=0）走
        # **三角形分片线性** —— 沿 P10-P01 对角线把格子切成两半，各用三个角点。
        # 实测（tools/probe_warp_interpolation.py，官方 Core 5-r.5）：控制网格被
        # 拉成非仿射时，Core 渲染质心与三角形预测差 0.43%、与双线性差 9.49%。
        # 仿射网格（整体平移）下两种插值相同 —— 仓库原有的对照用例全是仿射的，
        # 结构性看不见这个差异。双线性是 quad 形变（quad_transforms=1）的语义。
        if fx + fy < 1.0:
            w00, w10, w01, w11 = 1.0 - fx - fy, fx, fy, 0.0
        else:
            w00, w10, w01, w11 = 0.0, 1.0 - fy, 1.0 - fx, fx + fy - 1.0
        return (
            grid[i00] * w00 + grid[i10] * w10 + grid[i01] * w01 + grid[i11] * w11,
            grid[i00 + 1] * w00 + grid[i10 + 1] * w10
            + grid[i01 + 1] * w01 + grid[i11 + 1] * w11,
        )

    # 越界：沿用既有的「边界格线性外推、不夹取」——见本函数 docstring 的 #72。
    top_x = grid[i00] + (grid[i10] - grid[i00]) * fx
    top_y = grid[i00 + 1] + (grid[i10 + 1] - grid[i00 + 1]) * fx
    bot_x = grid[i01] + (grid[i11] - grid[i01]) * fx
    bot_y = grid[i01 + 1] + (grid[i11 + 1] - grid[i01 + 1]) * fx
    return (top_x + (bot_x - top_x) * fy, top_y + (bot_y - top_y) * fy)


class Field:
    """一份 bindings.json + 一组参数值 = 一个形变场。"""

    def __init__(self, doc, values):
        self.doc = doc
        self.half_w = doc["canvas"]["width"] / 2.0
        self.half_h = doc["canvas"]["height"] / 2.0
        self.values = dict(values)
        self.defaults = {p["id"]: p["default"] for p in doc.get("parameters", [])}
        self.deformers = {d["id"]: d for d in doc.get("deformers", [])}
        # 局部坐标：与播放器一致，减掉半画布；rest 也跟着搬。
        self._warp_grid = {}
        self._warp_rest = {}
        for d in self.deformers.values():
            self._warp_rest[d["id"]] = [
                d["rest"][0] - self.half_w, d["rest"][1] - self.half_h,
                d["rest"][2] - self.half_w, d["rest"][3] - self.half_h,
            ]
            self._warp_grid[d["id"]] = [
                [c - self.half_w if i % 2 == 0 else c - self.half_h
                 for i, c in enumerate(g)]
                for g in d["grids"]
            ]
        self._current = {}
        self._evaluate_warps()

    def resolve(self, pid):
        if pid in self.values:
            return self.values[pid]
        return self.defaults.get(pid, 0.0)

    def _evaluate_warps(self):
        """第一步：变形器自身的键形混合（一帧只求一次）。"""
        for did, spec in self.deformers.items():
            rest = self._warp_grid[did][0]
            terms = keyform_terms([(pid, [k for k in keys])
                                   for pid, keys in spec.get("axes", [])],
                                  self.resolve)
            if not terms:
                self._current[did] = list(rest)
            elif len(terms) == 1:
                self._current[did] = list(self._warp_grid[did][terms[0][0]])
            else:
                self._current[did] = blend_flat(terms, self._warp_grid[did], len(rest))

    def _step(self, x, y, pid):
        """把点过**一级**变形器（自身静止矩形换算 (u,v) → 采样，越界外推）。"""
        spec = self.deformers[pid]
        x0, y0, x1, y1 = self._warp_rest[pid]
        w = x1 - x0
        h = y1 - y0
        u = (x - x0) / w if w else 0.5
        v = (y - y0) / h if h else 0.5
        return sample_warp_grid(self._current[pid], spec["rows"], spec["cols"], u, v)

    def chain(self, x, y, deformer_id):
        """第三步：顶点穿变形器链（先自身，再逐级父级）。"""
        pid = deformer_id
        guard = 0
        while pid and guard < 32 and pid in self.deformers:
            x, y = self._step(x, y, pid)
            pid = self.deformers[pid].get("parent") or ""
            guard += 1
        return x, y

    def art_outside_parent_grid(self, mesh):
        """这个网格有多少顶点落在**某一级**变形器静止矩形之外。

        单列成名的指标（``art outside parent grid``），**不并进**自检 1 的偏差数：
        尺子跟着 Core 外推以后，矩形之内与仿射/平移场上的越界画与 Core 一致到
        ≤1px，但**非仿射弯曲**的父级键形在越界处仍有实测 29px 的口径差
        （诊断 `H_out_4x3_bend`：夹取 118px、外推 29px、Core 446）。也就是说
        读数落进这个区域，就离开了「Core、自研播放器、尺子三方一致」的保证范围，
        必须单独看得见，不能混在"静止偏了多少像素"里当成产品缺陷。

        产物侧的正解是**把父级矩形铺满子孙的画**
        （`moc3_pipeline` 的变形器静止矩形取后代并集），让这个指标读 0。
        """
        flat = self._blended(mesh)
        pts = [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]
        outside_any = [False] * len(pts)
        levels = {}
        pid = mesh.get("deformer") or ""
        guard = 0
        while pid and guard < 32 and pid in self.deformers:
            x0, y0, x1, y1 = self._warp_rest[pid]
            hit = 0
            for i, (x, y) in enumerate(pts):
                if x < x0 or x > x1 or y < y0 or y > y1:
                    outside_any[i] = True
                    hit += 1
            if hit:
                levels[pid] = hit
            pts = [self._step(x, y, pid) for x, y in pts]
            pid = self.deformers[pid].get("parent") or ""
            guard += 1
        return {"total": sum(1 for f in outside_any if f),
                "vertices": len(outside_any), "levels": levels}

    def _blended(self, mesh):
        """第二步：网格自身键形带混合后的局部顶点表。"""
        half_w, half_h = self.half_w, self.half_h
        kf = mesh.get("keyforms")
        axes = [(pid, keys) for pid, keys in (kf["axes"] if kf else [])]
        tables = None
        if kf:
            tables = [[c - half_w if i % 2 == 0 else c - half_h
                       for i, c in enumerate(v)] for v in kf["vertices"]]
        rest = [c - half_w if i % 2 == 0 else c - half_h
                for i, c in enumerate(mesh["vertices"])]
        terms = keyform_terms(axes, self.resolve)
        if not terms or tables is None:
            return list(rest)
        if len(terms) == 1:
            return list(tables[terms[0][0]])
        return blend_flat(terms, tables, len(rest))

    def deformed(self, mesh):
        """返回该网格在局部坐标下的 (静止表, 形变表)。"""
        rest = [c - self.half_w if i % 2 == 0 else c - self.half_h
                for i, c in enumerate(mesh["vertices"])]
        blended = self._blended(mesh)
        did = mesh.get("deformer") or ""
        out = []
        for i in range(0, len(blended), 2):
            if did:
                x, y = self.chain(blended[i], blended[i + 1], did)
            else:
                x, y = blended[i], blended[i + 1]
            out.append((x, y))
        rest_pts = [(rest[i], rest[i + 1]) for i in range(0, len(rest), 2)]
        return rest_pts, out


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------

def selfcheck_rest(field_at_defaults, meshes):
    """自检 1：默认参数下重放必须回到静止值。"""
    worst = 0.0
    worst_where = ""
    for m in meshes:
        rest, def_ = field_at_defaults.deformed(m)
        for (rx, ry), (dx, dy) in zip(rest, def_):
            d = math.hypot(dx - rx, dy - ry)
            if d > worst:
                worst, worst_where = d, m["id"]
    return worst, worst_where


def rigid_fit(pts_a, pts_b):
    """最小二乘刚体（含缩放）拟合：返回 (角度°, 缩放, 非刚体残差 rms, 旋转中心)。

    旋转中心解 (I - R)·p = cb - R·ca —— 同角度但圆心差 d 的两个场，其间相对
    位移就是 θ(rad)·d，所以"角度都一样"并不等于"同一个场"。
    """
    n = len(pts_a)
    if n < 3:
        return None
    ca = (sum(p[0] for p in pts_a) / n, sum(p[1] for p in pts_a) / n)
    cb = (sum(p[0] for p in pts_b) / n, sum(p[1] for p in pts_b) / n)
    a = [(p[0] - ca[0], p[1] - ca[1]) for p in pts_a]
    b = [(p[0] - cb[0], p[1] - cb[1]) for p in pts_b]
    cross = sum(ax * by - ay * bx for (ax, ay), (bx, by) in zip(a, b))
    dot = sum(ax * bx + ay * by for (ax, ay), (bx, by) in zip(a, b))
    theta = math.atan2(cross, dot)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    sa = sum(ax * ax + ay * ay for ax, ay in a)
    ab = sum(bx * (ax * cos_t - ay * sin_t) + by * (ax * sin_t + ay * cos_t)
             for (ax, ay), (bx, by) in zip(a, b))
    scale = ab / sa if sa else 1.0
    rms = 0.0
    for (ax, ay), (bx, by) in zip(a, b):
        px = scale * (ax * cos_t - ay * sin_t)
        py = scale * (ax * sin_t + ay * cos_t)
        rms += (px - bx) ** 2 + (py - by) ** 2
    # p = R·p + t, t = cb - R·ca
    tx = cb[0] - (ca[0] * cos_t - ca[1] * sin_t)
    ty = cb[1] - (ca[0] * sin_t + ca[1] * cos_t)
    det = (1.0 - cos_t) ** 2 + sin_t ** 2
    if abs(det) < 1e-12:
        center = (float("nan"), float("nan"))
    else:
        center = (((1.0 - cos_t) * tx + sin_t * ty) / det,
                  ((1.0 - cos_t) * ty - sin_t * tx) / det)
    return math.degrees(theta), scale, math.sqrt(rms / n), center


def cross_layer_pairs(rest_tables, radius, band=None):
    """静止相距 <= radius 的跨层顶点对，按层对聚合。

    `band=(y0, y1)` 时只保留配对中点落在该 y 带（含 40px 余量）内的对 ——
    用来单独看那条被投诉的带子里究竟有没有层间错动。
    """
    lo = hi = None
    if band:
        lo, hi = band[0] - 40.0, band[1] + 40.0
    pairs = {}
    ids = list(rest_tables)
    for ai in range(len(ids)):
        for bi in range(ai + 1, len(ids)):
            a_id, b_id = ids[ai], ids[bi]
            pa, pb = rest_tables[a_id], rest_tables[b_id]
            bucket = []
            for i, (ax, ay) in enumerate(pa):
                for j, (bx, by) in enumerate(pb):
                    if abs(ax - bx) > radius or abs(ay - by) > radius:
                        continue
                    if math.hypot(ax - bx, ay - by) > radius:
                        continue
                    if lo is not None and not (lo <= (ay + by) / 2.0 <= hi):
                        continue
                    bucket.append((i, j))
            if bucket:
                pairs[(a_id, b_id)] = bucket
    return pairs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bindings")
    ap.add_argument("--pose", action="append", default=[],
                    help="逗号分隔的 Param=value，可重复")
    ap.add_argument("--band", nargs=2, type=int, default=(1056, 1076))
    ap.add_argument("--radius", type=float, default=25.0)
    ap.add_argument("--top", type=int, default=12, help="打印相对位移最大的前 N 个层对")
    args = ap.parse_args()

    doc = json.loads(Path(args.bindings).read_text(encoding="utf-8"))
    meshes = doc["meshes"]

    values = {}
    for chunk in args.pose:
        for token in chunk.split(","):
            if not token.strip():
                continue
            k, _, v = token.partition("=")
            values[k.strip()] = float(v)

    fit = min(CAPTURE_W / doc["canvas"]["width"],
              CAPTURE_H / doc["canvas"]["height"]) * 0.95
    print(f"画布 {doc['canvas']['width']:.0f}x{doc['canvas']['height']:.0f}，"
          f"捕获 {CAPTURE_W}x{CAPTURE_H} 的视图缩放 fit={fit:.4f}"
          f"（捕获 1px = 立绘 {1/fit:.2f}px）")

    base = Field(doc, {})
    w, where = selfcheck_rest(base, meshes)
    print(f"自检 1（默认参数重放 == 静止）：最大偏差 {w:.6f}px（{where}）"
          f"{'  ✅' if w < 1e-3 else '  ❌ 求值移植有误，后面读数全部作废'}")
    if w >= 1e-3:
        sys.exit(1)

    # art outside parent grid —— **单独一列，不并进上面那个偏差数**。
    # 尺子按 Core 外推越界画（#72），但外推只在「矩形之内」与「仿射/平移场」上
    # 与 Core 一致到 ≤1px；非仿射弯曲的父级键形在越界处实测还差 29px
    # （诊断 H_out_4x3_bend：夹取 118px、外推 29px）。读数落进这条名单的顶点，
    # 就不在「Core、自研播放器、尺子三方一致」的保证范围内，必须看得见。
    hits = [(m["id"], base.art_outside_parent_grid(m)) for m in meshes]
    hits = [(mid, rec) for mid, rec in hits if rec["total"]]
    print(f"art outside parent grid（越出某级变形器静止矩形的顶点）："
          f"{len(hits)} 个网格 / {sum(r['total'] for _i, r in hits)} 个顶点"
          f"{'  ✅ 全部画都在各自父级网格之内' if not hits else ''}")
    for mid, rec in hits:
        detail = ", ".join(f"{k}: {v}" for k, v in sorted(rec["levels"].items()))
        print(f"  {mid:18s} {rec['total']:4d}/{rec['vertices']:<5d} 之外（{detail}）")
    if hits:
        print("  ↑ 修法在产物侧：让父级静止矩形覆盖子孙的画（moc3_pipeline 的"
              "矩形取后代并集），把这条读数压到 0；不要靠夹取把它「量成」0。")

    pose = Field(doc, values)
    print(f"姿态：{len(values)} 个参数"
          f"{'：' + ', '.join(f'{k}={v:g}' for k, v in sorted(values.items())) if values else '（空）'}")

    rest_tables = {}
    pose_tables = {}
    for m in meshes:
        r, _ = base.deformed(m)
        rest_tables[m["id"]] = r
        _, pp = pose.deformed(m)
        pose_tables[m["id"]] = pp

    deformer_of = {m["id"]: (m.get("deformer") or "-") for m in meshes}
    print("\n每个网格在这个姿态下的等效刚体（拟合静止→形变，中心为立绘坐标）：")
    for m in meshes:
        if len(rest_tables[m["id"]]) < 3:
            continue
        fit_r = rigid_fit(rest_tables[m["id"]], pose_tables[m["id"]])
        if not fit_r:
            continue
        theta, scale, rms, center = fit_r
        cx = center[0] + doc["canvas"]["width"] / 2.0
        cy = center[1] + doc["canvas"]["height"] / 2.0
        print(f"  {m['id']:18s} def={deformer_of[m['id']]:16s} "
              f"θ={theta:7.3f}°  缩放={scale:6.4f}  rms={rms:7.3f}px  "
              f"中心=({cx:7.1f},{cy:7.1f})")

    half_h = doc["canvas"]["height"] / 2.0
    # 顶点表都是局部坐标（减过半画布），而被投诉的带子、以及所有对外报数都用
    # 立绘坐标 —— 这里换算一次，避免又出现"读数和图上对不上"的口径差。
    band_local = (args.band[0] - half_h, args.band[1] - half_h)

    def report(pairs, title):
        rows = []
        for (a_id, b_id), ij in pairs.items():
            worst = 0.0
            worst_k = None
            for i, j in ij:
                ra, da = rest_tables[a_id][i], pose_tables[a_id][i]
                rb, db = rest_tables[b_id][j], pose_tables[b_id][j]
                # 缝有没有开 = 配对**距离**的变化，不是位移之差。同一个刚体场里
                # 位移之差天生是 2·sin(θ/2)·间距（θ=10°、间距 25px 就是 4.36px），
                # 把它当成错动就是上一轮那个假证据的同一种口径错误。
                d0 = math.hypot(ra[0] - rb[0], ra[1] - rb[1])
                d1 = math.hypot(da[0] - db[0], da[1] - db[1])
                rel = abs(d1 - d0)
                if rel > worst:
                    worst, worst_k = rel, (ra, rb, d0, d1)
            rows.append((worst, a_id, b_id, len(ij), worst_k))
        rows.sort(reverse=True)
        print(f"\n{title}：{len(rows)} 个层对，"
              f"{sum(len(v) for v in pairs.values())} 对顶点"
              f"（静止相距 <= {args.radius:g}px）")
        for worst, a_id, b_id, n, wk in rows[:args.top]:
            same = "  ← 距离不变（同一场）" if worst < 1e-6 else ""
            print(f"  {worst:8.4f}px  {a_id} ↔ {b_id}  ({n} 对)"
                  f"  def {deformer_of[a_id]} / {deformer_of[b_id]}{same}")
            if wk and worst >= 1e-6:
                print(f"            最坏处静止坐标（立绘） A=({wk[0][0] + doc['canvas']['width']/2:.1f},"
                      f"{wk[0][1] + half_h:.1f}) B=({wk[1][0] + doc['canvas']['width']/2:.1f},"
                      f"{wk[1][1] + half_h:.1f})  距离 {wk[2]:.2f}→{wk[3]:.2f}")
        return rows

    all_rows = report(cross_layer_pairs(rest_tables, args.radius), "全画幅跨层相对位移")
    band_rows = report(cross_layer_pairs(rest_tables, args.radius, band=band_local),
                       f"带内跨层相对位移（立绘 y {args.band[0]}..{args.band[1]}）")

    zero = [r for r in all_rows if r[0] < 1e-6]
    print(f"\n自检 2（同场层对的距离变化必须为 0）：{len(zero)}/{len(all_rows)} 个层对读数为 0")
    nonzero = [r for r in all_rows if r[0] >= 1e-6]
    if nonzero:
        print("  非零层对（这些才是真开口）：")
        for worst, a_id, b_id, _n, _k in nonzero[:20]:
            print(f"    {worst:8.4f}px  {a_id} ↔ {b_id}")
    if band_rows and all(r[0] < 1e-6 for r in band_rows):
        print(f"  带内 y {args.band[0]}..{args.band[1]} 的 {len(band_rows)} 个层对"
              f"距离变化全部为 0 —— 那条带子的缝不是层间错动造成的")


if __name__ == "__main__":
    main()
