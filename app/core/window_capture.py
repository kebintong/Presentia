"""Capturing one window while it is covered (Windows).

Windows Graphics Capture (WGC) is the system API that OBS, Discord and the
Windows Snipping Tool use. It receives the window's own picture from the
desktop compositor, so the window keeps being captured while other windows
cover it — unlike a screen grab, which sees whatever is on top, and unlike
PrintWindow, which many apps (browsers, video players) answer with a black
image.

Two limits remain, and the follower in app.sidecar handles them honestly:

* A minimised window has no picture at all. ``restore_behind`` puts it back
  without bringing it to the front.
* Chromium browsers (Chrome, Edge, Brave, Opera, Vivaldi) stop drawing a
  window that is *completely* covered, to save power. WGC then keeps
  delivering the last picture. ``visible_fraction`` tells "covered, so the
  picture is old" apart from "on screen, but nothing moved".

The geometry helpers are plain Python so they can be tested anywhere.
"""

from __future__ import annotations

import sys
import threading
import time

Rect = tuple[int, int, int, int]  # left, top, right, bottom


# ── Geometry (pure) ───────────────────────────────────────────────────────────

MAX_PIECES = 4000  # a pathological desktop: stop splitting and call it covered


def rect_area(r: Rect) -> int:
    return max(0, r[2] - r[0]) * max(0, r[3] - r[1])


def intersect(a: Rect, b: Rect) -> Rect | None:
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return r if r[0] < r[2] and r[1] < r[3] else None


def subtract(pieces: list[Rect], cut: Rect) -> list[Rect]:
    """The parts of ``pieces`` (non-overlapping rectangles) outside ``cut``."""
    out: list[Rect] = []
    for p in pieces:
        hit = intersect(p, cut)
        if hit is None:
            out.append(p)
            continue
        l, t, r, b = p
        hl, ht, hr, hb = hit
        if t < ht:
            out.append((l, t, r, ht))      # above the cut
        if hb < b:
            out.append((l, hb, r, b))      # below
        if l < hl:
            out.append((l, ht, hl, hb))    # left, between
        if hr < r:
            out.append((hr, ht, r, hb))    # right, between
    return out


def visible_fraction_of(target: Rect, monitors: list[Rect], above: list[Rect]) -> float:
    """Share of ``target`` that is on a monitor and not under any rectangle
    in ``above`` (the opaque windows in front of it). 0.0 = fully hidden."""
    total = rect_area(target)
    if total <= 0:
        return 0.0
    pieces = [r for m in monitors if (r := intersect(target, m)) is not None]
    for cut in above:
        if not pieces:
            return 0.0
        pieces = subtract(pieces, cut)
        if len(pieces) > MAX_PIECES:
            return 0.0
    return min(1.0, sum(rect_area(p) for p in pieces) / total)


# ── Win32 helpers ─────────────────────────────────────────────────────────────

GW_HWNDPREV = 3
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x2
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
SW_SHOWNOACTIVATE = 4
HWND_BOTTOM = 1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010


class _Win32:
    """The handful of user32/dwmapi calls used here, with proper types."""

    _inst: "_Win32 | None" = None

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as wt

        self.ct, self.wt = ctypes, wt
        u = ctypes.windll.user32
        self.u = u
        u.GetWindow.argtypes = [wt.HWND, wt.UINT]
        u.GetWindow.restype = wt.HWND
        u.IsWindowVisible.argtypes = [wt.HWND]
        u.IsIconic.argtypes = [wt.HWND]
        u.IsWindow.argtypes = [wt.HWND]
        u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        u.GetLayeredWindowAttributes.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD),
                                                 ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(wt.DWORD)]
        u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.UINT]
        try:
            self.dwm = ctypes.windll.dwmapi.DwmGetWindowAttribute
            self.dwm.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
        except Exception:  # noqa: BLE001
            self.dwm = None

    @classmethod
    def get(cls) -> "_Win32":
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def frame_rect(self, hwnd) -> Rect | None:
        """The visible frame (DWM bounds, without the invisible resize border)."""
        r = self.wt.RECT()
        if self.dwm is not None and self.dwm(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                             self.ct.byref(r), self.ct.sizeof(r)) == 0:
            return (r.left, r.top, r.right, r.bottom)
        if self.u.GetWindowRect(hwnd, self.ct.byref(r)):
            return (r.left, r.top, r.right, r.bottom)
        return None

    def cloaked(self, hwnd) -> bool:
        if self.dwm is None:
            return False
        v = self.wt.DWORD(0)
        return self.dwm(hwnd, DWMWA_CLOAKED, self.ct.byref(v), self.ct.sizeof(v)) == 0 and v.value != 0

    def see_through(self, hwnd) -> bool:
        """Windows that do not hide what is behind them (overlays, shadows,
        Presentia's own floating bubble). Mirrors Chromium's own check."""
        ex = self.u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if ex & WS_EX_TRANSPARENT:
            return True
        if ex & WS_EX_LAYERED:
            key, alpha, flags = self.wt.DWORD(), self.ct.c_ubyte(), self.wt.DWORD()
            if not self.u.GetLayeredWindowAttributes(hwnd, self.ct.byref(key), self.ct.byref(alpha),
                                                     self.ct.byref(flags)):
                return True  # per-pixel alpha (UpdateLayeredWindow): treat as see-through
            if not (flags.value & LWA_ALPHA) or alpha.value < 255:
                return True
        return False

    def windows_above(self, hwnd, limit: int = 600) -> list[Rect]:
        """Rectangles of the opaque, showing windows in front of ``hwnd``."""
        out: list[Rect] = []
        h = self.u.GetWindow(hwnd, GW_HWNDPREV)
        while h and limit > 0:
            limit -= 1
            if (self.u.IsWindowVisible(h) and not self.u.IsIconic(h)
                    and not self.cloaked(h) and not self.see_through(h)):
                r = self.frame_rect(h)
                if r is not None and rect_area(r) > 0:
                    out.append(r)
            h = self.u.GetWindow(h, GW_HWNDPREV)
        return out


def visible_fraction(hwnd: int, monitors: list[Rect]) -> float:
    """How much of the window is actually showing on screen (0.0–1.0).
    1.0 when it cannot be worked out, so callers never pause by mistake."""
    if sys.platform != "win32":
        return 1.0
    try:
        w = _Win32.get()
        h = w.wt.HWND(hwnd)
        target = w.frame_rect(h)
        if target is None or w.cloaked(h):  # cloaked: e.g. on another virtual desktop
            return 0.0
        return visible_fraction_of(target, monitors, w.windows_above(h))
    except Exception:  # noqa: BLE001
        return 1.0


def restore_behind(hwnd: int) -> bool:
    """Un-minimise a window without bringing it to the front or taking the
    keyboard focus: it goes back behind the other windows."""
    if sys.platform != "win32":
        return False
    try:
        w = _Win32.get()
        h = w.wt.HWND(hwnd)
        if not w.u.IsWindow(h):
            return False
        w.u.ShowWindow(h, SW_SHOWNOACTIVATE)
        w.u.SetWindowPos(h, w.wt.HWND(HWND_BOTTOM), 0, 0, 0, 0,
                         SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        return not w.u.IsIconic(h)
    except Exception:  # noqa: BLE001
        return False


# ── Windows Graphics Capture ──────────────────────────────────────────────────

def wgc_library() -> str:
    """For Settings → Diagnostics: the capture library and its version."""
    if sys.platform != "win32":
        return "not used on this system"
    try:
        from importlib.metadata import version

        import windows_capture  # noqa: F401

        return f"windows-capture {version('windows-capture')}"
    except Exception:  # noqa: BLE001
        return "missing"


class WgcCapture:
    """Keeps the newest picture of one window, delivered by Windows Graphics
    Capture on its own thread. ``latest()`` never blocks.

    WGC sends a frame only when the window's picture changes, so a quiet
    window simply keeps its last frame. ``seq`` goes up with each new one.
    """

    START_TIMEOUT = 2.5  # s to wait for the first frame before giving up

    def __init__(self, hwnd: int, max_fps: float = 30.0) -> None:
        self.hwnd = int(hwnd)
        self._gap = 1.0 / max(1.0, max_fps)
        self._lock = threading.Lock()
        self._frame = None
        self._seq = 0
        self._at = 0.0           # monotonic time of the newest frame
        self._copied_at = 0.0
        self._closed = False     # the window went away / capture ended
        self._stopping = False   # close() asked the capture thread to end
        self._control = None
        self.error = ""
        self.border = True       # Windows 10 always draws a yellow capture border

    # The callbacks run on the capture thread.
    def _on_frame(self, frame, control) -> None:
        if self._stopping:
            # End the capture from its own thread: stopping it from another
            # thread while a frame is being handed over can abort the whole
            # engine process (a crash in the capture library), which is how
            # monitoring died when the instructor changed pages.
            control.stop()
            return
        now = time.monotonic()
        # Meetings can repaint 60 times a second; copying every one wastes CPU
        # the face engine needs. Keep up to max_fps.
        if now - self._copied_at < self._gap:
            return
        buf = frame.frame_buffer
        if buf is None or buf.ndim != 3 or buf.shape[0] < 2 or buf.shape[1] < 2:
            return
        bgr = buf[:, :, :3].copy()  # the buffer is only valid inside this call
        self._copied_at = now
        with self._lock:
            self._frame = bgr
            self._seq += 1
            self._at = now

    def _on_closed(self) -> None:
        self._closed = True

    def start(self) -> bool:
        """Start capturing. False (with ``error`` set) when WGC is not
        available — old Windows, the library missing, or the window refused."""
        if sys.platform != "win32":
            self.error = "Windows Graphics Capture only exists on Windows"
            return False
        try:
            from windows_capture import WindowsCapture
        except Exception as exc:  # noqa: BLE001
            self.error = f"windows-capture not available: {exc}"
            return False

        # Hiding the yellow border and the cursor needs Windows 11 (or a
        # recent Windows 10 build); older systems refuse those settings, so
        # fall back to the defaults instead of failing.
        attempts = (
            {"cursor_capture": False, "draw_border": False},
            {"cursor_capture": False, "draw_border": None},
            {"cursor_capture": None, "draw_border": None},
        )
        for opts in attempts:
            self._closed = False
            try:
                cap = WindowsCapture(window_hwnd=self.hwnd, **opts)
                cap.frame_handler = self._on_frame
                cap.closed_handler = self._on_closed
                control = cap.start_free_threaded()
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc) or exc.__class__.__name__
                continue
            deadline = time.monotonic() + self.START_TIMEOUT
            while time.monotonic() < deadline:
                if self._seq > 0:
                    self._control = control
                    self.border = opts["draw_border"] is not False
                    self.error = ""
                    return True
                if control.is_finished():
                    break
                time.sleep(0.03)
            # No frame: find out why, then try the next settings.
            try:
                if control.is_finished():
                    control.wait()  # raises the capture thread's error, if any
                    self.error = "the capture ended at once"
                else:
                    # Running but silent: a minimised window delivers nothing
                    # until it is restored, which is still a working capture.
                    self._control = control
                    self.border = opts["draw_border"] is not False
                    self.error = ""
                    return True
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc) or exc.__class__.__name__
        return False

    def latest(self):
        """(BGR frame, seq, seconds since it arrived) or None before the first."""
        with self._lock:
            if self._frame is None:
                return None
            return self._frame, self._seq, time.monotonic() - self._at

    @property
    def running(self) -> bool:
        if self._control is None or self._closed:
            return False
        try:
            return not self._control.is_finished()
        except Exception:  # noqa: BLE001
            return False

    STOP_WAIT = 1.0  # s for the capture thread to end itself

    def close(self) -> None:
        control, self._control = self._control, None
        self._stopping = True
        if control is not None:
            # Usually the next frame ends it (see _on_frame). A window whose
            # picture does not change sends no frames, so after a moment it
            # is stopped from here instead.
            deadline = time.monotonic() + self.STOP_WAIT
            try:
                while time.monotonic() < deadline and not control.is_finished():
                    time.sleep(0.02)
                if not control.is_finished():
                    control.stop()
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._frame = None
