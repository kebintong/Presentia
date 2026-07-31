"""Class session: identity verification (liveness + recognition) and
continuous presence monitoring with real-time alerts."""

from __future__ import annotations

import time
from datetime import datetime

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from app.core.camera import CameraThread, frame_brightness
from app.core.face_engine import FaceEngine, MATCH_THRESHOLD
from app.core.liveness import FaceMeshTracker, LivenessChecker
from app.core.monitor import PresenceMonitor
from app.core.workers import AnalysisWorker
from app.data import db

LIVENESS_TIMEOUT = 60.0      # seconds to complete the challenge
RECOGNIZE_ATTEMPTS = 12      # embedding attempts before giving up
REID_INTERVAL = 60.0         # periodic re-identification while monitoring
REID_MISMATCHES_TO_ALERT = 2 # consecutive failed re-ids before alerting

BANNER_STYLES = {
    "info":  "background:#2d3748; color:#e2e8f0;",
    "ok":    "background:#22543d; color:#c6f6d5;",
    "warn":  "background:#744210; color:#fefcbf;",
    "error": "background:#742a2a; color:#fed7d7;",
}


class SessionPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._camera: CameraThread | None = None
        self._worker: AnalysisWorker | None = None
        self._tracker: FaceMeshTracker | None = None
        self._liveness: LivenessChecker | None = None
        self._monitor: PresenceMonitor | None = None

        self._session_id: int | None = None
        self._phase = "idle"  # idle | waiting | liveness | recognize | monitoring
        self._phase_started = 0.0
        self._active_student: dict | None = None      # verified student being monitored
        self._pending_student: dict | None = None     # student currently verifying
        self._target_embedding: np.ndarray | None = None
        self._recognize_attempts = 0
        self._reid_state = {"last": 0.0, "misses": 0}

        self._build_ui()

    # ------------------------------------------------------------ UI setup

    def _build_ui(self) -> None:
        from app.ui.video_widget import VideoWidget

        root = QHBoxLayout(self)
        left = QVBoxLayout()

        session_box = QGroupBox("Class session")
        s_layout = QVBoxLayout(session_box)
        self.session_name_edit = QLineEdit()
        self.session_name_edit.setPlaceholderText("Session name, e.g. CS101 Lecture")
        s_layout.addWidget(self.session_name_edit)
        self.session_btn = QPushButton("Start Session")
        self.session_btn.clicked.connect(self._toggle_session)
        s_layout.addWidget(self.session_btn)
        left.addWidget(session_box)

        join_box = QGroupBox("Attendance check-in")
        j_layout = QVBoxLayout(join_box)
        self.student_combo = QComboBox()
        j_layout.addWidget(self.student_combo)
        self.join_btn = QPushButton("Join && Verify")
        self.join_btn.setEnabled(False)
        self.join_btn.clicked.connect(self._start_verification)
        j_layout.addWidget(self.join_btn)
        left.addWidget(join_box)

        self.banner = QLabel("No active session.")
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.setAlignment(Qt.AlignCenter)
        self.banner.setMinimumHeight(48)
        self._set_banner("No active session.", "info")
        left.addWidget(self.banner)

        alerts_box = QGroupBox("Real-time alerts")
        a_layout = QVBoxLayout(alerts_box)
        self.alerts_list = QListWidget()
        a_layout.addWidget(self.alerts_list)
        left.addWidget(alerts_box, stretch=1)

        root.addLayout(left, stretch=1)

        self.video = VideoWidget()
        root.addWidget(self.video, stretch=2)

    def refresh_students(self) -> None:
        self.student_combo.clear()
        for s in db.list_students():
            self.student_combo.addItem(f"{s['student_no']} — {s['name']}", userData=s)

    # ------------------------------------------------------ session control

    def _toggle_session(self) -> None:
        if self._session_id is None:
            self._start_session()
        else:
            self._end_session()

    def _start_session(self) -> None:
        if not FaceEngine.is_ready():
            QMessageBox.information(
                self, "Please wait", "AI models are still loading, try again shortly."
            )
            return
        name = self.session_name_edit.text().strip() or (
            "Session " + datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        self.refresh_students()
        if self.student_combo.count() == 0:
            QMessageBox.warning(
                self, "No students", "Register at least one student first."
            )
            return

        self._session_id = db.create_session(name)
        self._phase = "waiting"
        self.session_btn.setText("End Session")
        self.session_name_edit.setEnabled(False)
        self.join_btn.setEnabled(True)
        self.alerts_list.clear()
        self._set_banner(f"Session '{name}' started. Select a student and press "
                         "Join & Verify.", "info")

        self._tracker = FaceMeshTracker()
        self._worker = AnalysisWorker(self)
        self._worker.result.connect(self._on_result)
        self._worker.start()

        self._camera = CameraThread(parent=self)
        self._camera.frame_ready.connect(self._on_frame)
        self._camera.camera_error.connect(self._on_camera_error)
        self._camera.camera_recovered.connect(self._on_camera_recovered)
        self._camera.start()

    def _end_session(self) -> None:
        if self._session_id is not None:
            if self._active_student is not None:
                db.log_event(self._session_id, self._active_student["id"],
                             "session_end", "Session ended; time-out recorded.")
            db.end_session(self._session_id)
        self._teardown()
        self._set_banner("Session ended. Attendance times were recorded.", "info")

    def _teardown(self) -> None:
        self._phase = "idle"
        self._session_id = None
        self._active_student = None
        self._pending_student = None
        self._target_embedding = None
        self._monitor = None
        self._liveness = None
        if self._camera is not None:
            self._camera.stop()
            self._camera = None
        if self._worker is not None:
            self._worker.stop()
            self._worker = None
        if self._tracker is not None:
            self._tracker.close()
            self._tracker = None
        self.session_btn.setText("Start Session")
        self.session_name_edit.setEnabled(True)
        self.join_btn.setEnabled(False)
        self.video.show_idle()

    # -------------------------------------------------------- verification

    def _start_verification(self) -> None:
        student = self.student_combo.currentData()
        if student is None or self._session_id is None:
            return
        self._pending_student = student
        # load once; the analysis thread must not depend on mutable page state
        self._target_embedding = db.get_student_embedding(student["id"])
        self._liveness = LivenessChecker(self._tracker)
        self._phase = "liveness"
        self._phase_started = time.monotonic()
        self._set_banner(f"Verifying {student['name']}: follow the prompts.", "info")
        self._worker.set_processor(self._process_liveness)

    # NOTE: the _process_* methods run on the analysis thread. The GUI thread
    # can switch phases (clearing _liveness/_pending_student/_active_student)
    # while one last frame is still being processed, so each processor grabs a
    # local reference and drops the frame if its state is already gone.

    def _process_liveness(self, frame: np.ndarray) -> dict | None:
        liveness = self._liveness
        if liveness is None:
            return None
        out = liveness.process(frame)
        out["kind"] = "liveness"
        out["brightness"] = frame_brightness(frame)
        return out

    def _process_recognize(self, frame: np.ndarray) -> dict | None:
        target = self._target_embedding
        if target is None:
            return None
        emb = FaceEngine.instance().embed_largest(frame)
        if emb is None:
            return {"kind": "recognize", "found": False, "score": 0.0}
        score = FaceEngine.similarity(emb, target)
        return {"kind": "recognize", "found": True, "score": score}

    def _process_monitor(self, frame: np.ndarray) -> dict | None:
        tracker, target = self._tracker, self._target_embedding
        if tracker is None or target is None:
            return None
        landmarks = tracker.landmarks(frame)
        out = {
            "kind": "monitor",
            "face_found": landmarks is not None,
            "brightness": frame_brightness(frame),
            "reid": None,
        }
        now = time.monotonic()
        if landmarks is not None and now - self._reid_state["last"] >= REID_INTERVAL:
            self._reid_state["last"] = now
            emb = FaceEngine.instance().embed_largest(frame)
            if emb is not None:
                out["reid"] = FaceEngine.similarity(emb, target)
        return out

    # ------------------------------------------------------ result handling

    def _on_result(self, result: dict) -> None:
        kind = result.get("kind")
        if kind == "liveness" and self._phase == "liveness":
            self._handle_liveness(result)
        elif kind == "recognize" and self._phase == "recognize":
            self._handle_recognize(result)
        elif kind == "monitor" and self._phase == "monitoring":
            self._handle_monitor(result)

    def _handle_liveness(self, result: dict) -> None:
        if time.monotonic() - self._phase_started > LIVENESS_TIMEOUT:
            self._phase = "waiting"
            self._worker.set_processor(None)
            self._set_banner("Liveness check timed out. Press Join & Verify to retry.",
                             "error")
            return
        if result["passed"]:
            self._phase = "recognize"
            self._recognize_attempts = 0
            self._set_banner("Liveness passed. Verifying your identity…", "info")
            self._worker.set_processor(self._process_recognize)
        else:
            self._set_banner(f"Liveness check — {result['prompt']}", "info")

    def _handle_recognize(self, result: dict) -> None:
        self._recognize_attempts += 1
        if result["found"] and result["score"] >= MATCH_THRESHOLD:
            student = self._pending_student
            self._active_student = student
            self._pending_student = None
            db.record_time_in(self._session_id, student["id"])
            db.log_event(self._session_id, student["id"], "verified",
                         f"Identity verified (similarity {result['score']:.2f}); "
                         "time-in recorded.")
            self._add_alert(f"{student['name']} verified — time-in recorded.", "ok")
            self._set_banner(f"{student['name']} is verified and being monitored.", "ok")
            self._phase = "monitoring"
            self._reid_state = {"last": time.monotonic(), "misses": 0}
            self._monitor = PresenceMonitor(self._on_presence_event)
            self._worker.set_processor(self._process_monitor)
            return

        if self._recognize_attempts >= RECOGNIZE_ATTEMPTS:
            self._phase = "waiting"
            self._worker.set_processor(None)
            name = self._pending_student["name"] if self._pending_student else "student"
            db.log_event(self._session_id,
                         self._pending_student["id"] if self._pending_student else None,
                         "verification_failed",
                         f"Face did not match {name} (best score "
                         f"{result['score']:.2f}).")
            self._add_alert(f"Verification FAILED for {name}.", "error")
            self._set_banner(
                f"Face does not match {name}. Press Join & Verify to retry.", "error"
            )

    def _handle_monitor(self, result: dict) -> None:
        if self._monitor is None:
            return
        self._monitor.update(result["face_found"], result["brightness"])
        reid = result.get("reid")
        if reid is not None:
            if reid < MATCH_THRESHOLD:
                self._reid_state["misses"] += 1
                if self._reid_state["misses"] >= REID_MISMATCHES_TO_ALERT:
                    self._reid_state["misses"] = 0
                    self._monitor.identity_mismatch(reid)
            else:
                self._reid_state["misses"] = 0

    # --------------------------------------------------------- monitoring

    def _on_presence_event(self, event_type: str, message: str) -> None:
        student = self._active_student
        student_id = student["id"] if student else None
        db.log_event(self._session_id, student_id, event_type, message)

        if event_type == "back_in_frame":
            self._add_alert(message, "ok")
            self._set_banner(f"{student['name']} is present and being monitored.", "ok")
        elif event_type == "out_of_frame":
            self._add_alert(message, "warn")
            self._set_banner(f"ALERT: {student['name']} is out of frame!", "warn")
        elif event_type == "camera_off":
            self._add_alert(message, "error")
            self._set_banner(f"ALERT: {student['name']}'s camera is off!", "error")
        elif event_type == "identity_mismatch":
            self._add_alert(message, "error")
            self._set_banner("ALERT: a different person may be on camera!", "error")

    def _on_camera_error(self, message: str) -> None:
        if self._phase == "monitoring" and self._monitor is not None:
            self._monitor.camera_failed()
        elif self._phase in ("liveness", "recognize"):
            self._set_banner(f"Camera problem: {message}", "error")
        self.video.show_idle("No camera signal")

    def _on_camera_recovered(self) -> None:
        if self._phase == "monitoring":
            self._add_alert("Camera signal recovered.", "ok")

    # -------------------------------------------------------------- frames

    def _on_frame(self, frame: np.ndarray) -> None:
        self.video.show_frame(frame)
        if self._worker is not None and self._phase in ("liveness", "recognize",
                                                        "monitoring"):
            self._worker.submit(frame)

    # ------------------------------------------------------------- helpers

    def _set_banner(self, text: str, level: str) -> None:
        self.banner.setText(text)
        self.banner.setStyleSheet(
            "QLabel#banner { %s border-radius: 6px; padding: 8px; font-weight: bold; }"
            % BANNER_STYLES[level]
        )

    def _add_alert(self, message: str, level: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        item = QListWidgetItem(f"[{ts}] {message}")
        colors = {"ok": "#68d391", "warn": "#f6ad55", "error": "#fc8181", "info": "#cbd5e0"}
        item.setForeground(QColor(colors.get(level, "#cbd5e0")))
        self.alerts_list.insertItem(0, item)

    # ---------------------------------------------------------- lifecycle

    def deactivate(self) -> None:
        """Called when switching tabs; keep the session running (monitoring
        continues in the background), nothing to do here."""

    def shutdown(self) -> None:
        """Called on app close."""
        if self._session_id is not None:
            db.end_session(self._session_id)
        self._teardown()
