"""GOLD restobar — Kassa jurnali.

Ishga tushirish:
    python main.py

Bitta .exe yasash: README.md ga qarang (build.bat yoki kassa.spec).
"""

from __future__ import annotations

import logging
import sys

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMessageBox

from branding import app_icon
from config import APP_NAME, LOG_PATH
from settings import AppSettings
from sheets import SheetsClient
from storage import Storage
from style import FONT_UI, qss
from ui import MainWindow

logger = logging.getLogger("kassa")


def setup_logging() -> None:
    """Xatolar faylga yoziladi — muammoni keyin tekshirish uchun."""
    try:
        logging.basicConfig(
            filename=LOG_PATH,
            level=logging.WARNING,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            encoding="utf-8",
        )
    except OSError:
        pass


def install_excepthook() -> None:
    def handler(exc_type, exc_value, exc_tb) -> None:
        logger.critical("Kutilmagan xato", exc_info=(exc_type, exc_value, exc_tb))
        try:
            QMessageBox.critical(
                None,
                "Xato",
                "Kutilmagan xato yuz berdi.\n\n"
                f"{exc_value}\n\n"
                "Dastur ishlashda davom etadi. Batafsil ma'lumot xato.log faylida.",
            )
        except Exception:  # noqa: BLE001 — xato ko'rsatishda qulamaymiz
            pass

    sys.excepthook = handler


def make_icon() -> QIcon:
    """Kichik 'G' belgisi — oyna va taskbar uchun."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#1B1F27"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    painter.setPen(QColor("#C9A227"))
    painter.setFont(QFont(FONT_UI, 30, QFont.Weight.Bold))
    painter.drawText(QRect(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, "G")
    painter.end()
    return QIcon(pixmap)


def main() -> int:
    setup_logging()
    install_excepthook()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("GOLD")
    app.setStyle("Fusion")
    app.setFont(QFont(FONT_UI, 10))
    app.setStyleSheet(qss())

    # Avval `image/logotip.jpg`, u bo'lmasa — chizilgan "G" belgisi.
    icon = app_icon(fallback=make_icon())
    app.setWindowIcon(icon)

    settings = AppSettings()
    storage = Storage()
    client = SheetsClient(
        credentials_file=settings.credentials_file,
        spreadsheet_id=settings.spreadsheet_id,
        worksheet_name=settings.worksheet_name,
        auto_create_sheet=settings.auto_create_sheet,
    )

    window = MainWindow(storage, settings, client)
    window.setWindowIcon(icon)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
