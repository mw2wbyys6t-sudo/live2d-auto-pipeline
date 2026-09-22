"""子进程诊断：我们的包里 motion 到底有没有起播，参数到底可不可写。

与其他隔离入口同样的理由：Cubism Core 在异常路径下会崩溃。这里只报告事实。
用法: python tools/diag_motion_start.py <model3.json> [group]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv) -> int:
    manifest = argv[1] if len(argv) > 1 else str(
        ROOT / "Work/go-export-check/export/gptest.model3.json")
    group = argv[2] if len(argv) > 2 else "idle"

    import live2d.v3 as sdk
    import pygame
    from drivers.live2d_runtime.native import CubismRenderer

    pygame.display.init()
    pygame.display.gl_set_attribute(pygame.GL_ALPHA_SIZE, 8)
    pygame.display.set_mode((64, 64), pygame.OPENGL | pygame.DOUBLEBUF)
    sdk.init()
    sdk.glInit()
    renderer = CubismRenderer(64, 64)
    report = {}
    try:
        renderer.load_model(manifest)
        model = renderer._model
        inner = model._model
        report["motions"] = {k: v for k, v in (model.GetMotions() or {}).items()}
        pid = "ParamBodyAngleZ"
        ids = [str(i) for i in renderer.parameter_ids]
        report["param_present"] = pid in ids
        idx = ids.index(pid) if pid in ids else -1

        def sample():
            return round(float(inner.GetParameterValue(idx)), 4) if idx >= 0 else None

        model.StopAllMotions()
        model.SetAutoBlinkEnable(False)
        model.SetAutoBreathEnable(False)
        report["finished_before_start"] = bool(model.IsMotionFinished())

        # 1) 手动写参数：确认这个参数在本模型里根本可写
        inner.SetAndSaveParameterValueById(pid, 12.0, 1.0)
        inner.Update(1 / 30)
        report["manual_write_reads_back"] = sample()

        # 2) 起播并逐帧采样
        report["finished_right_after_start"] = None
        try:
            model.StartMotion(group, 0, 3)
            report["finished_right_after_start"] = bool(model.IsMotionFinished())
        except Exception as exc:                     # noqa: BLE001
            report["start_error"] = f"{type(exc).__name__}: {exc}"
        trace = []
        for _ in range(40):
            inner.Update(1 / 30)
            trace.append(sample())
        report["motion_trace_first10"] = trace[:10]
        report["motion_trace_last10"] = trace[-10:]
        report["motion_trace_min"] = min(v for v in trace if v is not None)
        report["motion_trace_max"] = max(v for v in trace if v is not None)
        report["finished_after_play"] = bool(model.IsMotionFinished())

        # 3) 直接喂官方 motion 文件走 LoadExtraMotion 路径
        official = ROOT / "Work/native-sample/Haru/motions/haru_g_idle.motion3.json"
        try:
            loaded = inner.LoadExtraMotion("probeofficial", str(official))
            report["load_extra_motion_return"] = loaded
            model.StopAllMotions()
            model.StartMotion("probeofficial", 0, 3)
            trace2 = []
            for _ in range(40):
                inner.Update(1 / 30)
                trace2.append(sample())
            report["extra_motion_trace"] = [min(trace2), max(trace2)]
        except Exception as exc:                     # noqa: BLE001
            report["extra_motion_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        renderer.close()
        sdk.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
