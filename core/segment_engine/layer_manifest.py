#!/usr/bin/env python3
"""
Live2D Master Agent - Layer Manifest v1

Defines the canonical contract for layered outputs. Every layerer must emit a
``layer_manifest.json`` next to its PNG files so downstream consumers (PSD
export, Live2D builder, preview) read one structure instead of guessing from
file names.

The contract separates two quality levels:

``preview_ready``
    Coarse parts are present; the result is usable for visual inspection only.

``rig_ready``
    Every part needed for left/right-independent rigging is present, with an
    explicit side. Color clustering can never satisfy this level, because it
    cannot tell a left sleeve from a right sleeve.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.segment_engine.part_split import DERIVED_PARTS

SCHEMA_VERSION = "1.0"

MANIFEST_FILENAME = "layer_manifest.json"

SOURCE_SEMANTIC = "semantic"
SOURCE_COLOR_CLUSTER = "color_cluster"

GROUP_ORDER: List[Dict[str, str]] = [
    {"id": "head_hair", "name_zh": "01_头部·头发", "order": 1},
    {"id": "head_face", "name_zh": "01_头部·面部", "order": 2},
    {"id": "body", "name_zh": "02_身体", "order": 3},
    {"id": "clothing", "name_zh": "03_服装", "order": 4},
    {"id": "lower", "name_zh": "04_下半身", "order": 5},
    {"id": "accessory", "name_zh": "05_配件", "order": 6},
]

_GROUP_IDS = {g["id"] for g in GROUP_ORDER}

PART_SPECS: Dict[str, Dict[str, Any]] = {
    "hair_back": {"name_zh": "头发_后", "group": "head_hair", "z": 10, "sides": False},
    "hair_bangs": {"name_zh": "头发_刘海", "group": "head_hair", "z": 60, "sides": False},
    "hair_side": {"name_zh": "头发_鬓发", "group": "head_hair", "z": 55, "sides": True},
    "hair_braid": {"name_zh": "头发_辫子", "group": "head_hair", "z": 12, "sides": True},
    "hair_ornament": {"name_zh": "头发_发饰", "group": "head_hair", "z": 65, "sides": False},
    "ribbon": {"name_zh": "头发_丝带", "group": "head_hair", "z": 66, "sides": True},
    "face_base": {"name_zh": "面部_脸", "group": "head_face", "z": 40, "sides": False},
    "cheek": {"name_zh": "面部_脸颊", "group": "head_face", "z": 41, "sides": False},
    "eyebrow": {"name_zh": "面部_眉毛", "group": "head_face", "z": 44, "sides": True},
    "eye_white": {"name_zh": "面部_眼白", "group": "head_face", "z": 45, "sides": True},
    "eye_iris": {"name_zh": "面部_瞳孔", "group": "head_face", "z": 46, "sides": True},
    "eyelash": {"name_zh": "面部_睫毛", "group": "head_face", "z": 47, "sides": True},
    "nose": {"name_zh": "面部_鼻", "group": "head_face", "z": 42, "sides": False},
    "mouth": {"name_zh": "面部_嘴", "group": "head_face", "z": 43, "sides": False},
    "neck": {"name_zh": "身体_脖子", "group": "body", "z": 30, "sides": False},
    "neck_shadow": {"name_zh": "身体_颈部阴影", "group": "body", "z": 31, "sides": False},
    "torso": {"name_zh": "身体_躯干", "group": "body", "z": 32, "sides": False},
    "arm": {"name_zh": "身体_手臂", "group": "body", "z": 33, "sides": True},
    "forearm": {"name_zh": "身体_前臂", "group": "body", "z": 34, "sides": True},
    "hand": {"name_zh": "身体_手", "group": "body", "z": 35, "sides": True},
    "waist": {"name_zh": "身体_腰带", "group": "body", "z": 36, "sides": False},
    "clothes_top": {"name_zh": "服装_上装", "group": "clothing", "z": 50, "sides": False},
    "outer_body": {"name_zh": "服装_外套主体", "group": "clothing", "z": 51, "sides": False},
    "outer_collar": {"name_zh": "服装_外套领口", "group": "clothing", "z": 52, "sides": False},
    "sleeve": {"name_zh": "服装_袖子", "group": "clothing", "z": 53, "sides": True},
    "cuff": {"name_zh": "服装_袖口", "group": "clothing", "z": 54, "sides": True},
    "collar": {"name_zh": "服装_领口", "group": "clothing", "z": 57, "sides": False},
    "skirt_hem": {"name_zh": "服装_裙摆", "group": "clothing", "z": 49, "sides": True},
    "waistband": {"name_zh": "服装_腰带", "group": "clothing", "z": 50, "sides": False},
    "shirt_collar": {"name_zh": "服装_衬衫领口", "group": "clothing", "z": 54, "sides": False},
    "shirt_fold": {"name_zh": "服装_衬衫褶皱", "group": "clothing", "z": 55, "sides": False},
    "brooch": {"name_zh": "服装_胸针", "group": "clothing", "z": 56, "sides": False},
    "clothes_bottom": {"name_zh": "服装_下装", "group": "clothing", "z": 49, "sides": False},
    "skirt_body": {"name_zh": "服装_裙主体", "group": "clothing", "z": 48, "sides": False},
    "skirt_front": {"name_zh": "服装_裙摆_前", "group": "clothing", "z": 47, "sides": False},
    "skirt_side": {"name_zh": "服装_裙摆_侧", "group": "clothing", "z": 46, "sides": True},
    "skirt_back": {"name_zh": "服装_裙摆_后", "group": "clothing", "z": 20, "sides": False},
    "lining": {"name_zh": "服装_内衬", "group": "clothing", "z": 45, "sides": False},
    "sock": {"name_zh": "下半身_袜", "group": "lower", "z": 15, "sides": True},
    "boot": {"name_zh": "下半身_靴", "group": "lower", "z": 16, "sides": True},
    "boot_cuff": {"name_zh": "下半身_靴口", "group": "lower", "z": 17, "sides": True},
    "leg": {"name_zh": "下半身_腿", "group": "lower", "z": 14, "sides": True},
    "watch": {"name_zh": "配件_怀表", "group": "accessory", "z": 70, "sides": False},
    "pendant": {"name_zh": "配件_挂饰", "group": "accessory", "z": 71, "sides": False},
    "accessories": {"name_zh": "配件_未分类", "group": "accessory", "z": 69, "sides": False},
}

_UNKNOWN_SPEC = {
    "name_zh": "未识别部件",
    "group": "accessory",
    "z": 999,
    "sides": False,
}

ALIASES: Dict[str, Tuple[str, Optional[str]]] = {
    "scalp": ("hair_back", None),
    "hair_front": ("hair_bangs", None),
    "hair_mid": ("hair_bangs", None),
    "face": ("face_base", None),
    "eyes": ("eye_white", None),
    "eyes_left": ("eye_white", "L"),
    "eyes_right": ("eye_white", "R"),
    "iris": ("eye_iris", None),
    "irises": ("eye_iris", None),
    "eyebrows": ("eyebrow", None),
    "arms": ("arm", None),
    "hands": ("hand", None),
    "legs": ("leg", None),
    "clothes_inner": ("clothes_bottom", None),
    "skirt": ("skirt_body", None),
    "tail_ears": ("accessories", None),
    "effects": ("accessories", None),
    "background": ("accessories", None),
    "other": ("accessories", None),
    "skin": ("face_base", None),
    "头发": ("hair_back", None),
    "头发_后": ("hair_back", None),
    "头发_前": ("hair_bangs", None),
    "脸": ("face_base", None),
    "眼睛_白": ("eye_white", None),
    "眼睛_瞳": ("eye_iris", None),
    "眉毛": ("eyebrow", None),
    "嘴巴": ("mouth", None),
    "衣服": ("clothes_top", None),
    "衣服_暗": ("clothes_top", None),
}

REQUIRED_FOR_RIG: List[str] = [
    "hair_back",
    "hair_bangs",
    "face_base",
    "eyebrow:L",
    "eyebrow:R",
    "eye_white:L",
    "eye_white:R",
    "mouth",
    "neck",
    "clothes_top",
    "arm:L",
    "arm:R",
    "hand:L",
    "hand:R",
    "leg:L",
    "leg:R",
]

REQUIRED_FOR_PREVIEW: List[str] = [
    "hair_back",
    "face_base",
    "mouth",
    "neck",
    "clothes_top",
]

_UNSIDED_LIMBS = {
    "arm": "手臂",
    "forearm": "前臂",
    "hand": "手",
    "leg": "腿",
    "sock": "袜",
    "boot": "靴",
}


def _split_suffix(name: str) -> Tuple[str, Optional[str]]:
    lowered = name.strip().lower()
    for suffix, side in (("_left", "L"), ("_right", "R"), ("_l", "L"), ("_r", "R")):
        if lowered.endswith(suffix):
            return lowered[: -len(suffix)], side
    if lowered.endswith("_左"):
        return lowered[:-2], "L"
    if lowered.endswith("_右"):
        return lowered[:-2], "R"
    return lowered, None


def resolve_part(raw_name: str) -> Tuple[str, str, Optional[str]]:
    """Resolve an emitted layer name to ``(part_id, canonical_base, side)``."""
    base, side = _split_suffix(raw_name or "")

    if base in PART_SPECS:
        spec_sides = bool(PART_SPECS[base].get("sides"))
        return base, base, side if spec_sides else None

    if base in ALIASES:
        canonical, alias_side = ALIASES[base]
        spec_sides = bool(PART_SPECS.get(canonical, {}).get("sides"))
        resolved_side = side or alias_side
        return canonical, canonical, resolved_side if spec_sides else None

    return base or "unknown", "unknown", side


def _qualified(base: str, side: Optional[str]) -> str:
    return f"{base}:{side}" if side else base


@dataclass
class LayerEntry:
    id: str
    name_zh: str
    group: str
    side: Optional[str]
    z: int
    file: str
    bbox: Optional[List[int]] = None
    pixel_count: int = 0
    confidence: Optional[float] = None
    occluded_by: List[str] = field(default_factory=list)
    amodal_completed: bool = False
    derived: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name_zh": self.name_zh,
            "group": self.group,
            "side": self.side,
            "z": self.z,
            "file": self.file,
            "bbox": self.bbox,
            "pixel_count": self.pixel_count,
            "confidence": self.confidence,
            "occluded_by": list(self.occluded_by),
            "amodal_completed": bool(self.amodal_completed),
            "derived": bool(self.derived),
        }


@dataclass
class LayerManifest:
    source: str
    canvas: Dict[str, int]
    layers: List[LayerEntry]
    rig_ready: bool
    preview_ready: bool
    missing_required: List[str]
    missing_preview: List[str]
    warnings: List[str]
    schema_version: str = SCHEMA_VERSION
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "source": self.source,
            "canvas": dict(self.canvas),
            "rig_ready": self.rig_ready,
            "preview_ready": self.preview_ready,
            "draw_order": [entry.id for entry in sorted(self.layers, key=lambda e: (e.z, e.id))],
            "groups": list(GROUP_ORDER),
            "layers": [entry.to_dict() for entry in sorted(self.layers, key=lambda e: (e.z, e.id))],
            "missing_required": list(self.missing_required),
            "missing_preview": list(self.missing_preview),
            "warnings": list(self.warnings),
        }


def build_manifest(
    layers: Iterable[Dict[str, Any]],
    source: str,
    canvas: Tuple[int, int],
    warnings: Optional[Iterable[str]] = None,
) -> LayerManifest:
    """Build a manifest from layerer output without inventing missing parts."""
    entries: List[LayerEntry] = []
    emitted: set[str] = set()
    extra_warnings: List[str] = list(warnings or [])

    for layer in layers:
        raw = str(layer.get("name") or "").strip()
        if not raw:
            continue
        part_id, canonical, side = resolve_part(raw)
        spec = PART_SPECS.get(canonical, _UNKNOWN_SPEC)
        if canonical == "unknown":
            extra_warnings.append(f"未识别部件：{raw}")

        path = str(layer.get("path") or "")
        confidence = layer.get("confidence")
        entries.append(
            LayerEntry(
                id=raw,
                name_zh=str(layer.get("part_name") or spec["name_zh"]),
                group=str(spec["group"]),
                side=side,
                z=int(layer.get("z", spec["z"])),
                file=Path(path).name if path else f"{raw}.png",
                bbox=[int(v) for v in layer.get("bbox", [])] or None,
                pixel_count=int(layer.get("pixel_count", 0) or 0),
                confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
                occluded_by=list(layer.get("occluded_by", []) or []),
                amodal_completed=bool(layer.get("completed", layer.get("amodal_completed", False))),
                derived=canonical in DERIVED_PARTS,
            )
        )
        emitted.add(_qualified(canonical, side))

    if source == SOURCE_COLOR_CLUSTER:
        extra_warnings.append("颜色聚类结果仅用于预览，不可用于 Live2D 绑定")

    for base, label in _UNSIDED_LIMBS.items():
        if base in emitted:
            extra_warnings.append(f"{label}未区分左右，无法独立绑定")

    missing_preview = [q for q in REQUIRED_FOR_PREVIEW if q not in emitted]
    missing_required = [q for q in REQUIRED_FOR_RIG if q not in emitted]

    rig_ready = source == SOURCE_SEMANTIC and not missing_required
    preview_ready = not missing_preview
    if not preview_ready:
        extra_warnings.append("缺少必要部件，无法进入预览/分层检查")

    return LayerManifest(
        source=source,
        canvas={"width": int(canvas[0]), "height": int(canvas[1])},
        layers=entries,
        rig_ready=rig_ready,
        preview_ready=preview_ready,
        missing_required=missing_required,
        missing_preview=missing_preview,
        warnings=extra_warnings,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def write_manifest(output_dir: str, manifest: LayerManifest) -> str:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / MANIFEST_FILENAME
    with path.open("w", encoding="utf-8") as handle:
        json.dump(manifest.to_dict(), handle, ensure_ascii=False, indent=2)
    return str(path.resolve())


def validate_manifest(data: Dict[str, Any]) -> List[str]:
    """Return contract violations; empty list means valid."""
    errors: List[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version 应为 {SCHEMA_VERSION}")

    canvas = data.get("canvas") or {}
    if not isinstance(canvas.get("width"), int) or canvas.get("width", 0) <= 0:
        errors.append("canvas.width 必须为正整数")
    if not isinstance(canvas.get("height"), int) or canvas.get("height", 0) <= 0:
        errors.append("canvas.height 必须为正整数")

    layers = data.get("layers")
    if not isinstance(layers, list):
        return errors + ["layers 必须为数组"]

    seen: set[str] = set()
    for layer in layers:
        for key in ("id", "name_zh", "group", "z", "file"):
            if key not in layer:
                errors.append(f"图层缺少字段 {key}: {layer!r}")
                break
        layer_id = layer.get("id")
        if layer_id in seen:
            errors.append(f"图层 id 重复: {layer_id}")
        seen.add(layer_id)
        if layer.get("group") not in _GROUP_IDS:
            errors.append(f"未知分组 {layer.get('group')!r}（图层 {layer_id}）")
        if layer.get("side") not in (None, "L", "R"):
            errors.append(f"side 只能是 L/R/null（图层 {layer_id}）")

    for key in ("rig_ready", "preview_ready"):
        if not isinstance(data.get(key), bool):
            errors.append(f"{key} 必须为布尔值")

    return errors


def load_manifest(output_dir: str) -> Dict[str, Any]:
    path = Path(output_dir) / MANIFEST_FILENAME
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)
