#!/usr/bin/env python3
"""生成一个带参数形变键型的模型包，跑官方内核受控像素验证并落盘证据。

运行：LIVE2D_TEST_PIXELS=1 .venv/Scripts/python.exe tools/verify_keyform_evidence.py
输出：Work/moc3-keyform-verification.json + Work/moc3-keyform-evidence/*.png
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drivers.live2d_runtime.moc3_verify import (  # noqa: E402
    verify_moc3_consistency, verify_moc3_load, verify_moc3_runtime,
)
from live2d_builder.exporter.moc3_lint import lint_document  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    KeyformShape, MeshSpec, ParameterSpec, RigSpec, compile_static_rig,
)

OUT = ROOT / "Work" / "moc3-keyform-evidence"


def _rig(deform: bool) -> RigSpec:
    half, canvas = 100.0, 512.0
    verts = [(-half, -half), (half, -half), (half, half), (-half, half)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    tris = [(0, 1, 2), (0, 2, 3)]
    shapes = ()
    if deform:
        shapes = tuple(
            KeyformShape(key_value=key,
                         vertices=[(x + key / 30.0 * 100.0, y)
                                   for x, y in verts])
            for key in (-30.0, 0.0, 30.0))
    mesh = MeshSpec("ArtMeshTest", verts, tris, uvs,
                    keyform_parameter_id="ParamAngleX" if deform else "",
                    keyform_shapes=shapes)
    return RigSpec(meshes=[mesh],
                   parameters=[ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0)],
                   canvas_width=canvas, canvas_height=canvas,
                   pixels_per_unit=100.0)


def _package(name: str, doc):
    from PIL import Image

    folder = ROOT / "Work" / "moc3-keyform-package" / name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    (folder / f"{name}.moc3").write_bytes(doc.to_bytes())
    Image.new("RGBA", (64, 64), (255, 0, 255, 255)).save(
        folder / "texture_00.png")
    manifest = folder / f"{name}.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3,
        "Meta": {"ArchiveName": name},
        "FileReferences": {"Moc": f"{name}.moc3",
                           "Textures": ["texture_00.png"]},
    }), encoding="utf-8")
    return manifest, folder / f"{name}.moc3"


def main() -> int:
    t0 = time.time()
    driven = compile_static_rig(_rig(deform=True))
    static = compile_static_rig(_rig(deform=False))
    evidence = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "driven_lint_issues": [str(i) for i in lint_document(driven)],
        "static_lint_issues": [str(i) for i in lint_document(static)],
        "keyform_structure": {
            "art_mesh_keyform_counts": driven.get("art_mesh.keyform_counts"),
            "band_indices": driven.get(
                "art_mesh.keyform_binding_band_indices"),
            "keys_values": driven.get("keys.values"),
            "parameter_binding_counts": driven.get(
                "parameter.keyform_binding_counts"),
            "keyform_position_begins": driven.get(
                "art_mesh_keyform.keyform_position_begin_indices"),
        },
    }

    driven_manifest, driven_moc3 = _package("keyform", driven)
    static_manifest, _ = _package("static", static)
    evidence["consistency"] = {
        "driven": verify_moc3_consistency(str(driven_moc3)),
        "static": verify_moc3_consistency(str(static_manifest.parent /
                                              "static.moc3")),
    }
    evidence["load"] = {"driven": verify_moc3_load(str(driven_manifest))}
    pixel_dir = OUT
    if pixel_dir.exists():
        shutil.rmtree(pixel_dir)
    pixel_dir.mkdir(parents=True)
    evidence["pixel_driven"] = verify_moc3_runtime(
        str(driven_manifest), evidence_dir=str(pixel_dir))
    evidence["pixel_static_negative_control"] = verify_moc3_runtime(
        str(static_manifest))
    for key in ("consistency", "load"):
        for name, result in evidence[key].items():
            for stream in ("stdout", "stderr"):
                result[stream] = result[stream][:400]
    for key in ("pixel_driven", "pixel_static_negative_control"):
        for stream in ("stdout", "stderr"):
            evidence[key][stream] = evidence[key][stream][:600]

    evidence["elapsed_seconds"] = round(time.time() - t0, 2)
    target = ROOT / "Work" / "moc3-keyform-verification.json"
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    ok = (evidence["driven_lint_issues"] == []
          and all(r["ok"] for r in evidence["consistency"].values())
          and evidence["load"]["driven"]["ok"]
          and evidence["pixel_driven"]["ok"]
          and not evidence["pixel_static_negative_control"]["ok"])
    print(json.dumps({
        "all_gates_green": ok,
        "keyform_structure": evidence["keyform_structure"],
        "driven_checks": evidence["pixel_driven"]["checks"],
        "static_negative_control_checks":
            evidence["pixel_static_negative_control"]["checks"],
        "evidence": str(target),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
