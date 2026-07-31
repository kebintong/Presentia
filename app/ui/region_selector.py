"""Full-screen rubber-band selector to mark the Google Meet area on screen."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget


class RegionSelector(QWidget):
    """Dimmed full-screen overlay; drag to select a region, Esc to cancel.

    Emits `region_selected` with an mss-style dict in global screen
    coordinates: {left, top, width, height}.
    """

    region_selected = Signal(dict)
    cancelled = Signal()

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        # span all screens
        geo = QRect()
        for screen in QGuiApplication.screens():
            geo = geo.united(screen.geometry())
        self.setGeometry(geo)
        self._origin: QPoint | None = None
        self._current: QPoint | None = None

    def open(self) -> None:
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------- events

    def mousePressEvent(self, event) -> None:
        self._origin = event.globalPosition().toPoint()
        self._current = self._origin
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._origin is not None:
            self._current = event.globalPosition().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._origin is None:
            return
        rect = QRect(self._origin, event.globalPosition().toPoint()).normalized()
        self.close()
        if rect.width() < 50 or rect.height() < 50:
            self.cancelled.emit()
            return
        self.region_selected.emit({
            "left": rect.left(), "top": rect.top(),
            "width": rect.width(), "height": rect.height(),
        })

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            self.cancelled.emit()

    # ------------------------------------------------------------ painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if self._origin is not None and self._current is not None:
            # map global coords to this widget's local space
            top_left = self.mapFromGlobal(self._origin)
            bottom_right = self.mapFromGlobal(self._current)
            sel = QRect(top_left, bottom_right).normalized()
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(sel, Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setPen(QPen(QColor("#3182ce"), 2))
            painter.drawRect(sel)
        else:
            painter.setPen(QColor("#e2e8f0"))
            painter.drawText(
                self.rect(), Qt.AlignCenter,
                "Drag to select the Google Meet area  •  Esc to cancel",
            )
