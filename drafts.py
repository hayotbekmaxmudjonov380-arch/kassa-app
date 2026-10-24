"""Kiritilgan lekin hali tasdiqlanmagan (Enter bosilmagan) qatorlarni saqlash.

Muammo: kassir summani katakchaga yozadi, lekin chiroq o'chib qoladi yoki
kompyuter ishlamay qoladi. Enter bosilmagan summa faqat RAM'da turadi va
yo'qoladi.

Yechim: har bir o'zgarishda yozilgan holat (draft) JSON faylga saqlanadi.
Dastur qayta ishga tushganda o'sha fayl o'qilib, kiritilmagan qatorlarni
qaytaradi — kassir yana Enter bosadi va yozuv saqlanadi.

Fayl sinxronlanmaydigan joyda (LOCALAPPDATA) saqlanadi va yozish
atomik (vaqtinchalik fayl -> almashtirish) amalga oshiriladi.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import threading
from typing import Any, Dict, List, Optional

from config import DRAFTS_PATH

# Bu maydonlar o'z-o'zicha qatorni "to'ldirilgan" qilmaydi: eski versiyalar
# bo'sh qatorlarga ham Vaqt yozib qo'yardi (masalan "11:44").
_META_KEYS = ("vt", "rid", "sr", "ts")


def _meaningful_rows(rows: List[Any]) -> List[Dict[str, Any]]:
    """Faqat haqiqiy qiymati yoki ochilgan qator belgisi bor qatorlarni qoldiradi."""
    cleaned: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        has_value = any(
            str(value or "").strip()
            for key, value in row.items()
            if key not in _META_KEYS
        )
        has_row = bool(
            str(row.get("rid") or "").strip() or str(row.get("sr") or "").strip()
        )
        if has_value or has_row:
            cleaned.append(row)
    return cleaned


class DraftStore:
    """Kiritilmagan qatorlarni faylda saqlaydi (thread-safe, atomik yozish)."""

    def __init__(self, path: str = DRAFTS_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {"rows": [], "kassir": "", "sana": ""}
        self.load()

    # ------------------------------------------------------------- o'qish/yozish
    def load(self) -> Dict[str, Any]:
        """Fayldan o'qiydi; buzilgan bo'lsa bo'sh holatga qaytadi."""
        with self._lock:
            data: Dict[str, Any] = {"rows": [], "kassir": "", "sana": ""}
            try:
                if os.path.exists(self.path):
                    with open(self.path, "r", encoding="utf-8") as fh:
                        raw = json.load(fh)
                    if isinstance(raw, dict):
                        rows = raw.get("rows")
                        if isinstance(rows, list):
                            data["rows"] = _meaningful_rows(rows)
                        data["kassir"] = str(raw.get("kassir") or "")
                        data["sana"] = str(raw.get("sana") or "")
            except (OSError, ValueError):
                pass
            self._data = data
            return dict(self._data)

    def save(self, rows: List[Dict[str, Any]], kassir: str = "", sana: str = "") -> bool:
        """Atomik ravishda saqlaydi (vaqtinchalik fayl -> almashtirish)."""
        with self._lock:
            payload = {
                "rows": [r for r in rows if isinstance(r, dict)],
                "kassir": str(kassir or ""),
                "sana": str(sana or ""),
                "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
            }
            folder = os.path.dirname(os.path.abspath(self.path))
            tmp_path = ""
            try:
                if folder:
                    os.makedirs(folder, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(
                    dir=folder or None, prefix=".drafts_", suffix=".tmp"
                )
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                    fh.flush()
                    # Diskka to'liq yozilishini kutamiz — chiroq uzilsa ham fayl butun.
                    os.fsync(fh.fileno())
                os.replace(tmp_path, self.path)
                self._data = payload
                return True
            except OSError:
                if tmp_path:
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                return False

    # -------------------------------------------------------------------- qidiruv
    def rows(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._data.get("rows", [])]

    def has_content(self) -> bool:
        """Saqlangan qatorlarda biror ma'lumot bormi?"""
        with self._lock:
            for row in self._data.get("rows", []):
                for key, value in row.items():
                    if key in ("vt", "rid", "sr", "ts"):
                        continue
                    if str(value or "").strip():
                        return True
            return False

    def clear(self) -> None:
        """Draftlarni tozalaydi (yozuv saqlangandan keyin)."""
        with self._lock:
            self._data = {"rows": [], "kassir": "", "sana": ""}
            try:
                if os.path.exists(self.path):
                    os.remove(self.path)
            except OSError:
                pass


__all__ = ["DraftStore"]
