"""VTube Studio 模型配置（``<model>.vtube.json``）写出。

为什么需要它：VTS 只有在模型目录里有 vtube.json、且
``FileReferences.IdleAnimation`` 指向真实动作文件时，才会在导入后自动播放
待机动画。没有这份配置时模型导入后是静止站着的 —— 而物理链的输入全部来自
``ParamAngleX`` / ``ParamBodyAngleX`` / ``ParamBreath``，这些曲线只有待机动作
里才有，于是头发、裙摆一动不动（实测 2026-11-16：导入自建包后间隔 1.8s 抓
两帧，画面完全一致；同一个 VTS 里的大肥鱼因为有 vtube.json + IDLE 动作，
耳/尾/衣摆一直在摆）。

字段集合与取值对齐 VTS 1.35 首次导入时**自己写出**的文件：它用同一套 schema
读写，少一个键就会在下次保存时把整份配置重置回默认值。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from core.logger import get_logger

log = get_logger("exporter.vtube_json")

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _vts_timestamp(ts: Optional[float] = None) -> Dict[str, Any]:
    """VTS 的时间戳三件套（UTC / 本地 / 毫秒）。

    格式必须是 ``Monday, 16 November 2026, 09:32:22``：VTS 按英文长日期写，
    用 ``strftime('%A, %d %B %Y, %H:%M:%S')`` 在非英文 locale 下会写成中文，
    所以这里手工拼，保证跨机器一致。
    """
    ts = time.time() if ts is None else ts
    local = time.localtime(ts)
    utc = time.gmtime(ts)

    def fmt(t) -> str:
        return (f"{_WEEKDAYS[t.tm_wday]}, {t.tm_mday:02d} {_MONTHS[t.tm_mon - 1]} "
                f"{t.tm_year}, {t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}")

    return {
        "LastSavedVTubeStudioVersion": "1.35.10",
        "LastSavedPlatform": "Steam",
        "LastSavedDateUTC": fmt(utc),
        "LastSavedDateLocalTime": fmt(local),
        "LastSavedDateUnixMillisecondTimestamp": int(ts * 1000),
        "TotalSaveCount": 1,
    }


def build_vtube_json(character_name: str, model_file: str,
                     idle_animation: str = "", icon: str = "",
                     model_id: str = "") -> Dict[str, Any]:
    """构造一份 VTS 能直接接管的配置。"""
    return {
        "Version": 1,
        "Name": character_name,
        "ModelID": model_id or uuid.uuid4().hex,
        "FileReferences": {
            "Icon": icon,
            "Model": model_file,
            "IdleAnimation": idle_animation,
            "IdleAnimationWhenTrackingLost": "",
        },
        "ModelSaveMetadata": _vts_timestamp(),
        "SavedModelPosition": {
            "Position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "Rotation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            "Scale": {"x": 1.0, "y": 1.0, "z": 1.0},
        },
        "ModelPositionMovement": {
            "Use": True, "X": 6, "Y": 8, "Z": 11,
            "SmoothingX": 10, "SmoothingY": 10, "SmoothingZ": 10,
        },
        "ItemSettings": {
            "OnlyMoveWhenPinned": False,
            "AllowNormalHotkeyTriggers": True,
            "Multiplier_HeadAngleX": 1.0,
            "Multiplier_HeadAngleY": 1.0,
            "Multiplier_HeadAngleZ": 1.0,
            "Shift_HeadAngleX": 0.0,
            "Smoothing_HeadAngleX": 15.0,
            "Smoothing_HeadAngleY": 15.0,
            "Smoothing_HeadAngleZ": 15.0,
        },
        "PhysicsSettings": {
            "Use": True,
            "UseLegacyPhysics": False,
            "Live2DPhysicsFPS": 3,
            "PhysicsStrength": 50,
            "WindStrength": 0,
            "DraggingPhysicsStrength": 10,
        },
        "GeneralSettings": {
            "ModelVolume": 90,
            "TimeUntilTrackingLostIdleAnimation": 0.0,
            # 默认禁止创意工坊分享：产物含用户自己的立绘，不该被一键上传。
            "WorkshopSharingForbidden": True,
            "EnableExpressionSaving": True,
        },
        "ParameterSettings": [],
        "Hotkeys": [],
        "HotkeySettings": {
            "UseOnScreenHotkeys": False,
            "UseKeyboardHotkeys": True,
            "SendOnScreenHotkeysToPC": True,
            "OnScreenHotkeyAlpha": 75,
        },
        "ArtMeshDetails": {
            "ArtMeshesExcludedFromPinning": [],
            "ArtMeshesThatDeleteItemsOnDrop": [],
            "ArtMeshSceneLightingMultipliers": [],
            "ArtMeshMultiplyAndScreenColors": [],
            "ModelArtMeshGroups": [],
        },
        "ParameterCustomization": {
            "ParametersExcludedFromVNetSmoothing": [],
            "SoundTriggers": [],
        },
        "PhysicsCustomizationSettings": {
            "PhysicsMultipliersPerPhysicsGroup": [],
            "WindMultipliersPerPhysicsGroup": [],
            "DraggingPhysicsMultipliersPerPhysicsGroup": [],
        },
        "FolderInfo": {"HotkeyFolders": [], "ConfigItemFolders": []},
        "UserCustomization": {
            "PreferredLanguageSetBy": "",
            "PreferredLanguage": "",
            "AvailableLanguages": [],
            "UserParamFolderInfo": [],
            "UserParamInfo": [],
        },
        "SavedActiveExpressions": [],
    }


def write_vtube_json(output_dir: str, character_name: str,
                     idle_animation: str = "", icon: str = "") -> str:
    """写出 ``<character_name>.vtube.json``，返回路径。"""
    out = Path(output_dir)
    path = out / f"{character_name}.vtube.json"
    document = build_vtube_json(
        character_name=character_name,
        model_file=f"{character_name}.model3.json",
        idle_animation=idle_animation,
        icon=icon,
    )
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    log.success(f"VTube Studio 配置已写出：{path.name}"
                f"（待机动作：{idle_animation or '未指定'}）")
    return str(path)
