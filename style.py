"""Dizayn tokenlari va global uslublar (Qt Style Sheets) — Qog'oz kassa jurnali uslubi."""

from __future__ import annotations

# ---------------------------------------------------------------- tokenlar
COLORS = {
    "bg": "#F1F5F9",
    "surface": "#FFFFFF",
    "surface_alt": "#F8FAFC",
    "border": "#CBD5E1",
    "border_strong": "#94A3B8",
    "text": "#0F172A",
    "text_muted": "#475569",
    "text_faint": "#94A3B8",
    "header_bg": "#1E293B",
    "header_text": "#F8FAFC",
    "gold": "#D97706",
    "gold_dark": "#B45309",
    "gold_soft": "#FEF3C7",
    "green": "#16A34A",
    "green_dark": "#15803D",
    "green_soft": "#DCFCE7",
    "red": "#DC2626",
    "red_dark": "#B91C1C",
    "red_soft": "#FEE2E2",
    "amber": "#D97706",
    "amber_soft": "#FEF3C7",
    "input_bg": "#FFFFFF",
}

SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}

RADIUS = 8
RADIUS_SM = 5

FONT_UI = "Segoe UI"
FONT_NUM = "Segoe UI"


def _c(name: str) -> str:
    return COLORS[name]


def qss() -> str:
    """Qog'oz kassa jurnali uslublari (QSS)."""
    return f"""
* {{
    font-family: "{FONT_UI}";
    font-size: 14px;
    color: {_c('text')};
}}

QWidget#root {{
    background-color: {_c('bg')};
}}

/* ---------------------------------------------------------- sarlavha paneli */
QWidget#header {{
    background-color: {_c('header_bg')};
    border: none;
}}
QLabel#brandTitle {{
    color: {_c('gold')};
    font-size: 20px;
    font-weight: 700;
}}
QLabel#brandSubtitle {{
    color: #94A3B8;
    font-size: 13px;
}}
QLabel#headerLabel {{
    color: {_c('header_text')};
    font-size: 15px;
    font-weight: 600;
}}
QLabel#headerDate {{
    color: {_c('gold')};
    font-size: 18px;
    font-weight: 700;
}}
QLabel#headerClock {{
    color: #94A3B8;
    font-size: 13px;
}}
QPushButton#headerButton {{
    background-color: transparent;
    border: 1px solid #475569;
    border-radius: {RADIUS_SM}px;
    color: {_c('header_text')};
    padding: 6px 14px;
}}
QPushButton#headerButton:hover {{
    background-color: #334155;
    border-color: {_c('gold')};
}}

/* ------------------------------------------------------------- kartalar */
QFrame#card {{
    background-color: {_c('surface')};
    border: 1px solid {_c('border')};
    border-radius: {RADIUS}px;
}}
QLabel#cardTitle {{
    font-size: 12px;
    font-weight: 700;
    color: {_c('text_muted')};
    letter-spacing: 1px;
}}
QLabel#fieldLabel {{
    color: {_c('text_muted')};
    font-size: 13px;
    font-weight: 600;
}}
QLabel#hint {{
    color: {_c('text_faint')};
    font-size: 12px;
}}

/* --------------------------------------------------------------- kiritish */
QLineEdit {{
    background-color: {_c('input_bg')};
    border: 1px solid {_c('border_strong')};
    border-radius: {RADIUS_SM}px;
    padding: 5px 9px;
    min-height: 22px;
    selection-background-color: {_c('gold_soft')};
    selection-color: {_c('text')};
}}
QLineEdit:focus {{
    border: 2px solid {_c('gold')};
}}
QLineEdit#kassirEdit {{
    font-size: 15px;
    font-weight: 600;
    background-color: #F8FAFC;
    color: {_c('text')};
}}

/* ------------------------------------------------------------- tugmalar */
QPushButton#closeDayButton {{
    background-color: {_c('red')};
    color: #FFFFFF;
    border: 1px solid {_c('red_dark')};
    border-radius: {RADIUS_SM}px;
    font-size: 15px;
    font-weight: 700;
    padding: 9px 18px;
    min-height: 24px;
}}
QPushButton#closeDayButton:hover {{
    background-color: {_c('red_dark')};
}}
QPushButton#closeDayButton:pressed {{
    background-color: #991B1B;
}}

/* --------------------------------------------------------- qog'oz jadval */
QTableWidget#journalTable, QTableWidget#summaryTable {{
    background-color: {_c('surface')};
    border: 1px solid {_c('border')};
    gridline-color: {_c('border')};
    font-size: 13px;
    outline: none;
}}
QTableWidget::item {{
    padding: 4px 6px;
    border: none;
}}
QTableWidget::item:selected {{
    background-color: {_c('gold_soft')};
    color: {_c('text')};
}}

QHeaderView::section {{
    background-color: #E2E8F0;
    color: #1E293B;
    border: none;
    border-bottom: 2px solid {_c('border_strong')};
    border-right: 1px solid {_c('border')};
    padding: 6px 4px;
    font-size: 12px;
    font-weight: 700;
}}

/* ----------------------------------------------------------------- holat */
QLabel#statusText {{
    color: {_c('text_muted')};
    font-size: 13px;
}}
QLabel#toast {{
    border-radius: {RADIUS_SM}px;
    padding: 8px 14px;
    font-weight: 600;
    background-color: {_c('green_soft')};
    border: 1px solid {_c('green')};
    color: {_c('green_dark')};
}}
QLabel#toast[level="error"] {{
    background-color: {_c('red_soft')};
    border-color: {_c('red')};
    color: {_c('red_dark')};
}}
QLabel#toast[level="warn"] {{
    background-color: {_c('amber_soft')};
    border-color: {_c('amber')};
    color: {_c('amber')};
}}
QLabel#toast[level="info"] {{
    background-color: {_c('gold_soft')};
    border-color: {_c('gold')};
    color: {_c('gold_dark')};
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {_c('border_strong')};
    border-radius: 5px;
    min-height: 28px;
}}
"""


__all__ = ["COLORS", "SPACE", "RADIUS", "RADIUS_SM", "FONT_UI", "qss"]
