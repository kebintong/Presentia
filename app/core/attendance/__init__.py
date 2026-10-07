"""Attendance rules for the Meeting Monitor, as a pure, testable core.

See claude/meet-monitor-attendance-rules-2026-10-08.md (the final plan).
"""

from .core import AttendanceCore
from .messages import CATEGORY, copy_message, describe
from .model import Event, FaceObs, FinalStatus, Interval, Observation
from .rules import Rules, for_platform

__all__ = ["AttendanceCore", "Event", "FaceObs", "FinalStatus", "Interval", "Observation",
           "Rules", "for_platform", "describe", "copy_message", "CATEGORY"]
