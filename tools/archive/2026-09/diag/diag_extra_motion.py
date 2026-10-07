"""子进程诊断：把「我们的 motion 文件内容」和「model3.json 预加载路径」分开判。

两条加载路径都用绝对路径喂给内核，逐帧回读参数：
* LoadExtraMotion(我们的文件)  —— 只考察文件内容能不能被内核播放
* LoadExtraMotion(官方文件)    —— 同包正对照，排除模型/驱动循环差异
用法: python tools/diag_extra_motion.py <model3.json> <motion1> [motion2]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PARAMS = ("ParamBodyAngleZ", "ParamAngleZ", "ParamAngleX")


def trace_model(model, inner, indices, frames=90, dt=1 / 30):
    values = {pid: [] for pid in indices}
    for _ in range(frames):
        inner.Update(dt)
        for pid, idx in indices.items():
            values[pid].append(float(inner.GetParameterValue(idx)))
    return {pid: {"min": round(min(v), 3), "max": round(max(v), 3),
                  "range": round(max(v) - min(v), 3)}
            for pid, v in values.items()}


def main(argv) -> int:
    manifest = argv[1]
    files = argv[2:]
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
        model.StopAllMotions()
        model.SetAutoBlinkEnable(False)
        model.SetAutoBreathEnable(False)
        ids = [str(i) for i in renderer.parameter_ids]
        indices = {p: ids.index(p) for p in PARAMS if p in ids}
        report["probe_parameters"] = sorted(indices)
        report["baseline_rest"] = trace_model(model, inner, indices)
        for position, name in enumerate(files):
            path = Path(name)
            if not path.is_absolute():
                path = (ROOT / name).resolve()
            group = f"probe{position}"
            entry = {"file": path.name}
            try:
                entry["load_extra_motion_return"] = inner.LoadExtraMotion(
                    group, str(path))
                model.StartMotion(group, 0, 3)
                entry["finished_right_after_start"] = bool(
                    model.IsMotionFinished())
                entry["trace"] = trace_model(model, inner, indices)
                model.StopAllMotions()
            except Exception as exc:                 # noqa: BLE001
                entry["error"] = f"{type(exc).__name__}: {exc}"
            report[group] = entry
    finally:
        renderer.close()
        sdk.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
