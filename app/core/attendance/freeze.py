"""Frozen-video detection for one student's face patch.

Measured on Teams (plan section 2): a live face, even a still one, changes by
more than 1 at least once a second (blinks, breathing, encoder noise), while a
frozen tile changes by exactly 0. Teams only sends ~9-10 new pictures a second,
so most captured frames repeat the last one; the caller passes the *largest*
change since the previous pass, and a run of unchanged samples only counts as
frozen once it has lasted `freeze_after` seconds with no hole longer than
`max_gap` in it.
"""

from __future__ import annotations


class FreezeDetector:
    def __init__(self, eps: float, freeze_after: float, max_gap: float) -> None:
        self.eps = eps
        self.freeze_after = freeze_after
        self.max_gap = max_gap
        self.still_since: float | None = None
        self._last_t: float | None = None

    def reset(self) -> None:
        self.still_since = None
        self._last_t = None

    def update(self, t: float, motion: float | None) -> bool:
        """Feed one sample; True while the patch counts as frozen."""
        if motion is None:
            return self.frozen(t)
        if self._last_t is not None and t - self._last_t > self.max_gap:
            self.still_since = None  # a hole in the samples: start over
        self._last_t = t
        if motion > self.eps:
            self.still_since = None
            return False
        if self.still_since is None:
            self.still_since = t
        return self.frozen(t)

    def frozen(self, t: float) -> bool:
        return self.still_since is not None and t - self.still_since >= self.freeze_after

    @staticmethod
    def moving(motion: float | None, eps: float) -> bool:
        return motion is not None and motion > eps
