"""Spot meeting tiles whose face never moves.

A live person is never perfectly still: they blink, breathe, shift and
talk. A photo held up to the camera, a picture fed through a virtual camera
or a frozen feed shows almost the same pixels pass after pass. This watch
compares each recognised student's face area between recognition passes and
flags a student whose face has stayed still for a while, so the instructor
can ask for a liveness check.

It is a hint, not a verdict — someone listening very attentively can be
still for a moment, which is why the bar is a long stretch of near-identical
frames and the result only suggests a check. It does not catch a
pre-recorded video (that moves); the random action check does.
"""

from __future__ import annotations

import time

import cv2
import numpy as np

STILL_AFTER = 30.0     # seconds of stillness before a student is flagged
MOTION_FLOOR = 1.2     # mean grey-level change (0-255) that counts as movement
SIZE = 48              # faces are compared as SIZE x SIZE greyscale patches
SAME_PLACE = 0.85      # box overlap (IoU) below this counts as the head moving


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _patch(frame: np.ndarray, bbox) -> np.ndarray | None:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in bbox)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    grey = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    small = cv2.resize(grey, (SIZE, SIZE), interpolation=cv2.INTER_AREA).astype(np.float32)
    # Ignore overall brightness drift (auto-exposure on a still image).
    return small - small.mean()


class StillnessWatch:
    def __init__(self, still_after: float = STILL_AFTER, motion_floor: float = MOTION_FLOOR) -> None:
        self._still_after = still_after
        self._floor = motion_floor
        # sid → {"anchor", "patch", "still_since", "flagged"}. The patch is
        # always cut at the anchor box, so the detector's one-pixel jitter
        # on an unchanged picture is not mistaken for movement.
        self._state: dict[int, dict] = {}

    def update(self, frame: np.ndarray, matches, now: float | None = None) -> list[int]:
        """Feed one recognition pass (matches as (sid, score, bbox)).

        Returns the students who have *just* become suspicious.
        """
        now = time.monotonic() if now is None else now
        newly: list[int] = []
        for sid, _score, bbox in matches:
            bbox = tuple(int(v) for v in bbox)
            st = self._state.get(sid)
            if st is None or _iou(bbox, st["anchor"]) < SAME_PLACE:
                # New, or the head moved to a clearly different place.
                patch = _patch(frame, bbox)
                if patch is None:
                    continue
                if st is None:
                    self._state[sid] = {"anchor": bbox, "patch": patch,
                                        "still_since": None, "flagged": False}
                else:
                    st.update(anchor=bbox, patch=patch, still_since=None, flagged=False)
                continue
            patch = _patch(frame, st["anchor"])
            if patch is None:
                continue
            motion = float(np.mean(np.abs(patch - st["patch"])))
            st["patch"] = patch
            if motion >= self._floor:
                st["still_since"] = None
                st["flagged"] = False  # moving again: a later freeze warns again
                continue
            if st["still_since"] is None:
                st["still_since"] = now
            elif not st["flagged"] and now - st["still_since"] >= self._still_after:
                st["flagged"] = True
                newly.append(sid)
        return newly

    @property
    def still_after(self) -> float:
        return self._still_after

    def keep_only(self, visible: set[int]) -> None:
        """Forget students not on screen this pass; a face that comes back
        starts a fresh comparison."""
        for sid in [s for s in self._state if s not in visible]:
            del self._state[sid]

    def flagged(self) -> set[int]:
        return {sid for sid, st in self._state.items() if st["flagged"]}

    def clear(self, sid: int) -> None:
        """Forget a flag, e.g. after the student passed a liveness check."""
        st = self._state.get(sid)
        if st is not None:
            st["flagged"] = False
            st["still_since"] = None

