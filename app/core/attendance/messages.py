"""Words for the core's events: Activity lines, notifications, the W2 message."""

from __future__ import annotations

from .model import Event


def mmss(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    return f"{s // 60}:{s % 60:02d}"


def copy_message(name: str, off_for: float) -> str:
    """The text the instructor pastes into the meeting chat at Warning 2."""
    minutes = max(1, int(off_for // 60))
    first = name.split()[0] if name.strip() else "there"
    return (f"Hi {first}, please turn your camera on. Presentia shows it has been off for "
            f"{minutes} minute{'s' if minutes != 1 else ''}, and attendance needs your face on screen.")


# kind -> (category, level) for the Activity filters and colours.
CATEGORY = {
    "arrived": "arrivals", "arrival_deadline": "arrivals",
    "off_cam": "warnings", "off_cam_warning": "warnings", "absent_final": "warnings", "back_on_cam": "warnings",
    "frozen": "connection", "recovered": "connection", "disconnected": "connection", "reconnected": "connection",
    "grace_expired": "connection", "marked_connection": "connection", "suspicious_freeze": "warnings",
    "monitor_paused": "monitor", "monitor_resumed": "monitor", "monitor_gap": "monitor",
    "shared_tile": "warnings", "duplicate_face": "warnings",
}

_PAUSE = {"minimized": "the meeting window was minimised", "covered": "the meeting window was covered",
          "hidden": "the meeting window was hidden", "closed": "the meeting window was closed"}


def describe(ev: Event, names: dict[int, str], rules=None) -> tuple[str, str]:
    """(text, level) where level is ok | info | warn | error."""
    n = names.get(ev.student_id, "A student") if ev.student_id is not None else ""
    d = ev.data
    k = ev.kind
    if k == "arrived":
        st = d.get("status")
        if st == "late":
            return f"{n} arrived late (+{mmss(d.get('after', 0))}).", "warn"
        if st == "absent_by_arrival":
            return f"{n} arrived after {mmss(d.get('after', 0))} — counted Absent (over 20 min late).", "error"
        return f"{n} arrived.", "ok"
    if k == "arrival_deadline":
        c = len(d.get("students", []))
        return (f"{c} student{'s' if c != 1 else ''} not here {int(d.get('after', 0) // 60)} min after start — Absent unless excused."
                if c else "Everyone arrived."), "warn" if c else "ok"
    if k == "off_cam":
        return (f"{n}'s camera is off again (same episode)." if d.get("continued")
                else f"{n} is not on camera."), "info"
    if k == "off_cam_warning":
        lvl = d.get("level", 1)
        why = " after losing connection" if d.get("kind") == "connection" else ""
        extra = " Copy the reminder to send in the meeting chat." if lvl == 2 else (" Final warning." if lvl == 3 else "")
        return f"Warning {lvl}: {n}'s camera has been off {mmss(d.get('off_for', 0))}{why}.{extra}", "warn"
    if k == "absent_final":
        reason = {"check_connection": "Absent — check connection (camera off after a disconnect)",
                  "suspicious_freeze": "Absent — video frozen too long"}.get(d.get("reason"), "Absent — camera off 4 min")
        return f"{n}: {reason}.", "error"
    if k == "back_on_cam":
        return f"{n} is back on camera.", "ok"
    if k == "frozen":
        return f"{n}'s video froze — may have lost connection.", "info"
    if k == "recovered":
        return f"{n}'s video is moving again (frozen {mmss(d.get('frozen_for', 0))}).", "ok"
    if k == "disconnected":
        return f"{n} disconnected after {mmss(d.get('frozen_for', 0))} frozen — waiting up to {mmss(d.get('grace', 180))}.", "info"
    if k == "reconnected":
        return f"{n} reconnected — no penalty.", "ok"
    if k == "grace_expired":
        return f"{n} has not reconnected in {mmss(d.get('grace', 180))} — camera-off warnings start.", "warn"
    if k == "marked_connection":
        return f"{n} marked as a connection issue — warnings withdrawn, waiting up to {mmss(d.get('grace', 180))}.", "info"
    if k == "suspicious_freeze":
        return (f"{n}'s video has been frozen {mmss(d.get('frozen_for', 0))} without disconnecting — "
                "it may be a photo or a virtual camera. Run a liveness check."), "error"
    if k == "shared_tile":
        return f"{n} was seen on another participant's camera.", "warn"
    if k == "duplicate_face":
        return f"{n}'s face appeared twice at once — check for a photo or a second account.", "warn"
    if k == "monitor_paused":
        why = _PAUSE.get(d.get("reason"), "the meeting could not be seen")
        return f"Monitor paused — {why}. Nobody is penalised meanwhile.", "info"
    if k == "monitor_resumed":
        return f"Monitor resumed after {mmss(d.get('seconds', 0))}.", "info"
    if k == "monitor_gap":
        return f"No pictures for {mmss(d.get('seconds', 0))} — that time is not counted.", "info"
    return k.replace("_", " "), "info"
