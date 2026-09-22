"""二分「内核拒绝加载 motion 文件」的最小条件。

每个候选都写成临时 motion 文件，用 LoadExtraMotion 绝对路径喂给内核，
以「有没有打印 Load extra motion failed / 参数有没有真动」为判据。
对照组是官方 Haru 的 idle 原文件（已知能加载、能播）。
运行：.venv/Scripts/python.exe tools/bisect_motion_encoding.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORK = ROOT / "Work" / "motion-encoding"
HARU_MANIFEST = ROOT / "Work/native-sample/Haru/Haru.model3.json"
OFFICIAL = ROOT / "Work/native-sample/Haru/motions/haru_g_idle.motion3.json"


def counts(segments):
    """按实测布局数 (段数, 点数)：0/2/3 型 1 点，1 型 3 点。"""
    per_type = {0: 1, 1: 3, 2: 1, 3: 1}
    point_total = 1
    i = 2
    n = 0
    while i < len(segments):
        seg_type = int(segments[i])
        points = per_type[seg_type]
        point_total += points
        i += 1 + 2 * points
        n += 1
    return n, point_total


def document(curves, duration, **meta_over):
    segments = points = 0
    for curve in curves:
        n, p = counts(curve["Segments"])
        segments += n
        points += p
    meta = {"Duration": duration, "Fps": 30.0, "Loop": True,
            "AreBeziersRestricted": True, "CurveCount": len(curves),
            "TotalSegmentCount": segments, "TotalPointCount": points,
            "UserDataCount": 0, "TotalUserDataSize": 0}
    meta.update(meta_over)
    return {"Version": 3, "Meta": meta, "Curves": curves}


def linear(points):
    flat = [points[0][0], points[0][1]]
    for time, value in points[1:]:
        flat += [0, time, value]
    return flat


def official_curves():
    source = json.loads(OFFICIAL.read_text(encoding="utf-8-sig"))
    return {c["Id"]: c for c in source["Curves"]
            if c.get("Target") == "Parameter"}


def to_linear(curve):
    """把官方贝塞尔曲线压成线性折线：只保留每段终点。"""
    per_type = {0: 1, 1: 3, 2: 1, 3: 1}
    seg = curve["Segments"]
    points = [(seg[0], seg[1])]
    i = 2
    while i < len(seg):
        seg_type = int(seg[i])
        n = per_type[seg_type]
        flat = seg[i + 1: i + 1 + 2 * n]
        points.append((flat[-2], flat[-1]))
        i += 1 + 2 * n
    return {"Target": "Parameter", "Id": curve["Id"], "Segments": linear(points)}


def candidates():
    official = official_curves()
    angle = official["ParamAngleX"]
    return {
        "1 官方整份 idle（正对照）": json.loads(
            OFFICIAL.read_text(encoding="utf-8-sig")),
        "2 官方单条 ParamAngleX 原样": document([dict(angle)], 10),
        "3 官方 ParamAngleX 压成线性": document([to_linear(angle)], 10),
        "4 两点线性 0→6": document([{"Target": "Parameter", "Id": "ParamAngleX",
                                     "Segments": linear([(0.0, -4.0), (6.0, 14.0)])}], 6),
        "5 三点线性": document([{"Target": "Parameter", "Id": "ParamAngleX",
                                 "Segments": linear([(0.0, 0.0), (3.0, 14.0),
                                                     (6.0, 0.0)])}], 6),
        "6 两点线性 + 末点等于 Duration": document(
            [{"Target": "Parameter", "Id": "ParamAngleX",
              "Segments": linear([(0.0, 0.0), (10.0, 14.0)])}], 10),
        "7 我们的产物形状（181 点线性, 6s）": document(
            [{"Target": "Parameter", "Id": "ParamAngleX",
              "Segments": linear([(round(i / 30, 6),
                                   round(14.0 * ((i % 20) - 10) / 10, 6))
                                  for i in range(181)])}], 6),
        "8 官方单条 + TotalPointCount 少 1": document(
            [dict(angle)], 10, TotalPointCount=document([dict(angle)], 10)
            ["Meta"]["TotalPointCount"] - 1),
    }


def probe(path: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools/diag_extra_motion.py"),
         str(HARU_MANIFEST), str(path)],
        capture_output=True, cwd=str(ROOT), timeout=300,
        encoding="utf-8", errors="replace")
    payload = {}
    text = proc.stdout or ""
    start = text.find("\n{")
    if start >= 0:
        try:  # 报告之后还会跟 live2d 的关闭横幅，只能取第一个 JSON 文档
            payload = json.JSONDecoder().raw_decode(text[start + 1:])[0]
        except ValueError:
            payload = {}
    loaded = "probe0" in payload
    trace = (payload.get("probe0") or {}).get("trace") or {}
    moved = {k: v["range"] for k, v in trace.items() if v["range"] > 1e-4}
    warn = f"Load extra motion failed: {path}" in (proc.stderr or "") + text
    return {"payload": payload, "loaded": loaded and not warn, "warn": warn,
            "moved": moved}


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    for tag, doc in candidates().items():
        path = WORK / f"{tag.split()[0]}.motion3.json"
        path.write_text(json.dumps(doc, ensure_ascii=False,
                                   separators=(",", ":")), encoding="utf-8")
        result = probe(path)
        entry = result["payload"].get("probe0") or {}
        trace = entry.get("trace") or {}
        biggest = max((v["range"] for v in trace.values()), default=0.0)
        print(f"  {tag:34s} 加载={result['loaded']} warn={result['warn']} "
              f"ret={entry.get('load_extra_motion_return')} "
              f"起播即结束={entry.get('finished_right_after_start')} "
              f"动到的参数={len(result['moved'])} 最大行程={biggest:.2f} "
              f"{entry.get('error', '')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
