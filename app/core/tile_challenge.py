"""Random action check on one student's meeting tile.

When the instructor doubts that a tile shows a live person — a photo, a
looping clip, a pre-recorded video — they start this check. Presentia picks a
random sequence (blink, turn to one side then the other, look up, in a random
order); the instructor sends it to the student, and the student does it on
camera. A recording cannot know the order in advance, and a photo cannot do
any of it.

The face is followed in the screen capture at the preview frame rate (not
the slower recognition rate), because a blink lasts only a fraction of a
second. Only the area around the student's recognised face is analysed, so
the cost is one small FaceMesh pass per frame while a check runs.

Identity is part of the result: the tile must still be recognised as that
student near the end of the check, so a different person cannot do the
actions for them.
"""

from __future__ import annotations

import time

import cv2
import numpy as np

from app.core.liveness import FaceMeshTracker, LivenessChecker

STEP_TIME = 15.0      # per action: instruction relayed by the host + network delay
TOTAL_TIME = 90.0     # hard stop for the whole check
IDENTITY_FRESH = 6.0  # the student must have been recognised this recently at the end
SMALL_FACE_PX = 70    # below this face width the tile is too small for blinks
PAD = 0.6             # context around the face box, as a share of its size
MIN_CROP = 256        # FaceMesh input is upscaled to at least this many pixels


class TileChallenge:
    def __init__(self, student_id: int, name: str, tracker: FaceMeshTracker | None = None) -> None:
        self.student_id = student_id
        self.name = name
        self._tracker = tracker or FaceMeshTracker()
        # Non-directional: whether a Meet tile is mirrored is unknown, so the
        # check asks for "either side" and then "the other side".
        self.checker = LivenessChecker(
            self._tracker, directional=False, randomized=True, step_time=STEP_TIME,
        )
        self.started = time.monotonic()
        self._bbox: tuple[int, int, int, int] | None = None
        self._identified_at: float | None = None
        self.result: str | None = None   # "passed" / "failed"
        self.reason = ""

    # ── fed by the recognition pass ────────────────────────────────────

    def note_identified(self, bbox: tuple[int, int, int, int]) -> None:
        """The student was recognised on screen at this box."""
        self._bbox = tuple(int(v) for v in bbox)  # type: ignore[assignment]
        self._identified_at = time.monotonic()

    # ── instructions ───────────────────────────────────────────────────

    def instructions(self) -> list[str]:
        return self.checker.action_prompts()

    def chat_text(self) -> str:
        """Ready to paste into the meeting chat."""
        steps = " ".join(f"({i}) {p}." for i, p in enumerate(self.instructions(), 1))
        return (
            f"Attendance check for {self.name}: please look at your camera, "
            f"then do these in order: {steps}"
        )

    # ── per frame ──────────────────────────────────────────────────────

    def _crop(self, frame: np.ndarray) -> np.ndarray | None:
        if self._bbox is None:
            return None
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = self._bbox
        bw, bh = max(1, x2 - x1), max(1, y2 - y1)
        cx1 = max(0, int(x1 - bw * PAD))
        cy1 = max(0, int(y1 - bh * PAD))
        cx2 = min(w, int(x2 + bw * PAD))
        cy2 = min(h, int(y2 + bh * PAD))
        crop = frame[cy1:cy2, cx1:cx2]
        if crop.size == 0:
            return None
        short = min(crop.shape[:2])
        if short < MIN_CROP:
            k = MIN_CROP / float(short)
            crop = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
        return np.ascontiguousarray(crop)

    def _finish(self, result: str, reason: str) -> None:
        self.result = result
        self.reason = reason

    def process(self, frame: np.ndarray) -> dict:
        """Advance the check with one screen frame; returns state for the UI."""
        now = time.monotonic()
        if self.result is None:
            if now - self.started > TOTAL_TIME:
                self._finish("failed", f"Time is up — {self.name} did not finish the actions.")
            else:
                crop = self._crop(frame)
                if crop is not None:
                    self.checker.process(crop)
                if self.checker.failed:
                    self._finish("failed", self.checker.reason)
                elif self.checker.passed:
                    if self._identified_at is None or now - self._identified_at > IDENTITY_FRESH:
                        self._finish(
                            "failed",
                            f"The actions were done, but the face was not recognised as "
                            f"{self.name} at the end.",
                        )
                    else:
                        self._finish("passed", f"{self.name} passed the liveness check.")
        return self.state()

    def state(self) -> dict:
        st = self.checker._state(self._bbox is not None)  # noqa: SLF001 — same package
        small = self._bbox is not None and (self._bbox[2] - self._bbox[0]) < SMALL_FACE_PX
        return {
            "student_id": self.student_id,
            "prompt": self.reason if self.result else (
                st["prompt"] if self._bbox is not None
                else f"Looking for {self.name} on screen…"
            ),
            "step": st["step"],
            "steps": st["steps"],
            "face_found": self._bbox is not None,
            "small_face": small,
            "seconds_left": max(0, int(TOTAL_TIME - (time.monotonic() - self.started))),
            "result": self.result,
        }

    def close(self) -> None:
        try:
            self._tracker.close()
        except Exception:  # noqa: BLE001
            pass
