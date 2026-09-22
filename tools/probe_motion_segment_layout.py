#!/usr/bin/env python3
"""在**我们自己的包**上逐个替换 motion 文件，定位「加载了但不驱动参数」的确切条件。

隔离理由与判据同 drivers/live2d_runtime/moc3_motion_probe.py：一切结论只来自
官方内核的实际取值，不接受「文件看着对」。运行：
    .venv/Scripts/python.exe tools/probe_motion_segment_layout.py
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
PARAM = "ParamBodyAngleZ"


def document(curves, duration):
    """Meta 计数用探针里那套已对账过的解析器算，避免二次猜布局。"""
    from drivers.live2d_runtime.moc3_motion_probe import parse_segments

    segment_count = point_count = 0
    for curve in curves:
        _start, parsed = parse_segments(curve["Segments"])
        segment_count += len(parsed)
        # 初始点 1 个，其后每段贡献它自己的点数
        point_count += 1 + sum(len(points) for _type, points in parsed)
    return {"Version": 3, "Meta": {
        "Duration": duration, "Fps": 30.0, "Loop": True,
        "AreBeziersRestricted": True, "CurveCount": len(curves),
        "TotalSegmentCount": segment_count, "TotalPointCount": point_count,
        "UserDataCount": 0, "TotalUserDataSize": 0}, "Curves": curves}


def official_curve(parameter_id=PARAM):
    source = json.loads(OFFICIAL.read_text(encoding="utf-8-sig"))
    for curve in source["Curves"]:
        if curve.get("Target") == "Parameter" and curve["Id"] == parameter_id:
            return dict(curve)
    raise AssertionError(f"官方样例里没有 {parameter_id} 曲线")


def linear(points):
    """[(t, v), ...] -> 官方线性 Segments（初始点 + 每段前缀 0）。"""
    flat = [points[0][0], points[0][1]]
    for time, value in points[1:]:
        flat += [0, time, value]
    return flat


def variants():
    ours = json.loads(MOTION.read_text(encoding="utf-8"))
    ramp = [{"Target": "Parameter", "Id": PARAM,
             "Segments": linear([(0.0, -10.0), (6.0, 10.0)])}]
    return {
        "A 我们的产物（181 点线性）": (ours, 6),
        "B 官方同参数曲线原样进包": (document([official_curve()], 10), 10),
        "C 两点线性斜坡 -10→10": (document(ramp, 6), 6),
        "D 我们的产物 + 官方曲线并存": (document(
            [*ours["Curves"], official_curve()], 10), 10),
    }


def probe(mode: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "drivers.live2d_runtime.moc3_motion_probe",
         str(MANIFEST), "--frames", "45", "--mode", mode],
        capture_output=True, cwd=str(ROOT), timeout=300,
        encoding="utf-8", errors="replace")
    for line in reversed((proc.stdout or "").strip().splitlines()):
        if line.startswith("LIVE2D_MOTION="):
            return json.loads(line[len("LIVE2D_MOTION="):])
    return {"ok": False, "error": f"无输出 exit={proc.returncode} "
                                 f"{(proc.stderr or '')[-200:]}"}


def describe(result: dict) -> str:
    curves = result.get("curves") or {}
    summary = " ".join(
        f"{pid}:行程{data['observed_range']:.2f}"
        f"{'✓动' if data['moved'] else '✗不动'}"
        f"(误差{data['max_abs_error']:.2f})"
        for pid, data in list(curves.items())[:3])
    return (f"ok={result.get('ok')} 结束={result.get('motion_finished')} "
            f"{result.get('error', '')} {summary}")


def main() -> int:
    if not MOTION.is_file():
        print("先跑 tools/check_go_export_path.py realistic")
        return 1
    original = MOTION.read_text(encoding="utf-8")
    try:
        for tag, (doc, _duration) in variants().items():
            MOTION.write_text(json.dumps(doc, ensure_ascii=False,
                                         separators=(",", ":")), encoding="utf-8")
            for mode in ("update", "framework"):
                print(f"  {tag:26s} {mode:9s} -> {describe(probe(mode))}",
                      flush=True)
    finally:
        MOTION.write_text(original, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
