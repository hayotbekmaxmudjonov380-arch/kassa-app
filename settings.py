"""Dastur sozlamalari (settings.json).

Saqlanadigan ma'lumotlar:
  - spreadsheet_id / worksheet_name  — qaysi jadvalga yoziladi
  - credentials_file                — service account JSON kaliti
  - last_kassir                    — oxirgi kiritilgan kassir ismi
  - auto_create_sheet              — varaq yo'q bo'lsa yaratish

Fayl JSON formatida, inson o'qiy oladigan holda yoziladi.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from typing import Any, Dict

from config import (
    SERVICE_ACCOUNT_FILE,
    SETTINGS_PATH,
    SPREADSHEET_ID,
    WORKSHEET_NAME,
)

# Eski (1.0) jadval ID'si — sozlamalarda qolib ketgan bo'lsa, yangisiga
# avtomatik o'tamiz (aks holda yozuvlar eski jadvalga ketardi).
LEGACY_SPREADSHEET_IDS = {"1g3sb3swmuWLHGuMt0_hoYwIf0myHH8q-pKRF5l9vAn8"}

DEFAULTS: Dict[str, Any] = {
    "spreadsheet_id": SPREADSHEET_ID,
    "worksheet_name": WORKSHEET_NAME,
    "credentials_file": SERVICE_ACCOUNT_FILE,
    "last_kassir": "",
    "auto_create_sheet": True,
    "last_update_check": "",
}


class AppSettings:
    """Sozlamalarni o'qiydi, saqlaydi va xotirada ushlab turadi."""

    def __init__(self, path: str = SETTINGS_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = dict(DEFAULTS)
        self.load()

    # ------------------------------------------------------------- o'qish/saqlash
    def load(self) -> None:
        """Fayldan o'qiydi; buzilgan bo'lsa standart qiymatlarga qaytadi."""
        with self._lock:
            data = dict(DEFAULTS)
            try:
                if os.path.exists(self.path):
                    with open(self.path, "r", encoding="utf-8") as fh:
                        raw = json.load(fh)
                    if isinstance(raw, dict):
                        for key in DEFAULTS:
                            if key in raw:
                                data[key] = raw[key]
            except (OSError, ValueError):
                pass
            if str(data.get("spreadsheet_id") or "") in LEGACY_SPREADSHEET_IDS:
                data["spreadsheet_id"] = SPREADSHEET_ID
            self._data = data

    def save(self) -> bool:
        """Atomik ravishda yozadi (vaqtinchalik fayl -> almashtirish)."""
        with self._lock:
            folder = os.path.dirname(os.path.abspath(self.path))
            try:
                if folder:
                    os.makedirs(folder, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(
                    dir=folder or None, prefix=".settings_", suffix=".tmp"
                )
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self._data, fh, ensure_ascii=False, indent=2)
                    fh.flush()
                    # Diskka to'liq yozilishini kutamiz: chiroq uzilsa ham fayl
                    # bo'sh yoki buzilgan holda qolmasin.
                    os.fsync(fh.fileno())
                os.replace(tmp_path, self.path)
                return True
            except OSError:
                return False

    # ------------------------------------------------------------------ qiymatlar
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._data:
                return self._data[key]
            return DEFAULTS.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    # ------------------------------------------------------------------ qulaylik
    @property
    def spreadsheet_id(self) -> str:
        value = str(self.get("spreadsheet_id") or SPREADSHEET_ID)
        if value in LEGACY_SPREADSHEET_IDS:
            return SPREADSHEET_ID
        return value

    @property
    def worksheet_name(self) -> str:
        return str(self.get("worksheet_name") or WORKSHEET_NAME)

    @property
    def credentials_file(self) -> str:
        path = str(self.get("credentials_file") or SERVICE_ACCOUNT_FILE)
        # Sozlamalar boshqa kompyuterdan ko'chirilgan bo'lishi mumkin —
        # unda eski absolyut yo'l (masalan 'C:\Users\boshqa\...') qolgan
        # bo'ladi. U fayl mavjud bo'lmasa, dastur yonidagi standart
        # service_account.json ga qaytamiz — shunda kalitni papkaga
        # tashlash kifoya, qo'lda sozlama tuzatish shart emas.
        if not os.path.isfile(path) and os.path.isfile(SERVICE_ACCOUNT_FILE):
            return SERVICE_ACCOUNT_FILE
        return path

    @property
    def auto_create_sheet(self) -> bool:
        return bool(self.get("auto_create_sheet", True))

    @property
    def last_kassir(self) -> str:
        return str(self.get("last_kassir") or "")

    def set_credentials_from(self, source_path: str) -> str:
        """Tanlangan kalit faylni dastur papkasiga ko'chiradi va yo'lini saqlaydi.

        Natija: bitta .exe fayl ishlashiga yetarli — kalit fayl uning yonida.
        """
        source = os.path.abspath(source_path)
        if not os.path.isfile(source):
            raise FileNotFoundError(f"Kalit fayl topilmadi: {source}")

        target = SERVICE_ACCOUNT_FILE
        if os.path.abspath(source) != os.path.abspath(target):
            folder = os.path.dirname(os.path.abspath(target))
            if folder:
                os.makedirs(folder, exist_ok=True)
            shutil.copyfile(source, target)

        self.set("credentials_file", target)
        return target


__all__ = ["AppSettings", "SETTINGS_PATH", "DEFAULTS"]
