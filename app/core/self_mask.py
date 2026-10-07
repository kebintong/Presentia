"""Keep Presentia's own windows out of what the monitor analyses.

Presentia's windows (the main window, the floating bubble, Live View, the
pop-out) are visible to screen recorders and screenshots, so a screen-area
capture would film them too when they sit over the watched area: Live View
would show every face a second time, and the bubble would be read as part
of the meeting. Before a captured screen area is analysed, the parts covered
by a visible Presentia window are painted over.

Only screen-area grabs need this. A window picked with "Select Window" is
captured on its own and never contains anything drawn on top of it.

Windows only; elsewhere `apply` returns the frame unchanged.
"""

from __future__ import annotations

import os
import sys
import threading
import time

import numpy as np

FILL = (24, 24, 24)  # BGR; dark grey, no faces to find here
REFRESH = 0.25       # seconds between window lists


class SelfMask:
    def __init__(self, pids: set[int] | None = None) -> None:
        self._ok = sys.platform == "win32"
        self._pids = pids if pids is not None else _presentia_pids()
        self._rects: list[tuple[int, int, int, int]] = []
        self._at = 0.0
        self._lock = threading.Lock()
        if self._ok:
            try:
                import ctypes
                from ctypes import wintypes

                self._ct = ctypes
                self._wt = wintypes
                self._u32 = ctypes.windll.user32
                self._dwm = ctypes.windll.dwmapi.DwmGetWindowAttribute
            except Exception:
                self._ok = False

    def rects(self) -> list[tuple[int, int, int, int]]:
        """Screen rectangles (left, top, right, bottom) of visible Presentia windows."""
        if not self._ok or not self._pids:
            return []
        now = time.monotonic()
        with self._lock:
            if now - self._at >= REFRESH:
                try:
                    self._rects = self._scan()
                except Exception:
                    self._rects = []
                self._at = now
            return list(self._rects)

    def _scan(self) -> list[tuple[int, int, int, int]]:
        ct, wt, u32 = self._ct, self._wt, self._u32
        found: list[tuple[int, int, int, int]] = []
        pid = wt.DWORD()

        def visit(hwnd, _lp):
            if not u32.IsWindowVisible(hwnd) or u32.IsIconic(hwnd):
                return True
            u32.GetWindowThreadProcessId(hwnd, ct.byref(pid))
            if pid.value not in self._pids:
                return True
            cloaked = wt.DWORD(0)
            if self._dwm(hwnd, 14, ct.byref(cloaked), ct.sizeof(cloaked)) == 0 and cloaked.value:
                return True  # DWMWA_CLOAKED: on another virtual desktop, etc.
            r = wt.RECT()
            # DWMWA_EXTENDED_FRAME_BOUNDS: the visible frame, without the
            # invisible resize border GetWindowRect includes.
            if self._dwm(hwnd, 9, ct.byref(r), ct.sizeof(r)) != 0:
                if not u32.GetWindowRect(hwnd, ct.byref(r)):
                    return True
            if r.right - r.left > 1 and r.bottom - r.top > 1:
                found.append((r.left, r.top, r.right, r.bottom))
            return True

        proc = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(visit)
        u32.EnumWindows(proc, 0)
        return found

    def apply(self, frame: np.ndarray, left: int, top: int) -> np.ndarray:
        """Paint over Presentia windows inside `frame`, a grab whose top-left
        corner is at screen point (left, top). Edits and returns `frame`."""
        return paint(frame, left, top, self.rects())


def paint(frame: np.ndarray, left: int, top: int,
          rects: list[tuple[int, int, int, int]]) -> np.ndarray:
    h, w = frame.shape[:2]
    for l, t, r, b in rects:
        x1, y1 = max(0, l - left), max(0, t - top)
        x2, y2 = min(w, r - left), min(h, b - top)
        if x2 > x1 and y2 > y1:
            frame[y1:y2, x1:x2] = FILL
    return frame


def _presentia_pids() -> set[int]:
    """The desktop app's process (it owns every Presentia window) and ours."""
    pids = {os.getpid()}
    raw = os.environ.get("PRESENTIA_PARENT_PID", "")
    if raw.isdigit() and int(raw) > 0:
        pids.add(int(raw))
    return pids
