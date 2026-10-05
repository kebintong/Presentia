"""Excel export of a class's attendance (Students page → Export to Excel)."""

from __future__ import annotations

import re
from datetime import datetime

from app.data import db
from app.data.xlsx import (
    ABSENT, CENTER, LATE, NUMBER, PERCENT, PRESENT, Column, Sheet, build_workbook,
)

_STATUS_CELL = {"Present": ("P", PRESENT), "Late": ("L", LATE), "Absent": ("A", ABSENT)}


def _minute(stamp: str | None) -> str:
    """'2026-10-05 15:20:23' → '2026-10-05 15:20'."""
    return stamp[:16] if stamp else ""


def export_filename(class_name: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", class_name).strip() or "Class"
    return f"{safe} - attendance {datetime.now():%Y-%m-%d}.xlsx"


def class_workbook(class_id: int) -> tuple[str, bytes] | None:
    """(file name, .xlsx bytes) for the class, or None if it does not exist."""
    cls = db.get_class(class_id)
    if cls is None:
        return None
    summary = db.class_attendance_summary(class_id)
    sessions, cells = db.class_attendance_matrix(class_id)

    summary_sheet = Sheet(
        name="Summary",
        columns=[
            Column("Student No.", 16),
            Column("Name", 30),
            Column("Joined Class", 18),
            Column("Present", 10, NUMBER),
            Column("Late", 9, NUMBER),
            Column("Absent", 10, NUMBER),
            Column("Sessions Counted", 11, NUMBER),
            Column("Attendance", 12, PERCENT),
            Column("Last Seen", 18),
            Column("Alerts", 9, NUMBER),
        ],
        rows=[
            [
                s["student_no"], s["name"], _minute(s["joined_at"]),
                s["present"], s["late"], s["absent"], s["sessions"],
                s["rate"], _minute(s["last_seen"]), s["alerts"],
            ]
            for s in summary
        ],
        freeze_cols=2,
        header_height=32,
    )

    matrix_cols = [Column("Student No.", 16), Column("Name", 30)]
    matrix_cols += [
        Column(f"{_minute(se['started_at'])}\n{se['name']}", 15, CENTER) for se in sessions
    ]
    matrix_cols += [
        Column("Present", 10, NUMBER), Column("Late", 9, NUMBER),
        Column("Absent", 10, NUMBER), Column("Attendance", 12, PERCENT),
    ]
    matrix_rows = []
    for s in summary:
        mine = cells.get(s["id"], {})
        row: list = [s["student_no"], s["name"]]
        for se in sessions:
            status = mine.get(se["id"])
            row.append(_STATUS_CELL[status] if status in _STATUS_CELL else "")
        row += [s["present"], s["late"], s["absent"], s["rate"]]
        matrix_rows.append(row)
    matrix_sheet = Sheet(
        name="Attendance",
        columns=matrix_cols,
        rows=matrix_rows,
        freeze_cols=2,
        header_height=48,
    )

    info_sheet = Sheet(
        name="Class Info",
        columns=[Column("Item", 22), Column("Value", 60)],
        rows=[
            ["Class", cls["name"]],
            ["Section", cls["section"] or "—"],
            ["Students", (len(summary), NUMBER)],
            ["Sessions held", (len(sessions), NUMBER)],
            ["Exported", f"{datetime.now():%Y-%m-%d %H:%M}"],
            ["", ""],
            ["P / L / A", "Present / Late / Absent (Attendance sheet)"],
            ["Blank cell", "The student had not joined the class yet, or the session was still running."],
            ["Attendance", "(Present + Late) ÷ sessions counted for that student."],
        ],
        autofilter=False,
    )
    data = build_workbook([summary_sheet, matrix_sheet, info_sheet])
    return export_filename(cls["name"]), data
