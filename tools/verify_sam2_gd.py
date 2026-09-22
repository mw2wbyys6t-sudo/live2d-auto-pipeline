#!/usr/bin/env python3
"""
Real GPU end-to-end verification of the SAM2 + GroundingDINO backend.

This is NOT a unit test: it loads the actual weights, runs them on the actual
device, and fails loudly (non-zero exit) if anything is unverifiable.

Run:
    HF_ENDPOINT=https://hf-mirror.com PYTHONIOENCODING=utf-8 \\
        .venv/Scripts/python.exe tools/verify_sam2_gd.py [--image PATH] [--out DIR]

Exit codes:
    0  every check below passed
    1  at least one check failed (printed as FAIL with the reason)
    2  the backend could not even load (ModelUnavailable)

Checks
    * torch + CUDA actually available (no silent CPU downgrade)
    * GroundingDINO + SAM2 weights load from the mirror/cache
    * per-part coverage/confidence reported for all 15 standard parts
    * a part with no detection is listed in ``missing_parts``, not emitted as
      an all-zero mask
    * layer PNGs + composite preview are written
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from core.segment_engine.sam2_gd import (
    METHOD_SAM2,
    ModelUnavailable,
    Sam2GroundingDinoSegmenter,
)
from core.segment_engine.semantic import SemanticSegmenter

DEFAULT_IMAGE = "docs/assets/demo_input.png"
DEFAULT_OUT = "Work/sam2-gd"

# Parts that must come back non-empty for this to count as a real separation.
# Everything else is reported but not required (a given illustration may
# genuinely hide e.g. hands or legs).
REQUIRED_PARTS = [
    "hair_back", "hair_front", "face", "eyes_left", "eyes_right", "neck",
    "clothes_top",
]


#: 判定下限：低于这两条就是「跑通了但没分出来」，不算通过
MIN_DETECTED_PARTS = 5
MIN_MEAN_COVERAGE = 0.01


def _tile(label: str, sub: str, rgb: np.ndarray, tint=(0, 255, 120)) -> Image.Image:
    """One montage cell: mask tinted over a dark checker, with captions."""
    h, w = rgb.shape[:2]
    canvas = np.full((h, w, 3), 28, dtype=np.uint8)
    block = 16
    yy, xx = np.mgrid[0:h, 0:w]
    checker = ((yy // block + xx // block) % 2).astype(bool)
    canvas[checker] = 40
    overlay = canvas.copy()
    mask = rgb[..., 0] > 127
    overlay[mask] = (
        0.45 * overlay[mask] + 0.55 * np.array(tint, dtype=np.float64)
    ).astype(np.uint8)
    img = Image.fromarray(overlay, "RGB")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, w - 1, 22], fill=(0, 0, 0))
    draw.text((4, 3), label, fill=(255, 255, 255))
    draw.text((4, 26), sub, fill=tint)
    return img


def build_montage(
    image: Image.Image,
    masks: dict,
    report: dict,
    boxes: dict,
    out_path: Path,
) -> None:
    rgb = np.array(image.convert("RGB"))
    h, w = rgb.shape[:2]
    scale = 380.0 / max(h, w)
    tw, th = max(1, int(w * scale)), max(1, int(h * scale))

    cells = []
    base = Image.fromarray(
        np.array(Image.fromarray(rgb).resize((tw, th), Image.LANCZOS)), "RGB"
    ).copy()
    d = ImageDraw.Draw(base)
    for part, dets in boxes.items():
        for det in dets:
            x0, y0, x1, y1 = det.box
            d.rectangle(
                [x0 * scale, y0 * scale, x1 * scale, y1 * scale],
                outline=(255, 60, 60), width=1,
            )
    cells.append(("GroundingDINO boxes", f"{report['box_count']} boxes",
                  np.array(base)))

    for part in SemanticSegmenter.STANDARD_PARTS:
        ev = report["parts_report"][part]
        sub = ("cov=%.4f conf=%.2f iou=%.2f" % (ev["coverage"], ev["confidence"], ev["sam_iou"]))
        if part in masks:
            m = np.asarray(masks[part], dtype=bool)
            m_small = np.array(Image.fromarray(m.astype(np.uint8) * 255, "L").resize(
                (tw, th), Image.NEAREST))
            cells.append((part, sub, m_small, (0, 255, 120) if ev["detected"] else (255, 80, 80)))
        else:
            cells.append((part, "MISSING - no mask emitted",
                          np.zeros((th, tw, 3), dtype=np.uint8), (200, 40, 40)))

    cols = 4
    rows = (len(cells) + cols - 1) // cols
    pad, cap = 8, 0
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + pad + 40) + pad), (12, 12, 12))
    for i, cell in enumerate(cells):
        label, sub, arr = cell[0], cell[1], cell[2]
        tint = cell[3] if len(cell) > 3 else (0, 255, 120)
        arr3 = arr if arr.ndim == 3 else np.dstack([arr] * 3)
        tile = _tile(label, sub, arr3, tint=tint).resize((tw, th + 40), Image.NEAREST)
        r, c = divmod(i, cols)
        sheet.paste(tile, (pad + c * (tw + pad), pad + r * (th + pad + 40)))
    sheet.save(out_path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    # A previous run's PNGs would otherwise linger and look like this run
    # produced them (it did happen: a stale mouth.png survived a run where
    # mouth was correctly reported missing).
    layers_dir = out_dir / "layers"
    if layers_dir.is_dir():
        for stale in layers_dir.iterdir():
            if stale.is_file():
                stale.unlink()
    failures: list[str] = []

    import torch
    print(f"[env] python      = {sys.version.split()[0]}")
    print(f"[env] torch       = {torch.__version__}")
    print(f"[env] HF_ENDPOINT = {os.environ.get('HF_ENDPOINT')}")
    if not torch.cuda.is_available():
        print("[FAIL] torch.cuda.is_available() is False - no CUDA device. "
              "This verification requires a real GPU run.")
        return 2
    props = torch.cuda.get_device_properties(0)
    print(f"[env] gpu         = {props.name} ({props.total_memory / 1e9:.1f} GB)")

    img_path = Path(args.image)
    if not img_path.is_file():
        print(f"[FAIL] image not found: {img_path}")
        return 2
    image = Image.open(img_path).convert("RGBA")
    print(f"[img] {img_path} {image.size[0]}x{image.size[1]}")

    seg = Sam2GroundingDinoSegmenter(device=args.device)
    t0 = time.time()
    try:
        seg.load()
    except ModelUnavailable as exc:
        print(f"[FAIL] ModelUnavailable on load(): {exc}")
        return 2
    print(f"[ok ] models loaded in {time.time() - t0:.1f}s "
          f"({seg.detector_id} + {seg._sam_id}) method={seg.method}")

    try:
        result = seg.layer(image, output_dir=str(layers_dir))
    except ModelUnavailable as exc:
        print(f"[FAIL] layer() raised ModelUnavailable: {exc}")
        return 2

    # layer() spreads the whole report into its return dict; use that so the
    # numbers printed below are the ones the caller actually receives.
    report = result
    detections = seg.detect(image)

    # Read the masks back off disk: verifying the written PNGs is stronger than
    # trusting the in-memory arrays the pipeline saw.
    masks = {}
    for layer in result["layers"]:
        p = Path(layer["path"])
        if p.is_file():
            masks[layer["part_name"]] = np.array(Image.open(p).convert("RGBA"))[..., 3] > 0

    print(f"\n[method] {result['method']}  device={result['device']}  "
          f"elapsed={result['elapsed_s']}s  boxes={result['box_count']}")
    if result["method"] != METHOD_SAM2:
        failures.append(f"method is {result['method']!r}, not {METHOD_SAM2!r} "
                        f"(SAM2 was substituted!)")

    print(f"\n{'part':<16}{'det':>4}{'boxes':>7}{'conf':>7}{'sam_iou':>9}"
          f"{'coverage':>11}{'pixels':>10}")
    print("-" * 64)
    for part in SemanticSegmenter.STANDARD_PARTS:
        ev = report["parts_report"][part]
        print(f"{part:<16}{str(ev['detected']):>4}{ev['box_count']:>7}"
              f"{ev['confidence']:>7.3f}{ev['sam_iou']:>9.3f}"
              f"{ev['coverage']:>11.6f}{ev['pixel_count']:>10d}")
        if ev["detected"] and ev["coverage"] <= 0:
            failures.append(f"{part} reported detected with coverage 0")
        if part in REQUIRED_PARTS and not ev["detected"]:
            failures.append(f"required part {part} has no mask "
                            f"({'; '.join(ev['notes']) or 'no notes'})")

    print("-" * 64)
    print(f"mean coverage over 15 parts: {report['mean_coverage']:.6f}")
    print(f"detected: {len(report['detected_parts'])}/15  "
          f"missing: {report['missing_parts']}")
    if report.get("dropped_boxes"):
        print("dropped false-positive boxes:")
        for b in report["dropped_boxes"]:
            print(f"  - {b}")
    if report.get("notes"):
        print("notes: " + " | ".join(report["notes"]))

    # no all-zero mask may reach the layer list
    for layer in result["layers"]:
        if layer["pixel_count"] <= 0:
            failures.append(f"layer {layer['part_name']} written with 0 pixels")
    if not result["success"]:
        failures.append("layer() returned success=False")
    if result["layer_count"] != len(result["layers"]):
        failures.append("layer_count != len(layers)")
    for key in ("preview_path", "composite_preview"):
        p = result.get(key)
        if not p or not Path(p).is_file():
            failures.append(f"{key} missing on disk: {p}")

    # Cross-check the directory contents against the reported layer list: a
    # stale PNG surviving from an earlier run must not look like a result.
    on_disk = {p.stem for p in Path(result["output_dir"]).glob("*.png")
               if p.stem not in ("preview", "composite_preview")}
    reported = {l["part_name"] for l in result["layers"]}
    if on_disk != reported:
        failures.append(f"PNGs on disk {sorted(on_disk)} != reported layers "
                        f"{sorted(reported)}")
    for part in result["missing_parts"]:
        if part in on_disk:
            failures.append(f"missing part {part} nevertheless has a PNG on disk")

    # 「跑通了」不等于「分出来了」：没有覆盖率门槛时，一张全黑掩码也能让
    # 上面所有检查全绿。这两条是判定的下限，不是理想值。
    detected = report.get("detected_parts") or []
    if len(detected) < MIN_DETECTED_PARTS:
        failures.append(f"只检出 {len(detected)} 个部件，低于复核线 "
                        f"{MIN_DETECTED_PARTS}")
    if float(report.get("mean_coverage") or 0.0) < MIN_MEAN_COVERAGE:
        failures.append(f"平均覆盖率 {report.get('mean_coverage')} < "
                        f"{MIN_MEAN_COVERAGE}（碎片噪声级别）")
    # 报告说检出的部件，掩码必须真的在那儿
    lying = [p for p, row in report["parts_report"].items()
             if bool(row["detected"]) != (
                 p in masks and bool(np.asarray(masks[p]).any())
                 and float(row["coverage"]) > 0.0)]
    if lying:
        failures.append(f"parts_report 与实际掩码不符: {lying}")
    print(f"\n[coverage] 检出 {len(detected)}/{len(report['parts_report'])} 部件，"
          f"平均覆盖率 {report.get('mean_coverage')}")

    montage = out_dir / "sam2_gd_montage.png"
    build_montage(image, masks, report, detections, montage)
    print(f"\n[out] montage  -> {montage}")
    print(f"[out] layers   -> {result['output_dir']}  "
          f"({result['layer_count']} PNGs)")
    print(f"[out] vram     -> {torch.cuda.memory_allocated() / 1e9:.2f} GB alloc, "
          f"{torch.cuda.memory_reserved() / 1e9:.2f} GB reserved")

    if failures:
        print("\n=== FAILURES ===")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1
    print("\n=== PASS: every check verified on GPU ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
