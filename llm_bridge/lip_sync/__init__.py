
#!/usr/bin/env python3
"""
口型同步增强模块 — 基于 .cdi3.json 的音素-参数映射

增强能力：
- 解析 .cdi3.json 口型配置文件，动态加载音素映射
- 支持多套口型方案（A/I/U/E/O 五音素 / 扩展 12 音素 / 自定义）
- 音素识别（通过 Whisper/ASR 输出）→ Live2D 参数平滑映射
- 口型方案运行时切换
- 内置静音检测与过渡平滑

依赖：
- .cdi3.json 文件（Cubism Editor 生成或手写）
"""

from __future__ import annotations

import json
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger

log = get_logger("lip_sync")


# ------------------------------------------------------------------
# 常量与数据结构
# ------------------------------------------------------------------

class LipSyncScheme(Enum):
    """内置口型方案。"""
    VOWEL_5 = "vowel_5"          # A / I / U / E / O 五音素（日系标准）
    VOWEL_12 = "vowel_12"        # 扩展 12 音素（英/中兼容）
    SIMPLE_OPEN_CLOSE = "simple" # 简单开闭（仅 ParamMouthOpenY）


# 五音素方案默认映射（日系标准）
VOWEL_5_MAPPING: Dict[str, Dict[str, float]] = {
    # 音素 → {Live2D参数: 目标值}
    "A": {"ParamMouthOpenY": 1.0, "ParamMouthForm": 0.0},     # 大开口
    "I": {"ParamMouthOpenY": 0.4, "ParamMouthForm": 0.8},     # 咧嘴
    "U": {"ParamMouthOpenY": 0.2, "ParamMouthForm": -0.5},    # 噘嘴
    "E": {"ParamMouthOpenY": 0.6, "ParamMouthForm": 0.5},     # 半开咧嘴
    "O": {"ParamMouthOpenY": 0.7, "ParamMouthForm": -0.3},    # 圆嘴
    "sil": {"ParamMouthOpenY": 0.0, "ParamMouthForm": 0.0},   # 静音
}

# 扩展 12 音素映射
VOWEL_12_MAPPING: Dict[str, Dict[str, float]] = {
    **VOWEL_5_MAPPING,
    "AA": {"ParamMouthOpenY": 0.9, "ParamMouthForm": 0.0},
    "IH": {"ParamMouthOpenY": 0.4, "ParamMouthForm": 0.6},
    "UH": {"ParamMouthOpenY": 0.3, "ParamMouthForm": -0.4},
    "M":  {"ParamMouthOpenY": 0.0, "ParamMouthForm": 0.0},    # 闭唇鼻音
    "F":  {"ParamMouthOpenY": 0.15, "ParamMouthForm": -0.2},  # 唇齿音
    "S":  {"ParamMouthOpenY": 0.2, "ParamMouthForm": 0.4},    # 齿音
    "sil": {"ParamMouthOpenY": 0.0, "ParamMouthForm": 0.0},
}


# ------------------------------------------------------------------
# 数据结构
# ------------------------------------------------------------------

@dataclass
class PhonemeEvent:
    """单个音素事件。"""
    phoneme: str                    # 音素符号，如 "A" / "sil"
    start_time: float = 0.0         # 开始时间（秒，相对音频起点）
    end_time: float = 0.0           # 结束时间（秒）
    confidence: float = 1.0         # 置信度 0~1

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)


@dataclass
class LipSyncConfig:
    """口型同步配置。"""
    scheme: LipSyncScheme = LipSyncScheme.VOWEL_5
    transition_time: float = 0.08           # 参数过渡时间（秒），越小越灵敏
    silence_threshold: float = 0.15         # 静音检测阈值（音素时长低于此值视为静音）
    smooth_factor: float = 0.3              # 平滑系数 0~1（越大响应越快）
    fallback_mouth_open: float = 0.0        # 静音时嘴巴张开度（默认闭合）


# ------------------------------------------------------------------
# 主类
# ------------------------------------------------------------------

class LipSyncEngine:
    """口型同步引擎。

    核心流程：
    1. 加载 .cdi3.json 或使用内置方案
    2. 接收 PhonemeEvent 序列（来自 ASR/Whisper）
    3. 根据当前时间和音素事件计算 Live2D 参数目标值
    4. 平滑过渡到目标值，返回当前帧参数

    使用方法::

        engine = LipSyncEngine()
        engine.load_cdi3("/path/to/model.cdi3.json")
        engine.feed_phonemes(phoneme_events)

        # 每帧调用
        params = engine.get_current_params(current_time)
    """

    def __init__(self) -> None:
        self._config = LipSyncConfig()
        self._custom_mapping: Dict[str, Dict[str, float]] = {}
        self._active_scheme_mapping: Dict[str, Dict[str, float]] = {}

        # 音素队列（按时间排列）
        self._phoneme_queue: List[PhonemeEvent] = []
        self._current_phoneme: Optional[PhonemeEvent] = None

        # 参数平滑状态
        self._current_params: Dict[str, float] = {}
        self._target_params: Dict[str, float] = {}
        self._last_update_time: float = 0.0

        # 统计
        self._phoneme_history: deque = deque(maxlen=100)

        # 初始化默认方案
        self.set_scheme(LipSyncScheme.VOWEL_5)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def load_cdi3(self, cdi3_path: str) -> bool:
        """从 .cdi3.json 加载口型映射配置。

        .cdi3.json 格式示例::

            {
                "Version": 1,
                "Parameters": [{
                    "Id": "ParamMouthOpenY",
                    "GroupId": "Mouth",
                    "Name": "口开闭",
                    "Blend": [
                        {"Id": "PhonemeA", "Type": "Add", "Value": 1.0},
                        {"Id": "PhonemeI", "Type": "Add", "Value": 0.4}
                    ]
                }]
            }
        """
        path = Path(cdi3_path)
        if not path.exists():
            log.warning(f".cdi3.json 未找到: {cdi3_path}，使用内置方案")
            return False

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning(f".cdi3.json 解析失败: {exc}")
            return False

        # 从 .cdi3.json 中提取音素-参数映射
        cdi3_mapping: Dict[str, Dict[str, float]] = {}

        params = data.get("Parameters", [])
        for param_group in params:
            param_id = param_group.get("Id", "")
            blends = param_group.get("Blend", [])
            for blend in blends:
                phoneme_id = blend.get("Id", "")
                value = blend.get("Value", 0.0)
                if phoneme_id not in cdi3_mapping:
                    cdi3_mapping[phoneme_id] = {}
                cdi3_mapping[phoneme_id][param_id] = value

        if cdi3_mapping:
            self._custom_mapping = cdi3_mapping
            self._active_scheme_mapping = cdi3_mapping
            log.info(f"已加载 .cdi3.json: {len(cdi3_mapping)} 个音素映射")
            return True
        else:
            log.warning(".cdi3.json 中未找到有效映射，使用内置方案")
            return False

    def set_scheme(self, scheme: LipSyncScheme) -> None:
        """切换口型方案。

        Args:
            scheme: 内置方案枚举
        """
        self._config.scheme = scheme
        if scheme == LipSyncScheme.VOWEL_5:
            self._active_scheme_mapping = dict(VOWEL_5_MAPPING)
        elif scheme == LipSyncScheme.VOWEL_12:
            self._active_scheme_mapping = dict(VOWEL_12_MAPPING)
        elif scheme == LipSyncScheme.SIMPLE_OPEN_CLOSE:
            self._active_scheme_mapping = {
                "any": {"ParamMouthOpenY": 1.0},
                "sil": {"ParamMouthOpenY": 0.0},
            }
        log.info(f"口型方案切换至: {scheme.value}")

    def set_custom_mapping(
        self, mapping: Dict[str, Dict[str, float]]
    ) -> None:
        """设置自定义音素-参数映射。

        Args:
            mapping: {音素: {Live2D参数: 值}}，如
                {"AH": {"ParamMouthOpenY": 0.8, "ParamMouthForm": 0.1}}
        """
        self._custom_mapping = mapping
        self._active_scheme_mapping = mapping
        log.info(f"已设置自定义映射: {len(mapping)} 个音素")

    def feed_phonemes(self, events: List[PhonemeEvent]) -> None:
        """输入音素事件序列（按时间排序）。

        通常由 ASR 模块（Whisper、Rhubarb Lip Sync 等）提供。
        支持追加式调用，引擎内部自动去重和排序。

        Args:
            events: PhonemeEvent 列表。
        """
        # 去重 + 排序
        existing_ids = {(e.phoneme, e.start_time) for e in self._phoneme_queue}
        for event in events:
            key = (event.phoneme, event.start_time)
            if key not in existing_ids:
                self._phoneme_queue.append(event)
                existing_ids.add(key)

        self._phoneme_queue.sort(key=lambda e: e.start_time)
        log.debug(f"已接收 {len(events)} 个音素事件，队列共 {len(self._phoneme_queue)} 个")

    def get_current_params(
        self,
        current_time: float,
        delta_time: Optional[float] = None,
    ) -> Dict[str, float]:
        """获取当前时刻的口型参数。

        Args:
            current_time: 当前音频播放时间（秒）
            delta_time: 距上次调用的时间差（秒），用于平滑计算

        Returns:
            {Live2D参数名: 值}
        """
        if delta_time is None:
            if self._last_update_time == 0:
                delta_time = 0.016  # 默认 60fps
            else:
                delta_time = current_time - self._last_update_time
        self._last_update_time = current_time

        # 查找当前位置的音素
        active_phoneme = self._find_active_phoneme(current_time)

        if active_phoneme and active_phoneme != self._current_phoneme:
            self._phoneme_history.append(active_phoneme)

        self._current_phoneme = active_phoneme

        # 计算目标参数
        if active_phoneme:
            self._target_params = self._get_phoneme_target(active_phoneme.phoneme)
        else:
            # 静音状态
            self._target_params = {
                "ParamMouthOpenY": self._config.fallback_mouth_open,
                "ParamMouthForm": 0.0,
            }

        # 平滑过渡
        self._current_params = self._smooth_params(
            self._current_params,
            self._target_params,
            delta_time,
        )

        return dict(self._current_params)

    def clear(self) -> None:
        """清空所有音素数据和状态。"""
        self._phoneme_queue.clear()
        self._current_phoneme = None
        self._current_params.clear()
        self._target_params.clear()
        self._last_update_time = 0.0
        log.debug("口型引擎已重置")

    def get_active_phoneme(self) -> Optional[str]:
        """获取当前正在播放的音素符号。"""
        if self._current_phoneme:
            return self._current_phoneme.phoneme
        return None

    @property
    def current_params(self) -> Dict[str, float]:
        return dict(self._current_params)

    @property
    def config(self) -> LipSyncConfig:
        return self._config

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _find_active_phoneme(
        self, current_time: float
    ) -> Optional[PhonemeEvent]:
        """在音素队列中二分查找当前时间对应的音素。"""
        if not self._phoneme_queue:
            return None

        # 清理过期音素
        while (
            self._phoneme_queue
            and self._phoneme_queue[0].end_time < current_time - 0.1
        ):
            self._phoneme_queue.pop(0)

        if not self._phoneme_queue:
            return None

        # 找到覆盖 current_time 的音素
        for event in self._phoneme_queue:
            if event.start_time <= current_time <= event.end_time:
                return event

        # 如果当前时间在两个音素之间，取最近的
        closest = None
        min_dist = float("inf")
        for event in self._phoneme_queue:
            # 选择离当前时间最近的已完成音素
            if event.end_time <= current_time:
                dist = current_time - event.end_time
                if dist < min_dist:
                    min_dist = dist
                    closest = event
            # 或者选择即将开始的音素
            else:
                dist = event.start_time - current_time
                if dist < min_dist:
                    min_dist = dist
                    closest = event

        if min_dist > 0.3:  # 超 300ms 视为静音
            return None
        return closest

    def _get_phoneme_target(self, phoneme: str) -> Dict[str, float]:
        """根据音素查找目标参数值。"""
        # 精确匹配
        if phoneme in self._active_scheme_mapping:
            return dict(self._active_scheme_mapping[phoneme])

        # 静音检测
        if phoneme == "sil" or phoneme.startswith("sp"):
            return {"ParamMouthOpenY": self._config.fallback_mouth_open}

        # 模糊匹配（取第一字符，如 "AH" → "A"）
        first_char = phoneme[0].upper()
        if first_char in self._active_scheme_mapping:
            return dict(self._active_scheme_mapping[first_char])

        # 兜底：用 "any" 通配
        if "any" in self._active_scheme_mapping:
            return dict(self._active_scheme_mapping["any"])

        # 最终兜底
        return {"ParamMouthOpenY": 0.3}

    def _smooth_params(
        self,
        current: Dict[str, float],
        target: Dict[str, float],
        delta_time: float,
    ) -> Dict[str, float]:
        """指数平滑参数过渡。

        根据 config.transition_time 计算平滑系数。
        """
        result = dict(current)

        # 过渡速度 = 1 - e^(-dt * 3 / transition_time)
        tau = max(0.01, self._config.transition_time)
        t = min(1.0, delta_time * 3.0 / tau)
        alpha = 1.0 - math.exp(-t)

        all_keys = set(current.keys()) | set(target.keys())
        for key in all_keys:
            current_val = current.get(key, 0.0)
            target_val = target.get(key, 0.0)
            result[key] = current_val + (target_val - current_val) * alpha

        return result


# ------------------------------------------------------------------
# 音素解析工具
# ------------------------------------------------------------------

def parse_rhubarb_output(rhubarb_text: str) -> List[PhonemeEvent]:
    """解析 Rhubarb Lip Sync 输出的音素时间线。

    Rhubarb 输出格式示例::

        0.00	A
        0.12	B
        0.24	C

    Args:
        rhubarb_text: Rhubarb stdout 文本（制表符分隔）

    Returns:
        PhonemeEvent 列表
    """
    events: List[PhonemeEvent] = []
    lines = rhubarb_text.strip().split("\n")

    for i, line in enumerate(lines):
        parts = line.strip().split("\t")
        if len(parts) < 2:
            continue
        try:
            start_time = float(parts[0])
            phoneme = parts[1]
            end_time = (
                float(lines[i + 1].split("\t")[0])
                if i + 1 < len(lines)
                else start_time + 0.1
            )
            events.append(PhonemeEvent(
                phoneme=phoneme,
                start_time=start_time,
                end_time=end_time,
            ))
        except (ValueError, IndexError):
            continue

    return events


def parse_whisper_word_timestamps(
    segments: List[Dict[str, Any]],
) -> List[PhonemeEvent]:
    """从 Whisper 词级时间戳生成近似音素事件。

    Whisper 不直接输出音素，这是基于词级时间戳的近似方案。
    适合用于简单开闭口型同步（SIMPLE_OPEN_CLOSE 方案）。

    Args:
        segments: Whisper segments 输出，每个含 start/end/text

    Returns:
        PhonemeEvent 列表（使用 "any" 和 "sil" 标签）
    """
    events: List[PhonemeEvent] = []
    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", 0)

        # 检测静音间隙
        if events and start - events[-1].end_time > 0.2:
            events.append(PhonemeEvent(
                phoneme="sil",
                start_time=events[-1].end_time,
                end_time=start,
            ))

        events.append(PhonemeEvent(
            phoneme="any",  # 通配：有语音即张嘴
            start_time=start,
            end_time=end,
        ))

    return events
