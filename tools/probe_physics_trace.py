"""官方内核的物理**逐帧轨迹**探针：拿 Core 的输出序列当基准。

要回答的问题：浏览器里那套 Verlet（或此前那套弹簧-阻尼近似）复刻内核到什么
程度。`moc3_physics_probe` 只给「滞后/过冲/回摆」三个布尔，不足以判断形状；
这里把每帧的输出参数值记下来，才能和自研播放器的轨迹逐帧比。

用法：
    python tools/probe_physics_trace.py <model3.json> --input ParamAngleX \
        --output ParamHairSwing --frames 60 --dt 0.0166667
输出：JSON（含 baseline / trace / settle_trace），stdout 最后一行是 JSON。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def trace(manifest: str, input_name: str, output_name: str, frames: int,
          dt: float, step: float | None = None, base: float | None = None):
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
        if input_name not in ids:
            return {"ok": False, "error": f"模型里没有输入参数 {input_name}"}
        if output_name not in ids:
            return {"ok": False, "error": f"模型里没有输出参数 {output_name}"}
        out_index = ids.index(output_name)
        lo, hi = session.parameter_bounds(input_name)
        amplitude = step if step is not None else (hi if hi != 0 else 1.0)
        base_value = base if base is not None else 0.0

        def advance(value):
            session.set_parameter(input_name, value)
            session.update(dt)
            return session.get_parameter_value(out_index)

        for _ in range(frames):
            advance(base_value)
        baseline = session.get_parameter_value(out_index)
        trace_values = [advance(amplitude) for _ in range(frames)]
        for _ in range(frames):
            advance(base_value)
        settle = session.get_parameter_value(out_index)
        return {
            "ok": True,
            "input": input_name,
            "output": output_name,
            "baseline": baseline,
            "step_value": amplitude,
            "default": base_value,
            "trace": trace_values,
            "settle": settle,
            "frames": frames,
            "dt": dt,
        }
    finally:
        session.close()
        sdk.glRelease()
        sdk.dispose()
        pygame.display.quit()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manifest")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--dt", type=float, default=1.0 / 60.0)
    ap.add_argument("--step", type=float, default=None)
    ap.add_argument("--base", type=float, default=None)
    args = ap.parse_args(argv)
    result = trace(args.manifest, args.input, args.output, args.frames,
                   args.dt, args.step, args.base)
    print("PHYSICS_TRACE=" + json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
