#!/usr/bin/env python3
"""
Real-time face tracking with MediaPipe Face Mesh.

Extracts 468 facial landmarks and computes 52 Apple ARKit BlendShape
coefficients. Gracefully degrades when mediapipe or a webcam is unavailable.
"""

import math
import time
from typing import Optional, Dict, List, Tuple

from core.logger import get_logger

log = get_logger("face_tracker")

# Optional dependency: mediapipe
try:
    import mediapipe as mp
    import cv2
    import numpy as np
    _MEDIAPIPE_AVAILABLE = True
except ImportError:
    _MEDIAPIPE_AVAILABLE = False
    mp = None  # type: ignore
    cv2 = None  # type: ignore
    np = None  # type: ignore


# ARKit BlendShape names (52 coefficients)
ARKIT_BLENDSHAPES: List[str] = [
    "browDownLeft", "browDownRight", "browInnerUp",
    "browOuterUpLeft", "browOuterUpRight",
    "cheekPuff", "cheekSquintLeft", "cheekSquintRight",
    "eyeBlinkLeft", "eyeBlinkRight", "eyeLookDownLeft", "eyeLookDownRight",
    "eyeLookInLeft", "eyeLookInRight", "eyeLookOutLeft", "eyeLookOutRight",
    "eyeLookUpLeft", "eyeLookUpRight",
    "eyeSquintLeft", "eyeSquintRight", "eyeWideLeft", "eyeWideRight",
    "jawForward", "jawLeft", "jawRight", "jawOpen",
    "mouthClose", "mouthDimpleLeft", "mouthDimpleRight",
    "mouthFrownLeft", "mouthFrownRight", "mouthFunnel",
    "mouthLeft", "mouthRight",
    "mouthLowerDownLeft", "mouthLowerDownRight",
    "mouthPressLeft", "mouthPressRight",
    "mouthPucker", "mouthRollLower", "mouthRollUpper",
    "mouthShrugLower", "mouthShrugUpper",
    "mouthSmileLeft", "mouthSmileRight",
    "mouthStretchLeft", "mouthStretchRight",
    "mouthUpperUpLeft", "mouthUpperUpRight",
    "noseSneerLeft", "noseSneerRight",
    "tongueOut",
]


class FaceTracker:
    """Real-time face tracking via MediaPipe Face Mesh (468 landmarks).

    Parameters
    ----------
    camera_id : int
        Video capture device index (default 0).
    min_detection_confidence : float
        Minimum confidence for face detection.
    min_tracking_confidence : float
        Minimum confidence for face tracking.
    """

    def __init__(
        self,
        camera_id: int = 0,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ):
        self.camera_id = camera_id
        self.min_detection_confidence = min_detection_confidence
        self.min_tracking_confidence = min_tracking_confidence

        self._running = False
        self._cap = None
        self._face_mesh = None
        self._last_landmarks: Optional[Dict] = None
        self._last_blendshapes: Dict[str, float] = {name: 0.0 for name in ARKIT_BLENDSHAPES}
        self._frame_time = 0.0

        if not _MEDIAPIPE_AVAILABLE:
            log.warning(
                "mediapipe/cv2/numpy not installed. FaceTracker will run in "
                "stub mode (returning None). Install with: pip install mediapipe opencv-python"
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Initialize MediaPipe Face Mesh and open the webcam."""
        if not _MEDIAPIPE_AVAILABLE:
            log.warning("Cannot start FaceTracker: mediapipe not available")
            return

        try:
            self._face_mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=False,
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=self.min_detection_confidence,
                min_tracking_confidence=self.min_tracking_confidence,
            )
            self._cap = cv2.VideoCapture(self.camera_id)
            if not self._cap.isOpened():
                log.error(f"Cannot open camera (id={self.camera_id})")
                self._cap = None
                return
            self._running = True
            log.success(f"FaceTracker started (camera={self.camera_id})")
        except Exception as e:
            log.error(f"Failed to start FaceTracker: {e}")
            self._running = False

    def stop(self) -> None:
        """Release the camera and the Face Mesh model."""
        self._running = False
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        if self._face_mesh is not None:
            try:
                self._face_mesh.close()
            except Exception:
                pass
            self._face_mesh = None
        log.info("FaceTracker stopped")

    def is_running(self) -> bool:
        """Return True if the tracker is actively capturing."""
        return self._running and self._cap is not None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "FaceTracker":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------------

    def get_landmarks(self) -> Optional[Dict]:
        """Capture a frame and return 468 normalized landmarks.

        Returns
        -------
        dict or None
            ``{"points": [(x, y, z), ... 468], "image_size": (w, h)}`` or
            ``None`` if no face is detected or mediapipe is unavailable.
        """
        if not self.is_running() or self._face_mesh is None:
            return None

        try:
            ret, frame = self._cap.read()
            if not ret:
                return None

            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self._face_mesh.process(rgb)

            if not results.multi_face_landmarks:
                return None

            face = results.multi_face_landmarks[0]
            points: List[Tuple[float, float, float]] = []
            for lm in face.landmark:
                points.append((lm.x, lm.y, lm.z))

            self._last_landmarks = {"points": points, "image_size": (w, h)}
            self._frame_time = time.time()
            self._compute_blendshapes(points, w, h)
            return self._last_landmarks

        except Exception as e:
            log.error(f"Error getting landmarks: {e}")
            return None

    def get_blendshapes(self) -> Dict[str, float]:
        """Return the most recent 52 ARKit blendshape coefficients.

        Returns ``{name: float 0-1}`` for all 52 blendshapes.
        """
        return dict(self._last_blendshapes)

    # ------------------------------------------------------------------
    # Blendshape computation from landmark geometry
    # ------------------------------------------------------------------

    def _compute_blendshapes(self, points: List[Tuple[float, float, float]],
                             img_w: int, img_h: int) -> None:
        """Compute approximate ARKit blendshapes from 468 landmarks.

        This is a heuristic approximation — it does not use MediaPipe's
        built-in blendshape model (which requires the refined attention
        model). It derives coefficients from relative distances between
        key landmark indices.
        """
        bs = {name: 0.0 for name in ARKIT_BLENDSHAPES}

        def pt(i: int) -> Tuple[float, float, float]:
            return points[i]

        def dist2d(a: Tuple[float, ...], b: Tuple[float, ...]) -> float:
            return math.hypot(a[0] - b[0], a[1] - b[1])

        def clamp01(v: float) -> float:
            return max(0.0, min(1.0, v))

        def normalize(val: float, ref: float) -> float:
            if ref < 1e-9:
                return 0.0
            return clamp01(val / ref)

        # Reference scale: distance between outer eye corners (landmark 33 and 263)
        eye_outer_l = pt(33)
        eye_outer_r = pt(263)
        ref_scale = dist2d(eye_outer_l, eye_outer_r)
        if ref_scale < 1e-6:
            self._last_blendshapes = bs
            return

        # --- Eye blink (landmarks 159/145 left, 386/374 right) ---
        left_eye_top, left_eye_bot = pt(159), pt(145)
        right_eye_top, right_eye_bot = pt(386), pt(374)
        left_open = dist2d(left_eye_top, left_eye_bot)
        right_open = dist2d(right_eye_top, right_eye_bot)
        eye_open_ref = ref_scale * 0.18
        bs["eyeBlinkLeft"] = clamp01(1.0 - normalize(left_open, eye_open_ref))
        bs["eyeBlinkRight"] = clamp01(1.0 - normalize(right_open, eye_open_ref))

        # --- Eye look direction from iris landmarks (468+9=477) ---
        # MediaPipe refined landmarks: 468-477 are iris
        if len(points) > 477:
            left_iris = pt(468)
            right_iris = pt(473)
            left_eye_center = pt(168)  # approximate center
            right_eye_center = pt(362)  # approximate center
            # Horizontal look
            lx = (left_iris[0] - left_eye_center[0]) / ref_scale
            rx = (right_iris[0] - right_eye_center[0]) / ref_scale
            look_x = (lx + rx) * 5.0
            if look_x > 0:
                bs["eyeLookOutLeft"] = clamp01(look_x)
                bs["eyeLookInRight"] = clamp01(look_x)
            else:
                bs["eyeLookInLeft"] = clamp01(-look_x)
                bs["eyeLookOutRight"] = clamp01(-look_x)
            # Vertical look
            ly = (left_iris[1] - left_eye_center[1]) / ref_scale
            if ly > 0:
                bs["eyeLookDownLeft"] = clamp01(ly * 5.0)
                bs["eyeLookDownRight"] = clamp01(ly * 5.0)
            else:
                bs["eyeLookUpLeft"] = clamp01(-ly * 5.0)
                bs["eyeLookUpRight"] = clamp01(-ly * 5.0)

        # --- Jaw open (13 top lip, 14 bottom lip) ---
        mouth_open = dist2d(pt(13), pt(14))
        jaw_ref = ref_scale * 0.35
        bs["jawOpen"] = normalize(mouth_open, jaw_ref)

        # --- Mouth smile (61 left corner, 291 right corner) ---
        mouth_center = pt(13)
        left_corner = pt(61)
        right_corner = pt(291)
        # Smile lifts corners up (smaller y relative to center)
        left_smile = clamp01((mouth_center[1] - left_corner[1]) / (ref_scale * 0.15))
        right_smile = clamp01((mouth_center[1] - right_corner[1]) / (ref_scale * 0.15))
        bs["mouthSmileLeft"] = left_smile
        bs["mouthSmileRight"] = right_smile
        bs["mouthForm"] = (left_smile + right_smile) * 0.5  # pseudo-parameter

        # --- Mouth width stretch ---
        mouth_width = dist2d(left_corner, right_corner)
        width_ref = ref_scale * 0.55
        stretch = clamp01((mouth_width - width_ref * 0.8) / (width_ref * 0.5))
        bs["mouthStretchLeft"] = stretch
        bs["mouthStretchRight"] = stretch

        # --- Mouth funnel / pucker ---
        if mouth_open > ref_scale * 0.05:
            # If mouth is open but corners are pulled in, it's a funnel
            if mouth_width < width_ref * 0.9:
                bs["mouthFunnel"] = clamp01(1.0 - mouth_width / width_ref)
            else:
                bs["mouthPucker"] = clamp01(1.0 - mouth_width / width_ref) * 0.5

        # --- Brows (inner up: landmark 55/285, inner brow 105/334) ---
        # browInnerUp approximated by distance from brow to eye
        left_brow_inner = pt(105)
        right_brow_inner = pt(334)
        left_brow_outer = pt(70)
        right_brow_outer = pt(300)
        left_eye_top_pt = pt(159)
        right_eye_top_pt = pt(386)

        brow_ref = ref_scale * 0.2
        bs["browInnerUp"] = normalize(
            dist2d(left_brow_inner, left_eye_top_pt) +
            dist2d(right_brow_inner, right_eye_top_pt),
            brow_ref * 2
        )
        bs["browOuterUpLeft"] = normalize(dist2d(left_brow_outer, left_eye_top_pt), brow_ref)
        bs["browOuterUpRight"] = normalize(dist2d(right_brow_outer, right_eye_top_pt), brow_ref)

        # Brow down: if brows are closer to eyes than baseline
        baseline_brow = ref_scale * 0.18
        left_brow_down = clamp01(1.0 - dist2d(left_brow_inner, left_eye_top_pt) / baseline_brow)
        right_brow_down = clamp01(1.0 - dist2d(right_brow_inner, right_eye_top_pt) / baseline_brow)
        bs["browDownLeft"] = left_brow_down * 0.5
        bs["browDownRight"] = right_brow_down * 0.5

        # --- Jaw left/right (from chin landmark 152 vs center) ---
        chin = pt(152)
        nose_tip = pt(1)
        jaw_offset = (chin[0] - nose_tip[0]) / ref_scale
        if jaw_offset > 0:
            bs["jawRight"] = clamp01(jaw_offset * 3.0)
        else:
            bs["jawLeft"] = clamp01(-jaw_offset * 3.0)

        # --- Cheek puff (from cheek landmarks 205/425 bulging) ---
        # Heuristic: cheek area expands relative to eye-outer distance
        left_cheek = pt(205)
        right_cheek = pt(425)
        cheek_width = dist2d(left_cheek, right_cheek)
        cheek_ref = ref_scale * 1.3
        bs["cheekPuff"] = normalize(cheek_width, cheek_ref)

        # --- Eye squint (orbicularis: distance 159<->233) ---
        left_squint = dist2d(pt(159), pt(233))
        right_squint = dist2d(pt(386), pt(463))
        squint_ref = ref_scale * 0.25
        bs["eyeSquintLeft"] = clamp01(1.0 - normalize(left_squint, squint_ref)) * 0.7
        bs["eyeSquintRight"] = clamp01(1.0 - normalize(right_squint, squint_ref)) * 0.7

        # --- Wide eyes (opposite of squint) ---
        bs["eyeWideLeft"] = clamp01((left_open / eye_open_ref - 1.0) * 2.0)
        bs["eyeWideRight"] = clamp01((right_open / eye_open_ref - 1.0) * 2.0)

        # --- Nose sneer (97/326 upper lip area) ---
        left_nose = pt(97)
        right_nose = pt(326)
        nose_ref = ref_scale * 0.15
        bs["noseSneerLeft"] = normalize(dist2d(left_nose, pt(165)), nose_ref) * 0.5
        bs["noseSneerRight"] = normalize(dist2d(right_nose, pt(391)), nose_ref) * 0.5

        # --- Mouth frown / dimple ---
        # Frown: corners pulled down (lower y than center baseline)
        left_frown = clamp01((left_corner[1] - mouth_center[1]) / (ref_scale * 0.1))
        right_frown = clamp01((right_corner[1] - mouth_center[1]) / (ref_scale * 0.1))
        bs["mouthFrownLeft"] = left_frown
        bs["mouthFrownRight"] = right_frown
        bs["mouthDimpleLeft"] = left_frown * 0.5
        bs["mouthDimpleRight"] = right_frown * 0.5

        # --- Mouth press (lips pressed together) ---
        upper_lip = pt(13)
        lower_lip = pt(14)
        lip_dist = dist2d(upper_lip, lower_lip)
        if lip_dist < ref_scale * 0.02:
            bs["mouthPressLeft"] = 0.5
            bs["mouthPressRight"] = 0.5
            bs["mouthClose"] = 0.5

        # --- Mouth left/right from asymmetric corners ---
        mouth_mid = ((left_corner[0] + right_corner[0]) * 0.5,
                     (left_corner[1] + right_corner[1]) * 0.5)
        nose_x = nose_tip[0]
        asymmetry = (mouth_mid[0] - nose_x) / ref_scale
        if asymmetry > 0:
            bs["mouthRight"] = clamp01(asymmetry * 5.0)
        else:
            bs["mouthLeft"] = clamp01(-asymmetry * 5.0)

        # --- Lower down / upper up ---
        upper_up = (nose_tip[1] - upper_lip[1]) / ref_scale
        lower_down = (lower_lip[1] - chin[1]) / ref_scale
        bs["mouthUpperUpLeft"] = clamp01(upper_up * 8.0 - 0.5)
        bs["mouthUpperUpRight"] = bs["mouthUpperUpLeft"]
        bs["mouthLowerDownLeft"] = clamp01(-lower_down * 8.0 + 0.5)
        bs["mouthLowerDownRight"] = bs["mouthLowerDownLeft"]

        # Jaw forward (very rough)
        jaw_fwd = (chin[2] if len(chin) > 2 else 0) - (nose_tip[2] if len(nose_tip) > 2 else 0)
        bs["jawForward"] = clamp01(jaw_fwd * 10.0 + 0.5)

        self._last_blendshapes = bs

    # ------------------------------------------------------------------
    # Head rotation estimation
    # ------------------------------------------------------------------

    def get_head_rotation(self) -> Optional[Dict[str, float]]:
        """Estimate head rotation (pitch, yaw, roll) in degrees.

        Uses 3D solvePnP on key landmarks. Returns ``{"x": ..., "y": ...,
        "z": ...}`` or ``None``.
        """
        if not self.is_running() or self._last_landmarks is None:
            return None
        if cv2 is None or np is None:
            return None

        points = self._last_landmarks["points"]
        w, h = self._last_landmarks["image_size"]

        # 3D model points (approximate)
        model_points = np.array([
            (0.0, 0.0, 0.0),           # nose tip
            (0.0, -330.0, -65.0),      # chin
            (-225.0, 170.0, -135.0),   # left eye corner
            (225.0, 170.0, -135.0),    # right eye corner
            (-150.0, -150.0, -125.0),  # left mouth corner
            (150.0, -150.0, -125.0),   # right mouth corner
        ], dtype=np.float64)

        image_points = np.array([
            (points[1][0] * w, points[1][1] * h),
            (points[152][0] * w, points[152][1] * h),
            (points[33][0] * w, points[33][1] * h),
            (points[263][0] * w, points[263][1] * h),
            (points[61][0] * w, points[61][1] * h),
            (points[291][0] * w, points[291][1] * h),
        ], dtype=np.float64)

        focal = w
        camera_matrix = np.array([
            [focal, 0, w / 2],
            [0, focal, h / 2],
            [0, 0, 1],
        ], dtype=np.float64)
        dist = np.zeros((4, 1), dtype=np.float64)

        try:
            success, rvec, tvec = cv2.solvePnP(
                model_points, image_points, camera_matrix, dist
            )
            if not success:
                return None
            rmat, _ = cv2.Rodrigues(rvec)
            # Decompose to Euler angles
            sy = math.sqrt(rmat[0, 0] ** 2 + rmat[1, 0] ** 2)
            x_angle = math.degrees(math.atan2(rmat[2, 1], rmat[2, 2]))  # pitch
            y_angle = math.degrees(math.atan2(-rmat[2, 0], sy))          # yaw
            z_angle = math.degrees(math.atan2(rmat[1, 0], rmat[0, 0]))   # roll
            return {"x": x_angle, "y": y_angle, "z": z_angle}
        except Exception as e:
            log.debug(f"Head rotation estimation failed: {e}")
            return None
