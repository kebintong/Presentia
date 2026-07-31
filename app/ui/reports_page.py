"""Attendance reports: per-session table, manual status override, CSV export."""

from __future__ import annotations

import csv

from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QListWidget, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.data import db

STATUSES = ["Present", "Late", "Absent"]


class ReportsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Session:"))
        self.session_combo = QComboBox()
        self.session_combo.currentIndexChanged.connect(self._load_session)
        top.addWidget(self.session_combo, stretch=1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_sessions)
        top.addWidget(self.refresh_btn)
        self.export_btn = QPushButton("Export CSV")
        self.export_btn.clicked.connect(self._export_csv)
        top.addWidget(self.export_btn)
        root.addLayout(top)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Student No.", "Name", "Time In", "Time Out", "Status", "Alerts"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        root.addWidget(self.table, stretch=2)

        events_box = QGroupBox("Session event log")
        e_layout = QVBoxLayout(events_box)
        self.events_list = QListWidget()
        e_layout.addWidget(self.events_list)
        root.addWidget(events_box, stretch=1)

    # ------------------------------------------------------------- loading

    def refresh_sessions(self) -> None:
        current = self.session_combo.currentData()
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        for s in db.list_sessions():
            label = f"#{s['id']}  {s['name']}  ({s['started_at']})"
            if not s["ended_at"]:
                label += "  [ongoing]"
            self.session_combo.addItem(label, userData=s["id"])
        self.session_combo.blockSignals(False)

        if self.session_combo.count() == 0:
            self.table.setRowCount(0)
            self.events_list.clear()
            return
        # restore previous selection if still present
        idx = self.session_combo.findData(current)
        self.session_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._load_session()

    def _load_session(self) -> None:
        session_id = self.session_combo.currentData()
        if session_id is None:
            return
        self._rows = db.session_report(session_id)

        self.table.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            self.table.setItem(r, 0, QTableWidgetItem(row["student_no"]))
            self.table.setItem(r, 1, QTableWidgetItem(row["name"]))
            self.table.setItem(r, 2, QTableWidgetItem(row["time_in"] or ""))
            self.table.setItem(r, 3, QTableWidgetItem(row["time_out"] or ""))
            combo = QComboBox()
            combo.addItems(STATUSES)
            combo.setCurrentText(row["status"])
            combo.currentTextChanged.connect(
                lambda status, att_id=row["attendance_id"]: db.set_status(att_id, status)
            )
            self.table.setCellWidget(r, 4, combo)
            self.table.setItem(r, 5, QTableWidgetItem(str(row["alert_count"])))

        self.events_list.clear()
        for ev in db.session_events(session_id):
            who = f" [{ev['student_name']}]" if ev["student_name"] else ""
            self.events_list.addItem(
                f"{ev['occurred_at']}  {ev['event_type'].upper()}{who}: {ev['message']}"
            )

    # -------------------------------------------------------------- export

    def _export_csv(self) -> None:
        session_id = self.session_combo.currentData()
        if session_id is None:
            QMessageBox.information(self, "Nothing to export", "No session selected.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export attendance", f"attendance_session_{session_id}.csv",
            "CSV files (*.csv)",
        )
        if not path:
            return
        rows = db.session_report(session_id)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["Student No.", "Name", "Time In", "Time Out", "Status", "Alerts"]
            )
            for row in rows:
                writer.writerow([
                    row["student_no"], row["name"], row["time_in"] or "",
                    row["time_out"] or "", row["status"], row["alert_count"],
                ])
        QMessageBox.information(self, "Exported", f"Attendance exported to:\n{path}")

    # ---------------------------------------------------------- lifecycle

    def activate(self) -> None:
        """Called when the tab becomes visible."""
        self.refresh_sessions()

    def deactivate(self) -> None:
        pass
