"""Liveness detection: blink + head-turn challenge using MediaPipe FaceLandmarker.

A static photo cannot blink, and a flat photo/video replay struggles to follow
head-turn instructions, so the challenge is: blink twice, then turn the head
left, then right.
"""

from __future__ import annotations

import time
import urllib.request
from pathlib import Path

import numpy as np

# Face landmarker model asset (~3.7 MB), fetched once and cached locally.
_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task"
)
_MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "face_landmarker.task"

# FaceMesh landmark indices (see MediaPipe canonical face model).
_LEFT_EYE = [33, 160, 158, 133, 153, 144]
_RIGHT_EYE = [362, 385, 387, 263, 373, 380]
_NOSE_TIP = 1
_LEFT_CHEEK = 234
_RIGHT_CHEEK = 454

EAR_CLOSED = 0.20   # eye aspect ratio below this counts as closed
EAR_OPEN = 0.25     # must recover above this to complete a blink
BLINKS_REQUIRED = 3
BLINK_WINDOW = 4.0  # blinks must all happen within this many seconds
HOLD_FRAMES = 3     # consecutive samples a head turn must be held to count
YAW_LEFT = 0.36     # nose position ratio below this = head turned left
YAW_RIGHT = 0.64    # above this = head turned right
YAW_CENTER_LO, YAW_CENTER_HI = 0.42, 0.58


def _ensure_model() -> Path:
    if not _MODEL_PATH.exists():
        _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _MODEL_PATH.with_suffix(".download")
        urllib.request.urlretrieve(_MODEL_URL, tmp)
        tmp.rename(_MODEL_PATH)
    return _MODEL_PATH


class FaceMeshTracker:
    """Thin wrapper around MediaPipe FaceLandmarker returning landmarks or None.

    Also used by session monitoring as a fast "is a face visible" check.
    Landmark indices match the classic 468-point FaceMesh topology.
    """

    def __init__(self) -> None:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        self._mp = mp
        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(_ensure_model())),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._start = time.monotonic()
        self._last_ts = -1

    def landmarks(self, frame_bgr: np.ndarray):
        """Return the landmark list for the first face, or None."""
        rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        # VIDEO mode requires strictly increasing timestamps
        ts = int((time.monotonic() - self._start) * 1000)
        if ts <= self._last_ts:
            ts = self._last_ts + 1
        self._last_ts = ts
        result = self._landmarker.detect_for_video(image, ts)
        if not result.face_landmarks:
            return None
        return result.face_landmarks[0]

    def close(self) -> None:
        self._landmarker.close()


def _ear(landmarks, idx: list[int]) -> float:
    pts = np.array([(landmarks[i].x, landmarks[i].y) for i in idx])
    v1 = np.linalg.norm(pts[1] - pts[5])
    v2 = np.linalg.norm(pts[2] - pts[4])
    h = np.linalg.norm(pts[0] - pts[3])
    if h == 0:
        return 1.0
    return (v1 + v2) / (2.0 * h)


def _yaw_ratio(landmarks) -> float:
    """Horizontal nose position between the cheeks, 0 (left) .. 1 (right)."""
    left = landmarks[_LEFT_CHEEK].x
    right = landmarks[_RIGHT_CHEEK].x
    nose = landmarks[_NOSE_TIP].x
    if right - left == 0:
        return 0.5
    return (nose - left) / (right - left)


class LivenessChecker:
    """Stateful challenge: rapid blinks, then held head turns.

    Anti-coincidence hardening: blinks only count if they all happen within a
    short window (natural blinking is too spread out to pass), and head turns
    must be *held* for several consecutive samples (a random glance sideways
    doesn't count).

    Two modes:
    - directional=True (webcam selfie view, mirrored): turn LEFT, recenter,
      turn RIGHT — prompts match what the user sees on screen.
    - directional=False (remote video, e.g. a Meet tile, not mirrored): turn
      to EITHER side, recenter, then the OPPOSITE side. Robust regardless of
      whether the video is mirrored.
    """

    STAGE_BLINK = "blink"
    STAGE_TURN_LEFT = "turn_left"
    STAGE_CENTER = "recenter"
    STAGE_TURN_RIGHT = "turn_right"
    STAGE_TURN_ANY = "turn_any"
    STAGE_TURN_OPPOSITE = "turn_opposite"
    STAGE_PASSED = "passed"

    def __init__(self, tracker: FaceMeshTracker, directional: bool = True) -> None:
        self._tracker = tracker
        self._directional = directional
        self.reset()

    def reset(self) -> None:
        self.stage = self.STAGE_BLINK
        self._blink_times: list[float] = []
        self._eye_closed = False
        self._first_side: str | None = None  # "low"/"high" yaw side turned first
        self._hold = 0          # consecutive samples the current pose was held
        self._hold_side: str | None = None

    @property
    def passed(self) -> bool:
        return self.stage == self.STAGE_PASSED

    def prompt(self) -> str:
        return {
            self.STAGE_BLINK: (
                f"Blink {BLINKS_REQUIRED} times quickly "
                f"({len(self._blink_times)}/{BLINKS_REQUIRED})"
            ),
            self.STAGE_TURN_LEFT: "Turn your head to the LEFT and hold",
            self.STAGE_CENTER: "Look straight at the camera",
            self.STAGE_TURN_RIGHT: "Turn your head to the RIGHT and hold",
            self.STAGE_TURN_ANY: "Turn your head to either side and hold",
            self.STAGE_TURN_OPPOSITE: "Now turn to the OTHER side and hold",
            self.STAGE_PASSED: "Liveness check passed",
        }[self.stage]

    def _held(self, condition: bool, side: str | None = None) -> bool:
        """True once `condition` has been continuously true for HOLD_FRAMES
        samples (on the same side, when sides matter)."""
        if condition and (side is None or side == self._hold_side or self._hold == 0):
            self._hold += 1
            self._hold_side = side
        else:
            self._hold = 1 if condition else 0
            self._hold_side = side if condition else None
        return self._hold >= HOLD_FRAMES

    def process(self, frame_bgr: np.ndarray) -> dict:
        """Advance the challenge with one frame. Returns state for the UI."""
        landmarks = self._tracker.landmarks(frame_bgr)
        if landmarks is None:
            return {"face_found": False, "stage": self.stage,
                    "prompt": "Position your face inside the frame", "passed": self.passed}

        if self.stage == self.STAGE_BLINK:
            ear = min(_ear(landmarks, _LEFT_EYE), _ear(landmarks, _RIGHT_EYE))
            if not self._eye_closed and ear < EAR_CLOSED:
                self._eye_closed = True
            elif self._eye_closed and ear > EAR_OPEN:
                self._eye_closed = False
                now = time.monotonic()
                # only blinks inside the rolling window count; natural
                # blinking (one every few seconds) never accumulates enough
                self._blink_times = [
                    t for t in self._blink_times if now - t <= BLINK_WINDOW
                ] + [now]
                if len(self._blink_times) >= BLINKS_REQUIRED:
                    self._hold = 0
                    self.stage = (self.STAGE_TURN_LEFT if self._directional
                                  else self.STAGE_TURN_ANY)
        elif self.stage == self.STAGE_TURN_LEFT:
            if self._held(_yaw_ratio(landmarks) < YAW_LEFT):
                self._hold = 0
                self.stage = self.STAGE_CENTER
        elif self.stage == self.STAGE_TURN_ANY:
            yaw = _yaw_ratio(landmarks)
            side = "low" if yaw < YAW_LEFT else ("high" if yaw > YAW_RIGHT else None)
            if self._held(side is not None, side):
                self._first_side = self._hold_side
                self._hold = 0
                self.stage = self.STAGE_CENTER
        elif self.stage == self.STAGE_CENTER:
            if YAW_CENTER_LO < _yaw_ratio(landmarks) < YAW_CENTER_HI:
                self._hold = 0
                self.stage = (self.STAGE_TURN_RIGHT if self._directional
                              else self.STAGE_TURN_OPPOSITE)
        elif self.stage == self.STAGE_TURN_RIGHT:
            if self._held(_yaw_ratio(landmarks) > YAW_RIGHT):
                self.stage = self.STAGE_PASSED
        elif self.stage == self.STAGE_TURN_OPPOSITE:
            yaw = _yaw_ratio(landmarks)
            opposite = (yaw > YAW_RIGHT) if self._first_side == "low" else (yaw < YAW_LEFT)
            if self._held(opposite):
                self.stage = self.STAGE_PASSED

        return {"face_found": True, "stage": self.stage,
                "prompt": self.prompt(), "passed": self.passed}
