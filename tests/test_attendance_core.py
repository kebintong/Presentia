"""Replay tests for the attendance core (plan section 8, step 1).

Each test plays a timeline of analysis passes (4 a second, like the sidecar)
and checks the events, the per-student state and the final statuses.
"""

from __future__ import annotations

import random

import pytest

from app.core.attendance import AttendanceCore, FaceObs, Observation, Rules, describe, copy_message
from app.core.attendance.messages import CATEGORY

STEP = 0.25
LIVE = 0.6      # a live face changes a little every pass ...
PEAK = 1.6      # ... and clearly at least once a second
ROSTER = {1: "Juan Dela Cruz", 2: "Maria Santos", 3: "Carlo Reyes"}


def live_motion(t: float) -> float:
    return PEAK if int(t / STEP) % 4 == 0 else LIVE


class Sim:
    """Drives a core through time. `script(t)` returns {student_id: motion or
    None (face shown, motion unknown) or 'gone'} plus an optional capture state."""

    def __init__(self, roster=ROSTER, rules: Rules | None = None, class_start: float = 0.0):
        self.core = AttendanceCore(dict(roster), class_start, rules or Rules())
        self.events = []
        self.t = class_start

    def run(self, until: float, faces, capture: str = "ok", step: float = STEP):
        while self.t < until - 1e-9:
            self.t = round(self.t + step, 4)
            spec = faces(self.t) if callable(faces) else faces
            obs = Observation(self.t, capture, tuple(
                FaceObs(sid, motion=(live_motion(self.t) if mo == "live" else mo))
                for sid, mo in spec.items()))
            self.events += self.core.observe(obs)
        return self

    def kinds(self, sid=None):
        return [e.kind for e in self.events if sid is None or e.student_id == sid]

    def first(self, kind, sid=None):
        return next(e for e in self.events if e.kind == kind and (sid is None or e.student_id == sid))

    def all(self, kind, sid=None):
        return [e for e in self.events if e.kind == kind and (sid is None or e.student_id == sid)]

    def state(self, sid):
        return self.core.students[sid].state


def present(*sids):
    return {s: "live" for s in sids}


# ── Arrival ──────────────────────────────────────────────────────────────────

def test_on_time_arrival_needs_three_passes():
    s = Sim().run(30, {}).run(30.5, present(1))   # two passes only
    assert "arrived" not in s.kinds()
    s.run(31, present(1))
    ev = s.first("arrived", 1)
    assert ev.data["status"] == "on_time"
    assert ev.at == pytest.approx(30.25)          # the first of the agreeing sightings
    assert s.state(1) == "present"


def test_single_frames_never_count():
    s = Sim()
    s.run(60, lambda t: present(2) if int(t * 4) % 40 == 0 else {})   # one pass every 10 s
    assert "arrived" not in s.kinds()
    assert s.state(2) in ("not_arrived", "arriving")


def test_late_and_absent_by_arrival():
    s = Sim().run(11 * 60, {}).run(11 * 60 + 2, present(1))
    assert s.first("arrived", 1).data["status"] == "late"
    s.run(21 * 60, present(1)).run(21 * 60 + 2, present(1, 2))
    assert s.first("arrived", 2).data["status"] == "absent_by_arrival"
    deadline = s.first("arrival_deadline")
    assert set(deadline.data["students"]) == {2, 3}
    final = {f.student_id: f for f in s.core.finish(s.t)}
    assert (final[1].status, final[2].status, final[2].reason) == ("late", "absent", "late_arrival")
    assert (final[3].status, final[3].reason) == ("absent", "never_seen")


def test_arrival_right_after_a_monitor_gap_counts_from_the_gap():
    s = Sim().run(5 * 60, {}).run(12 * 60, {}, capture="minimized").run(12 * 60 + 2, present(1))
    ev = s.first("arrived", 1)
    assert ev.data["status"] == "on_time" and ev.data["after_gap"]
    assert ev.at == pytest.approx(5 * 60 + STEP)


# ── Camera off ───────────────────────────────────────────────────────────────

def _arrived(sim, sids=(1,), until=10):
    return sim.run(until, present(*sids))


def test_camera_off_ladder_and_w2_message():
    s = _arrived(Sim(), until=100)                 # last seen at 100 s
    s.run(400, {})
    warns = s.all("off_cam_warning", 1)
    assert [w.data["level"] for w in warns] == [1, 2, 3]
    assert [round(w.t) for w in warns] == [160, 220, 280]
    absent = s.first("absent_final", 1)
    assert round(absent.t) == 340 and absent.data["reason"] == "camera_off"
    assert s.state(1) == "absent_final"
    # W2 says to copy the reminder; the reminder names the student.
    text, level = describe(warns[1], ROSTER)
    assert "Warning 2" in text and "Copy" in text and level == "warn"
    assert copy_message("Juan Dela Cruz", 125).startswith("Hi Juan, please turn your camera on")
    # Coming back after W4 does not undo it.
    s.run(460, present(1))
    assert s.state(1) == "absent_final"
    final = {f.student_id: f for f in s.core.finish(s.t)}
    assert (final[1].status, final[1].reason) == ("absent", "camera_off")


def test_short_return_keeps_the_same_episode():
    s = _arrived(Sim(), until=100)
    s.run(190, {})                                 # off 90 s -> W1
    assert [w.data["level"] for w in s.all("off_cam_warning", 1)] == [1]
    s.run(200, present(1))                         # back 10 s (< 30 s)
    assert "back_on_cam" in s.kinds(1)
    s.run(240, {})                                 # off again
    w = s.all("off_cam_warning", 1)
    # 90 s already counted; W2 at 120 s of the episode = 30 s after leaving again.
    assert [x.data["level"] for x in w] == [1, 2]
    assert w[1].t == pytest.approx(230, abs=0.5)
    assert s.first("off_cam", 1).data["continued"] is False
    assert s.all("off_cam", 1)[1].data["continued"] is True


def test_long_return_starts_a_new_episode():
    s = _arrived(Sim(), until=100)
    s.run(190, {})                                 # W1
    s.run(230, present(1))                         # back 40 s (>= 30 s)
    s.run(300, {})                                 # off 70 s
    levels = [x.data["level"] for x in s.all("off_cam_warning", 1)]
    assert levels == [1, 1]


def test_low_score_flaps_never_raise_anything():
    rnd = random.Random(7)
    s = Sim().run(600, lambda t: present(1) if rnd.random() > 0.35 else {})
    assert s.state(1) == "present"
    assert not [k for k in s.kinds(1) if k not in ("arrived",)]


# ── Connection ───────────────────────────────────────────────────────────────

def test_freeze_then_recover_is_no_penalty():
    s = _arrived(Sim(), until=60)
    s.run(73, {1: 0.0})                            # Teams test A: 13 s frozen
    fr = s.first("frozen", 1)
    assert fr.at == pytest.approx(60.25) and fr.t == pytest.approx(68.25)
    assert s.state(1) == "frozen"
    s.run(76, {1: 11.0})                           # recovery: blocky, big changes
    assert s.first("recovered", 1)
    s.run(82, {})                                  # garbled face not recognised during the settle
    s.run(120, present(1))
    assert s.state(1) == "present"
    assert not s.all("off_cam", 1) and not s.all("off_cam_warning", 1)


def test_freeze_then_disconnect_and_back_within_grace():
    s = _arrived(Sim(), until=60)
    s.run(150, {1: 0.0})                           # frozen ~90 s (Teams test B)
    s.run(300, {})                                 # disconnected 150 s (< 180)
    d = s.first("disconnected", 1)
    assert d.data["frozen_for"] == pytest.approx(90, abs=1)
    assert s.state(1) == "disconnected"
    s.run(330, present(1))
    assert s.first("reconnected", 1) and s.state(1) == "present"
    assert not s.all("off_cam_warning", 1)
    final = {f.student_id: f for f in s.core.finish(s.t)}
    assert final[1].status == "present"


def test_disconnect_beyond_grace_runs_the_ladder_to_check_connection():
    s = _arrived(Sim(), until=60)
    s.run(80, {1: 0.0})                            # frozen 20 s
    s.run(80 + 180 + 250, {})                      # gone for good
    ge = s.first("grace_expired", 1)
    assert ge.t == pytest.approx(80 + 180, abs=0.5)
    assert [w.data["kind"] for w in s.all("off_cam_warning", 1)] == ["connection"] * 3
    a = s.first("absent_final", 1)
    assert a.data["reason"] == "check_connection"
    final = {f.student_id: f for f in s.core.finish(s.t)}
    assert final[1].reason == "check_connection" and "check_connection" in final[1].review


def test_suspicious_freeze():
    s = _arrived(Sim(), until=60)
    s.run(60 + 8 + 180 + 1, {1: 0.0})              # frozen, still on screen, > 3 min
    assert s.first("suspicious_freeze", 1)
    assert s.state(1) == "off_cam"
    s.run(60 + 8 + 180 + 65, {1: 0.0})             # still frozen -> warnings climb
    assert s.first("off_cam_warning", 1).data["kind"] == "suspicious"
    s.run(60 + 8 + 180 + 70, present(1))           # moves again
    assert s.first("back_on_cam", 1)
    text, level = describe(s.first("suspicious_freeze", 1), ROSTER)
    assert "liveness" in text and level == "error"


def test_instructor_can_mark_a_connection_issue():
    s = _arrived(Sim(), until=60)
    s.run(130, {})                                 # W1 at 120
    assert s.all("off_cam_warning", 1)
    ev = s.core.mark_connection_issue(1, s.t)
    assert ev[0].kind == "marked_connection" and ev[0].data["withdrawn_level"] == 1
    assert s.state(1) == "disconnected"
    s.run(250, {})                                 # 120 s, inside the fresh grace
    assert s.state(1) == "disconnected"
    s.run(275, present(1))                         # back; settled after 10 s
    assert s.state(1) == "present"
    assert s.core.mark_connection_issue(1, s.t) == []   # only for camera-off students


# ── Monitor gaps ─────────────────────────────────────────────────────────────

def test_monitor_pause_charges_nobody():
    s = _arrived(Sim(), (1, 2), until=60)
    s.run(60 + 600, {}, capture="minimized")       # 10 min blind
    assert s.first("monitor_paused").data["reason"] == "minimized"
    assert not s.all("off_cam")
    s.run(760, present(1))                         # 2 is really gone after the pause
    assert s.first("monitor_resumed").data["seconds"] == pytest.approx(600, abs=1)
    w = s.all("off_cam_warning", 2)
    # Counting starts after the pause: W1 = resume + 60 s, not earlier.
    assert w and w[0].t == pytest.approx(660 + 60, abs=0.5)
    assert s.core.gaps[0][2] == "minimized"


def test_stalled_passes_are_a_gap_not_absence():
    s = _arrived(Sim(), until=60)
    s.run(60.25, {})
    s.run(400, {}, step=339.75)                    # one pass 5 min later
    assert s.first("monitor_gap")
    assert not s.all("off_cam_warning")


# ── Flags and output ─────────────────────────────────────────────────────────

def test_duplicate_and_shared_tile_flags():
    core = AttendanceCore(dict(ROSTER), 0.0)
    evs = []
    for i in range(1, 6):
        evs += core.observe(Observation(i * STEP, faces=(FaceObs(1, 0.9, 0.5), FaceObs(1, 0.6, 0.5),
                                                          FaceObs(2, 0.8, 0.5, shared_tile=True))))
    kinds = [e.kind for e in evs]
    assert kinds.count("duplicate_face") == 1 and kinds.count("shared_tile") == 1
    final = {f.student_id: f for f in core.finish(2.0)}
    assert "duplicate_face" in final[1].review and "shared_tile" in final[2].review


def test_snapshot_counts_and_issues():
    s = _arrived(Sim(), (1, 2), until=60)
    s.run(60 + 130, present(2))                    # 1 off cam for 130 s
    snap = s.core.snapshot(s.t)
    c = snap["counts"]
    assert (c["here"], c["cam_off"], c["not_yet"], c["total"]) == (1, 1, 1, 3)
    issue = snap["issues"][0]
    assert issue["kind"] == "off_cam" and issue["student_id"] == 1 and issue["level"] == 2
    st = {x["id"]: x for x in snap["students"]}
    assert st[1]["next_at"] == 180 and st[3]["state"] == "not_arrived"


def test_intervals_and_time_out_are_kept():
    s = _arrived(Sim(), until=60)
    s.run(100, {})
    s.run(200, present(1))
    final = {f.student_id: f for f in s.core.finish(s.t)}
    ivs = [(iv.kind, round(iv.start), round(iv.end)) for iv in s.core.students[1].intervals]
    assert ivs[0][0] == "visible" and ivs[1][0] == "off_cam" and ivs[2][0] == "visible"
    assert final[1].time_out == pytest.approx(200, abs=0.5)  # not erased by the absence


def test_every_event_has_words_and_a_category():
    s = Sim()
    s.run(60, present(1, 2)).run(70, {1: 0.0, 2: "live"}).run(260, {2: "live"})
    s.run(270, {2: "live"}, capture="covered").run(400, {2: "live"}).run(1300, {})
    for ev in s.events:
        text, level = describe(ev, ROSTER)
        assert text and level in ("ok", "info", "warn", "error")
        if ev.student_id is not None:
            assert ev.kind in CATEGORY or ev.kind == "arrived"
    assert all(isinstance(e.to_dict()["id"], int) for e in s.events)
    ids = [e.id for e in s.events]
    assert ids == sorted(set(ids))


def test_teams_like_freeze_profile():
    """Test A, re-created: Teams sends ~9 of 30 frames; a pass reports the
    largest change since the previous pass. Live peaks 1.4-2.2, mean ~0.45;
    then 13 s of exact zeros; then 8 s of blocky recovery (up to 11)."""
    rnd = random.Random(3)

    def pass_motion(t):
        if 6 <= t - 60 < 19:
            return 0.0
        if 19 <= t - 60 < 27:
            return rnd.uniform(2, 11)
        # 7-8 frames per pass at 30 fps, ~2-3 of them new pictures.
        frames = [rnd.uniform(0.2, 0.7) if rnd.random() < 0.3 else 0.0 for _ in range(8)]
        if rnd.random() < 0.3:
            frames[0] = rnd.uniform(1.4, 2.2)
        return max(frames)

    s = _arrived(Sim(), until=60)
    s.run(90, lambda t: {1: pass_motion(t)})
    fr = s.all("frozen", 1)
    assert len(fr) == 1 and fr[0].at == pytest.approx(66, abs=0.5)
    assert s.first("recovered", 1).t == pytest.approx(79, abs=0.5)
    assert s.state(1) == "present"
