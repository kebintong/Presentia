"""Threaded webcam capture built on OpenCV, with a raw V4L2 fallback for
virtual cameras (Iriun, OBS) that OpenCV cannot open while they are in use.

Note: Previously used PySide6.QtCore.QThread / Signal. Replaced with a
pure-Python threading.Thread + callback design so the sidecar can import
this module in a frozen exe that does not include PySide6.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable, Optional

import cv2
import numpy as np

MAX_FRAME_WIDTH = 1280  # phone cameras stream 1080p+; downscale for analysis


class CameraThread:
    """Continuously reads frames from a webcam and calls back with them.

    Callbacks (all optional, called from the camera thread):
      on_frame(frame: np.ndarray)   – BGR frame, mirrored like a selfie view
      on_error(message: str)        – device lost or failed to open
      on_recovered()               – device recovered after a prior error

    If the device fails mid-run (unplugged / disabled), `on_error` is called
    and the thread keeps retrying to reopen so callers can detect recovery.
    """

    def __init__(
        self,
        index: int = 0,
        fps: int = 20,
        on_frame: Optional[Callable[[np.ndarray], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_recovered: Optional[Callable[[], None]] = None,
    ) -> None:
        self._index = index
        self._interval = 1.0 / fps
        self._stop = False
        self._on_frame = on_frame
        self._on_error = on_error
        self._on_recovered = on_recovered
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._thread.join(timeout=3.0)

    def _run(self) -> None:
        cap = self._open()
        failed = cap is None
        if failed and self._on_error:
            self._on_error("Could not open the camera.")

        while not self._stop:
            if cap is None or not cap.isOpened():
                # keep trying to recover
                time.sleep(0.5)
                cap = self._open()
                if cap is not None:
                    if self._on_recovered:
                        self._on_recovered()
                    failed = False
                continue

            ok, frame = cap.read()
            if not ok or frame is None:
                if not failed:
                    failed = True
                    if self._on_error:
                        self._on_error("Camera stopped delivering frames.")
                cap.release()
                cap = None
                continue

            if frame.shape[1] > MAX_FRAME_WIDTH:
                scale = MAX_FRAME_WIDTH / frame.shape[1]
                frame = cv2.resize(
                    frame, (MAX_FRAME_WIDTH, int(frame.shape[0] * scale))
                )
            # mirror so the preview behaves like a selfie view; analysis uses
            # the same orientation so left/right prompts match the user
            frame = cv2.flip(frame, 1)
            if self._on_frame:
                self._on_frame(frame)
            time.sleep(self._interval)

        if cap is not None:
            cap.release()

    def _open(self):
        cap = cv2.VideoCapture(self._index)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            return cap
        cap.release()

        # OpenCV refuses v4l2loopback devices (Iriun/OBS virtual cameras)
        # whose format is locked by an active stream; read them raw instead.
        if sys.platform.startswith("linux"):
            from app.core.v4l2_reader import RawV4L2Capture

            raw = RawV4L2Capture(f"/dev/video{self._index}")
            if raw.isOpened():
                return raw
            raw.release()
        return None


def frame_brightness(frame: np.ndarray) -> float:
    """Mean grayscale brightness (0-255); near-zero means covered/blacked out."""
    return float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean())
