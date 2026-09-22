#!/usr/bin/env python3
"""手工复现 Go 桥接的导出路径，确认产物规模真实存在。

运行：.venv/Scripts/python.exe tools/check_go_export_path.py
产物：Work/go-export-check/
"""
from __future__ import annotations

import json
import shutil
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from live2d_builder.pipeline import Live2DBuilder  # noqa: E402

OUT = ROOT / "Work" / "go-export-check"
LAYERS = OUT / "layers"

# synthetic: 任意命名 -> 变形器生成器匹配不上，产物是纯静态几何
# realistic: 语义分层实际会用的名字 -> 会生成 warp / rotation 变形器
PRESETS = {
    "synthetic": (("00_body", (220, 180, 160)),
                  ("01_hair", (60, 40, 40)),
                  ("02_face", (250, 220, 200))),
    "realistic": (("hair_front", (60, 40, 40)),
                  ("body", (220, 180, 160)),
                  ("eye_left", (250, 250, 250))),
}


def main() -> int:
    preset = sys.argv[1] if len(sys.argv) > 1 else "synthetic"
    if OUT.exists():
        shutil.rmtree(OUT)
    LAYERS.mkdir(parents=True)
    for name, color in PRESETS[preset]:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        for y in range(8, 56):
            for x in range(8, 56):
                img.putpixel((x, y), (*color, 255))
        img.save(LAYERS / f"{name}.png")

    layers = OrderedDict()
    for p in sorted(LAYERS.glob("*.png")):
        layers[p.stem] = Image.open(p).convert("RGBA")

    out_dir = OUT / "export"
    out_dir.mkdir(parents=True)
    result = Live2DBuilder(output_dir=str(out_dir),
                           character_name="gptest").build(layers)

    keys = ("output_dir", "model3_json", "moc3_ref", "textures", "physics",
            "validation", "compatibility", "build_meta", "elapsed_seconds",
            "moc3")
    summary = {k: result.get(k) for k in keys}
    summary["mesh_count"] = len(result.get("meshes") or {})
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    files = sorted(p.relative_to(out_dir).as_posix() for p in out_dir.rglob("*")
                   if p.is_file())
    print("导出目录文件:", files)
    moc3 = out_dir / "gptest.moc3"
    print("moc3 头 4 字节:", moc3.read_bytes()[:4], "大小:", moc3.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
