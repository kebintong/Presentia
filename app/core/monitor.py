"""Presence state machine for continuous monitoring during a session."""

from __future__ import annotations

import time
from typing import Callable

# States
PRESENT = "present"
OUT_OF_FRAME = "out_of_frame"
CAMERA_OFF = "camera_off"

# Frames darker than this mean the lens is covered or the feed is blanked.
DARK_FRAME_THRESHOLD = 12.0


class PresenceMonitor:
    """Tracks whether the verified student stays visible on camera.

    Fed with per-frame analysis results via :meth:`update` (and
    :meth:`camera_failed` when capture breaks). Debounces transitions so a
    student briefly leaning away doesn't spam alerts; `on_event(event_type,
    message)` fires only on state changes and identity mismatches.
    """

    def __init__(
        self,
        on_event: Callable[[str, str], None],
        out_of_frame_after: float = 5.0,
        camera_off_after: float = 3.0,
    ) -> None:
        self._on_event = on_event
        self._out_after = out_of_frame_after
        self._cam_after = camera_off_after
        self.state = PRESENT
        self._last_face_ts = time.monotonic()
        self._dark_since: float | None = None
        self._cam_fail_since: float | None = None

    # ------------------------------------------------------------------

    def update(self, face_found: bool, brightness: float) -> None:
        now = time.monotonic()
        self._cam_fail_since = None  # frames are flowing

        if brightness < DARK_FRAME_THRESHOLD:
            if self._dark_since is None:
                self._dark_since = now
            if now - self._dark_since >= self._cam_after:
                self._transition(CAMERA_OFF, "Camera appears to be off or covered.")
            return
        self._dark_since = None

        if face_found:
            self._last_face_ts = now
            self._transition(PRESENT, "Student is back in frame.")
        elif now - self._last_face_ts >= self._out_after:
            self._transition(OUT_OF_FRAME, "Student is out of the camera frame.")

    def camera_failed(self) -> None:
        # emitted once per failure by the capture thread, no debounce needed
        self._cam_fail_since = time.monotonic()
        self._transition(CAMERA_OFF, "Camera was turned off or disconnected.")

    def identity_mismatch(self, score: float) -> None:
        self._on_event(
            "identity_mismatch",
            f"Face on camera does not match the verified student (score {score:.2f}).",
        )

    # ------------------------------------------------------------------

    def _transition(self, new_state: str, message: str) -> None:
        if new_state == self.state:
            return
        prev, self.state = self.state, new_state
        if new_state == PRESENT:
            # only announce recovery, don't treat it as an alert
            self._on_event("back_in_frame", message)
        else:
            self._on_event(new_state, message)
        _ = prev
