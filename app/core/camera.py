"""Threaded webcam capture built on OpenCV, with a raw V4L2 fallback for
virtual cameras (Iriun, OBS) that OpenCV cannot open while they are in use."""

from __future__ import annotations

import sys
import time

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal

MAX_FRAME_WIDTH = 1280  # phone cameras stream 1080p+; downscale for analysis


class CameraThread(QThread):
    """Continuously reads frames from a webcam and emits them (BGR, mirrored).

    If the device fails mid-run (unplugged / disabled), `camera_error` is emitted
    and the thread keeps retrying to reopen so monitoring can detect recovery.
    """

    frame_ready = Signal(np.ndarray)
    camera_error = Signal(str)
    camera_recovered = Signal()

    def __init__(self, index: int = 0, fps: int = 20, parent=None):
        super().__init__(parent)
        self._index = index
        self._interval = 1.0 / fps
        self._stop = False

    def stop(self) -> None:
        self._stop = True
        self.wait(3000)

    def run(self) -> None:
        cap = self._open()
        failed = cap is None
        if failed:
            self.camera_error.emit("Could not open the camera.")

        while not self._stop:
            if cap is None or not cap.isOpened():
                # keep trying to recover
                time.sleep(0.5)
                cap = self._open()
                if cap is not None:
                    self.camera_recovered.emit()
                    failed = False
                continue

            ok, frame = cap.read()
            if not ok or frame is None:
                if not failed:
                    failed = True
                    self.camera_error.emit("Camera stopped delivering frames.")
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
            self.frame_ready.emit(cv2.flip(frame, 1))
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
