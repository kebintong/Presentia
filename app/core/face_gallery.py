"""Learning how students look in meetings.

A student's registration photo comes from their phone or webcam, in their
own lighting. In a meeting their face is small, compressed and lit by the
screen, so it matches their photo less well — and a half-visible face even
less. When a student is recognised clearly and unambiguously during a
meeting (see tile_tracker.LEARN_SCORE), that meeting picture is kept as an
extra picture of them, up to MAX_PER_STUDENT, so later looks match better.

Only clear matches are kept (never weak or "kept" identities), at most one
per student per minute, and only when it adds something new: a picture
almost the same as one already kept is skipped. When a student has the
maximum, the most redundant one is replaced.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

MAX_PER_STUDENT = 5
EVERY = 60.0          # s between pictures kept for one student
SAME = 0.85           # this similar to a picture already kept: nothing new


class FaceGallery:
    def __init__(
        self,
        known: list[tuple[int, np.ndarray]],
        lock: threading.Lock,
        load: Callable[[int], list[tuple[int, np.ndarray]]],
        add: Callable[[int, np.ndarray], int],
        replace: Callable[[int, np.ndarray], None],
        max_per_student: int = MAX_PER_STUDENT,
        every: float = EVERY,
    ) -> None:
        """`known` is the list recognition matches against (updated in
        place, under `lock`); load/add/replace keep the pictures on disk."""
        self._known = known
        self._lock = lock
        self._load, self._add, self._replace = load, add, replace
        self._max = max_per_student
        self._every = every
        self._faces: dict[int, list[tuple[int, np.ndarray]]] = {}
        self._last: dict[int, float] = {}

    def offer(self, sid: int, emb: np.ndarray, now: float | None = None) -> bool:
        """A clear meeting picture of `sid`. True if it was kept."""
        now = time.monotonic() if now is None else now
        if now - self._last.get(sid, -1e9) < self._every:
            return False
        self._last[sid] = now
        emb = np.asarray(emb, dtype=np.float32)
        with self._lock:
            theirs = [e for s, e in self._known if s == sid]
        if theirs and max(float(e @ emb) for e in theirs) >= SAME:
            return False
        faces = self._faces.get(sid)
        if faces is None:
            faces = self._faces[sid] = list(self._load(sid))
        if len(faces) < self._max:
            fid = self._add(sid, emb)
            faces.append((fid, emb))
            with self._lock:
                self._known.append((sid, emb))
            return True
        # Full: replace the picture most like the others (the least new one).
        def crowding(i: int) -> float:
            return max(float(faces[i][1] @ f) for j, (_, f) in enumerate(faces) if j != i)
        i = max(range(len(faces)), key=crowding)
        fid, old = faces[i]
        self._replace(fid, emb)
        faces[i] = (fid, emb)
        with self._lock:
            for k, (s, e) in enumerate(self._known):
                if s == sid and e is old:
                    self._known[k] = (sid, emb)
                    break
            else:
                self._known.append((sid, emb))
        return True
