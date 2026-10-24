"""Brend: logotip faylidan oyna/taskbar ikonkasi va yumaloq nishon yasash."""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap

from config import LOGO_PATH
from style import FONT_UI

ICON_SIZES: List[int] = [16, 24, 32, 48, 64, 128, 256]

# Kichik o'lchamlarda logotip tafsilotlari yo'qoladi — o'qiladigan "G" belgisi.
SMALL_ICON_MAX = 32
GOLD = "#C9A227"
INK = "#1B1F27"


def logo_pixmap(size: int, radius: int = 0) -> QPixmap:
    """`image/logotip.jpg` dan kvadrat (markazdan kesilgan) rasm.

    `radius` berilsa burchaklar yumaloqlanadi (sarlavha nishoni uchun).
    Fayl bo'lmasa bo'sh QPixmap qaytadi.
    """
    source = QPixmap(LOGO_PATH)
    if source.isNull():
        return QPixmap()

    scaled = source.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - size) // 2)
    y = max(0, (scaled.height() - size) // 2)
    scaled = scaled.copy(x, y, size, size)
    if radius <= 0:
        return scaled

    rounded = QPixmap(size, size)
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, scaled)
    finally:
        painter.end()
    return rounded


def letter_mark(size: int) -> QPixmap:
    """Kichik ikonka: to'q fon ustida oltin "G" (16-32 px uchun o'qiladi)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        inset = max(1.0, size * 0.03)
        radius = size * 0.22
        painter.setBrush(QColor(INK))
        painter.setPen(QColor(GOLD))
        painter.drawRoundedRect(
            QRectF(inset, inset, size - 2 * inset, size - 2 * inset), radius, radius
        )
        font = QFont(FONT_UI)
        font.setBold(True)
        font.setPixelSize(max(8, int(size * 0.62)))
        painter.setFont(font)
        painter.setPen(QColor(GOLD))
        painter.drawText(
            QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "G"
        )
    finally:
        painter.end()
    return pixmap


def icon_pixmap(size: int) -> QPixmap:
    """Ikonka uchun rasm: kichikda "G", kattada haqiqiy logotip."""
    if size <= SMALL_ICON_MAX:
        return letter_mark(size)
    pixmap = logo_pixmap(size)
    return pixmap if not pixmap.isNull() else letter_mark(size)


def app_icon(fallback: Optional[QIcon] = None) -> QIcon:
    """Logotipdan ko'p o'lchamli ikonka (16..256 px)."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(icon_pixmap(size))
    if icon.isNull() and fallback is not None:
        return fallback
    return icon
