#!/usr/bin/env python3
"""切清 motion3.json 触发官方内核段错误的到底是哪一个变量。

已观测：内容逐字节等价、只是把 indent=2 压成紧凑单行，就从 SIGSEGV 变成加载成功。
所以逐个变量单独测：缩进字符、行数（点数）、字节数、Duration 的整数/浮点写法。
运行：.venv/Scripts/python.exe tools/bisect_motion_format.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "Work" / "go-export-check" / "export"
MOTION = "motions/gptest_idle_00.motion3.json"
WORK = ROOT / "Work" / "motion-format"


def dense_document() -> dict:
    return json.loads((SRC / MOTION).read_text(encoding="utf-8"))


def small_document(points: int = 3) -> dict:
    doc = dense_document()
    curve = doc["Curves"][0]
    step = 6.0 / (points - 1)
    curve["Segments"] = [v for i in range(points)
                         for v in (round(i * step, 6), round(30.0 * i / (points - 1), 6))]
    doc["Meta"]["TotalSegmentCount"] = points - 1
    doc["Meta"]["TotalPointCount"] = points
    return doc


def variants():
    doc = dense_document()
    yield ("基线：indent=2（已知崩）", json.dumps(doc, ensure_ascii=False, indent=2))
    yield ("紧凑单行（已知通过）", json.dumps(doc, ensure_ascii=False))
    yield ("缩进改成 TAB", json.dumps(doc, ensure_ascii=False, indent="\t"))
    yield ("缩进改成 4 空格", json.dumps(doc, ensure_ascii=False, indent=4))
    yield ("indent=2 但只有 3 个点", json.dumps(small_document(3),
                                                ensure_ascii=False, indent=2))
    yield ("indent=2 但只有 20 个点", json.dumps(small_document(20),
                                                 ensure_ascii=False, indent=2))
    yield ("indent=2 但只有 100 个点", json.dumps(small_document(100),
                                                  ensure_ascii=False, indent=2))
    tabular = json.loads(json.dumps(doc))
    tabular["Meta"]["Duration"] = 6
    tabular["Meta"]["Fps"] = 30
    yield ("indent=2 + Duration/Fps 写成整数",
           json.dumps(tabular, ensure_ascii=False, indent=2))


def load_with(text: str) -> str:
    if WORK.exists():
        shutil.rmtree(WORK)
    shutil.copytree(SRC, WORK)
    (WORK / MOTION).write_text(text, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "drivers.live2d_runtime.moc3_load",
         str(WORK / "gptest.model3.json")],
        capture_output=True, cwd=str(ROOT), timeout=180,
        encoding="utf-8", errors="replace")
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode == 0 and '"ok": true' in out:
        return f"加载成功（{len(text)} 字节 / {text.count(chr(10)) + 1} 行）"
    if proc.returncode in (139, 3221225477) or "Segmentation" in out:
        return f"段错误（{len(text)} 字节 / {text.count(chr(10)) + 1} 行）"
    return f"exit={proc.returncode}（{len(text)} 字节）{out[-90:]}"


def main() -> int:
    if not (SRC / MOTION).is_file():
        print("先跑 tools/check_go_export_path.py realistic")
        return 1
    for tag, text in variants():
        print(f"  {tag:34s} -> {load_with(text)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
