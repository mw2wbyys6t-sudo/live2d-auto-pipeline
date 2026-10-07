#!/usr/bin/env python3
"""收缩法定位 motion3.json 让官方内核段错误的确切触发点。

背景：把生成的 motion 挂进 model3.json 后，LoadModelJson 直接 SIGSEGV；
把 Motions 清空就正常。所以从**已知合法**的官方 Haru motion 出发，一次只改
一处向我们靠拢，每处跑一次真实加载（子进程隔离，崩溃=该处有问题）。
运行：.venv/Scripts/python.exe tools/bisect_motion_load.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXPORT = ROOT / "Work" / "go-export-check" / "export"
OFFICIAL = ROOT / "Work" / "native-sample" / "Haru" / "motions" / "haru_g_idle.motion3.json"
WORK = ROOT / "Work" / "motion-bisect"


def variant_two_points() -> dict:
    """最小可用形状：一条 2 关键帧的线性参数曲线。"""
    return {
        "Version": 3,
        "Meta": {"Duration": 6.0, "Fps": 30.0, "Loop": True,
                 "AreBeziersRestricted": True, "CurveCount": 1,
                 "TotalSegmentCount": 1, "TotalPointCount": 2,
                 "UserDataCount": 0, "TotalUserDataSize": 0},
        "Curves": [{"Target": "Parameter", "Id": "ParamAngleZ",
                    "Segments": [0.0, 0.0, 6.0, 30.0]}],
    }


def variant_official() -> dict:
    return json.loads(OFFICIAL.read_text(encoding="utf-8"))


def variant_official_param_only() -> dict:
    """官方文件，但只留 Target=Parameter 且本模型确实存在的曲线。"""
    doc = variant_official()
    doc["Curves"] = [c for c in doc["Curves"] if c.get("Target") == "Parameter"]
    return doc


def variant_official_one_param() -> dict:
    doc = variant_official()
    params = [c for c in doc["Curves"] if c.get("Target") == "Parameter"]
    doc["Curves"] = params[:1]
    doc["Meta"]["CurveCount"] = 1
    return doc


def variant_dense() -> dict:
    """我们实际生成的那份（181 点、Duration 6.0）。"""
    return json.loads((EXPORT / "motions" / "gptest_idle_00.motion3.json")
                      .read_text(encoding="utf-8"))


def variant_dense_short_fps() -> dict:
    doc = variant_dense()
    doc["Meta"]["Fps"] = 5.0
    return doc


VARIANTS = (
    ("官方原样（对照，必须能加载）", variant_official),
    ("官方：只留 Parameter 曲线", variant_official_param_only),
    ("官方：只留 1 条 Parameter 曲线", variant_official_one_param),
    ("我们的最小形状：1 曲线 2 点", variant_two_points),
    ("我们的实际文件（181 点）", variant_dense),
    ("我们的文件 + Fps 改 5", variant_dense_short_fps),
)


def load_with(motion: dict) -> str:
    """把 motion 写进导出包并跑一次真实 LoadModelJson。"""
    if WORK.exists():
        shutil.rmtree(WORK)
    # 整树复制：expressions/ 等子目录缺了会先报路径错，测不到 motion
    shutil.copytree(EXPORT, WORK)
    motion_path = WORK / "motions" / "probe.motion3.json"
    motion_path.parent.mkdir(exist_ok=True)
    motion_path.write_text(json.dumps(motion, ensure_ascii=False), encoding="utf-8")

    manifest = WORK / "gptest.model3.json"
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    doc["FileReferences"]["Motions"] = {"idle": [{"File": "motions/probe.motion3.json"}]}
    manifest.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "drivers.live2d_runtime.moc3_load", str(manifest)],
        capture_output=True, cwd=str(ROOT), timeout=180,
        encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0 and '"ok": true' in out.replace('"ok":true', '"ok": true'):
        return "加载成功"
    if proc.returncode in (139, 0xC0000005, 3221225477) or "Segmentation" in out:
        return f"段错误 (exit={proc.returncode})"
    for line in reversed(out.strip().splitlines()):
        if line.startswith("{"):
            return f"失败: {line[:120]}"
    return f"exit={proc.returncode} 无输出"


def main() -> int:
    if not OFFICIAL.is_file():
        print(f"缺少官方样例：{OFFICIAL}")
        return 1
    if not (EXPORT / "gptest.model3.json").is_file():
        print("先跑 tools/check_go_export_path.py realistic 生成导出包")
        return 1
    for tag, factory in VARIANTS:
        try:
            motion = factory()
        except Exception as exc:
            print(f"  {tag:36s} 变体构造失败: {exc}")
            continue
        print(f"  {tag:36s} -> {load_with(motion)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
