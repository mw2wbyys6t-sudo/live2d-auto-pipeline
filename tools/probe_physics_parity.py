"""浏览器物理（TS 的 Verlet）与官方内核的**逐帧轨迹**对比。

判据：同一份 physics3.json、同一个输入阶跃，TS 引擎与官方 Core 的输出参数
轨迹逐帧比较。报告峰值偏差、稳态偏差与基线偏差。

用法：python tools/probe_physics_parity.py [--frames 60]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np
from PIL import Image

from live2d_builder.pipeline import Live2DBuilder


def _layer(w=300, h=420, color=(200, 120, 100, 255), box=None):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    arr = np.array(img)
    x0, y0, x1, y1 = box or (30, 30, w - 30, h - 30)
    arr[y0:y1, x0:x1] = color
    return Image.fromarray(arr, "RGBA")


def _layers():
    return {
        "face": _layer(box=(90, 40, 210, 200), color=(255, 220, 180, 255)),
        "hair_back": _layer(box=(70, 20, 230, 300), color=(80, 80, 200, 255)),
        "hair_front": _layer(box=(80, 30, 220, 160), color=(60, 60, 180, 255)),
        "clothes_top": _layer(box=(70, 210, 230, 390), color=(50, 150, 50, 255)),
    }


def _build(workdir: Path):
    builder = Live2DBuilder(output_dir=str(workdir), character_name="phys")
    builder.build(_layers())
    manifest = next(workdir.glob("*.model3.json"))
    physics = next(workdir.glob("*physics*.json"), None)
    if physics is None:
        refs = json.loads(manifest.read_text(encoding="utf-8")).get("FileReferences", {})
        name = refs.get("Physics")
        if name:
            physics = workdir / name
    return manifest, physics


def _chains(physics_doc):
    out = []
    for setting in physics_doc.get("PhysicsSettings", []):
        for entry in setting.get("Output", []):
            dest = (entry.get("Destination") or {}).get("Id")
            if not dest:
                continue
            for inp in setting.get("Input", []):
                src = (inp.get("Source") or {}).get("Id")
                if src:
                    out.append((src, dest, setting.get("Id"), entry.get("Type")))
    return out


def _run_core(manifest, src, dest, frames, dt, base=None):
    cmd = [sys.executable, str(ROOT / "tools" / "probe_physics_trace.py"),
           str(manifest), "--input", src, "--output", dest,
           "--frames", str(frames), "--dt", str(dt)]
    if base is not None:
        cmd += ["--base", str(base)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          timeout=600)
    for line in proc.stdout.splitlines():
        if line.startswith("PHYSICS_TRACE="):
            return json.loads(line[len("PHYSICS_TRACE="):])
    return {"ok": False, "error": proc.stdout[-500:] + proc.stderr[-500:]}


def _run_ts(physics_doc, ranges, defaults, src, dest, frames, dt, step_value,
            drive=None):
    config = {
        "physics3": physics_doc,
        "ranges": ranges,
        "defaults": defaults,
        "input": src,
        "output": dest,
        "frames": frames,
        "dt": dt,
        "stepValue": step_value,
        "base": defaults.get(src, 0.0),
        "drive": drive or {},
    }
    env = dict(os.environ,
               PHYSICS_CONFIG=json.dumps(config),
               PHYSICS_TS=str(ROOT / "web" / "lib" / "live2d-physics.ts"))
    proc = subprocess.run(
        ["node", str(ROOT / "tools" / "physics_trace_harness.mjs")],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=600)
    for line in proc.stdout.splitlines():
        if line.startswith("PHYSICS_TRACE="):
            payload = json.loads(line[len("PHYSICS_TRACE="):])
            payload["ok"] = True
            return payload
    return {"ok": False, "error": proc.stdout[-800:] + proc.stderr[-800:]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--dt", type=float, default=1.0 / 60.0)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="phys_parity_") as tmp:
        workdir = Path(tmp)
        manifest, physics = _build(workdir)
        if physics is None or not physics.is_file():
            print("产物里没有 physics3.json")
            return 2
        physics_doc = json.loads(physics.read_text(encoding="utf-8"))
        chains = _chains(physics_doc)
        if not chains:
            print("physics3 里没有可验的链")
            return 2
        model = json.loads(manifest.read_text(encoding="utf-8"))
        ranges, defaults = {}, {}
        raw_params = model.get("Parameters") or []
        if isinstance(raw_params, dict):
            raw_params = [{"Id": k, **(v if isinstance(v, dict) else {})}
                          for k, v in raw_params.items()]
        for spec in raw_params:
            name = str(spec.get("Id") or spec.get("Name") or "")
            if not name:
                continue
            ranges[name] = {"min": float(spec.get("Min", -30.0)),
                            "max": float(spec.get("Max", 30.0)),
                            "default": float(spec.get("Default", 0.0))}
            defaults[name] = float(spec.get("Default", 0.0))

        print(f"物理链 {len(chains)} 条，逐帧对比前 {args.frames} 帧（dt={args.dt:.5f}）")
        worst = None
        for src, dest, system, kind in chains:
            if src not in ranges or dest not in ranges:
                continue
            step_value = ranges[src]["max"] or 1.0
            base_value = defaults.get(src, 0.0)
            core = _run_core(manifest, src, dest, args.frames, args.dt, base_value)
            if not core.get("ok"):
                print(f"  {system}: {src}→{dest}  Core 失败：{core.get('error')}")
                continue
            ts = _run_ts(physics_doc, ranges, defaults, src, dest,
                         args.frames, args.dt, step_value)
            if not ts.get("ok"):
                print(f"  {system}: {src}→{dest}  TS 失败：{ts.get('error')}")
                continue
            ct, tt = core["trace"], ts["trace"]
            n = min(len(ct), len(tt))
            if n == 0:
                continue
            peak_core = max(abs(v - core["baseline"]) for v in ct[:n])
            peak_ts = max(abs(v - ts["baseline"]) for v in tt[:n])
            dev = max(abs(a - b) for a, b in zip(ct[:n], tt[:n]))
            # 允许 0/1 帧位移：官方内核的物理响应比参数写入晚一帧，
            # 位移后的偏差才反映**模型**对不对（相位差单列）。
            best_shift, best_dev = 0, dev
            for shift in (0, 1):
                if n - shift <= 1:
                    continue
                d = max(abs(a - b) for a, b in zip(ct[shift:], tt[:n - shift]))
                if d < best_dev:
                    best_shift, best_dev = shift, d
            rel = dev / peak_core if peak_core > 1e-9 else 0.0
            rel_shifted = best_dev / peak_core if peak_core > 1e-9 else 0.0
            row = (rel_shifted, dev, system, src, dest, peak_core, peak_ts)
            if worst is None or row[0] > worst[0]:
                worst = row
            print(f"  {system:16s} {src}→{dest} ({kind})  "
                  f"Core 峰值 {peak_core:8.4f} / TS 峰值 {peak_ts:8.4f}  "
                  f"直接偏差 {dev:7.4f}({rel*100:5.1f}%)  "
                  f"位移{best_shift}帧后偏差 {best_dev:7.4f}({rel_shifted*100:5.1f}%)")
        if worst is None:
            print("没有可比对的链")
            return 2
        rel, dev, system, src, dest, pc, pt = worst
        print(f"\n最差的一条：{system} {src}→{dest}  逐帧最大偏差 {dev:.4f}"
              f"（相对 Core 峰值 {rel*100:.1f}%）")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
