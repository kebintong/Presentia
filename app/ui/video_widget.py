"""Shared camera preview widget."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel


class VideoWidget(QLabel):
    """Displays BGR frames from the camera thread."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 360)
        self.setAlignment(Qt.AlignCenter)
        self.setObjectName("videoPreview")
        self.show_idle()

    def show_idle(self, text: str = "Camera is off") -> None:
        self.setPixmap(QPixmap())
        self.setText(text)

    def show_frame(self, frame_bgr: np.ndarray) -> None:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w, _ = rgb.shape
        image = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(image).scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.setText("")
        self.setPixmap(pix)
