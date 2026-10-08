"""The numbers behind the attendance rules (plan sections 1 and 3).

All times are in seconds. Defaults are the ones the instructor chose; the
reconnect grace and freeze timings were measured on Microsoft Teams and may get
their own values for Google Meet and Zoom (see `for_platform`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Rules:
    # Arrival, measured from class start (wall-clock time).
    late_after: float = 10 * 60          # first confirmed sighting later than this: Late
    absent_after: float = 20 * 60        # ... later than this (or never): Absent

    # Arrival confirmation: the same student in this many passes within the window.
    confirm_passes: int = 3
    confirm_window: float = 5.0

    # A face missing this long counts as gone (one poor frame never does).
    vanish_grace: float = 5.0

    # Without the student's tile showing the camera-off picture, a missing
    # face only means "face not seen": shown after this long…
    unseen_after: float = 30.0
    # …and only after this long does the camera-off ladder start (from zero).
    unseen_ladder_after: float = 3 * 60
    # On camera (live tile) without a clear look this long: one gentle note.
    unclear_note_after: float = 10 * 60

    # Camera-off ladder: one warning per step; warning 4 is Absent.
    warning_step: float = 60.0
    final_level: int = 4
    # Back on camera for this long ends the episode; shorter returns keep it open.
    reset_after_on: float = 30.0

    # Connection drops.
    reconnect_grace: float = 3 * 60      # disconnected this long: camera-off ladder starts
    freeze_eps: float = 0.15             # a patch change at or below this is "no change"
    freeze_after: float = 8.0            # unchanged this long: video frozen
    freeze_max_gap: float = 3.0          # samples further apart than this break a still run
    recovery_settle: float = 10.0        # after a freeze ends, recognition is not trusted
    suspicious_freeze: float = 3 * 60    # frozen this long without a disconnect: suspicious

    # Observations further apart than this are a monitor gap (nobody is charged).
    max_observation_gap: float = 3.0

    def with_changes(self, **kw) -> "Rules":
        return replace(self, **kw)


# Measured or assumed per meeting app. Teams is from the user's tests
# (freeze, then a disconnect about 90 s later). Meet and Zoom still need the
# same two tests; until then they use the Teams values.
_PLATFORMS: dict[str, dict] = {
    "teams": {},
    "meet": {},
    "zoom": {},
}


def for_platform(name: str | None, **overrides) -> Rules:
    base = Rules().with_changes(**_PLATFORMS.get((name or "").lower(), {}))
    return base.with_changes(**overrides) if overrides else base
