"""Compact always-on-top overlay shown next to the Google Meet window."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

_STATE_ICONS = {"present": "🟢", "missing": "🔴", "waiting": "⚪"}


class OverlayWindow(QWidget):
    """Frameless, draggable, always-on-top status panel for the instructor."""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag_offset = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._panel = QLabel()
        self._panel.setTextFormat(Qt.RichText)
        self._panel.setStyleSheet(
            "background: rgba(20, 24, 34, 220); color: #e2e8f0;"
            "border: 1px solid #4a5568; border-radius: 10px;"
            "padding: 10px 14px; font-size: 13px;"
        )
        layout.addWidget(self._panel)
        self.update_status([], None)

    # ------------------------------------------------------------------

    def update_status(self, roster: list[dict], last_alert: str | None) -> None:
        lines = ["<b>AI Attendance — live</b>"]
        for st in roster:
            icon = _STATE_ICONS.get(st["state"], "⚪")
            lines.append(f"{icon} {st['name']}")
        if not roster:
            lines.append("<i>No students in roster</i>")
        if last_alert:
            lines.append(f"<hr><span style='color:#fc8181'>{last_alert}</span>")
        self._panel.setText("<br>".join(lines))
        self.adjustSize()

    # ----------------------------------------------------------- dragging

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
