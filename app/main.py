"""Entry point: python -m app.main"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.data import db

STYLE = """
QMainWindow, QWidget { background: #1a202c; color: #e2e8f0; font-size: 14px; }
QTabWidget::pane { border: 1px solid #2d3748; border-radius: 4px; }
QTabBar::tab {
    background: #2d3748; color: #a0aec0; padding: 8px 24px;
    border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px;
}
QTabBar::tab:selected { background: #4a5568; color: #ffffff; }
QGroupBox {
    border: 1px solid #4a5568; border-radius: 6px; margin-top: 12px;
    padding-top: 8px; font-weight: bold;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QLineEdit, QComboBox {
    background: #2d3748; border: 1px solid #4a5568; border-radius: 4px;
    padding: 6px; color: #e2e8f0;
}
QComboBox QAbstractItemView { background: #2d3748; color: #e2e8f0; }
QPushButton {
    background: #3182ce; color: white; border: none; border-radius: 4px;
    padding: 8px 16px; font-weight: bold;
}
QPushButton:hover { background: #2b6cb0; }
QPushButton:disabled { background: #4a5568; color: #a0aec0; }
QListWidget, QTableWidget {
    background: #2d3748; border: 1px solid #4a5568; border-radius: 4px;
}
QHeaderView::section {
    background: #4a5568; color: #e2e8f0; padding: 6px; border: none;
}
QTableWidget::item { padding: 4px; }
QLabel#videoPreview {
    background: #0f141c; border: 2px solid #4a5568; border-radius: 8px;
    color: #718096; font-size: 16px;
}
QStatusBar { background: #2d3748; color: #a0aec0; }
"""


def main() -> None:
    db.init_db()
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)

    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
