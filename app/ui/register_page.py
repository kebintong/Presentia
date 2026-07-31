"""Student registration: personal info + face sample capture and enrollment."""

from __future__ import annotations

import numpy as np
import cv2
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from app.core.camera import CameraThread
from app.core.enrollment import GuidedEnrollment
from app.core.face_engine import FaceEngine
from app.core.liveness import FaceMeshTracker
from app.core.workers import AnalysisWorker
from app.data import db

MIN_DET_SCORE = 0.55


class RegisterPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._camera: CameraThread | None = None
        self._screen_cap = None  # ScreenCaptureThread during screen capture
        self._selector = None
        self._worker: AnalysisWorker | None = None
        self._samples: list[np.ndarray] = []
        self._guided: GuidedEnrollment | None = None
        self._mesh: FaceMeshTracker | None = None
        self._capturing = False
        self._students_cache: list[dict] = []
        self._build_ui()
        self.refresh_students()

    # ------------------------------------------------------------ UI setup

    def _build_ui(self) -> None:
        from app.ui.video_widget import VideoWidget

        root = QHBoxLayout(self)

        # left: form + students list
        left = QVBoxLayout()
        form_box = QGroupBox("Student information")
        form = QFormLayout(form_box)
        self.student_no_edit = QLineEdit()
        self.student_no_edit.setPlaceholderText("e.g. 2024-00123")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Full name")
        form.addRow("Student No.", self.student_no_edit)
        form.addRow("Name", self.name_edit)
        left.addWidget(form_box)

        self.capture_btn = QPushButton("Start Face Capture (webcam)")
        self.capture_btn.clicked.connect(self._toggle_capture)
        left.addWidget(self.capture_btn)

        self.screen_btn = QPushButton("Capture from Screen (Meet tile)")
        self.screen_btn.clicked.connect(self._toggle_screen_capture)
        left.addWidget(self.screen_btn)

        self.import_btn = QPushButton("Import Photos… (remote student)")
        self.import_btn.clicked.connect(self._import_photos)
        left.addWidget(self.import_btn)

        self.progress_label = QLabel("No face samples captured yet.")
        self.progress_label.setWordWrap(True)
        left.addWidget(self.progress_label)

        self.save_btn = QPushButton("Save Student")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_student)
        left.addWidget(self.save_btn)

        students_box = QGroupBox("Registered students")
        students_layout = QVBoxLayout(students_box)
        self.students_list = QListWidget()
        students_layout.addWidget(self.students_list)
        self.delete_btn = QPushButton("Delete Selected Student")
        self.delete_btn.clicked.connect(self._delete_selected)
        students_layout.addWidget(self.delete_btn)
        left.addWidget(students_box, stretch=1)

        root.addLayout(left, stretch=1)

        # right: camera preview
        self.video = VideoWidget()
        root.addWidget(self.video, stretch=2)

    # ---------------------------------------------------------- capturing

    def _toggle_capture(self) -> None:
        if self._capturing:
            self._stop_capture()
            self.progress_label.setText("Capture cancelled.")
            return
        if not self._begin_capture("Look at the camera…", directional=True):
            return
        self.capture_btn.setText("Cancel Capture")
        self._camera = CameraThread(parent=self)
        self._camera.frame_ready.connect(self._on_frame)
        self._camera.camera_error.connect(self._on_camera_error)
        self._camera.start()

    def _toggle_screen_capture(self) -> None:
        """Capture face samples from a screen region — e.g. a remote student
        pinned full-size in a Google Meet call."""
        if self._capturing:
            self._stop_capture()
            self.progress_label.setText("Capture cancelled.")
            return
        if not FaceEngine.is_ready():
            QMessageBox.information(
                self, "Please wait", "AI models are still loading, try again shortly."
            )
            return
        from app.ui.region_selector import RegionSelector

        self._selector = RegionSelector()
        self._selector.region_selected.connect(self._start_screen_capture)
        self._selector.cancelled.connect(lambda: self.window().showNormal())
        self.window().showMinimized()
        self._selector.open()

    def _start_screen_capture(self, region: dict) -> None:
        from app.core.screen import ScreenCaptureThread

        self.window().showNormal()
        if not self._begin_capture(
            "Capturing from screen — relay the prompts to the student.",
            directional=False,
        ):
            return
        self.screen_btn.setText("Cancel Screen Capture")
        self._screen_cap = ScreenCaptureThread(region, fps=5.0, parent=self)
        self._screen_cap.frame_ready.connect(self._on_frame)
        self._screen_cap.capture_error.connect(self._on_camera_error)
        self._screen_cap.start()

    def _begin_capture(self, prompt: str, directional: bool) -> bool:
        if not FaceEngine.is_ready():
            QMessageBox.information(
                self, "Please wait", "AI models are still loading, try again shortly."
            )
            return False
        self._samples = []
        self._capturing = True
        self.save_btn.setEnabled(False)
        self.progress_label.setText(prompt)

        self._mesh = FaceMeshTracker()
        self._guided = GuidedEnrollment(
            self._mesh, self._embed_largest, directional=directional
        )
        self._worker = AnalysisWorker(self)
        self._worker.set_processor(self._process_frame)
        self._worker.result.connect(self._on_result)
        self._worker.start()
        return True

    @staticmethod
    def _embed_largest(frame: np.ndarray) -> np.ndarray | None:
        face = FaceEngine.instance().largest_face(frame)
        if face is None or float(face.det_score) < MIN_DET_SCORE:
            return None
        return np.asarray(face.normed_embedding, dtype=np.float32)

    def _stop_capture(self) -> None:
        self._capturing = False
        if self._camera is not None:
            self._camera.stop()
            self._camera = None
        if self._screen_cap is not None:
            self._screen_cap.stop()
            self._screen_cap = None
        if self._worker is not None:
            self._worker.stop()
            self._worker = None
        if self._mesh is not None:
            self._mesh.close()
            self._mesh = None
        self._guided = None
        self.capture_btn.setText("Start Face Capture (webcam)")
        self.screen_btn.setText("Capture from Screen (Meet tile)")
        self.video.show_idle()

    def _on_frame(self, frame: np.ndarray) -> None:
        self.video.show_frame(frame)
        if self._worker is not None:
            self._worker.submit(frame)

    def _on_camera_error(self, message: str) -> None:
        if self._capturing:
            self.progress_label.setText(f"Camera problem: {message}")

    def _process_frame(self, frame: np.ndarray) -> dict | None:
        """Analysis thread: advance the guided pose challenge with one frame."""
        guided = self._guided  # local ref: GUI thread may clear it mid-frame
        if guided is None:
            return None
        return guided.process(frame)

    def _on_result(self, result: dict) -> None:
        if not self._capturing or self._guided is None:
            return
        if result["done"]:
            self._samples = list(self._guided.samples)
            n = len(self._samples)
            self._stop_capture()
            self.progress_label.setText(
                f"{n} samples captured. Fill in the details and press Save."
            )
            self.save_btn.setEnabled(True)
            return
        self.progress_label.setText(
            f"Step {result['count'] + 1}/{result['total']}: {result['prompt']}"
        )

    # -------------------------------------------------------- photo import

    def _import_photos(self) -> None:
        """Enroll a remote student from photos they sent (no webcam needed)."""
        if not FaceEngine.is_ready():
            QMessageBox.information(
                self, "Please wait", "AI models are still loading, try again shortly."
            )
            return
        if self._capturing:
            self._stop_capture()

        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose 1-5 clear photos of the student", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if not paths:
            return

        engine = FaceEngine.instance()
        embeddings, skipped = [], []
        preview = None
        for path in paths[:5]:
            # unicode-safe read (plain cv2.imread breaks on non-ASCII paths on Windows)
            data = np.fromfile(path, dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if img is None:
                skipped.append(path)
                continue
            face = engine.largest_face(img)
            if face is None or float(face.det_score) < MIN_DET_SCORE:
                skipped.append(path)
                continue
            embeddings.append(np.asarray(face.normed_embedding, dtype=np.float32))
            if preview is None:
                preview = img

        if not embeddings:
            QMessageBox.warning(
                self, "No usable face",
                "Could not find a clear face in the selected photos. "
                "Ask the student for a sharper, front-facing photo.",
            )
            return

        self._samples = embeddings
        if preview is not None:
            self.video.show_frame(preview)
        msg = f"Face data extracted from {len(embeddings)} photo(s)."
        if skipped:
            msg += f" Skipped {len(skipped)} photo(s) without a clear face."
        msg += " Fill in the details and press Save."
        self.progress_label.setText(msg)
        self.save_btn.setEnabled(True)

    # -------------------------------------------------------------- saving

    def _save_student(self) -> None:
        student_no = self.student_no_edit.text().strip()
        name = self.name_edit.text().strip()
        if not student_no or not name:
            QMessageBox.warning(self, "Missing info", "Enter both student number and name.")
            return
        if not self._samples:
            QMessageBox.warning(
                self, "No face data", "Capture face samples or import photos first."
            )
            return

        mean = np.mean(np.stack(self._samples), axis=0)
        mean /= np.linalg.norm(mean)
        try:
            db.add_student(student_no, name, mean.astype(np.float32))
        except Exception as exc:  # duplicate student_no, etc.
            QMessageBox.critical(self, "Could not save", str(exc))
            return

        self._samples = []
        self.save_btn.setEnabled(False)
        self.student_no_edit.clear()
        self.name_edit.clear()
        self.progress_label.setText(f"{name} registered successfully.")
        self.refresh_students()

    def refresh_students(self) -> None:
        self.students_list.clear()
        self._students_cache = db.list_students()
        for s in self._students_cache:
            self.students_list.addItem(f"{s['student_no']}  —  {s['name']}")

    def _delete_selected(self) -> None:
        row = self.students_list.currentRow()
        if row < 0 or row >= len(self._students_cache):
            QMessageBox.information(
                self, "No selection", "Select a student in the list first."
            )
            return
        student = self._students_cache[row]
        answer = QMessageBox.question(
            self, "Delete student",
            f"Delete {student['name']} ({student['student_no']})?\n\n"
            "Their face data and attendance history will be removed.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        db.delete_student(student["id"])
        self.refresh_students()
        self.progress_label.setText(f"{student['name']} deleted.")

    # ---------------------------------------------------------- lifecycle

    def activate(self) -> None:
        """Called when the tab becomes visible; students may have been
        enrolled elsewhere (e.g. from a live Meet call)."""
        self.refresh_students()

    def deactivate(self) -> None:
        """Called when the user switches away from this tab."""
        if self._capturing:
            self._stop_capture()
            self.progress_label.setText("Capture cancelled (left the page).")
