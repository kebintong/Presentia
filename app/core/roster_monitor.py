"""Per-student presence across the captured meeting.

Each analysis pass reports which registered students were recognised, and —
when the meeting's video tiles could be read (app.core.meet_tiles) — what
each remembered student's own tile shows: live video or the meeting's
"camera off" picture.

A face that is not recognised is not proof that the camera is off: the
student may have turned away, or only half their face may be in the tile.
So the states are:

  waiting   not seen yet this session
  present   recognised (recently)
  unclear   not recognised, but their tile still shows live video:
            on camera, face not clear. Counts as present.
  unseen    not recognised for `unseen_after` seconds and nothing shows
            whether their camera is on: "face not seen"
  cam_off   their tile shows the meeting's camera-off picture

Time only counts while passes arrive: when capture pauses (the meeting
window was minimised, tab sharing stopped…) nobody's clock runs.
"""

from __future__ import annotations

import time
from typing import Callable

MISSING_AFTER = 5.0      # s: a camera-off tile this long means camera off
UNSEEN_AFTER = 30.0      # s: no face and no tile evidence this long: "face not seen"
UNCLEAR_NOTE_AFTER = 600.0  # s on camera without a clear look: one gentle note
MAX_GAP = 3.0            # s between passes before the gap is not counted

VIDEO = "video"
AVATAR = "avatar"


class RosterMonitor:
    """Tracks last-seen times for every registered student in the roster.

    `on_event(student_id, event_type, message)` fires for:
      - "time_in":    first time the student is recognised in the session
      - "unseen":     face not seen for `unseen_after` s, camera state unknown
      - "camera_off": their tile shows the camera-off picture
      - "returned":   recognised again, or their camera is on again
      - "unclear":    on camera for a long time without a clear look
    """

    def __init__(
        self,
        roster: list[dict],
        on_event: Callable[[int, str, str], None],
        missing_after: float = MISSING_AFTER,
        unseen_after: float = UNSEEN_AFTER,
        unclear_note_after: float = UNCLEAR_NOTE_AFTER,
    ) -> None:
        self._on_event = on_event
        self._missing_after = max(1.0, float(missing_after))
        self._unseen_after = max(self._missing_after, float(unseen_after))
        self._note_after = unclear_note_after
        self._last_update: float | None = None
        self._students: dict[int, dict] = {}
        for s in roster:
            self.add_student(s)

    # ------------------------------------------------------------------

    def add_student(self, student: dict) -> None:
        """Add a student enrolled mid-session (e.g. from a Meet tile)."""
        self._students[student["id"]] = {
            "name": student["name"],
            "last_seen": None,   # last recognised (None until first sighting)
            "last_on": None,     # last recognised, or their tile showed live video
            "state": "waiting",
            "noted": False,      # the "face not clear for a while" note was given
        }

    def update(self, visible_ids: set[int], tiles: dict[int, str] | None = None) -> None:
        """Feed one analysis pass.

        visible_ids: students recognised on screen in this pass.
        tiles: for students not recognised, what their own tile shows
               ("video" or "avatar"), when that is known.
        """
        now = time.monotonic()
        tiles = tiles or {}
        if self._last_update is not None:
            gap = now - self._last_update
            if gap > MAX_GAP:
                # Nothing was looked at meanwhile: that time is nobody's.
                for st in self._students.values():
                    for key in ("last_seen", "last_on"):
                        if st[key] is not None:
                            st[key] += gap
        self._last_update = now

        for sid, st in self._students.items():
            name = st["name"]
            state = st["state"]
            if sid in visible_ids:
                if state == "waiting":
                    self._on_event(sid, "time_in", f"{name} detected in the meeting — time-in recorded.")
                elif state in ("unseen", "cam_off"):
                    self._on_event(sid, "returned", f"{name} is back on camera.")
                st["state"] = "present"
                st["last_seen"] = st["last_on"] = now
                st["noted"] = False
                continue
            if state == "waiting":
                continue

            away = now - st["last_seen"]          # since the face was recognised
            evidence = tiles.get(sid)
            if evidence == VIDEO:
                st["last_on"] = now
                if away < self._missing_after and state == "present":
                    continue
                if state in ("unseen", "cam_off"):
                    self._on_event(sid, "returned",
                                   f"{name}'s camera is on again (face not clear yet).")
                st["state"] = "unclear"
                if away >= self._note_after and not st["noted"]:
                    st["noted"] = True
                    self._on_event(sid, "unclear",
                                   f"{name}'s camera is on, but Presentia has not seen their face clearly "
                                   f"for {int(away // 60)} minutes. Click their name to check.")
                continue
            off = now - st["last_on"]             # since anything showed them on camera
            if evidence == AVATAR and off >= self._missing_after:
                if state != "cam_off":
                    st["state"] = "cam_off"
                    self._on_event(sid, "camera_off", f"{name}'s camera is off.")
            elif off >= self._unseen_after and state in ("present", "unclear"):
                st["state"] = "unseen"
                self._on_event(sid, "unseen",
                               f"{name}'s face has not been seen for {int(self._unseen_after)} seconds "
                               "(camera off, or out of view).")

    # ------------------------------------------------------------------

    def status(self) -> list[dict]:
        """Current roster state for the UI: one dict per student."""
        now = time.monotonic()
        out = []
        for student_id, st in self._students.items():
            item = {"id": student_id, "name": st["name"], "state": st["state"]}
            if st["state"] in ("unseen", "cam_off") and st["last_on"] is not None:
                item["away"] = round(now - st["last_on"], 1)
            elif st["state"] == "unclear" and st["last_seen"] is not None:
                item["unclear_for"] = round(now - st["last_seen"], 1)
            out.append(item)
        return out

    def present_ids(self) -> set[int]:
        """Students counted as here now (recognised, or on camera)."""
        return {sid for sid, st in self._students.items() if st["state"] in ("present", "unclear")}
