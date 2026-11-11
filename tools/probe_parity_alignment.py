"""诊断：物理一致性测试报偏差、而 probe_physics_parity 报 0 的原因。

两者只有两处不同：
  1. 默认值读法 —— 测试读 model3.json 的 "Value"，探针读 "Default"；
  2. 帧对齐 —— 测试**强制**位移 1 帧（Core[1:] 对 TS[:-1]），
     探针在 0/1 帧里挑偏差最小的那个。

这条脚本把两处分别量出来，逐链打印 baseline、前几帧、两种位移下的偏差。
用法：python tools/probe_parity_alignment.py
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import probe_physics_parity as parity


def _weight_is_full(doc, system, dest):
    for setting in doc.get("PhysicsSettings", []):
        if setting.get("Id") != system:
            continue
        for entry in setting.get("Output", []):
            if (entry.get("Destination") or {}).get("Id") == dest:
                return float(entry.get("Weight", 100.0)) >= 100.0
    return False


def _dev(a, b):
    n = min(len(a), len(b))
    return max((abs(x - y) for x, y in zip(a[:n], b[:n])), default=float("nan"))


def _mutant_root(tmp: Path, kind: str) -> Path:
    """做一份可被 parity._run_ts 指向的假 ROOT，**不动仓库里的文件**。

    kind="fps"       → 引擎的 BASE_FPS 30 改 33（延迟项灵敏度；恒定重力下
                       AERODYNAMIC_DRAG 不参与运算，改它无效，故选这个）
    kind="norestore" → 跑手不再帧首还原物理输出（帧契约灵敏度）
    """
    fake = tmp / f"mutant_{kind}"
    (fake / "web" / "lib").mkdir(parents=True, exist_ok=True)
    (fake / "tools").mkdir(parents=True, exist_ok=True)
    engine = (ROOT / "web" / "lib" / "live2d-physics.ts").read_text(encoding="utf-8")
    harness = (ROOT / "tools" / "physics_trace_harness.mjs").read_text(encoding="utf-8")
    if kind == "fps":
        assert "BASE_FPS = 30" in engine, "引擎常量变了，变异脚本要跟着改"
        engine = engine.replace("BASE_FPS = 30", "BASE_FPS = 33")
    elif kind == "norestore":
        assert "engine.restoreOutputs(io);" in harness, "跑手帧契约变了，变异脚本要跟着改"
        harness = harness.replace("engine.restoreOutputs(io);", "")
    else:
        raise ValueError(kind)
    (fake / "web" / "lib" / "live2d-physics.ts").write_text(engine, encoding="utf-8")
    (fake / "tools" / "physics_trace_harness.mjs").write_text(harness, encoding="utf-8")
    return fake


def _mutation_check(workdir: Path, manifest: Path, doc: dict, ranges: dict,
                    defaults: dict) -> bool:
    """闸门必须能失败：敏感变异要把偏差推到 1e-6 以上。返回是否全部通过。

    fps 变异对任何链都敏感（延迟项直接进积分）；norestore 只对输出权重 <100
    的链敏感（权重 100 是直赋，混合基准根本不参与）。
    """
    print("\n=== mutation check (gate must be able to fail) ===")
    chains = [(s, d, sys_) for s, d, sys_, _k in parity._chains(doc)
              if s in ranges and d in ranges]
    if not chains:
        print("  没有链可变异")
        return False
    src, dest, system = chains[0]
    breath = next((c for c in chains if c[2] == "Breathing"), None)
    core = parity._run_core(str(manifest), src, dest, 30, 1 / 60.0,
                            defaults.get(src, 0.0))
    if not core.get("ok"):
        print(f"  Core 失败，跳过变异：{core.get('error')}")
        return False
    ok = True
    real_root = parity.ROOT
    try:
        ts_real = parity._run_ts(doc, ranges, defaults, src, dest, 30, 1 / 60.0,
                                 ranges[src]["max"] or 1.0)
        for kind in ("fps", "norestore"):
            fake = _mutant_root(workdir, kind)
            parity.ROOT = fake
            used = fake / "web" / "lib" / "live2d-physics.ts"
            ts = parity._run_ts(doc, ranges, defaults, src, dest, 30, 1 / 60.0,
                                ranges[src]["max"] or 1.0)
            if not ts.get("ok") or not ts_real.get("ok"):
                print(f"  {kind}: TS 跑失败 {ts.get('error')}")
                ok = False
                continue
            d_core = _dev(core["trace"], ts["trace"])
            d_self = _dev(ts_real["trace"], ts["trace"])
            sensitive = kind == "fps" or not _weight_is_full(doc, system, dest)
            if not sensitive:
                verdict = ("预期无影响（权重100直赋）" if d_core <= 1e-6
                           else "!! 意外：权重100竟受帧契约影响")
                ok = ok and d_core <= 1e-6
            else:
                verdict = "OK 能失败" if d_core > 1e-6 else "!! 闸门抓不到"
                ok = ok and d_core > 1e-6
            print(f"  {kind:10s} {system} {src}->{dest} "
                  f"vs Core {d_core:.6f} / vs 真引擎 {d_self:.6f} {verdict}")
            print(f"      变异体={used} 存在={used.is_file()} "
                  f"字节={used.stat().st_size if used.is_file() else -1}")
        if breath:
            bsrc, bdest, bsys = breath
            parity.ROOT = real_root
            bcore = parity._run_core(str(manifest), bsrc, bdest, 30, 1 / 60.0,
                                     defaults.get(bsrc, 0.0))
            b_real = parity._run_ts(doc, ranges, defaults, bsrc, bdest, 30,
                                    1 / 60.0, ranges[bsrc]["max"] or 1.0)
            parity.ROOT = _mutant_root(workdir, "norestore")
            bts = parity._run_ts(doc, ranges, defaults, bsrc, bdest, 30, 1 / 60.0,
                                 ranges[bsrc]["max"] or 1.0)
            if bcore.get("ok") and bts.get("ok") and b_real.get("ok"):
                d = _dev(bcore["trace"], bts["trace"])
                d_self = _dev(b_real["trace"], bts["trace"])
                caught = d > 1e-6
                ok = ok and caught
                print(f"  norestore  {bsys} {bsrc}->{bdest} "
                      f"vs Core {d:.6f} / vs 真引擎 {d_self:.6f} "
                      f"{'OK 能失败' if caught else '!! 闸门抓不到'}")
            else:
                print(f"  norestore {bsys} 跑失败 core={bcore.get('ok')} "
                      f"ts={bts.get('ok')} real={b_real.get('ok')}")
                ok = False
        else:
            print("  没有 Breathing 链：norestore 的帧契约灵敏度未验证")
            ok = False
    finally:
        parity.ROOT = real_root
    print(f"  变异验证结论：{'全部敏感变异都被闸门抓到' if ok else '有变异没被抓到/没测到'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mutate", action="store_true",
                    help="额外跑变异验证（证明闸门能失败）")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="parity_align_") as tmp:
        workdir = Path(tmp)
        manifest, physics = parity._build(workdir)
        doc = json.loads(physics.read_text(encoding="utf-8"))
        model = json.loads(manifest.read_text(encoding="utf-8"))

        ranges, test_defaults, probe_defaults = {}, {}, {}
        print("=== model3.json Parameters (relevant) ===")
        for spec in model.get("Parameters") or []:
            name = str(spec.get("Id") or "")
            if not name:
                continue
            ranges[name] = {"min": float(spec.get("Min", -30.0)),
                            "max": float(spec.get("Max", 30.0))}
            test_defaults[name] = float(spec.get("Value", spec.get("Default", 0.0)))
            probe_defaults[name] = float(spec.get("Default", 0.0))
        for name in ("ParamAngleX", "ParamAngleY", "ParamBodyAngleX",
                     "ParamBodyAngleY", "ParamBreath", "ParamHairSwing",
                     "ParamHairSwingBack", "ParamBodySway",
                     "ParamShoulderL", "ParamShoulderR"):
            if name in ranges:
                print(f"  {name:22s} range={ranges[name]} "
                      f"Value={test_defaults[name]} Default={probe_defaults[name]}")

        print("\n=== per-chain alignment ===")
        for src, dest, system, _kind in parity._chains(doc):
            if src not in ranges or dest not in ranges:
                continue
            if not _weight_is_full(doc, system, dest):
                continue
            for label, defaults in (("test-defaults", test_defaults),
                                    ("probe-defaults", probe_defaults)):
                core = parity._run_core(str(manifest), src, dest, 30, 1 / 60.0,
                                        defaults.get(src, 0.0))
                ts = parity._run_ts(doc, ranges, defaults, src, dest, 30,
                                    1 / 60.0, ranges[src]["max"] or 1.0)
                if not core.get("ok") or not ts.get("ok"):
                    print(f"  {system} {src}->{dest} [{label}] RUN FAILED "
                          f"core={core.get('error')} ts={ts.get('error')}")
                    continue
                ct, tt = core["trace"], ts["trace"]
                n = min(len(ct), len(tt))
                d0 = max(abs(a - b) for a, b in zip(ct[:n], tt[:n]))
                d1 = (max(abs(a - b) for a, b in zip(ct[1:], tt[:n - 1]))
                      if n > 2 else float("nan"))
                print(f"  {system:12s} {src}->{dest} [{label}] "
                      f"base={core['baseline']:.6f}/{ts['baseline']:.6f} "
                      f"shift0={d0:.6f} shift1={d1:.6f}")
                print(f"      core[:5]={[round(v, 5) for v in ct[:5]]}")
                print(f"      ts  [:5]={[round(v, 5) for v in tt[:5]]}")
        if args.mutate:
            if not _mutation_check(workdir, manifest, doc, ranges, test_defaults):
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())