#!/usr/bin/env python3
"""二分定位「真实导出在 live2d-py 里一个像素都不画」的原因。

两条独立线索，分别验证：
  A. model3.json 的多余顶层键 / 引用文件；
  B. moc3 自身（用真实导出的画布/ppu/单位坐标规模造一个合成包做对照）。
运行：.venv/Scripts/python.exe tools/bisect_blank_render.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drivers.live2d_runtime.moc3_verify import render_probe  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    MeshSpec, ParameterSpec, RigSpec, compile_static_rig,
)

SRC = ROOT / "Work" / "go-export-check" / "export"
WORK = ROOT / "Work" / "bisect"


def blank_frame_sha() -> str:
    return __import__("hashlib").sha256(
        np.zeros((500, 400, 4), dtype=np.uint8).tobytes()).hexdigest()


def clone(tag: str, manifest_doc: dict | None) -> Path:
    folder = WORK / tag
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for item in SRC.iterdir():
        if item.is_file():
            shutil.copy2(item, folder / item.name)
    manifest = folder / "gptest.model3.json"
    if manifest_doc is not None:
        manifest.write_text(json.dumps(manifest_doc, ensure_ascii=False),
                            encoding="utf-8")
    return manifest


def show(tag: str, manifest: Path) -> tuple:
    result = render_probe(str(manifest))
    status = (f"不透明={result['opaque_pixels']:>6} bbox={result['alpha_bbox']}")
    if not result["ok"]:
        status += f" blocker={result['blocker']}"
    print(f"  {tag:26s} {status}")
    return result


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    print(f"全透明帧 sha256 = {blank_frame_sha()[:16]}…（用于确认「什么都没画」）")
    base = json.loads((SRC / "gptest.model3.json").read_text(encoding="utf-8-sig"))

    print("\nA. 削减 model3.json 的内容")
    show("baseline 完整清单", clone("baseline", None))

    drop_top = [k for k in base if k not in ("Version", "FileReferences")]
    for key in drop_top:
        doc = {k: v for k, v in base.items() if k != key}
        show(f"去掉顶层 {key}", clone(f"top_{key}", doc))

    refs_min = {"Version": 3, "FileReferences": {
        "Moc": base["FileReferences"]["Moc"],
        "Textures": base["FileReferences"]["Textures"]}}
    show("只留 Moc+Textures", clone("min_refs", refs_min))

    print("\nB. 用真实导出的尺寸约定造合成包（单网格，画布 64，ppu 100）")
    verts = [(-24.0, -24.0), (24.0, -24.0), (24.0, 24.0), (-24.0, 24.0)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    for ppu in (100.0, 64.0):
        spec = RigSpec(meshes=[MeshSpec("ArtMesh1", verts,
                                       [(0, 1, 2), (0, 2, 3)], uvs)],
                       parameters=[ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0)],
                       canvas_width=64.0, canvas_height=64.0,
                       pixels_per_unit=ppu)
        folder = WORK / f"synth_ppu{int(ppu)}"
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        shutil.copy2(SRC / "gptest.texture_00.png", folder / "texture_00.png")
        (folder / "m.moc3").write_bytes(compile_static_rig(spec).to_bytes())
        manifest = folder / "m.model3.json"
        manifest.write_text(json.dumps({
            "Version": 3,
            "FileReferences": {"Moc": "m.moc3", "Textures": ["texture_00.png"]},
        }), encoding="utf-8")
        show(f"合成 ppu={ppu:g}", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
