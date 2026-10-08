"""Frames from a browser tab, shared the way OBS or Meet's "Present a tab" does.

Browsers stop drawing a window that is covered or minimised, so capturing
the meeting window from outside stops as soon as the instructor switches to
another app. A tab that is being *shared* keeps being drawn, though —
covered, minimised, or not the active tab. So Presentia serves a small page
(/share) that the instructor opens in the same browser as the meeting; it
asks the browser to share a tab (the browser's own picker lists tabs by
title) and sends a few pictures a second here over /ws/tabfeed.

Only one tab is shared at a time; a new connection replaces the old one.
The page needs the one-time token Presentia put in its link, and only the
page Presentia itself serves may connect (the browser's Origin header).
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time

import cv2
import numpy as np

ORIGINS = ("http://127.0.0.1:7788", "http://localhost:7788")

# The browser sends a new picture only when the tab changes. A meeting page
# always changes (video, speaking rings, the clock), so no picture for this
# long means the browser stopped sending it: monitoring pauses rather than
# reading an old picture as "everyone is still here".
STALE_AFTER = 15.0


class TabFeed:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str | None = None
        self._client = 0           # id of the connected share page (0: none)
        self._next = 0
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._at = 0.0
        self._label = ""
        self._surface = ""
        self._size = (0, 0)
        self._had_frames = False
        self._ended = False
        self._stop_requests: set[int] = set()

    # ── the link ────────────────────────────────────────────────────────────

    def new_token(self) -> str:
        """A fresh link token. A page already sharing keeps going."""
        with self._lock:
            self._token = secrets.token_urlsafe(18)
            return self._token

    def check(self, token: str | None, origin: str | None) -> bool:
        with self._lock:
            ok_token = bool(token and self._token and hmac.compare_digest(token, self._token))
        return ok_token and (origin or "") in ORIGINS

    # ── the share page's connection ─────────────────────────────────────────

    def connect(self) -> int:
        with self._lock:
            self._next += 1
            if self._client:
                self._stop_requests.add(self._client)   # replaced by this one
            self._client = self._next
            self._ended = False
            return self._client

    def disconnect(self, cid: int) -> None:
        with self._lock:
            if self._client == cid:
                self._client = 0
            self._stop_requests.discard(cid)

    def hello(self, cid: int, label: str, surface: str) -> None:
        with self._lock:
            if cid == self._client:
                # Chromium names a shared tab's track "web-contents-media-stream://…",
                # not after the tab: nothing worth showing.
                label = "" if (label or "").startswith("web-contents-media-stream") else label
                self._label = (label or "")[:200]
                self._surface = (surface or "")[:20]
                self._ended = False

    def ended(self, cid: int) -> None:
        """The instructor pressed the browser's "Stop sharing"."""
        with self._lock:
            if cid == self._client:
                self._ended = True

    def push(self, cid: int, jpeg: bytes) -> bool:
        """One picture from the page (JPEG). False if it is not the current page."""
        if cid != self._client or not jpeg:
            return False
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return True
        with self._lock:
            if cid != self._client:
                return False
            self._frame = frame
            self._seq += 1
            self._at = time.monotonic()
            self._size = (frame.shape[1], frame.shape[0])
            self._had_frames = True
            self._ended = False
        return True

    def should_stop(self, cid: int) -> bool:
        """The page `cid` was replaced or asked to stop sharing."""
        with self._lock:
            return cid in self._stop_requests

    def stop(self) -> None:
        """Ask the sharing page to stop, and forget its picture."""
        with self._lock:
            if self._client:
                self._stop_requests.add(self._client)
            self._frame = None
            self._had_frames = False
            self._ended = True

    # ── for monitoring ──────────────────────────────────────────────────────

    def latest(self) -> tuple[str, np.ndarray | None, int]:
        """(state, frame, seq). States: ok (a picture; the browser only sends
        new ones when the tab changes, so a recent one is still current while
        the page is connected), waiting (no tab chosen yet), stale (no new
        picture for STALE_AFTER s), stopped (sharing ended or the page was
        closed)."""
        with self._lock:
            live = self._client != 0 and not self._ended
            if live and self._frame is not None:
                if time.monotonic() - self._at > STALE_AFTER:
                    return "stale", None, self._seq
                return "ok", self._frame, self._seq
            if not self._had_frames and not self._ended:
                return "waiting", None, self._seq
            return "stopped", None, self._seq

    def status(self) -> dict:
        with self._lock:
            live = self._client != 0 and not self._ended
            return {
                "connected": self._client != 0,
                "sharing": live and self._frame is not None,
                "ended": self._ended,
                "label": self._label,
                "surface": self._surface,
                "width": self._size[0],
                "height": self._size[1],
                "age": round(time.monotonic() - self._at, 1) if self._at else None,
            }


FEED = TabFeed()
