"""Kichik yordamchi funksiyalar: sana, vaqt, summa formati."""

import datetime as dt
from typing import Optional

DATE_FORMAT = "%d.%m.%Y"
TIME_FORMAT = "%H:%M:%S"
LIST_TIME_FORMAT = "%H:%M"


def today_str(now: Optional[dt.datetime] = None) -> str:
    return (now or dt.datetime.now()).strftime(DATE_FORMAT)


def now_time_str(now: Optional[dt.datetime] = None) -> str:
    return (now or dt.datetime.now()).strftime(TIME_FORMAT)


def now_iso(now: Optional[dt.datetime] = None) -> str:
    return (now or dt.datetime.now()).isoformat(timespec="seconds")


def list_time(vaqt: str) -> str:
    """'14:05:09' -> '14:05' (ro'yxat uchun qisqa ko'rinish)."""
    parts = vaqt.split(":")
    return ":".join(parts[:2]) if len(parts) >= 2 else vaqt


def fmt_money(value: float) -> str:
    """1250000 -> '1 250 000' (minglik ajratgich — joy)."""
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ")


def parse_summa(text: str) -> Optional[float]:
    """'1 250 000,5' yoki '1250000.5' -> 1250000.5. Noto'g'ri kiritsa None."""
    if text is None:
        return None
    cleaned = (
        text.replace("\u00a0", "")
        .replace(" ", "")
        .replace("so'm", "")
        .replace("сўм", "")
        .replace(",", ".")
        .strip()
    )
    if not cleaned or cleaned in {".", "-", "+"}:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value
