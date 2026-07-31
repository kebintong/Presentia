"""Threaded screen capture of a chosen region (the Google Meet window area)."""

from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QThread, Signal


class ScreenCaptureThread(QThread):
    """Grabs a fixed screen region at a low rate and emits BGR frames.

    Face analysis is the bottleneck anyway, so ~1 fps of capture is plenty for
    attendance monitoring and keeps CPU usage negligible.
    """

    frame_ready = Signal(np.ndarray)
    capture_error = Signal(str)

    def __init__(self, region: dict, fps: float = 1.0, parent=None):
        """`region` is an mss-style dict: {left, top, width, height} in px."""
        super().__init__(parent)
        self._region = region
        self._interval = 1.0 / fps
        self._stop = False

    def stop(self) -> None:
        self._stop = True
        self.wait(3000)

    def run(self) -> None:
        import mss

        try:
            with mss.mss() as sct:
                while not self._stop:
                    start = time.monotonic()
                    shot = sct.grab(self._region)
                    frame = np.asarray(shot, dtype=np.uint8)[:, :, :3]  # BGRA -> BGR
                    self.frame_ready.emit(np.ascontiguousarray(frame))
                    remaining = self._interval - (time.monotonic() - start)
                    if remaining > 0:
                        time.sleep(remaining)
        except Exception as exc:  # noqa: BLE001 - e.g. Wayland without X11 access
            self.capture_error.emit(str(exc))
