"""Testlar uchun umumiy sozlash.

Bitta offscreen QApplication yaratiladi — oyna ko'rinmasdan ishlaydi.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
