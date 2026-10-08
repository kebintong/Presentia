"""The attendance rules as a pure state machine (plan sections 1, 3, 4 and 6).

No threads, no clock, no database: feed it one `Observation` per analysis pass
and it returns the `Event`s that pass caused. Everything the instructor sees
(roster chips, Activity, the bubble, notifications) and everything saved for
the report comes from these events and from `snapshot()` / `finish()`.

Time only counts while the monitor can actually see the meeting. Each pass
advances the clocks by the time since the previous pass; while capture is
paused, or when passes stop arriving for longer than
`rules.max_observation_gap`, nothing advances and the gap is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import model as m
from .freeze import FreezeDetector
from .rules import Rules

# Interval kind for each state (None: not tracked).
_INTERVAL = {
    m.NOT_ARRIVED: None,
    m.ARRIVING: None,
    m.PRESENT: "visible",
    m.UNCLEAR: "visible",
    m.UNSEEN: "unseen",
    m.RECOVERING: "visible",
    m.FROZEN: "frozen",
    m.DISCONNECTED: "disconnected",
    m.OFF_CAM: "off_cam",
    m.ABSENT_FINAL: "off_cam",
}


@dataclass
class _Episode:
    """One camera-off stretch; warnings climb with `elapsed`."""
    kind: str
    elapsed: float = 0.0
    level: int = 0
    started: float = 0.0        # when the face was last seen
    back_for: float = 0.0       # time visible again; the episode ends at rules.reset_after_on
    back: bool = False


@dataclass
class _Student:
    id: int
    name: str
    state: str = m.NOT_ARRIVED
    since: float = 0.0
    sightings: list[float] = field(default_factory=list)
    unseen: float = 0.0               # chargeable time since last seen
    last_seen: float | None = None
    time_in: float | None = None
    arrival: str | None = None        # on_time | late | absent_by_arrival
    episode: _Episode | None = None
    frozen_for: float = 0.0
    frozen_at: float | None = None
    grace_used: float = 0.0
    settle: float = 0.0
    final_reason: str = ""            # set when absent_final
    flags: set[str] = field(default_factory=set)
    intervals: list[m.Interval] = field(default_factory=list)
    freeze: FreezeDetector | None = None
    visible_now: bool = False
    no_evidence: float = 0.0          # time with no face and no live tile
    off_evidence: float = 0.0         # time their tile showed the camera-off picture
    unclear_for: float = 0.0          # time on camera without a clear look
    noted: bool = False               # the "face not clear" note was given


class AttendanceCore:
    def __init__(self, roster: dict[int, str], class_start: float, rules: Rules | None = None) -> None:
        self.rules = rules or Rules()
        self.class_start = class_start
        r = self.rules
        self.students: dict[int, _Student] = {
            sid: _Student(sid, name, freeze=FreezeDetector(r.freeze_eps, r.freeze_after, r.freeze_max_gap))
            for sid, name in roster.items()
        }
        self.gaps: list[tuple[float, float | None, str]] = []   # (start, end, reason)
        self._paused: tuple[float, str] | None = None
        self._last_t: float | None = None
        self._last_gap_end: float | None = None
        self._deadline_done = False
        self._events: list[m.Event] = []
        self._next_id = 1
        self.finished = False

    # ── input ────────────────────────────────────────────────────────────────

    def observe(self, obs: m.Observation) -> list[m.Event]:
        out: list[m.Event] = []
        self._events = out
        t = obs.t
        if self.finished:
            return out

        # The monitor cannot see: stop every clock until it can again.
        if obs.capture_state != m.CAPTURE_OK:
            if self._paused is None:
                self._paused = (t, obs.capture_state)
                self._emit("monitor_paused", t, data={"reason": obs.capture_state})
                for s in self.students.values():
                    s.freeze.reset()
            self._last_t = t
            return out

        dt = 0.0
        if self._paused is not None:
            start, reason = self._paused
            self._paused = None
            self.gaps.append((start, t, reason))
            self._last_gap_end = t
            self._emit("monitor_resumed", t, at=start, data={"reason": reason, "seconds": round(t - start, 1)})
        elif self._last_t is not None:
            gap = t - self._last_t
            if gap > self.rules.max_observation_gap:
                # Passes stopped arriving (busy machine, restart): nobody can be
                # charged for time the monitor did not look.
                self.gaps.append((self._last_t, t, "no_frames"))
                self._last_gap_end = t
                self._emit("monitor_gap", t, at=self._last_t, data={"seconds": round(gap, 1)})
                for s in self.students.values():
                    s.freeze.reset()
            elif gap > 0:
                dt = gap
        self._last_t = t

        seen = self._best_faces(obs.faces, t)
        tiles = dict(obs.tiles)
        for s in self.students.values():
            self._step(s, seen.get(s.id), dt, t, tiles.get(s.id))
        self._check_deadline(t)
        return out

    def mark_connection_issue(self, student_id: int, t: float) -> list[m.Event]:
        """The instructor says a camera-off student has connection trouble:
        warnings for that episode are withdrawn and the reconnect grace starts."""
        out: list[m.Event] = []
        self._events = out
        s = self.students.get(student_id)
        if s is None or s.state != m.OFF_CAM:
            return out
        level = s.episode.level if s.episode else 0
        s.episode = None
        s.grace_used = 0.0
        self._set(s, m.DISCONNECTED, t)
        self._emit("marked_connection", t, s.id, data={"withdrawn_level": level,
                                                       "grace": self.rules.reconnect_grace})
        return out

    # ── per-student rules ────────────────────────────────────────────────────

    def _best_faces(self, faces, t) -> dict[int, m.FaceObs]:
        best: dict[int, m.FaceObs] = {}
        for f in faces:
            if f.student_id not in self.students:
                continue
            other = best.get(f.student_id)
            if other is not None:
                s = self.students[f.student_id]
                if "duplicate_face" not in s.flags:
                    s.flags.add("duplicate_face")
                    self._emit("duplicate_face", t, s.id)
                if other.score >= f.score:
                    continue
            best[f.student_id] = f
        return best

    def _step(self, s: _Student, f: m.FaceObs | None, dt: float, t: float, tile: str | None = None) -> None:
        r = self.rules
        visible = f is not None
        if visible:
            s.unseen = 0.0
            s.last_seen = t
            s.no_evidence = s.off_evidence = 0.0
            if f.shared_tile and "shared_tile" not in s.flags:
                s.flags.add("shared_tile")
                self._emit("shared_tile", t, s.id)
        else:
            s.unseen += dt
            s.no_evidence = 0.0 if tile == m.TILE_VIDEO else s.no_evidence + dt
            s.off_evidence = s.off_evidence + dt if tile == m.TILE_AVATAR else 0.0
        motion = f.motion if f is not None else None

        st = s.state
        if st in (m.NOT_ARRIVED, m.ARRIVING):
            self._arrival(s, visible, t)
        elif st == m.ABSENT_FINAL:
            # Final for the session; keep the record of when they were visible.
            want = "visible" if visible else "off_cam"
            cur = s.intervals[-1] if s.intervals else None
            if cur is None or cur.kind != want:
                self._interval(s, want, t)
        elif st == m.PRESENT:
            self._present(s, visible, motion, dt, t, tile)
        elif st == m.UNCLEAR:
            self._unclear(s, visible, motion, dt, t, tile)
        elif st == m.UNSEEN:
            self._unseen(s, visible, motion, dt, t, tile)
        elif st == m.RECOVERING:
            s.settle += dt
            if visible and s.freeze.update(t, motion):
                self._freeze(s, t)
            elif s.settle >= r.recovery_settle:
                self._set(s, m.PRESENT, t)
                if not visible:
                    self._present(s, visible, motion, 0.0, t, tile)
        elif st == m.FROZEN:
            if visible:
                s.frozen_for += dt    # frozen time ends when the tile goes away
            if visible and FreezeDetector.moving(motion, r.freeze_eps):
                s.freeze.reset()
                s.settle = 0.0
                self._emit("recovered", t, s.id, data={"frozen_for": round(s.frozen_for, 1)})
                self._set(s, m.RECOVERING, t)
            elif not visible and s.unseen >= r.vanish_grace:
                # The tile went away after freezing: Teams disconnects ~90 s in.
                s.grace_used = s.unseen
                self._emit("disconnected", t, s.id, at=s.last_seen, data={
                    "frozen_for": round(s.frozen_for, 1), "grace": r.reconnect_grace})
                self._set(s, m.DISCONNECTED, s.last_seen or t)
            elif s.frozen_for >= r.suspicious_freeze:
                self._emit("suspicious_freeze", t, s.id, data={"frozen_for": round(s.frozen_for, 1)})
                s.flags.add("suspicious_freeze")
                s.episode = _Episode(m.EP_SUSPICIOUS, started=t)
                self._set(s, m.OFF_CAM, t)
        elif st == m.DISCONNECTED:
            if visible:
                s.settle = 0.0
                s.freeze.reset()
                self._emit("reconnected", t, s.id, data={"away": round(s.grace_used, 1)})
                self._set(s, m.RECOVERING, t)
            else:
                s.grace_used += dt
                if s.grace_used >= r.reconnect_grace:
                    s.flags.add("connection")
                    self._emit("grace_expired", t, s.id, data={"grace": r.reconnect_grace})
                    s.episode = _Episode(m.EP_CONNECTION, started=t)
                    self._set(s, m.OFF_CAM, t)
        elif st == m.OFF_CAM:
            self._off_cam(s, visible, motion, dt, t, tile)

    def _arrival(self, s: _Student, visible: bool, t: float) -> None:
        r = self.rules
        if visible:
            s.sightings = [x for x in s.sightings if t - x <= r.confirm_window] + [t]
            if s.state == m.NOT_ARRIVED:
                s.state = m.ARRIVING
                s.since = t
            if len(s.sightings) >= r.confirm_passes:
                first = s.sightings[0]
                # Seen right after the monitor was blind: they may have been
                # there all along, so they arrived when the blind spell began.
                gap = self._gap_covering(first)
                time_in = gap if gap is not None else first
                s.time_in = time_in
                late = time_in - self.class_start
                s.arrival = (m.ABSENT_BY_ARRIVAL if late > r.absent_after
                             else m.LATE if late > r.late_after else m.ON_TIME)
                s.sightings = []
                self._set(s, m.PRESENT, time_in)
                s.freeze.reset()
                self._emit("arrived", t, s.id, at=time_in, data={
                    "status": s.arrival, "after": round(max(0.0, late), 1),
                    "after_gap": gap is not None})
        elif s.state == m.ARRIVING and s.sightings and t - s.sightings[-1] > r.confirm_window:
            s.state = m.NOT_ARRIVED
            s.sightings = []

    def _gap_covering(self, t: float) -> float | None:
        """Start of a monitor gap that ended shortly before `t`."""
        for start, end, _ in reversed(self.gaps):
            if end is not None and 0 <= t - end <= self.rules.confirm_window:
                return start
        return None

    def _present(self, s: _Student, visible: bool, motion, dt: float, t: float,
                 tile: str | None = None) -> None:
        r = self.rules
        if visible:
            if s.freeze.update(t, motion):
                self._freeze(s, t)
                return
            self._still_back(s, dt)
            return
        if s.unseen < r.vanish_grace:
            return
        if tile == m.TILE_VIDEO:
            # Their camera is on; the face is just not clear (turned, half
            # out of the picture). Counts as here.
            s.freeze.reset()
            s.unclear_for = s.unseen
            self._set(s, m.UNCLEAR, s.last_seen or t)
            return
        if tile != m.TILE_AVATAR:
            # Nothing shows the camera is off: only "face not seen", later.
            if s.no_evidence >= r.unseen_after:
                s.freeze.reset()
                self._emit("face_not_seen", t, s.id, at=s.last_seen, data={"after": round(s.unseen, 1)})
                self._set(s, m.UNSEEN, s.last_seen or t)
            return
        if s.off_evidence >= r.vanish_grace:
            self._camera_off(s, t, s.unseen)

    def _still_back(self, s: _Student, dt: float) -> None:
        ep = s.episode
        if ep is not None and ep.back:
            ep.back_for += dt
            if ep.back_for >= self.rules.reset_after_on:
                s.episode = None   # back long enough: the episode is over

    def _unclear(self, s: _Student, visible: bool, motion, dt: float, t: float, tile: str | None) -> None:
        r = self.rules
        if visible:
            s.noted = False
            self._set(s, m.PRESENT, t)
            return
        if tile == m.TILE_VIDEO:
            s.unclear_for += dt
            self._still_back(s, dt)
            if s.unclear_for >= r.unclear_note_after and not s.noted:
                s.noted = True
                self._emit("face_unclear", t, s.id, data={"for": round(s.unclear_for, 1)})
            return
        if tile == m.TILE_AVATAR:
            if s.off_evidence >= r.vanish_grace:
                self._camera_off(s, t, s.off_evidence)
            return
        if s.no_evidence >= r.unseen_after:
            self._emit("face_not_seen", t, s.id, at=t - s.no_evidence, data={"after": round(s.no_evidence, 1)})
            self._set(s, m.UNSEEN, t - s.no_evidence)

    def _unseen(self, s: _Student, visible: bool, motion, dt: float, t: float, tile: str | None) -> None:
        r = self.rules
        if visible:
            self._emit("face_seen", t, s.id, data={"unseen_for": round(t - s.since, 1)})
            s.freeze.reset()
            self._set(s, m.PRESENT, t)
            return
        if tile == m.TILE_VIDEO:
            self._emit("face_seen", t, s.id, data={"unseen_for": round(t - s.since, 1), "unclear": True})
            s.unclear_for = 0.0
            self._set(s, m.UNCLEAR, t)
            return
        if tile == m.TILE_AVATAR:
            if s.off_evidence >= r.vanish_grace:
                self._camera_off(s, t, s.off_evidence)
            return
        if s.no_evidence >= r.unseen_ladder_after:
            # Not seen for minutes and no sign of the camera being on: the
            # camera-off ladder starts now, from zero.
            self._camera_off(s, t, 0.0, not_seen=True)

    def _camera_off(self, s: _Student, t: float, off_for: float, not_seen: bool = False) -> None:
        """Gone without freezing first: camera off (or left)."""
        ep = s.episode
        if ep is not None and ep.back:
            ep.back = False            # back too briefly: same episode goes on
            ep.elapsed += off_for
        else:
            s.episode = _Episode(m.EP_CAMERA, elapsed=off_for, started=t - off_for)
        s.freeze.reset()
        data = {"continued": ep is not None, "level": s.episode.level}
        if not_seen:
            data["not_seen"] = True
        at = t - off_for if not not_seen else t
        self._emit("off_cam", t, s.id, at=at, data=data)
        self._set(s, m.OFF_CAM, at)
        self._ladder(s, t)

    def _freeze(self, s: _Student, t: float) -> None:
        at = s.freeze.still_since or t
        s.frozen_for = t - at
        s.frozen_at = at
        self._emit("frozen", t, s.id, at=at)
        self._set(s, m.FROZEN, at)

    def _off_cam(self, s: _Student, visible: bool, motion, dt: float, t: float,
                 tile: str | None = None) -> None:
        r = self.rules
        ep = s.episode
        # A suspicious freeze only ends when the picture moves again.
        back = visible and (ep is None or ep.kind != m.EP_SUSPICIOUS
                            or FreezeDetector.moving(motion, r.freeze_eps))
        # Their tile shows live video again: the camera is back on, even if
        # the face is not clear yet.
        unclear = (not visible and tile == m.TILE_VIDEO
                   and (ep is None or ep.kind != m.EP_SUSPICIOUS))
        if back or unclear:
            if ep is not None:
                ep.back = True
                ep.back_for = 0.0
            s.freeze.reset()
            data = {"level": ep.level if ep else 0, "off_for": round(ep.elapsed, 1) if ep else 0,
                    "kind": ep.kind if ep else m.EP_CAMERA}
            if unclear:
                data["unclear"] = True
                s.unclear_for = 0.0
            self._emit("back_on_cam", t, s.id, data=data)
            self._set(s, m.UNCLEAR if unclear else m.PRESENT, t)
            return
        if ep is None:
            s.episode = ep = _Episode(m.EP_CAMERA, started=t)
        ep.elapsed += dt
        self._ladder(s, t)

    def _ladder(self, s: _Student, t: float) -> None:
        r = self.rules
        ep = s.episode
        level = min(r.final_level, int(ep.elapsed // r.warning_step))
        while ep.level < level:
            ep.level += 1
            data = {"level": ep.level, "off_for": round(ep.elapsed, 1), "kind": ep.kind}
            if ep.level >= r.final_level:
                s.final_reason = {m.EP_CONNECTION: "check_connection",
                                  m.EP_SUSPICIOUS: "suspicious_freeze"}.get(ep.kind, "camera_off")
                data["reason"] = s.final_reason
                self._emit("absent_final", t, s.id, data=data)
                self._set(s, m.ABSENT_FINAL, t)
                return
            self._emit("off_cam_warning", t, s.id, data=data)

    def _check_deadline(self, t: float) -> None:
        if self._deadline_done or t - self.class_start <= self.rules.absent_after:
            return
        self._deadline_done = True
        missing = [s.id for s in self.students.values() if s.state in (m.NOT_ARRIVED, m.ARRIVING)]
        self._emit("arrival_deadline", t, data={"students": missing, "after": self.rules.absent_after})

    # ── bookkeeping ──────────────────────────────────────────────────────────

    def _set(self, s: _Student, state: str, at: float) -> None:
        s.state = state
        s.since = at
        kind = _INTERVAL[state]
        cur = s.intervals[-1] if s.intervals else None
        if cur is not None and cur.end is None and cur.kind == kind:
            return
        if kind is None:
            if cur is not None and cur.end is None:
                cur.end = at
            return
        self._interval(s, kind, at)

    def _interval(self, s: _Student, kind: str, at: float) -> None:
        cur = s.intervals[-1] if s.intervals else None
        if cur is not None and cur.end is None:
            cur.end = max(cur.start, at)
        s.intervals.append(m.Interval(kind, at))

    def _emit(self, kind: str, t: float, student_id: int | None = None, at: float | None = None,
              data: dict | None = None) -> None:
        ev = m.Event(kind, t, student_id, at, data or {}, id=self._next_id)
        self._next_id += 1
        self._events.append(ev)

    # ── output ───────────────────────────────────────────────────────────────

    def snapshot(self, t: float) -> dict:
        """Everything the roster, the bubble and Live View show, as plain data."""
        r = self.rules
        students = []
        counts = {"here": 0, "late": 0, "cam_off": 0, "unseen": 0, "connection": 0, "not_yet": 0,
                  "absent": 0, "total": len(self.students)}
        issues = []
        for s in self.students.values():
            item = {"id": s.id, "name": s.name, "state": s.state, "since": s.since,
                    "arrival": s.arrival, "time_in": s.time_in, "flags": sorted(s.flags)}
            if s.state in (m.PRESENT, m.RECOVERING, m.UNCLEAR):
                counts["here"] += 1
            elif s.state == m.UNSEEN:
                counts["unseen"] += 1
            elif s.state in (m.NOT_ARRIVED, m.ARRIVING):
                counts["not_yet"] += 1
            elif s.state == m.OFF_CAM:
                counts["cam_off"] += 1
            elif s.state in (m.FROZEN, m.DISCONNECTED):
                counts["connection"] += 1
            elif s.state == m.ABSENT_FINAL:
                counts["absent"] += 1
            if s.arrival == m.LATE:
                counts["late"] += 1
            ep = s.episode
            if s.state == m.OFF_CAM and ep is not None:
                item.update(level=ep.level, off_for=round(ep.elapsed, 1), episode=ep.kind,
                            next_at=round((ep.level + 1) * r.warning_step, 1))
                issues.append({"kind": "suspicious_freeze" if ep.kind == m.EP_SUSPICIOUS else "off_cam",
                               "student_id": s.id, "level": ep.level, "off_for": round(ep.elapsed, 1),
                               "episode": ep.kind})
            elif s.state == m.DISCONNECTED:
                item.update(grace_left=round(max(0.0, r.reconnect_grace - s.grace_used), 1))
                issues.append({"kind": "disconnected", "student_id": s.id,
                               "grace_left": item["grace_left"]})
            elif s.state == m.UNSEEN:
                item.update(unseen_for=round(t - s.since, 1))
                issues.append({"kind": "unseen", "student_id": s.id, "unseen_for": item["unseen_for"]})
            elif s.state == m.UNCLEAR:
                item.update(unclear_for=round(s.unclear_for, 1))
            elif s.state == m.FROZEN:
                item.update(frozen_for=round(s.frozen_for, 1))
                issues.append({"kind": "frozen", "student_id": s.id, "frozen_for": item["frozen_for"]})
            elif s.state == m.ABSENT_FINAL:
                item.update(reason=s.final_reason)
            students.append(item)
        return {"t": t, "class_start": self.class_start, "paused": self._paused[1] if self._paused else None,
                "counts": counts, "students": students, "issues": issues}

    def finish(self, t: float) -> list[m.FinalStatus]:
        """Close everything and give each student's status for the report."""
        if self._paused is not None:
            start, reason = self._paused
            self.gaps.append((start, t, reason))
            self._paused = None
        self.finished = True
        out = []
        for s in self.students.values():
            cur = s.intervals[-1] if s.intervals else None
            if cur is not None and cur.end is None:
                cur.end = max(cur.start, t)
            review: list[str] = []
            if s.state == m.ABSENT_FINAL:
                status, reason = "absent", s.final_reason
                review.append(s.final_reason)
            elif s.arrival is None:
                status, reason = "absent", "never_seen"
            elif s.arrival == m.ABSENT_BY_ARRIVAL:
                status, reason = "absent", "late_arrival"
                review.append("late_arrival")
            elif s.arrival == m.LATE:
                status, reason = "late", ""
            else:
                status, reason = "present", ""
            if s.state == m.OFF_CAM:
                review.append("off_cam_at_end")
            if s.state == m.UNSEEN:
                review.append("not_seen_at_end")
            if s.state in (m.DISCONNECTED, m.FROZEN):
                review.append("connection_at_end")
            for flag in ("shared_tile", "suspicious_freeze", "duplicate_face", "connection"):
                if flag in s.flags and flag not in review:
                    review.append(flag)
            seen = [iv for iv in s.intervals if iv.kind == "visible"]
            time_out = seen[-1].end if seen else None
            out.append(m.FinalStatus(s.id, status, reason, s.time_in, time_out, review))
        return out
