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
PITCH_UP_RATIO = 0.82  # "look up": nose rises to this share of its straight-ahead position


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
    """Stateful check-in challenge that a photo, GIF or recorded clip can't pass.

    Two sequences:

    - randomised (default, Settings → Accessibility): look straight, then
      three actions drawn at random every time — blink twice, blink three
      times, turn left, turn right, look up — each with its own time limit.
      A pre-recorded clip cannot know the order, and a GIF cannot react.
    - fixed (randomised off): blink 3 times quickly, turn left, look straight,
      turn right — the original sequence, easier for users who need more time.

    Anti-coincidence hardening: blinks only count if they come quickly
    (within BLINK_WINDOW), and head poses must be *held* for a few samples.

    ``directional=False`` (remote video such as a Meet tile, whose mirroring is
    unknown) asks for "either side" and then "the other side" instead of
    LEFT/RIGHT. Optionally a SpoofVote (app.core.antispoof) samples the face
    throughout, and a face that looks like a print or a screen fails at the end.
    """

    STAGE_PASSED = "passed"
    STAGE_FAILED = "failed"
    STEP_TIME = 8.0        # seconds allowed per randomised action
    FIXED_TIME = 60.0      # the fixed sequence keeps the old, generous limit

    _PROMPTS = {
        "center": "Look straight at the camera",
        "blink2": "Blink twice quickly",
        "blink3": "Blink three times quickly",
        "left": "Turn your head to the LEFT and hold",
        "right": "Turn your head to the RIGHT and hold",
        "turn_any": "Turn your head to either side and hold",
        "turn_opposite": "Now turn to the OTHER side and hold",
        "up": "Tilt your head UP and hold",
    }

    def __init__(self, tracker: FaceMeshTracker, directional: bool = True,
                 randomized: bool = True, spoof=None) -> None:
        self._tracker = tracker
        self._directional = directional
        self._randomized = randomized
        self._spoof = spoof
        # LEFT/RIGHT assume the mirrored selfie preview; see _yaw().
        self.mirrored = True
        self.reset()

    def reset(self) -> None:
        import random

        if self._randomized:
            turns = ["left", "right"] if self._directional else ["turn_any"]
            pool_blink = ["blink2", "blink3"]
            picks = [random.choice(pool_blink), random.choice(turns)]
            extra = [a for a in (turns + ["up"]) if a not in picks]  # one blink step is enough
            picks.append(random.choice(extra))
            random.shuffle(picks)
            if not self._directional and "turn_any" in picks:
                picks.insert(picks.index("turn_any") + 1, "turn_opposite")
            self.steps = ["center"] + picks
        else:
            self.steps = (["blink3", "left", "center", "right"] if self._directional
                          else ["blink3", "turn_any", "center", "turn_opposite"])
        self.step = 0
        self.stage = self.steps[0]
        self._step_started: float | None = None
        self._blink_times: list[float] = []
        self._eye_closed = False
        self._first_side: str | None = None
        self._hold = 0
        self._hold_side: str | None = None
        self._pitch_samples: list[float] = []
        self._pitch_base: float | None = None
        self.reason = ""
        self.spoof_score: float | None = None

    @property
    def passed(self) -> bool:
        return self.stage == self.STAGE_PASSED

    @property
    def failed(self) -> bool:
        return self.stage == self.STAGE_FAILED

    def prompt(self) -> str:
        if self.stage == self.STAGE_PASSED:
            return "Liveness check passed"
        if self.stage == self.STAGE_FAILED:
            return self.reason
        text = self._PROMPTS[self.stage]
        if self.stage in ("blink2", "blink3"):
            text += f" ({len(self._blink_times)}/{self._blinks_needed()})"
        return text

    def _blinks_needed(self) -> int:
        return 2 if self.stage == "blink2" else BLINKS_REQUIRED

    def _yaw(self, landmarks) -> float:
        """Yaw in the mirrored (selfie) frame of reference."""
        yaw = _yaw_ratio(landmarks)
        return yaw if self.mirrored else 1.0 - yaw

    @staticmethod
    def _pitch(landmarks) -> float:
        """Nose position between the eye line and the chin (smaller = head up)."""
        eye_y = (landmarks[33].y + landmarks[263].y) / 2
        chin_y = landmarks[152].y
        if chin_y - eye_y == 0:
            return 0.5
        return (landmarks[_NOSE_TIP].y - eye_y) / (chin_y - eye_y)

    def _held(self, condition: bool, side: str | None = None) -> bool:
        """True once `condition` has held for HOLD_FRAMES consecutive samples
        (on the same side, when sides matter)."""
        if condition and (side is None or side == self._hold_side or self._hold == 0):
            self._hold += 1
            self._hold_side = side
        else:
            self._hold = 1 if condition else 0
            self._hold_side = side if condition else None
        return self._hold >= HOLD_FRAMES

    def _advance(self) -> None:
        self.step += 1
        self._hold = 0
        self._hold_side = None
        self._blink_times = []
        self._step_started = None
        if self.step < len(self.steps):
            self.stage = self.steps[self.step]
            return
        # All actions done — the replay check has the last word.
        if self._spoof is not None:
            ok, score = self._spoof.verdict()
            self.spoof_score = score
            if not ok:
                self.stage = self.STAGE_FAILED
                self.reason = ("This looks like a photo or a screen, not a live camera. "
                               "Check-in stopped.")
                return
        self.stage = self.STAGE_PASSED

    def _fail(self, why: str) -> None:
        self.stage = self.STAGE_FAILED
        self.reason = why

    def process(self, frame_bgr: np.ndarray) -> dict:
        """Advance the challenge with one frame. Returns state for the UI."""
        if self.stage in (self.STAGE_PASSED, self.STAGE_FAILED):
            return self._state(True)
        now = time.monotonic()
        if self._step_started is None:
            self._step_started = now
        limit = self.STEP_TIME if self._randomized else self.FIXED_TIME
        if self.stage != "center" and now - self._step_started > limit:
            self._fail(f"Too slow: \"{self._PROMPTS[self.stage]}\" was not done in time.")
            return self._state(True)

        landmarks = self._tracker.landmarks(frame_bgr)
        if landmarks is None:
            return {"face_found": False, "stage": self.stage,
                    "prompt": "Position your face inside the frame", "passed": False,
                    "failed": False, "step": self.step, "steps": len(self.steps)}

        if self._spoof is not None:
            h, w = frame_bgr.shape[:2]
            xs = [p.x for p in landmarks]
            ys = [p.y for p in landmarks]
            self._spoof.maybe_add(frame_bgr, (min(xs) * w, min(ys) * h, max(xs) * w, max(ys) * h))

        st = self.stage
        yaw = self._yaw(landmarks)
        if st == "center":
            centred = YAW_CENTER_LO < yaw < YAW_CENTER_HI
            if centred:
                self._pitch_samples.append(self._pitch(landmarks))
            if self._held(centred):
                if self._pitch_base is None and self._pitch_samples:
                    self._pitch_base = float(np.median(self._pitch_samples))
                self._advance()
        elif st in ("blink2", "blink3"):
            ear = min(_ear(landmarks, _LEFT_EYE), _ear(landmarks, _RIGHT_EYE))
            if not self._eye_closed and ear < EAR_CLOSED:
                self._eye_closed = True
            elif self._eye_closed and ear > EAR_OPEN:
                self._eye_closed = False
                # only quick blinks count; natural blinking is too spread out
                self._blink_times = [t for t in self._blink_times if now - t <= BLINK_WINDOW] + [now]
                if len(self._blink_times) >= self._blinks_needed():
                    self._advance()
        elif st == "left":
            if self._held(yaw < YAW_LEFT):
                self._advance()
        elif st == "right":
            if self._held(yaw > YAW_RIGHT):
                self._advance()
        elif st == "turn_any":
            side = "low" if yaw < YAW_LEFT else ("high" if yaw > YAW_RIGHT else None)
            if self._held(side is not None, side):
                self._first_side = self._hold_side
                self._advance()
        elif st == "turn_opposite":
            opposite = (yaw > YAW_RIGHT) if self._first_side == "low" else (yaw < YAW_LEFT)
            if self._held(opposite):
                self._advance()
        elif st == "up":
            base = self._pitch_base if self._pitch_base is not None else 0.45
            if self._held(self._pitch(landmarks) < base * PITCH_UP_RATIO):
                self._advance()
        return self._state(True)

    def _state(self, face_found: bool) -> dict:
        out = {"face_found": face_found, "stage": self.stage, "prompt": self.prompt(),
               "passed": self.passed, "failed": self.failed,
               "step": min(self.step + 1, len(self.steps)), "steps": len(self.steps)}
        if self.spoof_score is not None:
            out["spoof_score"] = round(self.spoof_score, 3)
        return out
