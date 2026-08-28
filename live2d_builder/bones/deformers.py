#!/usr/bin/env python3
"""Live2D bone hierarchy and warp/rotation deformer generators.

Defines the standard 32-bone Cubism 4 skeleton and the warp/rotation
deformers used for hair swing, body sway, and eye tracking.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from core.logger import get_logger

log = get_logger("rigging.deformers")


# ======================================================================
# Bone hierarchy
# ======================================================================

class BoneHierarchy:
    """Build a standard Live2D Cubism 4 bone tree adapting to available parts.

    The skeleton follows the canonical 32-bone layout::

        Root
         +-- Body
         |    +-- Torso
         |    |    +-- Chest (breathing pivot)
         |    |    +-- Waist
         |    +-- Neck
         |    |    +-- Head
         |    |         +-- Face
         |    |         +-- Hair_Back
         |    |         +-- Hair_Side_L
         |    |         +-- Hair_Side_R
         |    |         +-- Hair_Front
         |    |         +-- Hair_Top
         |    |         +-- Ear_L
         |    |         +-- Ear_R
         |    |         +-- Eye_L
         |    |         |    +-- Eyeball_L
         |    |         |    +-- Eyelash_L
         |    |         |    +-- Brow_L
         |    |         +-- Eye_R
         |    |         |    +-- Eyeball_R
         |    |         |    +-- Eyelash_R
         |    |         |    +-- Brow_R
         |    |         +-- Nose
         |    |         +-- Mouth
         |    +-- ArmBack_L
         |    |    +-- ForearmBack_L
         |    +-- ArmBack_R
         |    |    +-- ForearmBack_R
         |    +-- Skirt
         |    +-- Leg_L
         |    |    +-- Calf_L
         |    |         +-- Foot_L
         |    +-- Leg_R
         |         +-- Calf_R
         |              +-- Foot_R
    """

    STANDARD_BONES: Dict[str, Dict] = {
        # name -> {parent, group, order}
        "Root":          {"parent": None,            "group": "root",    "order": 0},
        "Body":          {"parent": "Root",          "group": "body",    "order": 1},
        "Torso":         {"parent": "Body",          "group": "body",    "order": 2},
        "Chest":         {"parent": "Torso",         "group": "body",    "order": 3},
        "Waist":         {"parent": "Torso",         "group": "body",    "order": 4},
        "Neck":          {"parent": "Body",          "group": "body",    "order": 5},
        "Head":          {"parent": "Neck",          "group": "head",    "order": 6},
        "Face":          {"parent": "Head",          "group": "face",    "order": 7},
        "Hair_Back":     {"parent": "Head",          "group": "hair",    "order": 8},
        "Hair_Side_L":   {"parent": "Head",          "group": "hair",    "order": 9},
        "Hair_Side_R":   {"parent": "Head",          "group": "hair",    "order": 10},
        "Hair_Front":    {"parent": "Head",          "group": "hair",    "order": 11},
        "Hair_Top":      {"parent": "Head",          "group": "hair",    "order": 12},
        "Ear_L":         {"parent": "Head",          "group": "ears",    "order": 13},
        "Ear_R":         {"parent": "Head",          "group": "ears",    "order": 14},
        "Eye_L":         {"parent": "Head",          "group": "eyes",    "order": 15},
        "Eyeball_L":     {"parent": "Eye_L",         "group": "eyes",    "order": 16},
        "Eyelash_L":     {"parent": "Eye_L",         "group": "eyes",    "order": 17},
        "Brow_L":        {"parent": "Head",          "group": "brows",   "order": 18},
        "Eye_R":         {"parent": "Head",          "group": "eyes",    "order": 19},
        "Eyeball_R":     {"parent": "Eye_R",         "group": "eyes",    "order": 20},
        "Eyelash_R":     {"parent": "Eye_R",         "group": "eyes",    "order": 21},
        "Brow_R":        {"parent": "Head",          "group": "brows",   "order": 22},
        "Nose":          {"parent": "Head",          "group": "nose",    "order": 23},
        "Mouth":         {"parent": "Head",          "group": "mouth",   "order": 24},
        "ArmBack_L":     {"parent": "Body",          "group": "arms",    "order": 25},
        "ForearmBack_L": {"parent": "ArmBack_L",     "group": "arms",    "order": 26},
        "ArmBack_R":     {"parent": "Body",          "group": "arms",    "order": 27},
        "ForearmBack_R": {"parent": "ArmBack_R",     "group": "arms",    "order": 28},
        "Skirt":         {"parent": "Waist",         "group": "clothes", "order": 29},
        "Leg_L":         {"parent": "Waist",         "group": "legs",    "order": 30},
        "Calf_L":        {"parent": "Leg_L",         "group": "legs",    "order": 31},
        "Foot_L":        {"parent": "Calf_L",        "group": "legs",    "order": 32},
        "Leg_R":         {"parent": "Waist",         "group": "legs",    "order": 33},
        "Calf_R":        {"parent": "Leg_R",         "group": "legs",    "order": 34},
        "Foot_R":        {"parent": "Calf_R",        "group": "legs",    "order": 35},
    }

    # Layer-name to bone mapping for automatic assignment
    LAYER_BONE_MAP: Dict[str, str] = {
        "hair_back": "Hair_Back",
        "hair_shadow_back": "Hair_Back",
        "hair_back_left": "Hair_Side_L",
        "hair_back_right": "Hair_Side_R",
        "hair_front": "Hair_Front",
        "hair_side": "Hair_Front",
        "hair_top": "Hair_Top",
        "face_base": "Face",
        "face_blush": "Face",
        "cheek": "Face",
        "ear_left": "Ear_L",
        "ear_right": "Ear_R",
        "neck": "Neck",
        "chest": "Chest",
        "waist_hips": "Waist",
        "body": "Torso",
        "eye_left": "Eye_L",
        "eye_l": "Eye_L",
        "eyeball_left": "Eyeball_L",
        "eyeball_l": "Eyeball_L",
        "eyelash_left": "Eyelash_L",
        "eyelash_l": "Eyelash_L",
        "eye_right": "Eye_R",
        "eye_r": "Eye_R",
        "eyeball_right": "Eyeball_R",
        "eyeball_r": "Eyeball_R",
        "eyelash_right": "Eyelash_R",
        "eyelash_r": "Eyelash_R",
        "brow_left": "Brow_L",
        "brow_l": "Brow_L",
        "eyebrow_l": "Brow_L",
        "brow_right": "Brow_R",
        "brow_r": "Brow_R",
        "eyebrow_r": "Brow_R",
        "nose": "Nose",
        "mouth_cavity": "Mouth",
        "mouth_tongue": "Mouth",
        "mouth_teeth": "Mouth",
        "mouth_lowerlip": "Mouth",
        "mouth_upperlip": "Mouth",
        "mouth": "Mouth",
        "clothes_inner": "Torso",
        "clothes_outer": "Torso",
        "clothes": "Torso",
        "accessories": "Torso",
        "upper_arm_back_left": "ArmBack_L",
        "forearm_back_left": "ForearmBack_L",
        "hand_back_left": "ForearmBack_L",
        "upper_arm_back_right": "ArmBack_R",
        "forearm_back_right": "ForearmBack_R",
        "hand_back_right": "ForearmBack_R",
        "thigh_left": "Leg_L",
        "calf_left": "Calf_L",
        "foot_left": "Foot_L",
        "thigh_right": "Leg_R",
        "calf_right": "Calf_R",
        "foot_right": "Foot_R",
        "skirt": "Skirt",
        "bg": "Root",
        "background": "Root",
    }

    def __init__(self) -> None:
        self._tree: Dict[str, Dict] = {}
        self._layer_assignments: Dict[str, str] = {}

    def build(
        self,
        layer_names: List[str],
        centroids: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> Dict:
        """Build the bone tree, keeping only bones that have matching layers.

        The Root, Body, and Head bones are always included. Bones whose
        group has no corresponding layer are pruned (but the structural
        bones Head/Body remain).

        Args:
            layer_names: List of available layer names (lower-case normalised).
            centroids:  Optional dict of layer_name -> (x, y) centroid used
                        to compute bone positions.

        Returns:
            dict with ``tree`` (nested), ``flat`` (list), and ``layer_assignments``.
        """
        normalised = [self._normalise(n) for n in layer_names]
        present_groups: set = set()
        assignments: Dict[str, str] = {}

        for orig, norm in zip(layer_names, normalised):
            bone = self.LAYER_BONE_MAP.get(norm, "Face")
            assignments[orig] = bone
            info = self.STANDARD_BONES.get(bone)
            if info:
                present_groups.add(info["group"])

        # Always keep root/body/head structural bones
        for structural in ("Root", "Body", "Neck", "Head", "Torso"):
            self.STANDARD_BONES.get(structural)  # ensure exists

        # Build flat list
        flat: List[Dict] = []
        for name, info in self.STANDARD_BONES.items():
            if name not in ("Root", "Body", "Neck", "Head", "Torso"):
                if info["group"] not in present_groups:
                    continue
            flat.append({
                "name": name,
                "parent": info["parent"],
                "group": info["group"],
                "order": info["order"],
                "children": [],
            })

        # Wire children
        by_name = {b["name"]: b for b in flat}
        for b in flat:
            if b["parent"] and b["parent"] in by_name:
                by_name[b["parent"]]["children"].append(b["name"])

        # Compute positions
        positions = self.get_bone_positions(centroids or {})
        for b in flat:
            pos = positions.get(b["name"], (0.0, 0.0))
            b["x"] = round(pos[0], 2)
            b["y"] = round(pos[1], 2)

        self._tree = by_name.get("Root", {"name": "Root", "children": []})
        self._layer_assignments = assignments

        log.info(f"Built bone hierarchy with {len(flat)} bones")
        return {
            "tree": self._tree,
            "flat": flat,
            "layer_assignments": assignments,
        }

    def to_json(self) -> List[Dict]:
        """Export bones in a Cubism Editor import-friendly format.

        Returns:
            List of bone dicts with keys: name, parent, x, y, length,
            index, parent_index.
        """
        result: List[Dict] = []
        flat_bones: List[Dict] = []

        def _collect(node: Dict) -> None:
            flat_bones.append(node)
            for child_name in node.get("children", []):
                # children are names, look up from _tree
                pass

        # Rebuild from STANDARD_BONES ordering
        for name, info in self.STANDARD_BONES.items():
            flat_bones.append({"name": name, "parent": info["parent"]})

        name_to_idx = {b["name"]: i for i, b in enumerate(flat_bones)}
        for i, b in enumerate(flat_bones):
            parent_idx = name_to_idx.get(b["parent"], -1) if b["parent"] else -1
            result.append({
                "name": b["name"],
                "parent": b["parent"] or "",
                "index": i,
                "parent_index": parent_idx,
                "x": 0.0,
                "y": 0.0,
                "length": 0.0,
            })
        return result

    def get_bone_positions(
        self,
        centroids: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> Dict[str, Tuple[float, float]]:
        """Calculate default bone positions based on layer centroids or a
        standard proportion model.

        Uses an 800x1000 canvas with the character centred. Values are
        approximate and intended as starting points for Cubism Editor.
        """
        positions: Dict[str, Tuple[float, float]] = {
            "Root":          (0.0,    0.0),
            "Body":          (0.0,   50.0),
            "Torso":         (0.0,  120.0),
            "Chest":         (0.0,  100.0),
            "Waist":         (0.0,  200.0),
            "Neck":          (0.0,   60.0),
            "Head":          (0.0,  -40.0),
            "Face":          (0.0,  -30.0),
            "Hair_Back":     (0.0,  -80.0),
            "Hair_Side_L":   (-60.0, -40.0),
            "Hair_Side_R":   ( 60.0, -40.0),
            "Hair_Front":    (0.0,  -90.0),
            "Hair_Top":      (0.0, -120.0),
            "Ear_L":         (-70.0, -20.0),
            "Ear_R":         ( 70.0, -20.0),
            "Eye_L":         (-35.0, -40.0),
            "Eyeball_L":     (-35.0, -40.0),
            "Eyelash_L":     (-35.0, -30.0),
            "Brow_L":        (-35.0, -60.0),
            "Eye_R":         ( 35.0, -40.0),
            "Eyeball_R":     ( 35.0, -40.0),
            "Eyelash_R":     ( 35.0, -30.0),
            "Brow_R":        ( 35.0, -60.0),
            "Nose":          (0.0,  -10.0),
            "Mouth":         (0.0,   10.0),
            "ArmBack_L":     (-90.0,  80.0),
            "ForearmBack_L": (-130.0, 150.0),
            "ArmBack_R":     ( 90.0,  80.0),
            "ForearmBack_R": ( 130.0, 150.0),
            "Skirt":         (0.0,  220.0),
            "Leg_L":         (-30.0, 240.0),
            "Calf_L":        (-30.0, 340.0),
            "Foot_L":        (-30.0, 440.0),
            "Leg_R":         ( 30.0, 240.0),
            "Calf_R":        ( 30.0, 340.0),
            "Foot_R":        ( 30.0, 440.0),
        }

        # Override with actual centroids when provided
        if centroids:
            for layer_name, (cx, cy) in centroids.items():
                norm = self._normalise(layer_name)
                bone = self.LAYER_BONE_MAP.get(norm)
                if bone and bone in positions:
                    positions[bone] = (float(cx), float(cy))

        return positions

    @staticmethod
    def _normalise(name: str) -> str:
        """Normalise a layer name for lookup (lower-case, spaces to underscores)."""
        return name.strip().lower().replace(" ", "_").replace("-", "_")


# ======================================================================
# Deformer hierarchy (warp / rotation deformers)
# ======================================================================

class DeformerHierarchy:
    """Create warp and rotation deformers for hair, body, and eye tracking.

    Warp deformers use a 2D control grid; rotation deformers pivot around
    a single point. These map to Live2D Cubism's ArtMesh deformers.
    """

    DEFAULT_WARP_DEFORMERS: List[Dict] = [
        {"name": "HairFrontSwing", "parent": "Head", "rows": 3, "cols": 3,
         "type": "warp", "targets": ["hair_front", "hair_side"]},
        {"name": "HairBackSwing",  "parent": "Head", "rows": 4, "cols": 2,
         "type": "warp", "targets": ["hair_back", "hair_shadow_back"]},
        {"name": "HairSideSwing_L","parent": "Head", "rows": 2, "cols": 2,
         "type": "warp", "targets": ["hair_back_left"]},
        {"name": "HairSideSwing_R","parent": "Head", "rows": 2, "cols": 2,
         "type": "warp", "targets": ["hair_back_right"]},
        {"name": "BodySway",       "parent": "Torso","rows": 2, "cols": 2,
         "type": "warp", "targets": ["body", "clothes_inner", "clothes_outer"]},
        {"name": "SkirtSway",      "parent": "Skirt","rows": 4, "cols": 3,
         "type": "warp", "targets": ["skirt"]},
        {"name": "BreathChest",    "parent": "Chest","rows": 2, "cols": 2,
         "type": "warp", "targets": ["chest"]},
    ]

    DEFAULT_ROTATION_DEFORMERS: List[Dict] = [
        {"name": "EyeTrack_L",     "parent": "Eye_L",  "pivot": (-35, -40),
         "type": "rotation", "targets": ["eyeball_l", "eye_left"]},
        {"name": "EyeTrack_R",     "parent": "Eye_R",  "pivot": (35, -40),
         "type": "rotation", "targets": ["eyeball_r", "eye_right"]},
    ]

    def __init__(self) -> None:
        self._deformers: List[Dict] = []
        self._by_name: Dict[str, Dict] = {}

    @staticmethod
    def _norm_name(name: str) -> str:
        return name.strip().lower().replace(" ", "_").replace("-", "_")

    def build(
        self,
        bone_tree: Dict,
        meshes: Dict,
        layer_names: Optional[List[str]] = None,
        centroids: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> Dict:
        """Create warp/rotation deformers for all relevant parts.

        Args:
            bone_tree: Output of :meth:`BoneHierarchy.build`.
            meshes:    Dict of mesh dicts (used to infer deformer bounds).
            layer_names: Optional override of available layer names.
            centroids: Optional ``{layer_name: (cx, cy)}`` alpha-weighted
                       centroids. When supplied, rotation deformers pivot
                       around the *actual* eyeball centroid instead of a
                       hard-coded constant, preventing the eye from
                       rotating around the wrong point ("flying out").

        Returns:
            dict with ``deformers`` (list) and ``by_name`` (dict).
        """
        available = set(layer_names or [])
        if not available and meshes:
            available = set(meshes.keys())
        available_norm = {self._norm_name(n) for n in available}

        self._deformers = []
        self._by_name = {}

        for spec in self.DEFAULT_WARP_DEFORMERS:
            if not any(t in available_norm for t in spec["targets"]):
                continue
            d = self.create_warp_deformer(
                name=spec["name"],
                parent=spec["parent"],
                grid_rows=spec["rows"],
                grid_cols=spec["cols"],
                targets=[t for t in spec["targets"] if t in available_norm],
            )
            self._deformers.append(d)
            self._by_name[d["name"]] = d

        for spec in self.DEFAULT_ROTATION_DEFORMERS:
            if not any(t in available_norm for t in spec["targets"]):
                continue
            active_targets = [t for t in spec["targets"] if t in available_norm]
            # Centroid-aware pivot: snap to the real eyeball centroid.
            pivot = spec["pivot"]
            if centroids:
                centroid_pivot = self._resolve_eyeball_pivot(active_targets, centroids)
                if centroid_pivot is not None:
                    pivot = centroid_pivot
            d = self.create_rotation_deformer(
                name=spec["name"],
                parent=spec["parent"],
                pivot=pivot,
                targets=active_targets,
            )
            self._deformers.append(d)
            self._by_name[d["name"]] = d

        log.info(f"Created {len(self._deformers)} deformers")
        return {"deformers": self._deformers, "by_name": self._by_name}

    def _resolve_eyeball_pivot(
        self,
        targets: List[str],
        centroids: Dict[str, Tuple[float, float]],
    ) -> Optional[Tuple[float, float]]:
        """Find the eyeball centroid for a rotation deformer.

        Matching is case-insensitive and substring-based: each deformer
        target (e.g. ``eyeball_l``) is matched against centroid layer names
        (e.g. ``Eyeball_L`` / ``left_eyeball``). Only ``eyeball`` targets
        drive the pivot; non-eyeball targets (e.g. the white-of-eye layer)
        are ignored so the rotation centre stays on the eyeball itself.
        """
        # Pre-normalise centroid keys once.
        norm_centroids = {
            self._norm_name(name): (float(cx), float(cy))
            for name, (cx, cy) in centroids.items()
        }
        for target in targets:
            if "eyeball" not in target:
                continue
            # 1) exact normalized match
            if target in norm_centroids:
                return norm_centroids[target]
            # 2) substring match either direction (handles "eyeball_l" vs
            #    "eyeball_left" / "left_eyeball_l" naming variants).
            for cname, coord in norm_centroids.items():
                if "eyeball" not in cname:
                    continue
                if target in cname or cname in target:
                    return coord
        return None

    def create_warp_deformer(
        self,
        name: str,
        parent: str,
        grid_rows: int = 2,
        grid_cols: int = 2,
        targets: Optional[List[str]] = None,
    ) -> Dict:
        """Create a warp (free-form) deformer with an R*C control grid.

        Args:
            name:      Deformer identifier.
            parent:    Parent deformer or bone name.
            grid_rows: Number of control point rows.
            grid_cols: Number of control point columns.
            targets:   Layer names this deformer affects.

        Returns:
            Deformer definition dict.
        """
        rows = max(2, grid_rows)
        cols = max(2, grid_cols)
        # Default control grid: identity points on a 1x1 area
        points = []
        for r in range(rows):
            for c in range(cols):
                points.append([
                    round(c / (cols - 1), 4),
                    round(r / (rows - 1), 4),
                ])
        return {
            "name": name,
            "type": "warp",
            "parent": parent,
            "grid_rows": rows,
            "grid_cols": cols,
            "control_points": points,
            "targets": list(targets or []),
            "base_points": [list(p) for p in points],  # rest position
        }

    def create_rotation_deformer(
        self,
        name: str,
        parent: str,
        pivot: Tuple[float, float] = (0.0, 0.0),
        angle: float = 0.0,
        targets: Optional[List[str]] = None,
    ) -> Dict:
        """Create a rotation deformer pivoting around ``pivot``.

        Args:
            name:   Deformer identifier.
            parent: Parent deformer or bone name.
            pivot:  (x, y) pivot point in model space.
            angle:  Default rotation in degrees.
            targets: Layer names affected.

        Returns:
            Deformer definition dict.
        """
        return {
            "name": name,
            "type": "rotation",
            "parent": parent,
            "pivot": [float(pivot[0]), float(pivot[1])],
            "angle": float(angle),
            "targets": list(targets or []),
        }

    def to_json(self) -> Dict:
        """Export all deformers as a JSON-serialisable dict (Cubism import format)."""
        return {
            "version": 1,
            "deformers": [
                {
                    "name": d["name"],
                    "type": d["type"],
                    "parent": d["parent"],
                    **(
                        {
                            "grid_rows": d.get("grid_rows", 2),
                            "grid_cols": d.get("grid_cols", 2),
                            "control_points": d.get("control_points", []),
                        }
                        if d["type"] == "warp"
                        else {
                            "pivot": d.get("pivot", [0, 0]),
                            "angle": d.get("angle", 0.0),
                        }
                    ),
                    "targets": d.get("targets", []),
                }
                for d in self._deformers
            ],
        }
