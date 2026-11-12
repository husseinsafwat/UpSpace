"""UpSpace booking & billing desktop app (Qt / PySide6 + SQLite).

Run:  python main.py
"""
from __future__ import annotations

import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from config import DB_FILENAME
from db import Database
from ui import theme
from ui.window import MainWindow


def resource_dir() -> str:
    """Folder with assets (works for `python main.py` and a PyInstaller .exe)."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    """Folder where the database lives.

    Installed app (.exe): %LOCALAPPDATA%\\UpSpace\\data  (survives updates and uninstall).
    Running from source: next to main.py.
    """
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "UpSpace", "data")
        os.makedirs(path, exist_ok=True)
        return path
    return os.path.dirname(os.path.abspath(__file__))


ASSETS = os.path.join(resource_dir(), "assets")


def create_app(db_path: str | None = None):
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("UpSpace")
    app.setStyle("Fusion")                 # same look on every Windows version
    theme.load_fonts(ASSETS)
    app.setFont(theme.base_font())
    app.setStyleSheet(theme.stylesheet())
    icon = os.path.join(ASSETS, "upspace.ico")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    db = Database(db_path or os.path.join(data_dir(), DB_FILENAME))
    return app, MainWindow(db, ASSETS)


def main():
    if sys.platform == "win32":            # own taskbar icon instead of Python's
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("UpSpace.Bookings")
        except Exception:
            pass
    app, win = create_app()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
