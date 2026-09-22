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

from typing import Any, Dict, List, Optional

from core.logger import get_logger

# 摆长（内部无量纲值）到 Cubism 顶点 Radius/Y 的长度单位。官方 Haru 的发型
# 子顶点 Radius 是 8 这个量级，而我们的摆长在 0.15~1.2 之间。
_PENDULUM_UNIT = 10.0


def _as_percent(weight: float) -> float:
    """把权重统一到 Cubism 的 0-100 制（历史上这里混用过 0-1 与 0-100）。"""
    value = float(weight)
    return value * 100.0 if 0.0 < value <= 1.0 else value

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


def prune_physics_to_parameters(physics_data: Dict[str, Any],
                                declared) -> Dict[str, Any]:
    """丢掉引用了「模型里没声明的参数」的物理链。

    Cubism 对这种链是**静默丢弃**：文件合 schema、能加载、一致性全绿，参数却一动
    不动（官方内核实测：``drivers/live2d_runtime/moc3_physics_probe.py`` 直接报
    「physics 引用了模型里没有的参数」）。所以只能在产物侧保证自洽：
    输入或输出任一端未声明就砍掉该链，砍空了的整组也一并去掉。
    ``declared`` 为空（调用方没给参数表）时原样返回，不做臆测。
    """
    names = {str(p.get("Id")) for p in declared or [] if p.get("Id")}
    if not names or not physics_data:
        return physics_data
    kept_settings = []
    dropped = []
    for setting in physics_data.get("PhysicsSettings") or []:
        inputs = [i for i in setting.get("Input") or []
                  if i.get("Source", {}).get("Target") != "Parameter"
                  or i["Source"]["Id"] in names]
        outputs = [o for o in setting.get("Output") or []
                   if o.get("Destination", {}).get("Target") != "Parameter"
                   or o["Destination"]["Id"] in names]
        for entry in (setting.get("Input") or [])[len(inputs):]:
            dropped.append(f"{setting.get('Id')}: 输入 "
                           f"{entry.get('Source', {}).get('Id')}")
        for entry in (setting.get("Output") or [])[len(outputs):]:
            dropped.append(f"{setting.get('Id')}: 输出 "
                           f"{entry.get('Destination', {}).get('Id')}")
        has_input = any((i.get("Source") or {}).get("Target") == "Parameter"
                        for i in inputs)
        has_output = any((o.get("Destination") or {}).get("Target") == "Parameter"
                         for o in outputs)
        if has_input and has_output:
            setting["Input"] = inputs
            setting["Output"] = outputs
            kept_settings.append(setting)
        elif setting.get("Input") or setting.get("Output"):
            dropped.append(f"{setting.get('Id')}: 整组（输入或输出已被清空）")
    if not dropped:
        return physics_data
    physics_data["PhysicsSettings"] = kept_settings
    meta = physics_data.get("Meta") or {}
    meta["PhysicsSettingCount"] = len(kept_settings)
    meta["TotalInputCount"] = sum(len(s.get("Input") or [])
                                  for s in kept_settings)
    meta["TotalOutputCount"] = sum(len(s.get("Output") or [])
                                   for s in kept_settings)
    meta["VertexCount"] = sum(len(s.get("Vertices") or [])
                              for s in kept_settings)
    meta["PhysicsDictionary"] = [
        {"Id": s.get("Id"), "Name": s.get("Name") or ""}
        for s in kept_settings]
    log.warning(f"物理链引用了模型里未声明的参数，已剔除：{sorted(set(dropped))}")
    return physics_data


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

    def build_hair_physics(self, hair_layers: List[str]) -> Dict[str, Any]:
        """Build pendulum physics for hair layers.

        Creates separate physics groups for front hair (fast, short swing),
        back hair (slow, long swing), and side hair if present.
        """
        groups: List[Dict[str, Any]] = []

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
            pendulums=[
                {"length": 0.30, "damping": 0.85, "stiffness": 0.30, "mass": 1.0},
                {"length": 0.50, "damping": 0.90, "stiffness": 0.20, "mass": 0.8},
            ],
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
                pendulums=[
                    {"length": 0.80, "damping": 0.92, "stiffness": 0.15, "mass": 1.2},
                    {"length": 1.00, "damping": 0.95, "stiffness": 0.10, "mass": 1.0},
                    {"length": 1.20, "damping": 0.97, "stiffness": 0.08, "mass": 0.8},
                ],
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
                # 只输出 ParamBodySway。曾经这里还输出 ParamBreath，而
                # Breathing 组又以 ParamBreath 为输入 —— 官方内核把这种
                # 参数环当循环依赖丢弃，实测那两条链 peak 恒为 0
                # （drivers/live2d_runtime/moc3_physics_probe.py）。
                {"target": "Parameter", "id": "ParamBodySway", "weight": 100, "scale": 1.0, "reflect": False},
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
                # 双肩而不是 ParamBodyAngleY：物理参数**不得成环** ——
                # BodyBounce 以 ParamBodyAngleY 为输入，而官方 Haru 里
                # 「既被某组输出、又被另一组读取」的参数数量是 0；我们曾写成
                # BodyAngleY，实测该链被内核整个丢掉（peak 恒为 0）。
                # 吸气抬肩是常规做法，且这两个参数没有任何物理组读取。
                {"target": "Parameter", "id": "ParamShoulderL", "weight": 50,
                 "scale": 0.3, "reflect": False},
                {"target": "Parameter", "id": "ParamShoulderR", "weight": 50,
                 "scale": 0.3, "reflect": False},
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

    def build_skirt_physics(self, skirt_layers: List[str]) -> Dict[str, Any]:
        """Build cloth/skirt sway physics.

        Skirt physics uses a multi-segment pendulum with medium stiffness
        and gravity for realistic cloth motion.
        """
        if not skirt_layers:
            log.debug("No skirt layers provided; skipping skirt physics")
            return {"groups": []}

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
            pendulums=[
                {"length": 0.40, "damping": 0.88, "stiffness": 0.25, "mass": 1.0},
                {"length": 0.60, "damping": 0.92, "stiffness": 0.15, "mass": 0.9},
                {"length": 0.80, "damping": 0.95, "stiffness": 0.10, "mass": 0.7},
            ],
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
        """内部摆参数 -> physics3.json 的 PhysicsSettings 条目。

        Cubism 的 physics3 **没有** length/damping/stiffness/mass 这套摆模型，
        它用的是逐顶点的 Mobility / Delay / Acceleration / Radius（官方 Haru：
        根顶点 Position(0,0) Radius 0 Mobility 1 Delay 1 Acceleration 1，
        子顶点如 Position(0,8) Mobility 0.95 Delay 0.8 Acceleration 1.5 Radius 8）。
        映射关系（内部值 -> 官方字段）：

          Delay        <- damping        阻尼越大越拖后
          Mobility     <- 1 - stiffness  刚度越高越不跟随
          Acceleration <- 1 + mass/2     质量越大加速越猛
          Radius/Y     <- 累计 length×长度单位

        输出必须是 ``Type: "Angle"``、VertexIndex 指向**会动的**顶点（≥1）：
        实测写成 ``"X"`` 时官方内核完全不产生运动
        （`drivers/live2d_runtime/moc3_physics_probe.py`，同法在 Haru 上 14 条链全动）。
        Weight 是 0-100 制，不是 0-1 制。
        """
        verts: List[Dict[str, Any]] = [{
            # 根顶点是锚点：Radius 必须为 0，否则整条链被几何带偏
            "Position": {"X": 0, "Y": 0},
            "Mobility": 1, "Delay": 1, "Acceleration": 1, "Radius": 0,
        }]
        y = 0.0
        for pend in group["pendulums"]:
            y += float(pend.get("length", 0.5)) * _PENDULUM_UNIT
            mobility = 1.0 - float(pend.get("stiffness", 0.2))
            verts.append({
                "Position": {"X": 0, "Y": round(y, 3)},
                "Mobility": round(min(1.0, max(0.0, mobility)), 3),
                "Delay": round(min(1.0, max(0.0, float(
                    pend.get("damping", 0.9)))), 3),
                "Acceleration": round(
                    1.0 + float(pend.get("mass", 1.0)) / 2.0, 3),
                "Radius": round(y, 3),
            })
        # 顶点数按摆段数生成，保证每个输出引用的索引都存在
        group["vertices"] = len(verts)

        input_list = [{
            "Source": {"Target": inp["target"], "Id": inp["id"]},
            # 0-100 制；数量级对齐官方同类设置（发型 60/40、身体 50、呼吸 100），
            # 具体幅度属可调项，不宣称是推导结果。
            "Weight": _as_percent(inp.get("weight", 1.0)),
            "Type": "X",
            "Reflect": bool(inp.get("reflect", False)),
        } for inp in group["inputs"]]

        output_list = [{
            "Destination": {"Target": out["target"], "Id": out["id"]},
            "VertexIndex": min(len(verts) - 1, int(out.get("vertex", 1))),
            "Scale": float(out.get("scale", 1.0)),
            "Weight": _as_percent(out.get("weight", 100.0)),
            "Type": "Angle",
            "Reflect": bool(out.get("reflect", False)),
        } for out in group["outputs"]]

        limit = float(group.get("normalization_angle", 10.0)) or 10.0
        return {
            "Id": group["id"],
            "Name": group["name"],
            "Input": input_list,
            "Output": output_list,
            "Vertices": verts,
            "Normalization": {
                "Position": {"Minimum": -limit, "Default": 0, "Maximum": limit},
                "Angle": {"Minimum": -limit, "Default": 0, "Maximum": limit},
            },
        }
