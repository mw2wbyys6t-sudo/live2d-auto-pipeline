#!/usr/bin/env python3
"""定位「我们的 motion3.json 被加载但不驱动参数」的确切原因。

对照已经做过：官方 Haru 的 idle 在同样的驱动循环下确实改变参数
（mode=update，ParamAngleX 走了 4.0 行程），所以嫌疑全在文件内容。
这里把候选曲线逐个写进**我们自己的包**里跑，排除模型/清单差异。
运行：.venv/Scripts/python.exe tools/bisect_motion_playback.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXPORT = ROOT / "Work" / "go-export-check" / "export"
MOTION = EXPORT / "motions" / "gptest_idle_00.motion3.json"
MANIFEST = EXPORT / "gptest.model3.json"
OFFICIAL = ROOT / "Work" / "native-sample" / "Haru" / "motions" / "haru_g_idle.motion3.json"


def meta(curves, duration):
    segments = sum(len(c["Segments"]) // 2 - 1 for c in curves)
    return {"Duration": duration, "Fps": 30.0, "Loop": True,
            "AreBeziersRestricted": True, "CurveCount": len(curves),
            "TotalSegmentCount": segments,
            "TotalPointCount": sum(len(c["Segments"]) // 2 for c in curves),
            "UserDataCount": 0, "TotalUserDataSize": 0}


def doc(curves, duration=6):
    return {"Version": 3, "Meta": meta(curves, duration), "Curves": curves}


def official_param_curve(parameter_id="ParamAngleX"):
    source = json.loads(OFFICIAL.read_text(encoding="utf-8"))
    for curve in source["Curves"]:
        if curve.get("Target") == "Parameter" and curve["Id"] == parameter_id:
            return dict(curve)
    raise AssertionError(f"官方样例里没有 {parameter_id} 曲线")


def ours_dense():
    return json.loads(MOTION.read_text(encoding="utf-8"))


VARIANTS = {
    "1 我们的原文件（181 点线性，已知不动）": ours_dense,
    "2 官方 ParamAngleX 曲线放进我们的包": lambda: doc(
        [official_param_curve()], 10),
    "3 两点线性斜坡 -10→10": lambda: doc(
        [{"Target": "Parameter", "Id": "ParamBodyAngleZ",
          "Segments": [0.0, -10.0, 6.0, 10.0]}]),
    "4 三点线性 -10→10→-10": lambda: doc(
        [{"Target": "Parameter", "Id": "ParamBodyAngleZ",
          "Segments": [0.0, -10.0, 3.0, 10.0, 6.0, -10.0]}]),
    "5 我们的曲线 + FadeInTime/FadeOutTime": lambda: {
        **ours_dense(),
        "Curves": [{**ours_dense()["Curves"][0],
                    "FadeInTime": 0.5, "FadeOutTime": 0.5}]},
    "6 我们的曲线但 Duration 写成整数": lambda: {
        **ours_dense(),
        "Meta": {**ours_dense()["Meta"], "Duration": 6}},
    "7 我们的曲线 + 官方那条并存": lambda: {
        **ours_dense(),
        "Curves": [*ours_dense()["Curves"], official_param_curve()],
        "Meta": meta([*ours_dense()["Curves"], official_param_curve()], 10)},
}


def probe() -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "drivers.live2d_runtime.moc3_motion_probe",
         str(MANIFEST), "--frames", "45", "--mode", "update"],
        capture_output=True, cwd=str(ROOT), timeout=300,
        encoding="utf-8", errors="replace")
    for line in reversed((proc.stdout or "").strip().splitlines()):
        if line.startswith("LIVE2D_MOTION="):
            return json.loads(line[len("LIVE2D_MOTION="):])
    return {"ok": False, "error": f"无输出 exit={proc.returncode} "
                                 f"{(proc.stderr or '')[-120:]}"}


def main() -> int:
    if not MOTION.is_file():
        print("先跑 tools/check_go_export_path.py realistic")
        return 1
    original = MOTION.read_text(encoding="utf-8")
    try:
        for tag, factory in VARIANTS.items():
            document = factory()
            MOTION.write_text(json.dumps(document, ensure_ascii=False,
                                         separators=(",", ":")), encoding="utf-8")
            result = probe()
            curves = result.get("curves") or {}
            summary = " ".join(
                f"{pid}:行程{data['observed_range']:.2f}"
                f"{'✓动' if data['moved'] else '✗不动'}"
                for pid, data in list(curves.items())[:3])
            print(f"  {tag:38s} -> {result.get('ok') or 'False'} "
                  f"{result.get('error', '')} {summary}", flush=True)
    finally:
        MOTION.write_text(original, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
