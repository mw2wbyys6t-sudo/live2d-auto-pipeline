#!/usr/bin/env python3
"""Physics configuration builder for Live2D Cubism 4 physics3.json.

Generates physics groups using a pendulum model with proper gravity,
resistance, mobility, and delay parameters for:
- Hair swing (front, back, side)
- Body bounce / sway
- Breathing
- Skirt / cloth
- Animal ears and tail (optional)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger

log = get_logger("rigging.physics")


def geometry_spring_params(height_px: float) -> Tuple[float, float]:
    """Map a layer's pixel height to (stiffness, length_scale).

    Short hair (e.g. <100px) is stiff with a short pendulum; long hair
    (e.g. >400px) is soft with a long, flowing pendulum.

    stiffness_raw follows the design formula ``max(15, 90 - h/6)`` on a
    0..100 rigidity scale; it is normalised to the 0..1 pendulum domain the
    physics3.json schema expects. length grows proportionally with height,
    clamped to a sane 0.5x..2x range.
    """
    h = max(1.0, float(height_px))
    rigidity = max(15.0, 90.0 - h / 6.0)          # 15 (very long) .. 90 (short)
    stiffness = min(0.9, max(0.05, rigidity / 100.0))
    length_scale = max(0.5, min(2.0, h / 200.0))
    return stiffness, length_scale


class PhysicsBuilder:
    """Build physics3.json settings for a Live2D model.

    Each physics group uses a second-order pendulum model. The pendulum
    chain is represented as a list of ``vertices`` with input/output
    mappings and normalisation parameters.
    """

    def __init__(self, fps: int = 60) -> None:
        self.fps = max(1, fps)
        self._groups: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Group builders
    # ------------------------------------------------------------------

    @staticmethod
    def _metrics_height(
        metrics: Optional[List[Tuple[str, int]]],
        include: Tuple[str, ...],
        exclude: Tuple[str, ...] = (),
    ) -> Optional[float]:
        """Return the mean pixel height of metrics whose name matches any
        ``include`` substring (and none of ``exclude``)."""
        if not metrics:
            return None
        heights: List[float] = []
        for name, h in metrics:
            nl = name.lower()
            if exclude and any(x in nl for x in exclude):
                continue
            if any(k in nl for k in include):
                heights.append(float(h))
        if not heights:
            return None
        return sum(heights) / len(heights)

    @staticmethod
    def _apply_geometry(
        pendulums: List[Dict[str, float]],
        height_px: Optional[float],
    ) -> List[Dict[str, float]]:
        """Scale pendulum stiffness/length by the measured layer height."""
        if not height_px:
            return pendulums
        stiffness, length_scale = geometry_spring_params(height_px)
        scaled: List[Dict[str, float]] = []
        n = max(1, len(pendulums))
        for i, p in enumerate(pendulums):
            # Tips of a multi-segment chain are slightly softer.
            taper = 1.0 - 0.25 * (i / n)
            seg_stiff = max(0.05, min(0.95, stiffness * taper))
            scaled.append({
                **p,
                "length": round(p["length"] * length_scale, 4),
                "stiffness": round(seg_stiff, 4),
                "mass": p.get("mass", 1.0),
            })
        return scaled

    def build_hair_physics(
        self,
        hair_layers: List[str],
        metrics: Optional[List[Tuple[str, int]]] = None,
    ) -> Dict[str, Any]:
        """Build pendulum physics for hair layers.

        Creates separate physics groups for front hair (fast, short swing),
        back hair (slow, long swing), and side hair if present.

        Args:
            hair_layers: Hair layer names.
            metrics: Optional ``[(layer_name, pixel_height)]`` used to make
                stiffness/length geometry-aware (short hair stiff, long hair
                soft). When omitted, sensible defaults are used.
        """
        groups: List[Dict[str, Any]] = []

        front_h = self._metrics_height(
            metrics, include=("front", "side", "bang", "ahoge"), exclude=("back",)
        )
        back_h = self._metrics_height(metrics, include=("back",))

        # Hair front — short pendulum, quick response
        groups.append(self._make_pendulum_group(
            group_id="HairFront",
            name="前发摇摆 (Hair Front Swing)",
            inputs=[
                {"target": "Parameter", "id": "ParamAngleX", "weight": 10.0},
                {"target": "Parameter", "id": "ParamBodyAngleX", "weight": 5.0},
            ],
            outputs=[
                {"target": "Parameter", "id": "ParamHairSwing", "weight": 100, "scale": 1.0, "reflect": False},
            ],
            pendulums=self._apply_geometry([
                {"length": 0.30, "damping": 0.85, "stiffness": 0.30, "mass": 1.0},
                {"length": 0.50, "damping": 0.90, "stiffness": 0.20, "mass": 0.8},
            ], front_h),
            vertices=3,
            normalization_angle=90.0,
        ))

        # Hair back — longer, slower swing
        if any("back" in n.lower() for n in hair_layers):
            groups.append(self._make_pendulum_group(
                group_id="HairBack",
                name="后发摇摆 (Hair Back Swing)",
                inputs=[
                    {"target": "Parameter", "id": "ParamAngleX", "weight": 8.0},
                    {"target": "Parameter", "id": "ParamBodyAngleX", "weight": 8.0},
                ],
                outputs=[
                    {"target": "Parameter", "id": "ParamHairSwing", "weight": 100, "scale": 1.5, "reflect": False},
                ],
                pendulums=self._apply_geometry([
                    {"length": 0.80, "damping": 0.92, "stiffness": 0.15, "mass": 1.2},
                    {"length": 1.00, "damping": 0.95, "stiffness": 0.10, "mass": 1.0},
                    {"length": 1.20, "damping": 0.97, "stiffness": 0.08, "mass": 0.8},
                ], back_h),
                vertices=4,
                normalization_angle=90.0,
            ))

        self._groups.extend(groups)
        log.info(f"Built hair physics: {len(groups)} group(s)")
        return {"groups": groups}

    def build_body_physics(self) -> Dict[str, Any]:
        """Build body bounce and sway physics."""
        group = self._make_pendulum_group(
            group_id="BodyBounce",
            name="身体弹跳 (Body Bounce)",
            inputs=[
                {"target": "Parameter", "id": "ParamBodyAngleY", "weight": 10.0},
                {"target": "Parameter", "id": "ParamAngleY", "weight": 5.0},
            ],
            outputs=[
                {"target": "Parameter", "id": "ParamBodySway", "weight": 100, "scale": 1.0, "reflect": False},
                {"target": "Parameter", "id": "ParamBreath", "weight": 30, "scale": 0.5, "reflect": False},
            ],
            pendulums=[
                {"length": 0.20, "damping": 0.70, "stiffness": 0.50, "mass": 1.5},
            ],
            vertices=2,
            normalization_angle=10.0,
        )
        self._groups.append(group)
        log.info("Built body bounce physics")
        return group

    def build_breathing_physics(self) -> Dict[str, Any]:
        """Build breathing motion physics (slow sinusoidal)."""
        group = self._make_pendulum_group(
            group_id="Breathing",
            name="呼吸 (Breathing)",
            inputs=[
                {"target": "Parameter", "id": "ParamBreath", "weight": 1.0},
            ],
            outputs=[
                {"target": "Parameter", "id": "ParamBodyAngleY", "weight": 50, "scale": 0.3, "reflect": False},
            ],
            pendulums=[
                {"length": 2.00, "damping": 0.99, "stiffness": 0.05, "mass": 2.0},
            ],
            vertices=2,
            normalization_angle=10.0,
        )
        self._groups.append(group)
        log.info("Built breathing physics")
        return group

    def build_skirt_physics(
        self,
        skirt_layers: List[str],
        metrics: Optional[List[Tuple[str, int]]] = None,
    ) -> Dict[str, Any]:
        """Build cloth/skirt sway physics.

        Skirt physics uses a multi-segment pendulum with medium stiffness
        and gravity for realistic cloth motion. ``metrics`` optionally
        provides per-layer pixel heights for geometry-aware stiffness.
        """
        if not skirt_layers:
            log.debug("No skirt layers provided; skipping skirt physics")
            return {"groups": []}

        skirt_h = self._metrics_height(metrics, include=("skirt",))

        group = self._make_pendulum_group(
            group_id="Skirt",
            name="裙摆 (Skirt)",
            inputs=[
                {"target": "Parameter", "id": "ParamBodyAngleX", "weight": 10.0},
                {"target": "Parameter", "id": "ParamAngleX", "weight": 5.0},
            ],
            outputs=[
                {"target": "Parameter", "id": "ParamBodySway", "weight": 80, "scale": 1.2, "reflect": False},
            ],
            pendulums=self._apply_geometry([
                {"length": 0.40, "damping": 0.88, "stiffness": 0.25, "mass": 1.0},
                {"length": 0.60, "damping": 0.92, "stiffness": 0.15, "mass": 0.9},
                {"length": 0.80, "damping": 0.95, "stiffness": 0.10, "mass": 0.7},
            ], skirt_h),
            vertices=4,
            normalization_angle=30.0,
        )
        self._groups.append(group)
        log.info(f"Built skirt physics for {len(skirt_layers)} layer(s)")
        return group

    def build_ear_tail_physics(
        self,
        has_animal_ears: bool = False,
        has_tail: bool = False,
    ) -> Dict[str, Any]:
        """Build physics for animal ears and/or tail.

        Args:
            has_animal_ears: If True, adds ear physics.
            has_tail: If True, adds tail physics.
        """
        groups: List[Dict[str, Any]] = []

        if has_animal_ears:
            groups.append(self._make_pendulum_group(
                group_id="AnimalEars",
                name="兽耳 (Animal Ears)",
                inputs=[
                    {"target": "Parameter", "id": "ParamAngleX", "weight": 8.0},
                    {"target": "Parameter", "id": "ParamAngleY", "weight": 6.0},
                ],
                outputs=[
                    {"target": "Parameter", "id": "ParamHairSwing", "weight": 50, "scale": 0.5, "reflect": False},
                ],
                pendulums=[
                    {"length": 0.25, "damping": 0.80, "stiffness": 0.40, "mass": 0.6},
                ],
                vertices=2,
                normalization_angle=45.0,
            ))

        if has_tail:
            groups.append(self._make_pendulum_group(
                group_id="Tail",
                name="尾巴 (Tail)",
                inputs=[
                    {"target": "Parameter", "id": "ParamBodyAngleX", "weight": 10.0},
                    {"target": "Parameter", "id": "ParamBodyAngleY", "weight": 8.0},
                ],
                outputs=[
                    {"target": "Parameter", "id": "ParamBodySway", "weight": 100, "scale": 2.0, "reflect": False},
                ],
                pendulums=[
                    {"length": 0.50, "damping": 0.85, "stiffness": 0.20, "mass": 1.0},
                    {"length": 0.70, "damping": 0.90, "stiffness": 0.12, "mass": 0.8},
                    {"length": 0.90, "damping": 0.93, "stiffness": 0.08, "mass": 0.6},
                ],
                vertices=4,
                normalization_angle=60.0,
            ))

        self._groups.extend(groups)
        if groups:
            log.info(f"Built ear/tail physics: {len(groups)} group(s)")
        return {"groups": groups}

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def to_physics3_json(self) -> Dict[str, Any]:
        """Generate the full physics3.json data structure.

        Returns:
            dict conforming to the Live2D Cubism 4 physics3.json schema
            with version, meta, and physics_settings sections.
        """
        settings: List[Dict[str, Any]] = []
        for g in self._groups:
            settings.append(self._group_to_setting(g))

        physics_json: Dict[str, Any] = {
            "Version": 3,
            "Meta": {
                "PhysicsSettingCount": len(settings),
                "TotalInputCount": sum(len(s["Input"]) for s in settings),
                "TotalOutputCount": sum(len(s["Output"]) for s in settings),
                "VertexCount": sum(len(s["Vertices"]) for s in settings),
                "Fps": self.fps,
                "EffectiveForces": {
                    "Gravity": {"X": 0, "Y": -1},
                    "Wind": {"X": 0, "Y": 0},
                },
                "PhysicsDictionary": [
                    {"Id": s["Id"], "Name": s.get("Name", s["Id"])}
                    for s in settings
                ],
            },
            "PhysicsSettings": settings,
        }
        ns = len(settings)
        ni = physics_json["Meta"]["TotalInputCount"]
        no = physics_json["Meta"]["TotalOutputCount"]
        nv = physics_json["Meta"]["VertexCount"]
        log.info(f"physics3.json: {ns} settings, {ni} inputs, {no} outputs, {nv} vertices")
        return physics_json

    def reset(self) -> None:
        """Clear all accumulated physics groups."""
        self._groups = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_pendulum_group(
        group_id: str,
        name: str,
        inputs: List[Dict[str, Any]],
        outputs: List[Dict[str, Any]],
        pendulums: List[Dict[str, float]],
        vertices: int = 2,
        normalization_angle: float = 30.0,
    ) -> Dict[str, Any]:
        """Assemble a physics group definition (pre JSON conversion)."""
        return {
            "id": group_id,
            "name": name,
            "inputs": inputs,
            "outputs": outputs,
            "pendulums": pendulums,
            "vertices": max(2, vertices),
            "normalization_angle": normalization_angle,
        }

    @staticmethod
    def _group_to_setting(group: Dict[str, Any]) -> Dict[str, Any]:
        """Convert an internal group dict to physics3.json PhysicsSettings entry."""
        verts = []
        n = group["vertices"]
        for i in range(n):
            verts.append({
                "Position": {"X": i * 0.5, "Y": 0},
                "Mobility": 1.0,
                "Delay": 0.0,
                "Acceleration": 1.0,
                "Radius": max(1, i) * 50,
            })

        input_list = []
        for inp in group["inputs"]:
            input_list.append({
                "Source": {
                    "Target": inp["target"],
                    "Id": inp["id"],
                },
                "Weight": inp.get("weight", 1.0),
                "Type": "X",
                "Reflect": False,
            })

        output_list = []
        for out in group["outputs"]:
            output_list.append({
                "Destination": {
                    "Target": out["target"],
                    "Id": out["id"],
                },
                "VertexIndex": max(0, len(verts) - 1),
                "Scale": out.get("scale", 1.0),
                "Weight": out.get("weight", 100.0),
                "Type": "X",
                "Reflect": out.get("reflect", False),
            })

        # Build pendulum normalisation from the first pendulum's params
        pend = group["pendulums"][0] if group["pendulums"] else {}

        return {
            "Id": group["id"],
            "Name": group["name"],
            "Input": input_list,
            "Output": output_list,
            "Vertices": verts,
            "Normalization": {
                "Position": {"Minimum": -1, "Default": 0, "Maximum": 1},
                "Angle": {
                    "Minimum": -group["normalization_angle"],
                    "Default": 0,
                    "Maximum": group["normalization_angle"],
                },
            },
            "Pendulum": {
                "Length": pend.get("length", 0.5),
                "Damping": pend.get("damping", 0.9),
                "Stiffness": pend.get("stiffness", 0.2),
                "Mass": pend.get("mass", 1.0),
                "Gravity": 0.0,
                "Fps": 60,
            },
        }
