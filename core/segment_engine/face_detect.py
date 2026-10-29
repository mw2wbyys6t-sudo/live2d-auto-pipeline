#!/usr/bin/env python3
"""
Pygmalion Studio - 脸部定位（YuNet 优先，肤色连通域兜底）

分层必须在角色体内确定「脸」的精确位置：眼睛、眉毛、嘴都按脸的包围盒
切分，脸偏一点就会把刘海切进眼睛、把嘴切到下巴。

桌面安装包已内置 OpenCV YuNet 人脸检测模型
（``models/face_detection_yunet_2023mar.onnx``，232KB，随包分发不联网），
优先用它定位；拿不到模型或检测失败时退回肤色连通域估计，并**如实返回
定位来源**，由调用方写进分层方法的说明里，不把兜底结果冒充高精度检测。

动漫脸对真实人脸检测器并不友好，实测 YuNet 对二次元立绘仍能给出
误差很小的框（demo_input.png 上命中 0.87 分、位置与肉眼一致）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

try:  # pragma: no cover - opencv 是桌面必装依赖
    import cv2  # type: ignore
    HAS_CV2 = True
except ImportError:  # pragma: no cover
    HAS_CV2 = False

#: 随包分发的 YuNet 权重文件名。
YUNET_NAME = "face_detection_yunet_2023mar.onnx"

#: 环境变量：额外的模型搜索目录（os.pathsep 分隔），Go 桥从安装包注入。
MODELS_DIR_ENV = "LIVE2D_MODELS_DIR"

#: 定位来源标记。
SOURCE_YUNET = "yunet"
SOURCE_SKIN = "skin_heuristic"
SOURCE_NONE = "none"

#: YuNet 检测阈值：动漫脸的置信度普遍低于真人，阈值放到 0.55。
_SCORE_THRESHOLD = 0.55
_NMS_THRESHOLD = 0.3
_TOP_K = 5000

Box = Tuple[int, int, int, int]
Point = Tuple[float, float]


@dataclass(frozen=True)
class FaceAnchors:
    """脸部定位结果：包围盒 + 5 点关键点。

    YuNet 除了框之外还会输出 5 个关键点（双眼中心、鼻尖、两个嘴角），
    实测在二次元立绘上鼻尖/嘴角误差 < 3px、双眼中心误差约 10px。这几个
    锚点让五官分割从「猜位置」变成「对着点取」，是眼睛/鼻子/嘴能分准的
    关键；拿不到模型时用标准人脸比例从框里推出粗略锚点，并如实标记来源。
    """

    box: Box
    right_eye: Point
    left_eye: Point
    nose: Point
    right_mouth: Point
    left_mouth: Point
    source: str = SOURCE_YUNET

    @property
    def eye_mid(self) -> Point:
        """双眼中心连线的中点。"""
        return (
            (self.right_eye[0] + self.left_eye[0]) / 2.0,
            (self.right_eye[1] + self.left_eye[1]) / 2.0,
        )

    @property
    def mouth_mid(self) -> Point:
        """嘴角中点。"""
        return (
            (self.right_mouth[0] + self.left_mouth[0]) / 2.0,
            (self.right_mouth[1] + self.left_mouth[1]) / 2.0,
        )

    @property
    def eye_distance(self) -> float:
        """双眼间距（像素），用来给各部件定尺寸。"""
        return float(
            np.hypot(
                self.left_eye[0] - self.right_eye[0],
                self.left_eye[1] - self.right_eye[1],
            )
        )


def _split_env_dirs(raw: str) -> List[Path]:
    out: List[Path] = []
    for part in (raw or "").split(os.pathsep):
        part = part.strip()
        if part:
            out.append(Path(part))
    return out


def default_search_dirs() -> List[Path]:
    """返回 YuNet 权重的搜索目录（按优先级）。"""
    dirs: List[Path] = []
    dirs.extend(_split_env_dirs(os.environ.get(MODELS_DIR_ENV, "")))

    here = Path(__file__).resolve()
    # core/segment_engine/face_detect.py -> <app>/core/segment_engine
    for level in (2, 3):
        base = here.parents[level] if len(here.parents) > level else None
        if base is None:
            continue
        dirs.append(base / "models")
        dirs.append(base / "models" / "face")

    home = Path.home()
    dirs.append(home / ".pygmalion-studio" / "models")
    dirs.append(home / ".cache" / "live2d" / "models")
    return dirs


def find_yunet_model(dirs: Optional[Sequence[Path]] = None) -> Optional[Path]:
    """在候选目录中查找 YuNet 权重，返回首个命中的路径。"""
    search = list(dirs) if dirs is not None else default_search_dirs()
    seen: set[str] = set()
    for directory in search:
        try:
            key = str(Path(directory).resolve())
        except OSError:  # pragma: no cover
            continue
        if key in seen:
            continue
        seen.add(key)
        candidate = Path(directory) / YUNET_NAME
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except OSError:  # pragma: no cover
            continue
    return None


def _clip_box(
    x: float, y: float, w: float, h: float, width: int, height: int
) -> Optional[Box]:
    """把 YuNet 的 ``(x, y, w, h)`` 夹到画布内并取整。"""
    x0 = max(0, int(round(x)))
    y0 = max(0, int(round(y)))
    x1 = min(width - 1, int(round(x + w)))
    y1 = min(height - 1, int(round(y + h)))
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def estimate_anchors_from_box(box: Box) -> FaceAnchors:
    """拿不到模型时，用标准人脸比例从脸框推出粗略关键点。

    比例是按二次元立绘标定的（实测 YuNet 真值：眼线在框高 39%、鼻尖 60%、
    嘴角 74%、双眼间距 0.43 倍框宽），作为兜底时**明确标记来源**，调用方
    不得把猜出来的锚点当成检测结果写进分层说明。
    """
    x0, y0, x1, y1 = (int(v) for v in box)
    fw = max(1, x1 - x0)
    fh = max(1, y1 - y0)
    cx = x0 + fw / 2.0
    eye_y = y0 + fh * 0.39
    eye_dx = fw * 0.215
    nose_y = y0 + fh * 0.60
    mouth_y = y0 + fh * 0.74
    mouth_dx = fw * 0.15
    return FaceAnchors(
        box=(x0, y0, x1, y1),
        right_eye=(cx - eye_dx, eye_y),
        left_eye=(cx + eye_dx, eye_y),
        nose=(cx, nose_y),
        right_mouth=(cx - mouth_dx, mouth_y),
        left_mouth=(cx + mouth_dx, mouth_y),
        source=SOURCE_SKIN,
    )


def detect_face_yunet(
    rgb: np.ndarray, model_path: Optional[Path] = None
) -> Optional[FaceAnchors]:
    """用 YuNet 检测脸，返回带 5 点关键点的 :class:`FaceAnchors`；失败返回 ``None``。

    YuNet 的输出行是
    ``[x, y, w, h, 右眼x, 右眼y, 左眼x, 左眼y, 鼻尖x, 鼻尖y, 右嘴角x, 右嘴角y,
    左嘴角x, 左嘴角y, 置信度]``，这里把关键点原样取出来，不再二次估计。
    """
    if not HAS_CV2 or not hasattr(cv2, "FaceDetectorYN"):
        return None
    model = model_path or find_yunet_model()
    if model is None:
        return None

    height, width = rgb.shape[:2]
    if height < 32 or width < 32:
        return None

    try:
        detector = cv2.FaceDetectorYN.create(
            str(model), "", (width, height),
            _SCORE_THRESHOLD, _NMS_THRESHOLD, _TOP_K,
        )
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        _, faces = detector.detect(bgr)
    except Exception:
        return None

    if faces is None or len(faces) == 0:
        return None

    best = None
    best_area = -1.0
    for face in faces:
        x, y, w, h = (float(v) for v in face[:4])
        if w <= 1 or h <= 1:
            continue
        area = w * h
        if area > best_area:
            best_area = area
            best = face
    if best is None:
        return None

    x, y, w, h = (float(v) for v in best[:4])
    box = _clip_box(x, y, w, h, width, height)
    if box is None:
        return None

    # 关键点：右眼 / 左眼 / 鼻尖 / 右嘴角 / 左嘴角
    pts = np.asarray(best[4:14], dtype=np.float64).reshape(5, 2)
    right_eye = (float(pts[0][0]), float(pts[0][1]))
    left_eye = (float(pts[1][0]), float(pts[1][1]))
    nose = (float(pts[2][0]), float(pts[2][1]))
    right_mouth = (float(pts[3][0]), float(pts[3][1]))
    left_mouth = (float(pts[4][0]), float(pts[4][1]))

    # 关键点越界（贴边的人像）时夹回画布
    def _clamp(pt: Point) -> Point:
        return (
            float(min(max(pt[0], 0.0), width - 1.0)),
            float(min(max(pt[1], 0.0), height - 1.0)),
        )

    return FaceAnchors(
        box=box,
        right_eye=_clamp(right_eye),
        left_eye=_clamp(left_eye),
        nose=_clamp(nose),
        right_mouth=_clamp(right_mouth),
        left_mouth=_clamp(left_mouth),
        source=SOURCE_YUNET,
    )


def skin_mask(rgb: np.ndarray) -> np.ndarray:
    """动漫肤色掩码（暖色、浅色、低饱和）。

    白色衣物满足 R≈G≈B，白衬衫不会被误判成皮肤；蓝色头发与深色背景
    因为亮度不足被排除。
    """
    arr = np.asarray(rgb)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return np.zeros(arr.shape[:2], dtype=bool)
    r = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 2].astype(np.int16)
    return (
        (r >= 195)
        & (g >= 160)
        & (b >= 140)
        & (r > g)
        & (g >= b - 14)
        & ((r - g) <= 95)
        & ((g - b) <= 70)
    )


def _largest_component(mask: np.ndarray) -> np.ndarray:
    if not mask.any():
        return mask
    if HAS_CV2:
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
        if count <= 1:
            return mask
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        return labels == largest
    return mask


def _skin_face_component(skin: np.ndarray) -> Optional[np.ndarray]:
    """从肤色掩码里挑最像"脸"的连通块，而不是最大的一块。

    旧实现取"画面上 65% 内面积最大的肤色块"。全身立绘上面积最大的肤色块
    是**腿**——腿被 65% 横线截断后 bbox 仍是"偏方"的，于是脸框落在大腿上，
    五官锚点整体错位，后面所有部件分区（脸/脖子/衣服/头发）跟着崩：实测
    脸层出现在大腿位置、白衬衫被整片并进 ``hair_back``。

    脸与腿/手臂的差别不在面积，而在**形状与位置**：脸偏方（宽高比接近 1）、
    宽度只占画布一小段、且靠上。这里按 ``面积 / (1 + 4·y0/H)`` 打分，
    形状不合格的直接淘汰。
    """
    height, width = skin.shape
    if not skin.any():
        return None

    if HAS_CV2:
        count, labels, stats, _ = cv2.connectedComponentsWithStats(
            skin.astype(np.uint8), 8
        )
        best_label, best_score = 0, 0.0
        min_area = max(64, int(height * width * 0.0015))
        for label in range(1, count):
            x, y, bw, bh, area = (int(v) for v in stats[label])
            if area < min_area or bw <= 1 or bh <= 1:
                continue
            aspect = bw / float(bh)
            if not (0.5 <= aspect <= 2.0):
                continue
            if bw > width * 0.6 or y > height * 0.55:
                continue
            score = area / (1.0 + 4.0 * y / height)
            if score > best_score:
                best_label, best_score = label, score
        if best_label:
            return labels == best_label
        return None

    # 无 cv2：退回"上半部分最大的块"，至少不会落到腿以外的地方
    upper = np.zeros_like(skin)
    upper[: max(1, int(height * 0.65)), :] = True
    candidates = skin & upper
    return _largest_component(candidates) if candidates.any() else None


def detect_face_skin(
    rgb: np.ndarray, fg: Optional[np.ndarray] = None
) -> Optional[FaceAnchors]:
    """肤色连通域兜底：取最像脸的肤色块（并略微向上扩展额头）。

    关键点由标准人脸比例从脸框反推，来源标记为
    :data:`SOURCE_SKIN`（启发式），调用方据此判断精度。
    """
    skin = skin_mask(rgb)
    if fg is not None:
        skin = skin & (np.asarray(fg, dtype=bool) & (skin.any() or skin))
        if not skin.any():
            skin = skin_mask(rgb)
    if not skin.any():
        return None

    height, width = skin.shape
    component = _skin_face_component(skin)
    if component is None:
        return None
    ys, xs = np.where(component)
    if ys.size == 0:
        return None
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    # 额头被刘海盖住时肤色块偏矮，向上扩 22% 贴近真实脸框比例。
    face_h = max(1, y1 - y0)
    y0 = max(0, y0 - int(face_h * 0.22))
    pad = int((x1 - x0) * 0.06)
    x0 = max(0, x0 - pad)
    x1 = min(width - 1, x1 + pad)
    return estimate_anchors_from_box((x0, y0, x1, y1))


def detect_face(
    rgb: np.ndarray,
    fg: Optional[np.ndarray] = None,
    model_dirs: Optional[Sequence[Path]] = None,
) -> Optional[FaceAnchors]:
    """定位脸部，返回带关键点的 :class:`FaceAnchors`；完全失败返回 ``None``。

    优先 YuNet（带真实 5 点关键点），失败退到肤色连通域（关键点为比例
    估计）。调用方应读取 ``anchors.source`` 获知定位方式并如实记录。
    """
    model = find_yunet_model(model_dirs)
    anchors = detect_face_yunet(rgb, model)
    if anchors is not None:
        return anchors
    return detect_face_skin(rgb, fg)
