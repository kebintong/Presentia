"""Per-student presence tracking across the captured Meet area.

Each analysis pass reports which registered students were recognized anywhere
in the screen region. A student who stops being seen (camera turned off, left
the tile, or left the call) triggers a "missing" alert after a debounce; their
return triggers a recovery notice. First sighting marks time-in.
"""

from __future__ import annotations

import time
from typing import Callable

MISSING_AFTER = 5.0  # seconds unseen before a student is flagged missing


class RosterMonitor:
    """Tracks last-seen times for every registered student in the roster.

    `on_event(student_id, event_type, message)` fires for:
      - "time_in":  first time the student is recognized in the session
      - "missing":  student unseen for MISSING_AFTER seconds after appearing
      - "returned": student recognized again after being flagged missing
    """

    def __init__(
        self,
        roster: list[dict],
        on_event: Callable[[int, str, str], None],
        missing_after: float = MISSING_AFTER,
    ) -> None:
        self._on_event = on_event
        self._missing_after = missing_after
        self._students = {
            s["id"]: {
                "name": s["name"],
                "last_seen": None,   # None until first sighting
                "missing": False,
                "seen_once": False,
            }
            for s in roster
        }

    # ------------------------------------------------------------------

    def add_student(self, student: dict) -> None:
        """Add a student enrolled mid-session (e.g. from a Meet tile)."""
        self._students[student["id"]] = {
            "name": student["name"],
            "last_seen": None,
            "missing": False,
            "seen_once": False,
        }

    def update(self, visible_ids: set[int]) -> None:
        """Feed one analysis pass: the set of student ids recognized on screen."""
        now = time.monotonic()

        for student_id, st in self._students.items():
            if student_id in visible_ids:
                if not st["seen_once"]:
                    st["seen_once"] = True
                    self._on_event(
                        student_id, "time_in",
                        f"{st['name']} detected in the meeting — time-in recorded.",
                    )
                elif st["missing"]:
                    self._on_event(
                        student_id, "returned",
                        f"{st['name']} is visible again.",
                    )
                st["missing"] = False
                st["last_seen"] = now
            elif (
                st["seen_once"]
                and not st["missing"]
                and now - st["last_seen"] >= self._missing_after
            ):
                st["missing"] = True
                self._on_event(
                    student_id, "missing",
                    f"{st['name']} is no longer visible (camera off or left frame).",
                )

    # ------------------------------------------------------------------

    def status(self) -> list[dict]:
        """Current roster state for the UI: one dict per student."""
        out = []
        for student_id, st in self._students.items():
            if not st["seen_once"]:
                state = "waiting"
            elif st["missing"]:
                state = "missing"
            else:
                state = "present"
            out.append({"id": student_id, "name": st["name"], "state": state})
        return out
