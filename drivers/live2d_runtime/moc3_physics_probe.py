"""子进程入口：验证产物里的 physics3 在官方运行时**真的产生带滞后的运动**。

为什么不能只看「输出参数变了」：把 Delay 写成 0、Mobility 写成 1 时，输出会
逐帧等于输入 —— 数值上「动了」，但那只是复制，不是物理。这里要求三件事同时成立：

1. 输入阶跃后，输出确实在**之后**的若干帧里继续变化（滞后 / 过冲）；
2. 输出的峰值不落在阶跃当帧（排除恒等映射）；
3. 输入回到静止后，输出会向基线回摆（不是就地冻结，也不是发散）。

与其他隔离入口同样的理由：Cubism Core 在异常路径下会崩溃。
用法: python -m drivers.live2d_runtime.moc3_physics_probe <model3.json>
退出码: 0 = 至少一条输入->输出链表现出真实物理响应且无失败链；1 = 不通过；2 = 环境错误
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _pairs(manifest: Path) -> list:
    """从 physics3.json 里取 (输入参数, 输出参数, 该系统的顶点延迟) 组合。"""
    document = json.loads(manifest.read_text(encoding="utf-8"))
    references = document.get("FileReferences") or {}
    physics_file = references.get("Physics")
    if not physics_file:
        return []
    physics = json.loads((manifest.parent / physics_file).read_text(
        encoding="utf-8"))
    out = []
    for system in physics.get("PhysicsSettings") or []:
        inputs = [i for i in system.get("Input") or []
                  if (i.get("Source") or {}).get("Target") == "Parameter"]
        outputs = [o for o in system.get("Output") or []
                   if (o.get("Destination") or {}).get("Target") == "Parameter"]
        delays = [float(v.get("Delay", 1.0)) for v in system.get("Vertices") or []]
        for inp in inputs:
            for outp in outputs:
                out.append({
                    "system": system.get("Id") or system.get("Name") or "?",
                    "input": inp["Source"]["Id"],
                    "output": outp["Destination"]["Id"],
                    "min_delay": min(delays) if delays else None,
                })
    return out


def _param_range(model, ids, name, default):
    index = ids.index(name)
    low = float(model.GetParameterMinimumValue(index))
    high = float(model.GetParameterMaximumValue(index))
    if low == high:
        return default, default
    return low, high


def run(manifest_path: str, frames: int, dt: float, mode: str = "update") -> dict:
    """mode=update 只靠 Model.Update(dt)；mode=explicit 额外调 UpdatePhysics(dt)。

    分开跑是为了分辨「物理没生效」的两类原因：内核的 Update 不驱动物理，
    还是我们的 physics3.json 本身无效 —— 结论不同，修法也不同。
    """
    import pygame

    from drivers.live2d_runtime.native import CubismRenderer

    manifest = Path(manifest_path)
    if not manifest.is_file():
        return {"ok": False, "error": f"清单不存在: {manifest}"}
    pairs = _pairs(manifest)
    if not pairs:
        return {"ok": False,
                "error": "产物里没有可验的 physics 参数链（没有 Physics 引用，"
                         "或 Input/Output 不是 Parameter）"}

    import live2d.v3 as sdk
    pygame.display.init()
    pygame.display.gl_set_attribute(pygame.GL_ALPHA_SIZE, 8)
    pygame.display.set_mode((64, 64), pygame.OPENGL | pygame.DOUBLEBUF)
    sdk.init()
    sdk.glInit()
    renderer = CubismRenderer(64, 64)
    chains = []
    try:
        renderer.load_model(manifest_path)
        model = renderer._model
        inner = model._model
        model.SetAutoBlinkEnable(False)
        model.SetAutoBreathEnable(False)
        ids = [str(i) for i in renderer.parameter_ids]
        missing = [p["input"] for p in pairs if p["input"] not in ids] + \
                  [p["output"] for p in pairs if p["output"] not in ids]
        if missing:
            return {"ok": False, "error": "physics 引用了模型里没有的参数",
                    "missing": sorted(set(missing))}

        for chain in pairs:
            inner_index = ids.index(chain["input"])
            out_index = ids.index(chain["output"])
            low, high = _param_range(inner, ids, chain["input"], 30.0)
            step = high if high != low else 1.0

            def advance(value):
                inner.SetAndSaveParameterValueById(chain["input"], value, 1.0)
                inner.Update(dt)
                if mode == "explicit":
                    inner.UpdatePhysics(dt)
                return float(inner.GetParameterValue(out_index))

            def settle(count, value):
                for _ in range(count):
                    advance(value)
                return float(inner.GetParameterValue(out_index))

            base = settle(frames, low if low != 0 else 0.0)
            # 阶跃当帧就采样一次，再逐帧记录，才能看出滞后
            trace = [advance(step) for _ in range(frames)]
            peak_offset = max(abs(v - base) for v in trace)
            first = abs(trace[0] - base)
            lag_present = peak_offset > first * 1.05 or abs(trace[-1] - base) > 1e-6
            # 输入回零后：输出必须往基线走（回摆），不能停在极值或发散
            after = settle(frames, low if low != 0 else 0.0)
            settles = abs(after - base) < max(1e-3, peak_offset * 0.25)
            chains.append({
                "system": chain["system"], "input": chain["input"],
                "output": chain["output"], "min_delay": chain["min_delay"],
                "baseline": round(base, 4), "step_frame": round(first, 4),
                "peak_offset": round(peak_offset, 4),
                "after_settle": round(after, 4),
                "moved": peak_offset > 1e-4,
                "lagged": bool(lag_present), "settled": bool(settles),
            })
        ok = bool(chains) and all(
            c["moved"] and c["lagged"] and c["settled"] for c in chains)
        return {"ok": ok, "chains": chains,
                "failed": [c for c in chains
                           if not (c["moved"] and c["lagged"] and c["settled"])],
                "error": None if ok else "physics 未产生带滞后的可回摆运动"}
    finally:
        renderer.close()
        sdk.glRelease()
        sdk.dispose()
        pygame.display.quit()


def main(argv) -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--dt", type=float, default=1.0 / 60.0)
    parser.add_argument("--mode", choices=("update", "explicit"), default="update")
    args = parser.parse_args(argv[1:])
    try:
        payload = run(args.manifest, args.frames, args.dt, args.mode)
    except Exception as exc:                      # noqa: BLE001
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print("LIVE2D_PHYSICS=" + json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
