"""Guided face enrollment: samples are only captured when the person performs
the requested pose, so registration cannot complete passively.

Sequence: look straight -> turn left -> turn right -> straight -> blink.
The head turns double as varied-angle samples, which improves the stored
embedding. `directional=False` (screen/Meet-tile capture, not mirrored) asks
for "either side" then "the other side" instead of LEFT/RIGHT.
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np

from app.core.liveness import (
    _LEFT_EYE, _RIGHT_EYE, EAR_CLOSED, EAR_OPEN, _ear, _yaw_ratio,
)

# softer than the liveness thresholds: slight turns give useful sample
# variety without degrading embedding quality
REG_YAW_LO = 0.40
REG_YAW_HI = 0.60
HOLD_FRAMES = 2


class GuidedEnrollment:
    """Feed frames via :meth:`process`; collects one embedding per stage."""

    def __init__(
        self,
        tracker,
        embed_fn: Callable[[np.ndarray], Optional[np.ndarray]],
        directional: bool = True,
    ) -> None:
        self._tracker = tracker
        self._embed = embed_fn
        if directional:
            self._stages = [
                ("center", "Look straight at the camera"),
                ("turn_lo", "Turn your head slightly LEFT and hold"),
                ("turn_hi", "Turn your head slightly RIGHT and hold"),
                ("center", "Look straight at the camera again"),
                ("blink", "Blink once"),
            ]
        else:
            self._stages = [
                ("center", "Ask the student to look straight at their camera"),
                ("turn_any", "Ask them to turn their head slightly to either side and hold"),
                ("turn_opposite", "Now the other side, and hold"),
                ("center", "Look straight at the camera again"),
                ("blink", "Ask them to blink once"),
            ]
        self.samples: list[np.ndarray] = []
        self._stage_idx = 0
        self._hold = 0
        self._hold_side: str | None = None
        self._first_side: str | None = None
        self._eye_closed = False

    # ------------------------------------------------------------------

    @property
    def done(self) -> bool:
        return self._stage_idx >= len(self._stages)

    @property
    def total(self) -> int:
        return len(self._stages)

    def prompt(self) -> str:
        if self.done:
            return "All samples captured."
        return self._stages[self._stage_idx][1]

    def process(self, frame: np.ndarray) -> dict:
        """Advance with one frame. Returns state for the UI."""
        if self.done:
            return self._state(True, captured=False)
        landmarks = self._tracker.landmarks(frame)
        if landmarks is None:
            return self._state(False, captured=False)

        pose = self._stages[self._stage_idx][0]
        yaw = _yaw_ratio(landmarks)
        satisfied, side = False, None

        if pose == "center":
            satisfied = REG_YAW_LO < yaw < REG_YAW_HI
        elif pose == "turn_lo":
            satisfied = yaw <= REG_YAW_LO
        elif pose == "turn_hi":
            satisfied = yaw >= REG_YAW_HI
        elif pose == "turn_any":
            side = "lo" if yaw <= REG_YAW_LO else ("hi" if yaw >= REG_YAW_HI else None)
            satisfied = side is not None
        elif pose == "turn_opposite":
            wanted = "hi" if self._first_side == "lo" else "lo"
            side = "lo" if yaw <= REG_YAW_LO else ("hi" if yaw >= REG_YAW_HI else None)
            satisfied = side == wanted
        elif pose == "blink":
            ear = min(_ear(landmarks, _LEFT_EYE), _ear(landmarks, _RIGHT_EYE))
            if not self._eye_closed and ear < EAR_CLOSED:
                self._eye_closed = True
            elif self._eye_closed and ear > EAR_OPEN:
                self._eye_closed = False
                satisfied = True  # blink completed on this very frame

        if pose == "blink":
            held = satisfied  # a completed blink is captured immediately
        else:
            if satisfied and (side is None or side == self._hold_side or self._hold == 0):
                self._hold += 1
                self._hold_side = side
            else:
                self._hold = 1 if satisfied else 0
                self._hold_side = side if satisfied else None
            held = self._hold >= HOLD_FRAMES

        if not held:
            return self._state(True, captured=False)

        emb = self._embed(frame)
        if emb is None:
            return self._state(True, captured=False, note="Face not clear enough")
        self.samples.append(emb)
        if pose == "turn_any":
            self._first_side = self._hold_side
        self._stage_idx += 1
        self._hold = 0
        self._hold_side = None
        return self._state(True, captured=True)

    def _state(self, face_found: bool, captured: bool, note: str | None = None) -> dict:
        return {
            "kind": "guided",
            "face_found": face_found,
            "captured": captured,
            "done": self.done,
            "count": len(self.samples),
            "total": self.total,
            "prompt": note or (
                self.prompt() if face_found
                else "No face detected — position the face inside the frame"
            ),
        }
