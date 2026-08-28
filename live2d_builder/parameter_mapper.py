
#!/usr/bin/env python3
"""
面捕参数 ↔ Live2D 参数 可视化映射编辑器

支持用户自定义面捕参数与 Live2D 标准参数之间的映射关系，
包括参数名、缩放比、反向、平滑系数、死区等。

功能：
- 加载/保存 JSON 映射配置文件
- 添加/删除/修改映射条目
- 实时预览映射结果
- 内置 VTube Studio 和 OpenSeeFace 预设
- 导出为可被 vts_connector 或 blendshape_mapper 消费的格式
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger

log = get_logger("parameter_mapper")


# ------------------------------------------------------------------
# 数据结构
# ------------------------------------------------------------------

@dataclass
class ParameterMappingEntry:
    """单条映射条目。

    Attributes:
        source_name: 面捕来源参数名（VTS 或 OpenSeeFace blendshape）
        source_type: 来源类型（vts / openseeface / custom）
        target_param: 目标 Live2D 参数名（如 ParamMouthOpenY）
        scale: 缩放系数（1.0 = 1:1 映射）
        offset: 偏移量
        invert: 是否反向（如 eyeBlink → eyeOpen 时需反相）
        smooth: 平滑系数 0~1（0 = 不平滑, 1 = 完全平滑）
        deadzone: 死区（小于此值的输入被置零）
        clamp_min: 输出最小值剪裁
        clamp_max: 输出最大值剪裁
        enabled: 是否启用
    """
    source_name: str = ""
    target_param: str = ""
    source_type: str = "vts"
    scale: float = 1.0
    offset: float = 0.0
    invert: bool = False
    smooth: float = 0.3
    deadzone: float = 0.0
    clamp_min: float = -1.0
    clamp_max: float = 1.0
    enabled: bool = True

    def apply(self, source_value: float) -> float:
        """对输入值应用映射规则。"""
        if not self.enabled:
            return 0.0
        # 死区
        if abs(source_value) < self.deadzone:
            return 0.0
        # 缩放 + 偏移
        val = source_value * self.scale + self.offset
        if self.invert:
            val = 1.0 - val
        # 剪裁
        return max(self.clamp_min, min(self.clamp_max, val))


@dataclass
class ParameterMappingProfile:
    """参数映射配置档案。

    一个 Profile 包含一组完整的 ParameterMappingEntry，
    可序列化为 JSON 文件进行持久化。
    """
    name: str = "Default"
    description: str = ""
    entries: List[ParameterMappingEntry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "entries": [asdict(e) for e in self.entries],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ParameterMappingProfile":
        entries = [
            ParameterMappingEntry(**e)
            for e in data.get("entries", [])
        ]
        return cls(
            name=data.get("name", "Unnamed"),
            description=data.get("description", ""),
            entries=entries,
        )


# ------------------------------------------------------------------
# 内置预设
# ------------------------------------------------------------------

# VTube Studio 默认参数映射
VTS_DEFAULT_ENTRIES: List[ParameterMappingEntry] = [
    # 头部角度
    ParameterMappingEntry("FaceAngleX", "ParamAngleX", "vts", clamp_min=-30, clamp_max=30),
    ParameterMappingEntry("FaceAngleY", "ParamAngleY", "vts", clamp_min=-30, clamp_max=30),
    ParameterMappingEntry("FaceAngleZ", "ParamAngleZ", "vts", clamp_min=-30, clamp_max=30),
    # 身体角度
    ParameterMappingEntry("BodyAngleX", "ParamBodyAngleX", "vts", clamp_min=-10, clamp_max=10),
    ParameterMappingEntry("BodyAngleY", "ParamBodyAngleY", "vts", clamp_min=-10, clamp_max=10),
    # 眼睛
    ParameterMappingEntry("EyeLeftOpen", "ParamEyeLOpen", "vts", clamp_min=0, clamp_max=1),
    ParameterMappingEntry("EyeRightOpen", "ParamEyeROpen", "vts", clamp_min=0, clamp_max=1),
    ParameterMappingEntry("EyeLeftX", "ParamEyeBallX", "vts", clamp_min=-1, clamp_max=1),
    ParameterMappingEntry("EyeRightX", "ParamEyeBallX", "vts", clamp_min=-1, clamp_max=1),
    ParameterMappingEntry("EyeLeftY", "ParamEyeBallY", "vts", clamp_min=-1, clamp_max=1),
    ParameterMappingEntry("EyeRightY", "ParamEyeBallY", "vts", clamp_min=-1, clamp_max=1),
    # 嘴巴
    ParameterMappingEntry("MouthOpen", "ParamMouthOpenY", "vts", clamp_min=0, clamp_max=1),
    ParameterMappingEntry("MouthSmile", "ParamMouthForm", "vts", clamp_min=-1, clamp_max=1),
    # 眉毛
    ParameterMappingEntry("BrowLeftY", "ParamBrowLY", "vts", clamp_min=-1, clamp_max=1),
    ParameterMappingEntry("BrowRightY", "ParamBrowRY", "vts", clamp_min=-1, clamp_max=1),
    # 脸颊
    ParameterMappingEntry("CheekPuff", "ParamCheek", "vts", clamp_min=0, clamp_max=1),
]

# OpenSeeFace blendshape 映射
OSF_DEFAULT_ENTRIES: List[ParameterMappingEntry] = [
    ParameterMappingEntry("eyeBlinkLeft", "ParamEyeLOpen", "openseeface",
                          invert=True, clamp_min=0, clamp_max=1),
    ParameterMappingEntry("eyeBlinkRight", "ParamEyeROpen", "openseeface",
                          invert=True, clamp_min=0, clamp_max=1),
    ParameterMappingEntry("jawOpen", "ParamMouthOpenY", "openseeface",
                          clamp_min=0, clamp_max=1),
    ParameterMappingEntry("mouthSmileLeft", "ParamMouthForm", "openseeface",
                          scale=0.5, clamp_min=-1, clamp_max=1),
    ParameterMappingEntry("mouthSmileRight", "ParamMouthForm", "openseeface",
                          scale=0.5, clamp_min=-1, clamp_max=1),
    ParameterMappingEntry("browInnerUp", "ParamBrowLY", "openseeface",
                          scale=0.5, clamp_min=-1, clamp_max=1),
    ParameterMappingEntry("cheekPuff", "ParamCheek", "openseeface",
                          clamp_min=0, clamp_max=1),
    ParameterMappingEntry("tongueOut", "ParamTongue", "openseeface",
                          clamp_min=0, clamp_max=1),
]


# ------------------------------------------------------------------
# 主类
# ------------------------------------------------------------------

class ParameterMappingEditor:
    """参数映射编辑器。

    使用方法::

        editor = ParameterMappingEditor()
        editor.load_preset("vts")
        editor.add_entry("MyCustomParam", "ParamCheek", source_type="custom")
        editor.save("my_profile.json")

        # 实时映射
        result = editor.map_parameters({"FaceAngleX": 15.0, "MouthOpen": 0.7})
    """

    def __init__(self) -> None:
        self._profile = ParameterMappingProfile()
        self._entry_index: Dict[Tuple[str, str], int] = {}  # (source, target) → idx

    # ------------------------------------------------------------------
    # 预设管理
    # ------------------------------------------------------------------

    def load_preset(self, preset_name: str) -> bool:
        """加载内置预设。

        Args:
            preset_name: "vts" / "openseeface" / "prprlive"

        Returns:
            是否加载成功
        """
        if preset_name == "vts":
            entries = [ParameterMappingEntry(**asdict(e))
                       for e in VTS_DEFAULT_ENTRIES]
            self._profile = ParameterMappingProfile(
                name="VTube Studio Default",
                description="VTube Studio 面捕标准参数映射",
                entries=entries,
            )
        elif preset_name == "openseeface":
            entries = [ParameterMappingEntry(**asdict(e))
                       for e in OSF_DEFAULT_ENTRIES]
            self._profile = ParameterMappingProfile(
                name="OpenSeeFace Default",
                description="OpenSeeFace ARKit blendshape 映射",
                entries=entries,
            )
        elif preset_name == "prprlive":
            # PrprLive 参数名与 VTS 基本一致，但多了部分扩展
            entries = [ParameterMappingEntry(**asdict(e))
                       for e in VTS_DEFAULT_ENTRIES]
            entries.append(
                ParameterMappingEntry(
                    "CheekSquintLeft", "ParamCheek", "prprlive",
                    clamp_min=0, clamp_max=1,
                )
            )
            self._profile = ParameterMappingProfile(
                name="PrprLive Default",
                description="PrprLive 面捕参数映射（含扩展参数）",
                entries=entries,
            )
        else:
            log.warning(f"未知预设: {preset_name}")
            return False

        self._rebuild_index()
        log.info(f"已加载预设: {preset_name} ({len(self._profile.entries)} 条映射)")
        return True

    def load_file(self, file_path: str) -> bool:
        """从 JSON 文件加载映射配置。

        Args:
            file_path: JSON 配置文件路径

        Returns:
            是否加载成功
        """
        path = Path(file_path)
        if not path.exists():
            log.warning(f"映射文件不存在: {file_path}")
            return False

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._profile = ParameterMappingProfile.from_dict(data)
            self._rebuild_index()
            log.info(f"已加载映射文件: {file_path} ({len(self._profile.entries)} 条)")
            return True
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            log.warning(f"映射文件解析失败: {exc}")
            return False

    def save(self, file_path: str) -> bool:
        """保存映射配置到 JSON 文件。

        Args:
            file_path: 输出路径
        """
        path = Path(file_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._profile.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log.info(f"映射配置已保存: {file_path}")
            return True
        except OSError as exc:
            log.error(f"保存失败: {exc}")
            return False

    # ------------------------------------------------------------------
    # 映射条目管理
    # ------------------------------------------------------------------

    def add_entry(self, entry: ParameterMappingEntry) -> bool:
        """添加一条映射条目。

        Args:
            entry: 映射条目

        Returns:
            False 表示重复（同一 source+target 对已存在）
        """
        key = (entry.source_name, entry.target_param)
        if key in self._entry_index:
            log.warning(f"映射已存在: {entry.source_name} → {entry.target_param}")
            return False
        self._profile.entries.append(entry)
        self._entry_index[key] = len(self._profile.entries) - 1
        return True

    def remove_entry(self, source_name: str, target_param: str) -> bool:
        """删除一条映射条目。

        Args:
            source_name: 来源参数名
            target_param: 目标 Live2D 参数名
        """
        key = (source_name, target_param)
        if key not in self._entry_index:
            return False
        idx = self._entry_index.pop(key)
        # 替代删除（避免 reindex）
        self._profile.entries[idx].enabled = False
        return True

    def update_entry(
        self,
        source_name: str,
        target_param: str,
        **kwargs,
    ) -> bool:
        """更新映射条目的属性。

        支持更新的字段: scale, offset, invert, smooth, deadzone,
        clamp_min, clamp_max, enabled。

        Args:
            source_name: 来源参数名
            target_param: 目标 Live2D 参数名
            **kwargs: 要更新的字段
        """
        key = (source_name, target_param)
        if key not in self._entry_index:
            return False
        idx = self._entry_index[key]
        entry = self._profile.entries[idx]
        for k, v in kwargs.items():
            if hasattr(entry, k):
                setattr(entry, k, v)
        return True

    def get_entry(
        self, source_name: str, target_param: str
    ) -> Optional[ParameterMappingEntry]:
        """获取单条映射条目。"""
        key = (source_name, target_param)
        idx = self._entry_index.get(key)
        if idx is not None:
            return self._profile.entries[idx]
        return None

    def list_entries(self) -> List[ParameterMappingEntry]:
        """列出所有已启用的映射条目。"""
        return [e for e in self._profile.entries if e.enabled]

    def list_all_entries(self) -> List[ParameterMappingEntry]:
        """列出所有映射条目（含禁用的）。"""
        return list(self._profile.entries)

    # ------------------------------------------------------------------
    # 参数映射
    # ------------------------------------------------------------------

    def map_parameters(
        self, source_params: Dict[str, float]
    ) -> Dict[str, float]:
        """将来源参数实时映射为 Live2D 参数。

        Args:
            source_params: {来源参数名: 值}，如 {"FaceAngleX": 15.0}

        Returns:
            {Live2D参数名: 值}，如 {"ParamAngleX": 15.0}
        """
        result: Dict[str, float] = {}

        for source_name, source_val in source_params.items():
            # 按 source_name 查找匹配的映射条目
            for entry in self._profile.entries:
                if not entry.enabled:
                    continue
                if entry.source_name == source_name:
                    mapped_val = entry.apply(source_val)
                    # 同名参数取累加值（支持多源合一）
                    if entry.target_param in result:
                        result[entry.target_param] += mapped_val
                    else:
                        result[entry.target_param] = mapped_val

        return result

    def map_single(self, source_name: str, value: float) -> float:
        """映射单个参数值。

        Args:
            source_name: 来源参数名
            value: 原始输入值

        Returns:
            映射后的值，若无匹配则返回原值
        """
        for entry in self._profile.entries:
            if entry.enabled and entry.source_name == source_name:
                return entry.apply(value)
        return value

    def export_for_vts_connector(self) -> Dict[str, str]:
        """导出为 VTSConnector 可用的参数名映射表。

        Returns:
            {VTS参数名: Live2D参数名}
        """
        result = {}
        for entry in self._profile.entries:
            if entry.enabled and entry.source_type in ("vts", "custom"):
                result[entry.source_name] = entry.target_param
        return result

    def export_for_blendshape_mapper(self) -> Dict[str, dict]:
        """导出为 BlendShapeMapper 可用的映射格式。"""
        result = {}
        for entry in self._profile.entries:
            if entry.enabled:
                result[entry.target_param] = {
                    "blendshapes": [entry.source_name],
                    "scale": entry.scale,
                    "invert": entry.invert,
                }
        return result

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _rebuild_index(self) -> None:
        self._entry_index.clear()
        for i, entry in enumerate(self._profile.entries):
            key = (entry.source_name, entry.target_param)
            self._entry_index[key] = i

    @property
    def profile(self) -> ParameterMappingProfile:
        return self._profile

    @property
    def entry_count(self) -> int:
        return len([e for e in self._profile.entries if e.enabled])
