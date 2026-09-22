#!/usr/bin/env python3
"""共用探针脚手架：把编译出的 moc3 包成可加载的最小模型包。

只做验证用（官方样例之外的自造素材），不进产品代码路径。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image


def write_package(folder: Path, moc3_bytes: bytes, name: str,
                  texture_size: int = 64,
                  color=(255, 0, 255, 255)) -> Path:
    """写出 model3.json + 单张贴图 + moc3，返回清单路径。"""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    (folder / f"{name}.moc3").write_bytes(moc3_bytes)
    Image.new("RGBA", (texture_size, texture_size), color).save(
        folder / "texture_00.png")
    manifest = folder / f"{name}.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3,
        "Meta": {"ArchiveName": name},
        "FileReferences": {"Moc": f"{name}.moc3",
                           "Textures": ["texture_00.png"]},
    }), encoding="utf-8")
    return manifest


def square(cx: float, cy: float, half: float):
    verts = [(cx - half, cy - half), (cx + half, cy - half),
             (cx + half, cy + half), (cx - half, cy + half)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    return verts, uvs, [(0, 1, 2), (0, 2, 3)]
