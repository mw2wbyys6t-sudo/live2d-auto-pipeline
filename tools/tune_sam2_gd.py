#!/usr/bin/env python3
"""
Parameter sweep for the SAM2 + GroundingDINO backend, scored by internal
consistency (tools/score_layering.py) instead of mean coverage.

How it stays affordable: every knob below ``max_box_area_ratio`` / ``nms_iou``
/ ``max_boxes_per_query`` / ``max_total_boxes`` / ``face_gate_margin`` is
applied *after* GroundingDINO has spoken (``detect()`` filters the raw boxes,
``sam2_gd.py:411-425``). This tool therefore caches the raw per-prompt
detections and re-runs the production post-processing + composition on top of
them, so a config costs one SAM2 pass + composer + PNG writes instead of a
full re-detect. Nothing from ``core/`` is reimplemented here: the cache sits in
a subclass that overrides only :meth:`_detect_query`, so ``detect()``,
``segment_detailed()`` and ``layer()`` are the real production code paths.

Caveats measured, not assumed:
    * ``text_threshold`` is NOT sweepable this way — it feeds the token masks
      inside transformers' ``post_process_grounded_object_detection``, so a
      cached run cannot reproduce another value. Sweeping it needs a fresh
      network pass (``--no-cache-prompts`` forces re-detection per config).
    * ``box_threshold`` is a plain score cut on already-computed logits, so
      re-filtering a low-floor run is equivalent (verified by
      :meth:`SweepSegmenter.verify_floor_equivalence`).

Run:
    HF_HUB_OFFLINE=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe \\
        tools/tune_sam2_gd.py --sweep Work/sweep.json --out Work/tune

sweep.json:
    {"floor_box_threshold": 0.05,
     "configs": [{"name": "base", "set": {"box_threshold": 0.26}},
                 {"name": "mouth_prompt", "set": {"prompts": {"mouth": "mouth . open mouth"}}}]}
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.segment_engine.sam2_gd import Sam2GroundingDinoSegmenter  # noqa: E402
from core.segment_engine.layer_quality import (  # noqa: E402
    REQUIRED_PARTS,
    foreground_of,
    score,
)

DEFAULT_IMAGE = "docs/assets/demo_input.png"
DEFAULT_OUT = "Work/tune"

#: Knobs the sweep is allowed to move. Anything else in ``set`` is a hard error
#: so a typo cannot silently produce a config that equals another one.
SWEEPABLE = frozenset({
    "box_threshold", "text_threshold", "min_box_area_ratio",
    "max_box_area_ratio", "face_gate_margin", "max_boxes_per_query",
    "max_total_boxes", "nms_iou", "prompts",
})

#: The numeric members of SWEEPABLE, i.e. the instance attributes a config owns.
SWEEP_ATTRS = tuple(SWEEPABLE - {"prompts"})

#: Which of those are ints (everything else is a float).
INT_ATTRS = frozenset({"max_boxes_per_query", "max_total_boxes"})


class SweepSegmenter(Sam2GroundingDinoSegmenter):
    """Segmenter that re-uses raw GroundingDINO output across configs.

    The cached list is always the *floor* threshold's full output: production
    applies ``box_threshold`` inside
    :func:`post_process_grounded_object_detection` (``sam2_gd.py:442``), so a
    cache hit would otherwise hand ``detect()`` every floor-level box and the
    knob would silently do nothing. The score cut is re-applied on the way out.
    """

    def __init__(self, *args: Any, use_cache: bool = True,
                 floor_box_threshold: float = 0.05, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.use_cache = use_cache
        self.floor = float(floor_box_threshold)
        #: Each config starts from the production defaults. Without this the
        #: empty "base" config would run at the floor threshold, flood the
        #: ``max_total_boxes`` cap and truncate the later parts.
        self._defaults: Dict[str, Any] = {k: getattr(self, k) for k in SWEEP_ATTRS}
        self.box_threshold = self.floor
        self._raw_by_phrase: Dict[str, List[Any]] = {}
        self.network_seconds = 0.0
        self.network_passes = 0

    def _detect_query(self, rgb: Image.Image, phrase: str) -> List[Any]:
        """The only override: memoise the network call at the floor threshold."""
        if not self.use_cache:
            return super()._detect_query(rgb, phrase)
        if phrase not in self._raw_by_phrase:
            current = self.box_threshold
            self.box_threshold = self.floor
            try:
                t0 = time.time()
                raw = super()._detect_query(rgb, phrase)
            finally:
                self.box_threshold = current
            self.network_seconds += time.time() - t0
            self.network_passes += 1
            self._raw_by_phrase[phrase] = raw
        return [d for d in self._raw_by_phrase[phrase]
                if d.score >= self.box_threshold]

    def verify_floor_equivalence(self, image: Image.Image,
                                 floor: float, probe: float) -> Dict[str, Any]:
        """Is re-filtering the floor run the same as detecting at ``probe``?

        Compares the per-prompt box sets from the cached sweep against a fresh
        production pass at ``probe``. If this ever disagrees, the cached sweep
        is invalid for ``box_threshold`` and the sweep must run with
        ``--no-cache-prompts``.
        """
        rgb = image.convert("RGB")
        if not self._raw_by_phrase:
            self.box_threshold = floor
            self.detect(image)                  # fills the floor cache
        warm = {p: list(v) for p, v in self._raw_by_phrase.items()}
        self.box_threshold = probe
        filtered = {p: [d for d in boxes if d.score >= probe]
                    for p, boxes in warm.items()}
        direct = {p: Sam2GroundingDinoSegmenter._detect_query(self, rgb, p)
                  for p in warm}
        self.box_threshold = floor
        self._raw_by_phrase = warm

        mismatches = []
        for phrase, dets in direct.items():
            if sorted(d.box for d in filtered[phrase]) != sorted(d.box for d in dets):
                mismatches.append(phrase)
        return {"probe": probe, "prompts_compared": len(direct),
                "mismatched_prompts": mismatches}

    def apply_config(self, overrides: Dict[str, Any]) -> None:
        """Reset to the production defaults, then set this config's knobs."""
        unknown = set(overrides) - SWEEPABLE
        if unknown:
            raise ValueError(f"不可扫的旋钮: {sorted(unknown)}")
        if self.use_cache and "text_threshold" in overrides:
            raise ValueError(
                "text_threshold 参与 HF post_process_grounded_object_detection 的 "
                "token 掩码，缓存的 floor 前向无法复现别的取值；扫它要用 "
                "--no-cache-prompts")
        for key, value in self._defaults.items():
            setattr(self, key, value)
        prompts = dict(Sam2GroundingDinoSegmenter.PART_PROMPTS)
        for key, value in overrides.items():
            if key == "prompts":
                prompts.update(value)
            elif key in INT_ATTRS:
                setattr(self, key, int(value))
            else:
                setattr(self, key, float(value))
        self.PART_PROMPTS = prompts


def run_config(seg: SweepSegmenter, image: Image.Image, name: str,
               overrides: Dict[str, Any], out_root: Path) -> Dict[str, Any]:
    seg.apply_config(overrides)
    layers_dir = out_root / name / "layers"
    t0 = time.time()
    result = seg.layer(image, output_dir=str(layers_dir))
    wall = time.time() - t0
    scored = score(layers_dir, REQUIRED_PARTS, foreground_of(image))
    parts = scored.get("parts", {})
    row: Dict[str, Any] = {
        "name": name,
        "set": {k: v for k, v in overrides.items() if k != "prompts"},
        "prompt_overrides": sorted(overrides.get("prompts", {})),
        "wall_s": round(wall, 1),
        "network_s": round(seg.network_seconds, 1),
        "network_passes": seg.network_passes,
        "emitted": len(parts),
        "missing_parts": scored.get("missing_parts", []),
        "defect_count": scored.get("defect_count", -1),
        "defects": scored.get("defects", {}),
        "union_coverage": round(float(scored.get("union_coverage", 0.0)), 6),
        "missing_count": len(scored.get("missing_parts", [])),
        # A part emitted in the wrong place is worse than a part that is simply
        # absent (core/segment_engine/sam2_gd.py deliberately reports the
        # latter), so features_in_face violations are counted on their own.
        "wrong_place_parts": len(scored.get("defects", {}).get(
            "features_in_face", [])),
        "fired_groups": sorted(scored.get("defects", {})),
        "success": bool(result.get("success")),
        "notes": list(result.get("notes") or []),
        "per_part": {p: {k: v for k, v in row_.items()
                         if k in ("pixel_count", "coverage", "components")}
                     for p, row_ in parts.items()},
    }
    print(f"[{name:<22}] wall={row['wall_s']:>6.1f}s net={row['network_s']:>6.1f}s "
          f"passes={row['network_passes']:>3} parts={row['emitted']:>2} "
          f"defects={row['defect_count']:>2} wrong={row['wrong_place_parts']:>2} "
          f"union={row['union_coverage']:.4f} "
          f"missing={','.join(row['missing_parts']) or '-'}")
    print(f"{'':>7}fired: {','.join(row['fired_groups']) or 'none'}")
    for note in row["notes"]:
        # A truncated box cap means the config is not comparable with the
        # others: whole parts were dropped for running out of budget.
        print(f"    ! [note] {note}")
    for group, items in row["defects"].items():
        for msg in items:
            print(f"    - [{group}] {msg}")
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True, help="JSON file of configs")
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--no-cache-prompts", action="store_true",
                    help="re-run GroundingDINO for every config (needed to sweep "
                         "text_threshold honestly)")
    ap.add_argument("--verify-floor", type=float, default=None, metavar="PROBE",
                    help="after warming the cache, check that re-filtering at "
                         "PROBE equals a fresh detect at PROBE")
    args = ap.parse_args()

    spec = json.loads(Path(args.sweep).read_text(encoding="utf-8"))
    configs: Sequence[Dict[str, Any]] = spec.get("configs") or []
    if not configs:
        print("[FAIL] sweep 文件里没有 configs")
        return 2
    floor = float(spec.get("floor_box_threshold", 0.05))

    img_path = Path(args.image)
    if not img_path.is_file():
        print(f"[FAIL] image not found: {img_path}")
        return 2
    image = Image.open(img_path).convert("RGBA")

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    seg = SweepSegmenter(device=args.device, floor_box_threshold=floor,
                         use_cache=not args.no_cache_prompts)
    t0 = time.time()
    seg.load()
    print(f"[ok ] loaded in {time.time() - t0:.1f}s "
          f"({seg.detector_id} + {seg._sam_id}) floor_box_threshold={floor} "
          f"cache={seg.use_cache}")

    rows: List[Dict[str, Any]] = []
    for cfg in configs:
        name = str(cfg.get("name") or f"cfg{len(rows)}")
        rows.append(run_config(seg, image, name, cfg.get("set") or {}, out_root))
    if args.verify_floor is not None:
        eq = seg.verify_floor_equivalence(image, floor, args.verify_floor)
        rows.append({"name": "__floor_equivalence__", **eq})
        print(f"[floor] box_threshold re-filter equivalence at "
              f"{args.verify_floor}: "
              f"{'OK' if not eq['mismatched_prompts'] else eq['mismatched_prompts']}")

    out = out_root / "sweep_results.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    # Ranking deliberately ignores union_coverage: growing the union is exactly
    # what a wrong-place mask does. Fewer defects, then fewer misplaced parts,
    # then fewer missing parts.
    scored_rows = [r for r in rows if r.get("defect_count", -1) >= 0]
    best = min(scored_rows,
               key=lambda r: (r["defect_count"], r["wrong_place_parts"],
                              r["missing_count"]), default=None)
    print(f"\n[out] results -> {out}")
    if best:
        print(f"[best] {best['name']}: defects={best['defect_count']} "
              f"wrong={best['wrong_place_parts']} missing={best['missing_count']} "
              f"emitted={best['emitted']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
