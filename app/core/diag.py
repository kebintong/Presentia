"""Diagnostics: recent problems, and the optional diagnostic mode.

Always on (cheap, memory only): the last few warnings and errors, for
Settings → Diagnostics.

Diagnostic mode (Settings → Diagnostics, off by default): also writes a
detailed activity log to <data folder>/logs/presentia-diagnostic.log, and the
app offers a report whenever something fails. It turns itself off after
MODE_DAYS days. Nothing is sent anywhere unless the user presses Send.
"""

from __future__ import annotations

import logging
import logging.handlers
import threading
import traceback
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

_MAX = 60
_lock = threading.Lock()
_events: deque[dict] = deque(maxlen=_MAX)

MODE_DAYS = 7
LOG_MAX_BYTES = 2 * 1024 * 1024   # per file; one current file + 2 older ones ≈ 6 MB at most
LOG_BACKUPS = 2
_K_MODE = "diagnostic_mode_since"

enabled = False                      # diagnostic mode, cached for the request middleware
_file: logging.Logger | None = None


# ── recent problems (always) ───────────────────────────────────────────────

def record(source: str, message: str, level: str = "error") -> None:
    with _lock:
        _events.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
            "level": level,
            "message": message[:600],
        })
    log(f"[{source}] {message}", level)


def recent() -> list[dict]:
    """Newest first."""
    with _lock:
        return list(reversed(_events))


class _Handler(logging.Handler):
    def emit(self, rec: logging.LogRecord) -> None:
        if rec.name.startswith("presentia.diagnostic"):
            return
        try:
            msg = rec.getMessage()
            if rec.exc_info and rec.exc_info[1] is not None:
                msg = f"{msg}: {rec.exc_info[1]!r}"
                if enabled:
                    log("".join(traceback.format_exception(*rec.exc_info)).rstrip(), "error")
            record(rec.name, msg, "warning" if rec.levelno < logging.ERROR else "error")
        except Exception:  # noqa: BLE001 - logging must never raise
            pass


_installed = False


def install() -> None:
    """Collect WARNING and above from Python logging (uvicorn included), and
    reopen the activity log if diagnostic mode was left on."""
    global _installed
    if _installed:
        return
    _installed = True
    handler = _Handler(level=logging.WARNING)
    for name in ("", "uvicorn.error"):
        logging.getLogger(name).addHandler(handler)
    try:
        mode()  # applies a saved "on" (or its expiry)
    except Exception:  # noqa: BLE001 - the database may not be ready in odd setups
        pass


# ── diagnostic mode ────────────────────────────────────────────────────────

def log_path() -> Path:
    from app.data import db

    return db.DB_PATH.parent / "logs" / "presentia-diagnostic.log"


def _open_file() -> None:
    global _file
    if _file is not None:
        return
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger = logging.getLogger("presentia.diagnostic")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _file = logger


def _close_file() -> None:
    global _file
    if _file is None:
        return
    for h in list(_file.handlers):
        h.close()
        _file.removeHandler(h)
    _file = None


def log(message: str, level: str = "info") -> None:
    """One line in the activity log (only while diagnostic mode is on)."""
    if not enabled or _file is None:
        return
    lvl = {"error": logging.ERROR, "warning": logging.WARNING}.get(level, logging.INFO)
    try:
        _file.log(lvl, message)
    except Exception:  # noqa: BLE001
        pass


def mode() -> dict:
    """Current state; switches off (and says so) once MODE_DAYS have passed."""
    global enabled
    from app.data import db

    since_raw = db.get_setting(_K_MODE)
    if not since_raw:
        enabled = False
        _close_file()
        return {"enabled": False, "since": None, "until": None, "expired": False, "log_path": str(log_path())}
    since = datetime.fromisoformat(since_raw)
    until = since + timedelta(days=MODE_DAYS)
    if datetime.now() >= until:
        log("Diagnostic mode turned itself off after "f"{MODE_DAYS} days.")
        db.set_setting(_K_MODE, None)
        enabled = False
        _close_file()
        return {"enabled": False, "since": None, "until": None, "expired": True, "log_path": str(log_path())}
    if not enabled:
        enabled = True
        _open_file()
    return {
        "enabled": True,
        "since": since.isoformat(timespec="seconds"),
        "until": until.isoformat(timespec="seconds"),
        "expired": False,
        "log_path": str(log_path()),
    }


def set_mode(on: bool) -> dict:
    global enabled
    from app.data import db

    if on:
        if not db.get_setting(_K_MODE):
            db.set_setting(_K_MODE, datetime.now().isoformat(timespec="seconds"))
        state = mode()
        log(f"Diagnostic mode turned on (until {state['until']}).")
        return state
    log("Diagnostic mode turned off.")
    db.set_setting(_K_MODE, None)
    enabled = False
    _close_file()
    return mode()


def read_log(max_lines: int = 400) -> str:
    """The end of the activity log (older rotated file first if needed)."""
    path = log_path()
    files = [Path(f"{path}.{i}") for i in range(LOG_BACKUPS, 0, -1)] + [path]
    lines: list[str] = []
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                lines.extend(fh.readlines())
        except OSError:
            continue
    return "".join(lines[-max_lines:])


def log_size() -> int:
    path = log_path()
    total = 0
    for f in [path] + [Path(f"{path}.{i}") for i in range(1, LOG_BACKUPS + 1)]:
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total


def clear_log() -> None:
    was_open = _file is not None
    _close_file()
    path = log_path()
    for f in [path] + [Path(f"{path}.{i}") for i in range(1, LOG_BACKUPS + 1)]:
        try:
            f.unlink()
        except OSError:
            pass
    if was_open and enabled:
        _open_file()
        log("Activity log cleared.")
