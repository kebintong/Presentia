"""Background threads: AI model preloading and per-frame analysis."""

from __future__ import annotations

import threading
import traceback
from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import QThread, Signal

from app.core.face_engine import FaceEngine


class EngineLoader(QThread):
    """Loads (and on first run downloads) the InsightFace models off the UI thread."""

    ready = Signal()
    error = Signal(str)

    def run(self) -> None:
        try:
            FaceEngine.instance()
            self.ready.emit()
        except Exception as exc:  # noqa: BLE001 - surface any load failure to the UI
            traceback.print_exc()
            self.error.emit(str(exc))


class AnalysisWorker(QThread):
    """Processes the most recent frame through a swappable processor callable.

    Frames arrive faster than analysis runs; only the latest frame is kept, so
    the worker naturally drops frames instead of building a backlog. The
    processor is any callable ``fn(frame_bgr) -> dict | None``; returned dicts
    are emitted through the `result` signal on the GUI thread.
    """

    result = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cond = threading.Condition()
        self._frame: Optional[np.ndarray] = None
        self._processor: Optional[Callable[[np.ndarray], Optional[dict]]] = None
        self._stop = False

    def set_processor(self, fn: Optional[Callable[[np.ndarray], Optional[dict]]]) -> None:
        with self._cond:
            self._processor = fn
            self._frame = None

    def submit(self, frame: np.ndarray) -> None:
        with self._cond:
            self._frame = frame
            self._cond.notify()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()
        self.wait(3000)

    def run(self) -> None:
        while True:
            with self._cond:
                while not self._stop and (self._frame is None or self._processor is None):
                    self._cond.wait()
                if self._stop:
                    return
                frame, fn = self._frame, self._processor
                self._frame = None
            try:
                out = fn(frame)
            except Exception:  # noqa: BLE001 - never let one bad frame kill the loop
                traceback.print_exc()
                continue
            if out is not None:
                self.result.emit(out)
