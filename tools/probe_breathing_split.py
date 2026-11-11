"""Breathing 链：把「输出权重混合」与「Verlet 动力学」分开量。

背景：6 条满权重链与官方 Core 逐位一致（0.0000），只有 Breathing 差 80%。
这条链唯一的不同是输出 Weight=50（内核走 previous*(1-w)+raw*w）。

两个可判定的实验：
  A. 反推 —— 若内核真是 p_n = 0.5*p_{n-1} + 0.5*raw_n，那么
     raw_n = 2*p_n - p_{n-1}。从 Core 的输出轨迹可以**精确还原**它的 raw 序列。
  B. 换权重 —— 把 physics3.json 里 Breathing 的输出 Weight 改成 100 再跑一遍
     Core 与 TS：这时两边都直接赋值，比的就是纯动力学。

同时逐帧记录 Core 里的 ParamBreath，确认输入没有被内核的自动呼吸覆盖。

用法：python tools/probe_breathing_split.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import probe_physics_parity as parity

FRAMES = 30
DT = 1 / 60.0


def core_multi(manifest: str, src: str, watch: list[str], base: float,
               step: float, drive: dict | None = None) -> dict:
    """Core 逐帧轨迹，同时记录多个参数（输入是否被覆盖一眼可见）。

    drive：每帧在 update 之前额外写入的参数（模拟 motion/用户拖动）。
    """
    import pygame
    import live2d.v3 as sdk

    from drivers.live2d_runtime.native import CubismModelSession

    pygame.display.init()
    pygame.display.gl_set_attribute(pygame.GL_ALPHA_SIZE, 8)
    pygame.display.set_mode((64, 64), pygame.OPENGL | pygame.DOUBLEBUF)
    sdk.init()
    sdk.glInit()
    session = CubismModelSession(64, 64)
    try:
        session.load_model(manifest)
        session.set_auto_blink(False)
        session.set_auto_breath(False)
        ids = [str(i) for i in session.parameter_ids]
        idx = {name: ids.index(name) for name in watch if name in ids}

        def advance(value):
            session.set_parameter(src, value)
            for name, v in (drive or {}).items():
                session.set_parameter(name, v)
            session.update(DT)
            return {name: session.get_parameter_value(i)
                    for name, i in idx.items()}

        for _ in range(FRAMES):
            advance(base)
        baseline = {name: session.get_parameter_value(i)
                    for name, i in idx.items()}
        trace = [advance(step) for _ in range(FRAMES)]
        return {"ok": True, "baseline": baseline, "trace": trace,
                "missing": [n for n in watch if n not in idx]}
    finally:
        session.close()
        sdk.glRelease()
        sdk.dispose()
        pygame.display.quit()


def _dev(a, b):
    n = min(len(a), len(b))
    return max((abs(x - y) for x, y in zip(a[:n], b[:n])), default=float("nan"))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="breath_split_") as tmp:
        workdir = Path(tmp)
        manifest, physics = parity._build(workdir)
        doc = json.loads(physics.read_text(encoding="utf-8"))
        model = json.loads(manifest.read_text(encoding="utf-8"))
        ranges, defaults = {}, {}
        for spec in model.get("Parameters") or []:
            name = str(spec.get("Id") or "")
            if not name:
                continue
            ranges[name] = {"min": float(spec.get("Min", -30.0)),
                            "max": float(spec.get("Max", 30.0))}
            defaults[name] = float(spec.get("Value", spec.get("Default", 0.0)))

        print("=== Breathing setting (verbatim) ===")
        for setting in doc.get("PhysicsSettings", []):
            if setting.get("Id") == "Breathing":
                print(json.dumps(setting, ensure_ascii=False, indent=2))
        print(f"ParamBreath range={ranges.get('ParamBreath')} "
              f"default={defaults.get('ParamBreath')}")
        print(f"ParamShoulderL range={ranges.get('ParamShoulderL')} "
              f"default={defaults.get('ParamShoulderL')}")

        src, dest = "ParamBreath", "ParamShoulderL"
        base = defaults.get(src, 0.0)
        step = ranges[src]["max"] or 1.0

        print("\n=== A. original doc (Weight=50) ===")
        core = core_multi(str(manifest), src, [src, dest], base, step)
        if not core.get("ok"):
            print(f"Core failed: {core}")
            return 2
        ts = parity._run_ts(doc, ranges, defaults, src, dest, FRAMES, DT, step)
        ct = [row[dest] for row in core["trace"]]
        tt = ts["trace"]
        print(f"Core input per frame (first 6): "
              f"{[round(row[src], 5) for row in core['trace'][:6]]}")
        print(f"Core baseline={core['baseline']}  TS baseline={ts['baseline']:.6f}")
        print(f"Core out[:8]={[round(v, 5) for v in ct[:8]]}")
        print(f"TS   out[:8]={[round(v, 5) for v in tt[:8]]}")
        print(f"dev shift0={_dev(ct, tt):.6f}  shift1={_dev(ct[1:], tt):.6f}")
        # 反推 Core 的 raw（假设 p_n = 0.5 p_{n-1} + 0.5 raw_n）
        prev = core["baseline"][dest]
        recovered = []
        for v in ct:
            recovered.append(2.0 * v - prev)
            prev = v
        print(f"Core recovered raw[:8]={[round(v, 5) for v in recovered[:8]]}")

        print("\n=== B. Breathing outputs Weight=100 (pure dynamics) ===")
        for setting in doc.get("PhysicsSettings", []):
            if setting.get("Id") == "Breathing":
                for entry in setting.get("Output", []):
                    entry["Weight"] = 100.0
        physics.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        core_b = core_multi(str(manifest), src, [src, dest], base, step)
        ts_b = parity._run_ts(doc, ranges, defaults, src, dest, FRAMES, DT, step)
        cb = [row[dest] for row in core_b["trace"]]
        tb = ts_b["trace"]
        print(f"Core out[:8]={[round(v, 5) for v in cb[:8]]}")
        print(f"TS   out[:8]={[round(v, 5) for v in tb[:8]]}")
        print(f"dev shift0={_dev(cb, tb):.6f}  shift1={_dev(cb[1:], tb):.6f}")
        print(f"recovered-A vs Core-B dev={_dev(recovered, cb):.6f}")
        print(f"Core-B vs TS-A dev={_dev(cb, tt):.6f}")

        print("\n=== C. Weight=50 + 每帧外部写入 ParamShoulderL=0.4 ===")
        print("判定内核混合基准 previous 取谁：")
        print("  old=0        → p_n = 0.5*raw_n")
        print("  old=外部写入 → p_n = 0.5*0.4 + 0.5*raw_n")
        print("  old=上帧输出 → p_n = 0.5*p_{n-1} + 0.5*raw_n（TS 现在的做法）")
        drive_value = 0.4
        for setting in doc.get("PhysicsSettings", []):
            if setting.get("Id") == "Breathing":
                for entry in setting.get("Output", []):
                    entry["Weight"] = 50.0
        physics.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        core_c = core_multi(str(manifest), src, [src, dest], base, step,
                            drive={dest: drive_value})
        ts_c = parity._run_ts(doc, ranges, defaults, src, dest, FRAMES, DT,
                              step, drive={dest: drive_value})
        cc = [row[dest] for row in core_c["trace"]]
        tc = ts_c["trace"]
        # B 段权重 100 = 直接赋值，所以 cb 就是真 raw 序列
        pred_zero = [0.5 * v for v in cb]
        pred_drive = [0.5 * drive_value + 0.5 * v for v in cb]
        print(f"Core baseline={core_c['baseline'][dest]:.6f} "
              f"TS baseline={ts_c['baseline']:.6f}")
        print(f"Core out[:8]={[round(v, 5) for v in cc[:8]]}")
        print(f"TS   out[:8]={[round(v, 5) for v in tc[:8]]}")
        print(f"pred old=0   [:8]={[round(v, 5) for v in pred_zero[:8]]}")
        print(f"pred old=0.4 [:8]={[round(v, 5) for v in pred_drive[:8]]}")
        print(f"Core vs pred(old=0)   dev={_dev(cc, pred_zero):.6f}")
        print(f"Core vs pred(old=0.4) dev={_dev(cc, pred_drive):.6f}")
        print(f"Core vs TS            dev={_dev(cc, tc):.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())