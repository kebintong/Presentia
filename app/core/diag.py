"""Recent problems, for Settings → Diagnostics.

Keeps the last few warnings and errors in memory (nothing is written to disk
or sent anywhere) so the instructor can copy them into a report.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime

_MAX = 60
_lock = threading.Lock()
_events: deque[dict] = deque(maxlen=_MAX)


def record(source: str, message: str, level: str = "error") -> None:
    with _lock:
        _events.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
            "level": level,
            "message": message[:600],
        })


def recent() -> list[dict]:
    """Newest first."""
    with _lock:
        return list(reversed(_events))


class _Handler(logging.Handler):
    def emit(self, rec: logging.LogRecord) -> None:
        try:
            msg = rec.getMessage()
            if rec.exc_info and rec.exc_info[1] is not None:
                msg = f"{msg}: {rec.exc_info[1]!r}"
            record(rec.name, msg, "warning" if rec.levelno < logging.ERROR else "error")
        except Exception:  # noqa: BLE001 - logging must never raise
            pass


_installed = False


def install() -> None:
    """Collect WARNING and above from Python logging (uvicorn included)."""
    global _installed
    if _installed:
        return
    _installed = True
    handler = _Handler(level=logging.WARNING)
    for name in ("", "uvicorn.error"):
        logging.getLogger(name).addHandler(handler)
