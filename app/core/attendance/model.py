"""What goes into the attendance core and what comes out of it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Capture states (from the sidecar's capture code). Anything but "ok" means the
# monitor cannot see the meeting: every student's clock stops.
CAPTURE_OK = "ok"


@dataclass(frozen=True)
class FaceObs:
    """One recognised face in one analysis pass."""
    student_id: int
    score: float = 1.0
    # Largest change of the face patch since the previous pass (0 = identical
    # pixels). None when it could not be measured; then freeze detection skips it.
    motion: float | None = None
    # The face shares a meeting tile with another face (someone else's camera).
    shared_tile: bool = False


@dataclass(frozen=True)
class Observation:
    t: float                              # seconds (monotonic or epoch, consistently)
    capture_state: str = CAPTURE_OK       # ok | minimized | covered | closed | hidden | ...
    faces: tuple[FaceObs, ...] = ()


# Per-student states (plan section 4).
NOT_ARRIVED = "not_arrived"
ARRIVING = "arriving"
PRESENT = "present"
RECOVERING = "recovering"      # video moving again after a freeze; settling
FROZEN = "frozen"
DISCONNECTED = "disconnected"
OFF_CAM = "off_cam"
ABSENT_FINAL = "absent_final"

# Arrival status.
ON_TIME = "on_time"
LATE = "late"
ABSENT_BY_ARRIVAL = "absent_by_arrival"

# Episode kinds (why a camera-off ladder runs).
EP_CAMERA = "camera"           # face went away without freezing
EP_CONNECTION = "connection"   # disconnected longer than the grace
EP_SUSPICIOUS = "suspicious"   # frozen far longer than a real connection drop


@dataclass
class Event:
    """Something the instructor may need to know. `t` is when it was decided,
    `at` the moment it refers to (e.g. when the face was last seen)."""
    kind: str
    t: float
    student_id: int | None = None
    at: float | None = None
    data: dict[str, Any] = field(default_factory=dict)
    id: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "t": self.t, "at": self.at if self.at is not None else self.t,
                "student_id": self.student_id, **self.data}


@dataclass
class Interval:
    kind: str          # visible | frozen | disconnected | off_cam
    start: float
    end: float | None = None


@dataclass
class FinalStatus:
    student_id: int
    status: str                 # present | late | absent
    reason: str = ""            # late_arrival | never_seen | camera_off | check_connection | suspicious_freeze
    time_in: float | None = None
    time_out: float | None = None
    review: list[str] = field(default_factory=list)
