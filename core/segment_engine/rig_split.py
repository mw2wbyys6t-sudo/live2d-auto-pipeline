#!/usr/bin/env python3
"""把「一个部件一块」的掩码拆成**可绑骨骼的关节段**。

为什么要拆：现有分层按「语义部件」切（arm_L 是一条完整手臂、leg_L 是一条
完整腿），这种粒度只能整体平移——整条手臂一起转，肘和膝就没有弯折点。
绑骨骼需要的是**关节链**：上臂/前臂、大腿/小腿，每段一根骨骼、接点落在
关节上。

拆分依据是几何的，不猜姿态：

* 取该部件像素的主轴（PCA 第一主成分），沿主轴把像素投影成一维坐标 ``t``；
* 在 ``t`` 的指定分位处切一刀，切口垂直于主轴 —— 所以抬手、斜站都不会把
  切口切歪；
* 切口位置 = 关节。关节两侧的端点质心 = 骨骼枢轴。

「近端/远端」不写死为「上/下」，而是**离躯干更近的一端为近端**：这样
横举的手臂、斜伸的腿也能正确判出肩→肘→腕的顺序。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

#: 需要按关节拆分的部件 -> (近端名, 远端名)
LIMB_JOINTS: Dict[str, Tuple[str, str]] = {
    "arm_L": ("upper_arm_L", "forearm_L"),
    "arm_R": ("upper_arm_R", "forearm_R"),
    "leg_L": ("thigh_L", "shin_L"),
    "leg_R": ("thigh_R", "shin_R"),
}

#: 需要按段拆分的部件 -> 段数（长发要分段才能做出摆动）
HAIR_SEGMENTS: Dict[str, int] = {
    "hair_back": 3,
    "hair_front": 2,
}

#: 部件 -> 所属骨骼父节点（写入报告，方便直接建骨骼树）
PARENT_OF: Dict[str, str] = {
    "upper_arm_L": "chest",
    "upper_arm_R": "chest",
    "forearm_L": "upper_arm_L",
    "forearm_R": "upper_arm_R",
    "thigh_L": "hip",
    "thigh_R": "hip",
    "shin_L": "thigh_L",
    "shin_R": "thigh_R",
    "hand_L": "forearm_L",
    "hand_R": "forearm_R",
    # 躯干三段：胸（上衣）→ 腰（腰带）→ 胯（下装），本仓库的语义分层已经
    # 天然给出了这三段，不需要再额外切分。
    "clothes_top": "chest",
    "waistband": "hip",
    "clothes_bottom": "hip",
    "collar": "clothes_top",
    "cuff_L": "forearm_L",
    "cuff_R": "forearm_R",
    "skirt_hem_L": "clothes_bottom",
    "skirt_hem_R": "clothes_bottom",
    "sock_L": "shin_L",
    "sock_R": "shin_R",
    "neck": "chest",
    # 头面：全部挂 head，眼睛/嘴/眉之间不需要骨骼层级
    "face": "head",
    "eyebrow_L": "head",
    "eyebrow_R": "head",
    "eyewhite_left": "head",
    "eyewhite_right": "head",
    "irides_left": "head",
    "irides_right": "head",
    "mouth": "head",
    "nose": "head",
    "accessories": "head",
}

#: 太小的部件不值得拆：拆了也只是一堆没有意义的碎片。
MIN_SPLIT_PIXELS = 120
#: 拆出来的一段少于这个像素数，说明拆分没意义，保留整块。
MIN_SEGMENT_PIXELS = 40


def _points(mask: np.ndarray) -> np.ndarray:
    """掩码像素坐标，返回 ``(N, 2)`` 的 ``(x, y)``。"""
    ys, xs = np.nonzero(mask)
    return np.stack([xs, ys], axis=1).astype(np.float64)


def principal_axis(points: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """返回 ``(质心, 主轴单位向量)``；主轴方向未定（符号待定）。"""
    centroid = points.mean(axis=0)
    centered = points - centroid
    # 用协方差矩阵的特征向量，比最小二乘拟合对噪声更稳
    cov = np.cov(centered.T)
    vals, vecs = np.linalg.eigh(cov)
    axis = vecs[:, int(np.argmax(vals))]
    norm = float(np.hypot(axis[0], axis[1]))
    if norm <= 1e-9:
        return centroid, np.array([0.0, 1.0])
    return centroid, axis / norm


def _orient_away_from(
    points: np.ndarray, centroid: np.ndarray, axis: np.ndarray,
    near: Optional[Tuple[float, float]],
) -> np.ndarray:
    """让主轴指向「远离 near」的方向，于是 t 增大 = 远离躯干。

    ``near`` 为空时退回「指向下方」，这是立绘里肢体最常见的朝向。
    """
    t = (points - centroid) @ axis
    lo_t, hi_t = np.percentile(t, 3), np.percentile(t, 97)
    lo_pts = points[t <= lo_t]
    hi_pts = points[t >= hi_t]
    lo_c = lo_pts.mean(axis=0) if len(lo_pts) else centroid - axis
    hi_c = hi_pts.mean(axis=0) if len(hi_pts) else centroid + axis

    if near is None:
        # 朝下：让 hi 端在更下方
        return axis if hi_c[1] >= lo_c[1] else -axis

    near_v = np.array(near, dtype=np.float64)
    d_lo = float(np.hypot(*(lo_c - near_v)))
    d_hi = float(np.hypot(*(hi_c - near_v)))
    # 近端应该离 near 更近
    return axis if d_lo <= d_hi else -axis


def _cut_position(
    points: np.ndarray, axis: np.ndarray, cut_t: float, band: float = 1.5
) -> Optional[Tuple[float, float]]:
    """切口中心：落在切线附近的像素质心，作为关节坐标。"""
    t = points @ axis
    near = points[np.abs(t - cut_t) <= band]
    if len(near) == 0:
        return None
    c = near.mean(axis=0)
    return (float(c[0]), float(c[1]))


def _end_pivot(
    points: np.ndarray, axis: np.ndarray, side: str, frac: float = 0.12
) -> Optional[Tuple[float, float]]:
    """沿主轴取最靠某一端的像素质心，作为该端骨骼的枢轴。"""
    t = points @ axis
    lo_t, hi_t = np.percentile(t, 2), np.percentile(t, 98)
    span = max(1e-6, hi_t - lo_t)
    if side == "low":
        sel = points[t <= lo_t + span * frac]
    else:
        sel = points[t >= hi_t - span * frac]
    if len(sel) == 0:
        return None
    c = sel.mean(axis=0)
    return (float(c[0]), float(c[1]))


def split_limb_at_joint(
    mask: np.ndarray,
    ratio: float = 0.5,
    near: Optional[Tuple[float, float]] = None,
) -> Optional[Dict]:
    """沿主轴在 ``ratio`` 处切开，返回近端/远端两块与关节坐标。

    Returns:
        ``{"proximal", "distal", "joint", "pivot_proximal",
        "pivot_distal", "proximal_ratio"}``；部件太小或切出空段时返回
        ``None``（调用方应保留整块，不要硬拆）。
    """
    mask = np.asarray(mask, dtype=bool)
    total = int(mask.sum())
    if total < MIN_SPLIT_PIXELS:
        return None

    pts = _points(mask)
    centroid, axis = principal_axis(pts)
    axis = _orient_away_from(pts, centroid, axis, near)

    t = pts @ axis
    lo_t, hi_t = np.percentile(t, 2), np.percentile(t, 98)
    cut_t = lo_t + (hi_t - lo_t) * float(ratio)

    prox_sel = t <= cut_t
    dist_sel = ~prox_sel
    if int(prox_sel.sum()) < MIN_SEGMENT_PIXELS or int(dist_sel.sum()) < MIN_SEGMENT_PIXELS:
        return None

    proximal = np.zeros_like(mask)
    distal = np.zeros_like(mask)
    proximal[pts[prox_sel, 1].astype(int), pts[prox_sel, 0].astype(int)] = True
    distal[pts[dist_sel, 1].astype(int), pts[dist_sel, 0].astype(int)] = True

    joint = _cut_position(pts, axis, cut_t)
    return {
        "proximal": proximal,
        "distal": distal,
        "joint": joint,
        "pivot_proximal": _end_pivot(pts, axis, "low"),
        # 远端骨骼的枢轴就是关节本身
        "pivot_distal": joint,
        "proximal_ratio": float(prox_sel.sum()) / float(total),
    }


def split_into_segments(
    mask: np.ndarray,
    segments: int,
    near: Optional[Tuple[float, float]] = None,
    axis_hint: str = "vertical",
) -> Optional[List[Dict]]:
    """沿主轴等分成 ``segments`` 段（头发摆动用），从近端到远端排列。

    ``axis_hint="vertical"`` 时强制用竖直方向切，这是有意的：头发是「上端
    固定、发梢摆动」的结构，按 PCA 主轴切会让短而宽的刘海被横向切开，
    得到左右两半而不是上下两段，摆动方向就错了。

    Returns:
        ``[{"mask", "pivot", "cut", "index", "pixels"}, ...]``；无法有效分段
        时返回 ``None``。**所有像素都会被分到某一段**，不丢像素。
    """
    mask = np.asarray(mask, dtype=bool)
    total = int(mask.sum())
    if segments < 2 or total < MIN_SPLIT_PIXELS:
        return None

    pts = _points(mask)
    centroid, axis = principal_axis(pts)
    if axis_hint == "vertical":
        # 头发固定「自上而下」：第 1 段一定是最靠近发根的那一段。
        # 不能走 near 判向——传进来的 near 通常是躯干中心，会让发根/发梢反掉。
        axis = np.array([0.0, 1.0])
    else:
        axis = _orient_away_from(pts, centroid, axis, near)

    t = pts @ axis
    # 用真实极值而不是分位数：分段必须覆盖全部像素，用 2/98 分位会把
    # 两端的发丝整条丢掉（实测 hair_back 27414 px 分成 3 段只剩 26316）。
    lo_t, hi_t = float(t.min()), float(t.max())
    if hi_t - lo_t < 1e-6:
        return None
    edges = [lo_t + (hi_t - lo_t) * (i / segments) for i in range(segments + 1)]

    out: List[Dict] = []
    prev_pivot = _end_pivot(pts, axis, "low")
    for i in range(segments):
        last = i == segments - 1
        sel = (t >= edges[i]) & ((t <= edges[i + 1]) if last else (t < edges[i + 1]))
        if int(sel.sum()) < 1:
            return None
        seg = np.zeros_like(mask)
        seg[pts[sel, 1].astype(int), pts[sel, 0].astype(int)] = True
        cut = _cut_position(pts, axis, edges[i + 1]) if not last else None
        out.append({
            "mask": seg,
            "pivot": prev_pivot,
            "cut": cut,
            "index": i + 1,
            "pixels": int(seg.sum()),
        })
        prev_pivot = cut or prev_pivot
    return out


def _role_of(name: str) -> str:
    """去掉侧别后缀，得到骨骼角色名（``upper_arm_L`` -> ``upper_arm``）。"""
    for suffix in ("_L", "_R", "_left", "_right"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def build_rig_parts(
    part_masks: Dict[str, np.ndarray],
    near: Optional[Tuple[float, float]] = None,
) -> Tuple[Dict[str, np.ndarray], Dict[str, Dict], List[str]]:
    """把语义部件扩成绑定用的关节段。

    Args:
        part_masks: 部件名 -> 布尔掩码。
        near: 躯干参考点（通常是脸框中线），用于判定肢体的近端/远端。
            为空时按「主轴朝下」判，直立立绘同样正确。

    Returns:
        ``(parts, meta, warnings)``。``meta[name]`` 含
        ``role/parent/side/pivot/joint/source``，可直接喂给骨骼树与网格生成。
    """
    parts: Dict[str, np.ndarray] = {}
    meta: Dict[str, Dict] = {}
    warnings: List[str] = []

    for raw_name, raw_mask in part_masks.items():
        name = str(raw_name)
        mask = np.asarray(raw_mask, dtype=bool)
        if not mask.any():
            continue

        if name in LIMB_JOINTS:
            prox_name, dist_name = LIMB_JOINTS[name]
            split = split_limb_at_joint(mask, 0.5, near)
            if split is None:
                parts[name] = mask
                meta[name] = {
                    "role": _role_of(name),
                    "parent": PARENT_OF.get(name),
                    "source": name,
                    "pixels": int(mask.sum()),
                }
                warnings.append(f"{name} 太小或切出空段，未按关节拆分（保留整块）")
                continue
            side = "L" if name.endswith("_L") else ("R" if name.endswith("_R") else None)
            parts[prox_name] = split["proximal"]
            parts[dist_name] = split["distal"]
            meta[prox_name] = {
                "role": _role_of(prox_name),
                "parent": PARENT_OF.get(prox_name),
                "side": side,
                "source": name,
                "pivot": split["pivot_proximal"],
                "joint": split["joint"],
                "pixels": int(split["proximal"].sum()),
            }
            meta[dist_name] = {
                "role": _role_of(dist_name),
                "parent": prox_name,
                "side": side,
                "source": name,
                "pivot": split["pivot_distal"],
                "joint": None,
                "pixels": int(split["distal"].sum()),
            }
            continue

        seg_count = HAIR_SEGMENTS.get(name)
        if seg_count:
            segs = split_into_segments(mask, seg_count, near)
            if segs is None:
                parts[name] = mask
                meta[name] = {
                    "role": _role_of(name),
                    "parent": PARENT_OF.get(name),
                    "source": name,
                    "pixels": int(mask.sum()),
                }
                warnings.append(f"{name} 无法按主轴分段（保留整块）")
                continue
            for seg in segs:
                seg_name = f"{name}_{seg['index']}"
                parts[seg_name] = seg["mask"]
                meta[seg_name] = {
                    "role": "hair_segment",
                    "parent": f"{name}_{seg['index'] - 1}" if seg["index"] > 1 else "head",
                    "side": None,
                    "source": name,
                    "pivot": seg["pivot"],
                    "joint": seg["cut"],
                    "pixels": seg["pixels"],
                }
            continue

        parts[name] = mask
        meta[name] = {
            "role": _role_of(name),
            "parent": PARENT_OF.get(name),
            "source": name,
            "pixels": int(mask.sum()),
        }

    return parts, meta, warnings
