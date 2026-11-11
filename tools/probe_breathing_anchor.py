"""【已作废】反推 Breathing 链的「有效锚点摆幅」—— 它的猜测被实测证伪了。

留着只为记录这条死路：本脚本假定差异出在**输入归一化的量纲**，于是拿不同锚点
摆幅去逼近 Core 轨迹。结论是「没有任何单一摆幅能对上」，因为前提就错了 ——
它硬编码了 radius/mobility/delay/归一化 ±90，而真实 physics3.json 里
Breathing 的归一化是 Position ±10、输入 Type 是 X（不是 Angle）。

真正的机制由 tools/probe_breathing_split.py 的三段实验钉死：
  * 把输出 Weight 改成 100 后 Core 与 TS **逐位相同（0.000000）**
    ⇒ Verlet 动力学与 0..1 参数归一化都是对的，量纲猜测被证伪；
  * 差异只在 weight<100 的混合基准 previous 取谁：内核取「本帧物理之前的值」，
    物理自己的输出**不回流**；TS 原先取上一帧物理输出，于是峰值差 80%。

用法（仅作历史复现）：python tools/probe_breathing_anchor.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

DEG_TO_RAD = 0.017453292
BASE_FPS = 30
AERODYNAMIC_DRAG = 5.0


def wrap_pi(a):
    while a < -math.pi:
        a += 2 * math.pi
    while a > math.pi:
        a -= 2 * math.pi
    return a


def signed_angle(ax, ay, bx, by):
    return wrap_pi(math.atan2(ay, ax) - math.atan2(by, bx))


def simulate(anchor_amplitude, frames=40, dt=1 / 60.0,
             radius=20.0, mobility=0.95, delay=0.99, acceleration=2.0,
             gravity=(0.0, 1.0), base=0.5, step=1.0):
    """返回逐帧的输出角（弧度，未乘 Scale）。"""
    x = y = 0.0
    vx = vy = 0.0
    old_x = old_y = 0.0
    gx, gy = gravity
    seg_x, seg_y = 0.0, radius
    trace = []
    # 归一化输入：base 帧用 base，step 帧用 step；内核晚一帧 → 用上一帧的值
    previous = base
    for frame in range(frames):
        current = step if frame >= 1 else base
        # 归一化（参数 0..1，默认 0.5，归一化 ±90，Reflect=false → 取负）
        value = (previous - 0.5)
        normalized = value * (90.0 - 0.0) / (1.0 - 0.5) + 0.0
        anchor_x = -normalized * anchor_amplitude / 90.0
        previous = current
        # Verlet
        old_x, old_y = x, y
        force_x = gx * acceleration
        force_y = gy * acceleration
        tilt = signed_angle(gx, gy, gx, gy) / AERODYNAMIC_DRAG
        tc, ts = math.cos(tilt), math.sin(tilt)
        rx = tc * seg_x - ts * seg_y
        ry = ts * seg_x + tc * seg_y
        seg_x, seg_y = rx, ry
        d = delay * dt * BASE_FPS
        nx = anchor_x + seg_x + vx * d + force_x * d * d
        ny = 0.0 + seg_y + vy * d + force_y * d * d
        dx, dy = nx - anchor_x, ny - 0.0
        length = math.hypot(dx, dy)
        if length > 1e-9:
            nx = anchor_x + dx / length * radius
            ny = 0.0 + dy / length * radius
        x, y = nx, ny
        if d != 0:
            vx = ((x - old_x) / d) * mobility
            vy = ((y - old_y) / d) * mobility
        # 输出：段 (child − anchor) 与 (0,1) 的夹角（内核语义：signedAngle(seg, ref)）
        sx, sy = x - anchor_x, y - 0.0
        angle = signed_angle(sx, sy, 0.0, 1.0)
        trace.append(angle * 0.3)
    return trace


def main() -> int:
    core = [float(x) for x in sys.argv[1].split(",")] if len(sys.argv) > 1 else None
    print("锚点摆幅 → 与 Core 轨迹的偏差（越小越像内核）")
    for amplitude in (2.0, 4.5, 9.0, 20.0, 45.0, 90.0):
        trace = simulate(amplitude)
        if core:
            n = min(len(core), len(trace))
            dev = max(abs(a - b) for a, b in zip(core[:n], trace[:n]))
            peak_c = max(abs(v) for v in core[:n]) or 1e-9
            print(f"  摆幅 {amplitude:6.1f} → 最大偏差 {dev:.5f}"
                  f"（相对 Core 峰值 {dev / peak_c * 100:5.1f}%）"
                  f"  峰值 {max(abs(v) for v in trace[:n]):.4f}")
        else:
            print(f"  摆幅 {amplitude:6.1f} → 峰值 "
                  f"{max(abs(v) for v in trace):.4f}  前 8 帧 "
                  f"{[round(v, 4) for v in trace[:8]]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
