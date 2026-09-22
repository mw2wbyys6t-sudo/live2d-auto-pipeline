#!/usr/bin/env python3
"""真实导出像素对照：编译进 warp 变形器后，画面必须与「无变形器」逐像素相同。

一致性 =True 只证明字节合法；(s, t) 换算错了内核照样接受，但画面会变形。
运行：.venv/Scripts/python.exe tools/verify_deformer_export_pixels.py [preset]
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

from drivers.live2d_runtime.moc3_verify import (  # noqa: E402
    render_probe, verify_moc3_consistency, verify_moc3_load)
from live2d_builder.pipeline import Live2DBuilder  # noqa: E402

OUT = ROOT / "Work" / "deformer-export-pixels"

PRESETS = {
    "realistic": (("hair_front", (60, 40, 40)),
                  ("body", (220, 180, 160)),
                  ("eye_left", (250, 250, 250)),
                  ("face", (250, 220, 200)),
                  ("hair_back", (40, 25, 25))),
    "synthetic": (("00_body", (220, 180, 160)),
                  ("01_hair", (60, 40, 40)),
                  ("02_face", (250, 220, 200))),
}


class _NoDeformerBuilder(Live2DBuilder):
    """对照组：同一批图层，但不生成任何变形器。"""

    def _setup_deformers(self, bone_tree, meshes, layer_names, centroids=None):
        return {"deformers": [], "by_name": {}}


def _make_layers(preset: str) -> OrderedDict:
    layers_dir = OUT / "layers"
    if layers_dir.exists():
        shutil.rmtree(layers_dir)
    layers_dir.mkdir(parents=True)
    for name, color in PRESETS[preset]:
        img = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
        for y in range(12, 84):
            for x in range(12, 84):
                img.putpixel((x, y), (*color, 255))
        img.save(layers_dir / f"{name}.png")
    result = OrderedDict()
    for name, _ in PRESETS[preset]:
        result[name] = Image.open(layers_dir / f"{name}.png").convert("RGBA")
    return result


def _build(cls, layers, folder: Path) -> Path:
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    cls(output_dir=str(folder), character_name="defx").build(layers)
    return folder / "defx.model3.json"


def main() -> int:
    preset = sys.argv[1] if len(sys.argv) > 1 else "realistic"
    layers = _make_layers(preset)

    with_warp = _build(Live2DBuilder, layers, OUT / "with_warp")
    without = _build(_NoDeformerBuilder, layers, OUT / "without")

    for label, manifest in (("挂变形器", with_warp), ("无变形器", without)):
        moc3 = str(manifest).replace(".model3.json", ".moc3")
        consistency = verify_moc3_consistency(moc3)
        loaded = verify_moc3_load(str(manifest))
        print(f"{label}: 一致性={consistency['ok']} 加载={loaded['ok']} "
              f"{consistency['blocker'] or ''}")

    a = render_probe(str(with_warp), {}, png=str(OUT / "with_warp.png"))
    b = render_probe(str(without), {}, png=str(OUT / "without.png"))
    if not (a["ok"] and b["ok"]):
        print(f"渲染失败: {a['blocker']} / {b['blocker']}")
        return 1
    print(json.dumps({
        "with_warp": {k: a[k] for k in ("alpha_bbox", "opaque_pixels",
                                        "pixels_sha256")},
        "without": {k: b[k] for k in ("alpha_bbox", "opaque_pixels",
                                      "pixels_sha256")},
        "identical": a["pixels_sha256"] == b["pixels_sha256"],
    }, ensure_ascii=False, indent=2))
    return 0 if a["pixels_sha256"] == b["pixels_sha256"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
