#!/usr/bin/env python3
"""在手性已修正的前提下，判定图集 UV 的 v 轴到底该不该再翻一次。

直接改导出 moc3 的 uv.xys 三种写法，逐个渲染计数。
运行：.venv/Scripts/python.exe tools/probe_uv_variant.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from moc3 import _core  # noqa: E402
from drivers.live2d_runtime.moc3_verify import render_probe  # noqa: E402

SRC = ROOT / "Work" / "go-export-check" / "export"
WORK = ROOT / "Work" / "uv-variant"


def trial(tag: str, transform):
    folder = WORK / tag
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for item in SRC.iterdir():
        if item.is_file():
            shutil.copy2(item, folder / item.name)
    doc = _core.Moc3.from_file(str(folder / "gptest.moc3"))
    uv = list(doc.get("uv.xys"))
    doc.set("uv.xys", transform(uv))
    (folder / "gptest.moc3").write_bytes(doc.to_bytes())
    (folder / "gptest.model3.json").write_text(json.dumps({
        "Version": 3,
        "FileReferences": {"Moc": "gptest.moc3",
                           "Textures": ["gptest.texture_00.png"]},
    }), encoding="utf-8")
    result = render_probe(str(folder / "gptest.model3.json"),
                          png=str(folder / "frame.png"))
    print(f"  {tag:22s} 不透明={result['opaque_pixels']:>6} "
          f"bbox={result['alpha_bbox']} "
          f"{'' if result['ok'] else 'blocker=' + str(result['blocker'])}")


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    print("当前导出（已修正手性）里的 uv 形态：")
    doc = _core.Moc3.from_file(str(SRC / "gptest.moc3"))
    uv = list(doc.get("uv.xys"))
    print(f"  u[{min(uv[0::2]):.3f},{max(uv[0::2]):.3f}] "
          f"v[{min(uv[1::2]):.3f},{max(uv[1::2]):.3f}]")
    trial("as_is", lambda values: list(values))
    trial("v_unflipped", lambda values: [v if i % 2 == 0 else 1.0 - v
                                         for i, v in enumerate(values)])
    trial("u_unflipped", lambda values: [1.0 - v if i % 2 == 0 else v
                                         for i, v in enumerate(values)])
    trial("both_unflipped", lambda values: [1.0 - v for v in values])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
