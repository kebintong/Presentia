"""Main window: tabbed navigation between Register, Session and Reports."""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QTabWidget

from app.core.workers import EngineLoader
from app.ui.meet_page import MeetPage
from app.ui.register_page import RegisterPage
from app.ui.reports_page import ReportsPage
from app.ui.session_page import SessionPage


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Virtual Classroom Attendance — Alpha")
        self.resize(1100, 700)

        self.register_page = RegisterPage()
        self.meet_page = MeetPage()
        self.session_page = SessionPage()
        self.reports_page = ReportsPage()

        self.tabs = QTabWidget()
        self.tabs.addTab(self.register_page, "Register")
        self.tabs.addTab(self.meet_page, "Meet Monitor")
        self.tabs.addTab(self.session_page, "Webcam Session")
        self.tabs.addTab(self.reports_page, "Reports")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._prev_tab = 0
        self.setCentralWidget(self.tabs)

        self.statusBar().showMessage(
            "Loading AI models (first run downloads ~300 MB)…"
        )
        self._loader = EngineLoader(self)
        self._loader.ready.connect(self._on_engine_ready)
        self._loader.error.connect(self._on_engine_error)
        self._loader.start()

    def _on_engine_ready(self) -> None:
        self.statusBar().showMessage("AI models loaded — ready.", 10000)

    def _on_engine_error(self, message: str) -> None:
        self.statusBar().showMessage(f"Failed to load AI models: {message}")

    def _on_tab_changed(self, index: int) -> None:
        pages = [self.register_page, self.meet_page, self.session_page,
                 self.reports_page]
        prev = pages[self._prev_tab]
        if hasattr(prev, "deactivate"):
            prev.deactivate()
        current = pages[index]
        if hasattr(current, "activate"):
            current.activate()
        if current is self.session_page:
            self.session_page.refresh_students()
        self._prev_tab = index

    def closeEvent(self, event) -> None:
        self.meet_page.shutdown()
        self.session_page.shutdown()
        self.register_page.deactivate()
        super().closeEvent(event)
