"""Meet Monitor: screen-captures the Google Meet area, recognizes registered
students in the video tiles, records attendance, and raises real-time alerts
when a student disappears (camera off / left frame / left the call)."""

from __future__ import annotations

import time
from datetime import datetime

import cv2
import numpy as np
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from app.core.face_engine import FaceEngine
from app.core.liveness import FaceMeshTracker, LivenessChecker
from app.core.roster_monitor import RosterMonitor
from app.core.screen import ScreenCaptureThread
from app.core.tile_tracker import TileTracker
from app.core.workers import AnalysisWorker
from app.data import db

VERIFY_TIMEOUT = 60.0  # seconds to complete the tile liveness challenge

STATE_COLORS = {"present": "#68d391", "missing": "#fc8181", "waiting": "#a0aec0"}


class MeetPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._region: dict | None = None
        self._capture: ScreenCaptureThread | None = None
        self._worker: AnalysisWorker | None = None
        self._roster_monitor: RosterMonitor | None = None
        self._session_id: int | None = None
        self._roster: list[dict] = []
        self._embeddings: list[tuple[int, np.ndarray]] = []
        self._names: dict[int, str] = {}
        self._selector = None
        self._overlay = None
        self._last_alert: str | None = None
        self._unknowns: list[dict] = []
        self._annotations: dict = {"matches": [], "unknowns": []}
        self._tracker: TileTracker | None = None
        self._verify: dict | None = None   # active tile verification state
        self._verified: set[int] = set()   # student ids verified this session
        self._build_ui()

    # ------------------------------------------------------------ UI setup

    def _build_ui(self) -> None:
        from app.ui.video_widget import VideoWidget

        root = QHBoxLayout(self)
        left = QVBoxLayout()

        setup_box = QGroupBox("Meeting monitor")
        s_layout = QVBoxLayout(setup_box)
        self.session_name_edit = QLineEdit()
        self.session_name_edit.setPlaceholderText("Session name, e.g. CS101 on Meet")
        s_layout.addWidget(self.session_name_edit)
        self.region_btn = QPushButton("Select Meet Area on Screen")
        self.region_btn.clicked.connect(self._pick_region)
        s_layout.addWidget(self.region_btn)
        self.region_label = QLabel("No area selected yet.")
        self.region_label.setWordWrap(True)
        s_layout.addWidget(self.region_label)
        alert_row = QHBoxLayout()
        alert_row.addWidget(QLabel("Alert when missing for"))
        self.missing_spin = QSpinBox()
        self.missing_spin.setRange(2, 60)
        self.missing_spin.setValue(5)
        self.missing_spin.setSuffix(" s")
        alert_row.addWidget(self.missing_spin)
        alert_row.addStretch(1)
        s_layout.addLayout(alert_row)
        self.start_btn = QPushButton("Start Monitoring")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._toggle_monitoring)
        s_layout.addWidget(self.start_btn)
        self.overlay_btn = QPushButton("Show Overlay")
        self.overlay_btn.setEnabled(False)
        self.overlay_btn.clicked.connect(self._toggle_overlay)
        s_layout.addWidget(self.overlay_btn)
        left.addWidget(setup_box)

        roster_box = QGroupBox("Student roster — click a visible student to verify")
        r_layout = QVBoxLayout(roster_box)
        self.roster_list = QListWidget()
        self.roster_list.itemClicked.connect(self._on_roster_clicked)
        r_layout.addWidget(self.roster_list)
        self.verify_label = QLabel("")
        self.verify_label.setWordWrap(True)
        self.verify_label.setStyleSheet("color: #f6e05e; font-weight: bold;")
        r_layout.addWidget(self.verify_label)
        left.addWidget(roster_box, stretch=1)

        unknown_box = QGroupBox("Unknown faces — click to enroll")
        u_layout = QVBoxLayout(unknown_box)
        self.unknown_list = QListWidget()
        self.unknown_list.setViewMode(QListWidget.IconMode)
        self.unknown_list.setIconSize(QSize(56, 56))
        self.unknown_list.setFixedHeight(96)
        self.unknown_list.itemClicked.connect(self._enroll_unknown)
        u_layout.addWidget(self.unknown_list)
        left.addWidget(unknown_box)

        alerts_box = QGroupBox("Real-time alerts")
        a_layout = QVBoxLayout(alerts_box)
        self.alerts_list = QListWidget()
        a_layout.addWidget(self.alerts_list)
        left.addWidget(alerts_box, stretch=1)

        root.addLayout(left, stretch=1)

        self.video = VideoWidget()
        self.video.show_idle("Captured Meet area will appear here")
        root.addWidget(self.video, stretch=2)

    # ------------------------------------------------------ region picking

    def _pick_region(self) -> None:
        from app.ui.region_selector import RegionSelector

        self._selector = RegionSelector()
        self._selector.region_selected.connect(self._on_region)
        self._selector.cancelled.connect(lambda: self.window().showNormal())
        self.window().showMinimized()
        self._selector.open()

    def _on_region(self, region: dict) -> None:
        self._region = region
        self.region_label.setText(
            f"Area: {region['width']}x{region['height']} at "
            f"({region['left']}, {region['top']})"
        )
        self.start_btn.setEnabled(True)
        self.window().showNormal()

    # -------------------------------------------------------- monitoring

    def _toggle_monitoring(self) -> None:
        if self._session_id is None:
            self._start()
        else:
            self._stop()

    def _start(self) -> None:
        if not FaceEngine.is_ready():
            QMessageBox.information(
                self, "Please wait", "AI models are still loading, try again shortly."
            )
            return
        self._roster = db.list_students()
        # an empty roster is fine: students can be enrolled live from their tiles
        self._embeddings = db.all_embeddings()
        self._names = {s["id"]: s["name"] for s in self._roster}

        name = self.session_name_edit.text().strip() or (
            "Meet " + datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        self._session_id = db.create_session(name)
        self._roster_monitor = RosterMonitor(
            self._roster, self._on_roster_event,
            missing_after=float(self.missing_spin.value()),
        )
        self._last_alert = None
        self._annotations = {"matches": [], "unknowns": []}
        self._tracker = TileTracker(FaceEngine.instance(), lambda: self._embeddings)
        self._verify = None
        self._verified = set()
        self.verify_label.setText("")
        self.alerts_list.clear()
        self._refresh_roster_view()

        self._worker = AnalysisWorker(self)
        self._worker.set_processor(self._process_frame)
        self._worker.result.connect(self._on_result)
        self._worker.start()

        self._last_tracker_ts = 0.0
        self._cached_matches: list = []
        # capture fast for a smooth preview; the analysis worker naturally
        # drops frames and runs recognition as fast as the CPU allows
        self._capture = ScreenCaptureThread(self._region, fps=15.0, parent=self)
        self._capture.frame_ready.connect(self._on_frame)
        self._capture.capture_error.connect(self._on_capture_error)
        self._capture.start()

        self.start_btn.setText("Stop Monitoring")
        self.overlay_btn.setEnabled(True)
        self.region_btn.setEnabled(False)
        self.session_name_edit.setEnabled(False)
        self._add_alert(f"Monitoring started for session '{name}'.", "ok")

    def _stop(self) -> None:
        if self._session_id is not None:
            db.end_session(self._session_id)
        self._session_id = None
        if self._capture is not None:
            self._capture.stop()
            self._capture = None
        if self._worker is not None:
            self._worker.stop()
            self._worker = None
        self._roster_monitor = None
        self._tracker = None
        self._verify = None
        self.verify_label.setText("")
        if self._overlay is not None:
            self._overlay.close()
            self._overlay = None
        self.start_btn.setText("Start Monitoring")
        self.overlay_btn.setText("Show Overlay")
        self.overlay_btn.setEnabled(False)
        self.region_btn.setEnabled(True)
        self.session_name_edit.setEnabled(True)
        self._unknowns = []
        self.unknown_list.clear()
        self.video.show_idle("Captured Meet area will appear here")
        self._add_alert("Monitoring stopped. Attendance times recorded.", "info")

    # ----------------------------------------------------------- analysis

    def _on_frame(self, frame: np.ndarray) -> None:
        """Every captured frame: draw the latest known boxes and show it
        immediately (smooth preview), while analysis runs in the background."""
        if self._worker is None:
            return
        self._worker.submit(frame)
        annotated = frame.copy()
        for student_id, score, (x1, y1, x2, y2) in self._annotations["matches"]:
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (80, 220, 120), 2)
            cv2.putText(
                annotated, f"{self._names.get(student_id, '?')} {score:.2f}",
                (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (80, 220, 120), 2,
            )
        for i, (x1, y1, x2, y2) in enumerate(self._annotations["unknowns"]):
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (90, 90, 240), 2)
            cv2.putText(
                annotated, f"Unknown {i + 1}", (x1, max(20, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (90, 90, 240), 2,
            )
        verify_bbox = self._annotations.get("verify_bbox")
        if verify_bbox is not None:
            x1, y1, x2, y2 = verify_bbox
            cv2.rectangle(annotated, (x1 - 4, y1 - 4), (x2 + 4, y2 + 4),
                          (80, 220, 250), 3)
        self.video.show_frame(annotated)

    def _process_frame(self, frame: np.ndarray) -> dict | None:
        """Worker thread: recognize every roster student visible in the area.

        While a tile verification is active, the expensive recognition pass is
        throttled to ~1/s so the liveness challenge samples the student's tile
        at nearly full capture rate — fast blinks land between samples
        otherwise.
        """
        verify = self._verify  # local ref: GUI thread may clear it mid-frame
        now = time.monotonic()
        run_tracker = verify is None or now - self._last_tracker_ts >= 1.0

        if run_tracker:
            self._last_tracker_ts = now
            matches, unknown_faces = self._tracker.process(frame)
            self._cached_matches = matches
        else:
            matches, unknown_faces = self._cached_matches, []
        h, w = frame.shape[:2]

        verify_out = None
        if verify is not None:
            bbox = next((m[2] for m in matches if m[0] == verify["sid"]), None)
            if bbox is None:
                verify_out = {"face_found": False, "bbox": None,
                              "prompt": "Student's face is not visible",
                              "passed": False}
            else:
                x1, y1, x2, y2 = bbox
                pad_x, pad_y = int((x2 - x1) * 0.6), int((y2 - y1) * 0.6)
                crop = frame[max(0, y1 - pad_y):min(h, y2 + pad_y),
                             max(0, x1 - pad_x):min(w, x2 + pad_x)]
                verify_out = verify["liveness"].process(
                    np.ascontiguousarray(crop))
                verify_out["bbox"] = bbox
        unknowns = []
        for emb, (x1, y1, x2, y2) in unknown_faces:
            pad_x, pad_y = int((x2 - x1) * 0.3), int((y2 - y1) * 0.3)
            crop = frame[max(0, y1 - pad_y):min(h, y2 + pad_y),
                         max(0, x1 - pad_x):min(w, x2 + pad_x)].copy()
            unknowns.append({"embedding": emb, "crop": crop,
                             "bbox": (x1, y1, x2, y2)})

        return {"matches": matches, "unknowns": unknowns, "verify": verify_out,
                "tracked": run_tracker}

    def _on_result(self, result: dict) -> None:
        if self._roster_monitor is None:
            return
        self._annotations = {
            "matches": result["matches"],
            "unknowns": [u["bbox"] for u in result["unknowns"]],
            "verify_bbox": (result["verify"] or {}).get("bbox"),
        }
        if result["tracked"]:
            self._roster_monitor.update({m[0] for m in result["matches"]})
            self._refresh_roster_view()
            self._update_unknowns(result["unknowns"])
        if result["verify"] is not None:
            self._handle_verify_result(result["verify"])

    def _update_unknowns(self, unknowns: list[dict]) -> None:
        # only rebuild when the count changes, so the list stays stable while
        # the instructor is about to click a face
        if len(unknowns) == self.unknown_list.count() and unknowns:
            self._unknowns = unknowns  # keep embeddings/crops fresh
            return
        self._unknowns = unknowns
        self.unknown_list.clear()
        for i, u in enumerate(unknowns):
            crop = cv2.cvtColor(u["crop"], cv2.COLOR_BGR2RGB)
            ch, cw, _ = crop.shape
            img = QImage(crop.data, cw, ch, 3 * cw, QImage.Format_RGB888)
            item = QListWidgetItem(QIcon(QPixmap.fromImage(img.copy())), f"#{i + 1}")
            self.unknown_list.addItem(item)

    # -------------------------------------------------------------- events

    def _on_roster_event(self, student_id: int, event_type: str, message: str) -> None:
        if event_type == "time_in":
            db.record_time_in(self._session_id, student_id)
            db.log_event(self._session_id, student_id, "verified", message)
            self._add_alert(message, "ok")
        elif event_type == "missing":
            db.log_event(self._session_id, student_id, "out_of_frame", message)
            self._add_alert(message, "error")
            self._last_alert = message
        elif event_type == "returned":
            db.log_event(self._session_id, student_id, "back_in_frame", message)
            self._add_alert(message, "ok")
            self._last_alert = None

    def _enroll_unknown(self, item: QListWidgetItem) -> None:
        idx = self.unknown_list.row(item)
        if self._session_id is None or idx >= len(self._unknowns):
            return
        unknown = self._unknowns[idx]

        dialog = QDialog(self)
        dialog.setWindowTitle("Enroll student from meeting")
        form = QFormLayout(dialog)
        face_label = QLabel()
        crop = cv2.cvtColor(unknown["crop"], cv2.COLOR_BGR2RGB)
        ch, cw, _ = crop.shape
        img = QImage(crop.data, cw, ch, 3 * cw, QImage.Format_RGB888)
        face_label.setPixmap(QPixmap.fromImage(img.copy()).scaledToHeight(
            120, Qt.SmoothTransformation))
        form.addRow(face_label)
        no_edit = QLineEdit()
        no_edit.setPlaceholderText("e.g. 2024-00123")
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("Full name")
        form.addRow("Student No.", no_edit)
        form.addRow("Name", name_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() != QDialog.Accepted:
            return
        student_no, name = no_edit.text().strip(), name_edit.text().strip()
        if not student_no or not name:
            QMessageBox.warning(self, "Missing info",
                                "Enter both student number and name.")
            return
        try:
            student_id = db.add_student(student_no, name, unknown["embedding"])
        except Exception as exc:  # duplicate student number, etc.
            QMessageBox.critical(self, "Could not enroll", str(exc))
            return

        # make the new student known to the live session immediately
        self._embeddings.append((student_id, unknown["embedding"]))
        self._names[student_id] = name
        self._roster_monitor.add_student({"id": student_id, "name": name})
        if self._tracker is not None:
            self._tracker.reidentify()
        db.log_event(self._session_id, student_id, "verified",
                     f"{name} enrolled live from their meeting tile.")
        self._add_alert(f"{name} enrolled from the meeting.", "ok")
        self._refresh_roster_view()

    # ------------------------------------------------- tile verification

    def _on_roster_clicked(self, item: QListWidgetItem) -> None:
        if self._session_id is None or self._roster_monitor is None:
            return
        row = self.roster_list.row(item)
        status = self._roster_monitor.status()
        if row >= len(status):
            return
        student = status[row]

        if self._verify is not None and self._verify["sid"] == student["id"]:
            self._cancel_verification("Verification cancelled.")
            return
        if student["state"] != "present":
            self._add_alert(
                f"{student['name']} must be visible on screen to verify.", "warn"
            )
            return

        self._verify = {
            "sid": student["id"],
            "name": student["name"],
            "liveness": LivenessChecker(FaceMeshTracker(), directional=False),
            "started": time.monotonic(),
        }
        self._add_alert(
            f"Verifying {student['name']}: relay the on-screen instructions to "
            "them over the call. Click their name again to cancel.", "info",
        )

    def _handle_verify_result(self, result: dict) -> None:
        verify = self._verify
        if verify is None:
            return
        if time.monotonic() - verify["started"] > VERIFY_TIMEOUT:
            db.log_event(self._session_id, verify["sid"], "verification_failed",
                         f"Tile liveness check for {verify['name']} timed out.")
            self._cancel_verification(
                f"Verification of {verify['name']} timed out.", level="error")
            return
        if result["passed"]:
            self._verified.add(verify["sid"])
            db.log_event(self._session_id, verify["sid"], "verified",
                         f"{verify['name']} passed the liveness check via "
                         "their meeting tile.")
            self._add_alert(f"{verify['name']} verified (liveness passed).", "ok")
            self._verify = None
            self.verify_label.setText("")
            self._refresh_roster_view()
            return
        tip = "" if result["face_found"] else "  (tip: pin/enlarge their tile)"
        self.verify_label.setText(
            f"Verifying {verify['name']}: {result['prompt']}{tip}"
        )

    def _cancel_verification(self, message: str, level: str = "info") -> None:
        self._verify = None
        self.verify_label.setText("")
        self._add_alert(message, level)

    def _on_capture_error(self, message: str) -> None:
        self._add_alert(f"Screen capture failed: {message}", "error")
        self._stop()

    # ------------------------------------------------------------- helpers

    def _refresh_roster_view(self) -> None:
        roster = self._roster_monitor.status() if self._roster_monitor else []
        self.roster_list.clear()
        labels = {"present": "PRESENT", "missing": "MISSING", "waiting": "not seen yet"}
        for st in roster:
            mark = "  [verified]" if st["id"] in self._verified else ""
            item = QListWidgetItem(f"{st['name']} — {labels[st['state']]}{mark}")
            item.setForeground(QColor(STATE_COLORS[st["state"]]))
            self.roster_list.addItem(item)
        if self._overlay is not None:
            self._overlay.update_status(roster, self._last_alert)

    def _add_alert(self, message: str, level: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        colors = {"ok": "#68d391", "warn": "#f6ad55", "error": "#fc8181", "info": "#cbd5e0"}
        item = QListWidgetItem(f"[{ts}] {message}")
        item.setForeground(QColor(colors.get(level, "#cbd5e0")))
        self.alerts_list.insertItem(0, item)

    def _toggle_overlay(self) -> None:
        from app.ui.overlay import OverlayWindow

        if self._overlay is None:
            self._overlay = OverlayWindow()
            self._overlay.show()
            self.overlay_btn.setText("Hide Overlay")
            self._refresh_roster_view()
        else:
            self._overlay.close()
            self._overlay = None
            self.overlay_btn.setText("Show Overlay")

    # ---------------------------------------------------------- lifecycle

    def deactivate(self) -> None:
        """Keep monitoring while the user browses other tabs."""

    def shutdown(self) -> None:
        if self._session_id is not None:
            self._stop()
