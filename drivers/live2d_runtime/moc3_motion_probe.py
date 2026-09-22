"""子进程入口：让官方运行时真的播放我们生成的 motion3.json，并逐帧回读参数。

为什么需要它：motion3.json 写出来、被 model3.json 引用、甚至被内核解析，都不等于
它真的在驱动参数。这里把「文件里的曲线」与「内核逐帧实际取到的值」直接对账，
误差超限就是不合格 —— 不接受「画面好像动了」这种弱判据。

用法: python -m drivers.live2d_runtime.moc3_motion_probe <model3.json>
        [--group idle] [--frames 90] [--dt 0.0333] [--mode update|motion_update]
退出码: 0 = 播放且曲线对账通过；1 = 不通过；2 = 用法/依赖错误
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List


# 段类型 -> 该段的点数（官方含义：0 线性 / 1 贝塞尔 / 2 阶跃 / 3 逆阶跃）
_SEGMENT_POINTS = {0: 1, 1: 3, 2: 1, 3: 1}


def parse_segments(segments) -> tuple:
    """[t0,v0] + (类型前缀 + 点) -> ``(初始点, [(段类型, [该段的点])])``。

    布局由官方 Haru 六个动作文件与 Meta 计数逐项对账实测确认
    （点数假设 0:1 / 1:3 / 2:1 / 3:1 在 6/6 文件上 P、S 全等）。
    """
    start = (float(segments[0]), float(segments[1]))
    tail = segments[2:]
    parsed = []
    i = 0
    while i < len(tail):
        seg_type = int(tail[i])
        if seg_type not in _SEGMENT_POINTS:
            raise ValueError(f"未知段类型 {tail[i]!r}（偏移 {i}）")
        count = _SEGMENT_POINTS[seg_type]
        flat = tail[i + 1: i + 1 + 2 * count]
        if len(flat) != 2 * count:
            raise ValueError(f"段 {seg_type} 点数不足（偏移 {i}）")
        parsed.append((seg_type,
                       [(float(flat[k]), float(flat[k + 1]))
                        for k in range(0, 2 * count, 2)]))
        i += 1 + 2 * count
    return start, parsed


def endpoints(parsed) -> list:
    """曲线的 (时间, 值) 端点折线：初始点 + 每段最后一个点。

    贝塞尔的控制点刻意不参与对账 —— 含贝塞尔的曲线本来就不走线性判据。
    """
    start, segments = parsed
    points = [start]
    for _type, seg_points in segments:
        points.append(seg_points[-1])
    return points


def expected_at(points: list, time: float) -> float:
    """全线性曲线在 time 处的取值（两端之外取端点值）。"""
    times = [p[0] for p in points]
    if time <= times[0]:
        return points[0][1]
    if time >= times[-1]:
        return points[-1][1]
    for i in range(len(times) - 1):
        t0, t1 = times[i], times[i + 1]
        if t0 <= time <= t1:
            v0, v1 = points[i][1], points[i + 1][1]
            if t1 == t0:
                return v0
            ratio = (time - t0) / (t1 - t0)
            return v0 + (v1 - v0) * ratio
    return points[-1][1]


def load_curves(manifest: Path, group: str) -> tuple:
    """从 model3.json 找到该组的 motion 文件并读出曲线与淡入淡出时长。"""
    document = json.loads(manifest.read_text(encoding="utf-8"))
    entries = (document.get("FileReferences") or {}).get("Motions") or {}
    listed = entries.get(group) or []
    if not listed:
        return None, f"model3.json 的 FileReferences.Motions 里没有组 {group!r}"
    first = listed[0]
    relative = first.get("File") if isinstance(first, dict) else first
    if not relative:
        return None, f"{group} 的首个条目没有 File 字段：{first!r}"
    motion_path = manifest.parent / relative
    if not motion_path.is_file():
        return None, f"motion 文件不存在: {motion_path}"
    motion = json.loads(motion_path.read_text(encoding="utf-8"))
    curves = {c["Id"]: c for c in motion.get("Curves") or []
              if c.get("Target") == "Parameter"}
    if not curves:
        return None, f"{relative} 里没有 Parameter 曲线"
    fade_in = float(first.get("FadeInTime") or 0.0) if isinstance(first, dict) else 0.0
    fade_out = float(first.get("FadeOutTime") or 0.0) if isinstance(first, dict) else 0.0
    return {"path": motion_path, "duration": float(
        (motion.get("Meta") or {}).get("Duration", 0.0)), "curves": curves,
        "fade_in": fade_in, "fade_out": fade_out}, None


def run(manifest_path: str, group: str, frames: int, dt: float,
        mode: str) -> dict:
    import pygame

    from drivers.live2d_runtime.native import CubismRenderer

    manifest = Path(manifest_path)
    spec, problem = load_curves(manifest, group)
    if spec is None:
        return {"ok": False, "error": problem}

    pygame.display.init()
    pygame.display.gl_set_attribute(pygame.GL_ALPHA_SIZE, 8)
    pygame.display.set_mode((64, 64), pygame.OPENGL | pygame.DOUBLEBUF)
    import live2d.v3 as sdk
    sdk.init()
    sdk.glInit()

    renderer = CubismRenderer(64, 64)
    try:
        renderer.load_model(manifest_path)
        model = renderer._model
        declared = {str(k): v for k, v in (model.GetMotions() or {}).items()}
        if group not in declared:
            return {"ok": False,
                    "error": f"内核没有把 {group!r} 当作动作组读进来："
                             f"{sorted(declared)}",
                    "motions_seen": declared}

        ids = [str(i) for i in renderer.parameter_ids]
        index_of = {pid: ids.index(pid) for pid in spec["curves"] if pid in ids}
        missing = sorted(set(spec["curves"]) - set(index_of))
        if not index_of:
            return {"ok": False,
                    "error": "motion 曲线里的参数在模型里一个都不存在",
                    "curves": sorted(spec["curves"])}

        model.StopAllMotions()
        model.SetAutoBlinkEnable(False)
        model.SetAutoBreathEnable(False)
        rest = {pid: model.GetParameterValue(index_of[pid]) for pid in index_of}
        model.StartMotion(group, 0)

        inner = model._model
        # 逐曲线还原段布局：只有全线性曲线能做逐帧数值对账，
        # 含贝塞尔/阶跃的曲线退化为「是否真的动了」的弱判据。
        traces = {}
        for pid in index_of:
            start, parsed = parse_segments(spec["curves"][pid]["Segments"])
            traces[pid] = {
                "points": endpoints((start, parsed)),
                "linear": all(seg_type == 0 for seg_type, _ in parsed),
            }
        observed = {pid: [] for pid in index_of}
        errors = {pid: 0.0 for pid in index_of}
        # 逐帧误差的时间分布：用于分辨「恒定时间偏移」「淡入未收敛」与
        # 「插值方式不同」——三者的曲线形状完全不同。
        per_frame_errors: List[float] = []
        duration = spec["duration"] or dt * frames
        for step in range(frames):
            if mode == "motion_update":
                inner.UpdateMotion(dt)
                inner.Update(0.0)
            elif mode == "framework":
                # LAppModel.Update() 里被注释掉的那段官方流程：动作只在
                # LoadParameters/SaveParameters 括起来的窗口里生效
                inner.LoadParameters()
                inner.UpdateMotion(dt)
                inner.SaveParameters()
                inner.Update(dt)
            else:
                inner.Update(dt)
            elapsed = (step + 1) * dt
            looped = elapsed % duration if duration > 0 else elapsed
            # 淡入/淡出窗口里参数是「上一姿态 ↔ 曲线值」的混合，逐帧对账
            # 无意义，只跳过这些窗口；其余时刻必须对上。
            fading = (looped < spec["fade_in"] + 1e-9
                      or duration - looped < spec["fade_out"] - 1e-9)
            frame_error = 0.0
            for pid in index_of:
                value = model.GetParameterValue(index_of[pid])
                observed[pid].append(value)
                if traces[pid]["linear"] and not fading:
                    # 允许一帧内的时间对齐偏差：内核的取值时刻与「第 n 次
                    # Update 之后」的对应关系不是文件语义的一部分，
                    # 但错两帧以上就说明曲线本身不对。
                    error = min(abs(value - expected_at(
                        traces[pid]["points"], (looped + offset) % duration))
                        for offset in (-dt, 0.0, dt))
                    errors[pid] = max(errors[pid], error)
                    frame_error = max(frame_error, error)
            per_frame_errors.append(round(frame_error, 3))
        per_second = [max(per_frame_errors[i:i + max(1, int(round(1.0 / dt)))])
                      for i in range(0, len(per_frame_errors),
                                     max(1, int(round(1.0 / dt))))] \
            if dt > 0 else []

        curves = {}
        for pid in index_of:
            values = observed[pid]
            low = min(values)
            high = max(values)
            file_values = [v for _, v in traces[pid]["points"]]
            curves[pid] = {
                "rest": rest[pid],
                "observed_min": low,
                "observed_max": high,
                "observed_range": high - low,
                "file_min": min(file_values),
                "file_max": max(file_values),
                "all_linear": traces[pid]["linear"],
                "max_abs_error": errors[pid],
                "moved": (high - low) > 1e-4,
            }
        moved = [pid for pid, data in curves.items() if data["moved"]]
        # 全线性曲线要逐帧对账：行程对了但时刻错了同样不合格。
        tolerance = max(1e-3, 0.02 * max(
            (data["file_max"] - data["file_min"]) for data in curves.values()))
        mismatched = [pid for pid, data in curves.items()
                      if data["all_linear"] and data["max_abs_error"] > tolerance]
        return {
            "ok": bool(moved) and len(moved) == len(curves) and not mismatched,
            "tolerance": tolerance,
            "mismatched": mismatched,
            "error_per_second": per_second,
            "group": group,
            "motion_file": str(spec["path"]),
            "duration": duration,
            "frames": frames,
            "mode": mode,
            "curves": curves,
            "missing_parameters": missing,
            "motion_finished": bool(model.IsMotionFinished()),
        }
    finally:
        renderer.close()
        sdk.glRelease()
        sdk.dispose()
        pygame.display.quit()


def main(argv) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--group", default="idle")
    parser.add_argument("--frames", type=int, default=90)
    parser.add_argument("--dt", type=float, default=1.0 / 30.0)
    parser.add_argument("--mode", choices=("update", "motion_update", "framework"),
                        default="update")
    args = parser.parse_args(argv[1:])

    try:
        payload = run(args.manifest, args.group, args.frames, args.dt, args.mode)
    except Exception as exc:      # Core 崩溃由调用方按退出码归类
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print("LIVE2D_MOTION=" + json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
