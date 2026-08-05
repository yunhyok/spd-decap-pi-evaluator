"""SPD Decap PI Evaluator application entry point."""

from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ..version import APP_DISPLAY_NAME, ORGANIZATION_NAME
from .main_window import MainWindow


def main() -> int:
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName(APP_DISPLAY_NAME)
    application.setOrganizationName(ORGANIZATION_NAME)
    window = MainWindow()
    window.show()
    if "--smoke-test" in sys.argv:
        QTimer.singleShot(600, application.quit)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
