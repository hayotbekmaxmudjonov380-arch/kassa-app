"""Bazani davriy yedeklash (chiroq uzilishi va kompyuter o'chishidan himoya).

Yedek nusxalar DATA_DIR/backups papkasiga saqlanadi. Agar dastur papkasi
OneDrive ichida bo'lsa, nusxalar avtomatik ravishda bulutga boradi — ya'ni
dastur kompyuteri butunlay buzilganda ham ma'lumot qoladi.

Nusxa SQLite'ning o'z `backup()` API'si orqali olinadi: faylni bloklamaydi
va chiroq uzilib ketganda ham nusxa butun bo'ladi.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import threading
from typing import List, Optional

from config import (
    BACKUP_DIR,
    BACKUP_INTERVAL_MIN,
    BACKUP_KEEP,
    BACKUP_PREFIX,
)
from storage import Storage

_STAMP_RE = re.compile(re.escape(BACKUP_PREFIX) + r"\d{8}_\d{6}\.db$")


def backup_name(moment: Optional[dt.datetime] = None) -> str:
    """Vaqt tamgisini o'z ichiga olgan nusxa fayl nomi."""
    stamp = (moment or dt.datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{BACKUP_PREFIX}{stamp}.db"


def list_backups(folder: str = BACKUP_DIR) -> List[str]:
    """Saqlangan nusxa fayllarini (eskirganidan yangisiga) qaytaradi."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    files = [n for n in names if _STAMP_RE.match(n)]
    files.sort(reverse=True)
    return [os.path.join(folder, n) for n in files]


def prune_backups(keep: int = BACKUP_KEEP, folder: str = BACKUP_DIR) -> int:
    """Eski nusxalarni o'chiradi, faqat `keep` ta qoldiradi. O'chirilgan soni."""
    files = list_backups(folder)
    removed = 0
    for path in files[max(0, int(keep)):]:
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def make_backup(storage: Storage, folder: str = BACKUP_DIR) -> Optional[str]:
    """Yangi yedek nusxa oladi. Muvaffaqiyat bo'lsa fayl yo'lini qaytaradi."""
    path = os.path.join(folder, backup_name())
    if storage.backup_to(path):
        prune_backups(folder=folder)
        return path
    return None


class BackupService:
    """Orqa fonda davriy yedek oladi (timer orqali chaqiriladi).

    Xatolar ushlanadi — yedeklash dasturni hech qachon to'xtatmaydi.

    `folder` — nusxalar saqlanadigan papka. Testlar o'z papkasini berib,
    haqiqiy foydalanuvchi papkasini ifloslantirmaydi.
    """

    def __init__(
        self,
        storage: Storage,
        interval_min: int = BACKUP_INTERVAL_MIN,
        folder: str = BACKUP_DIR,
    ):
        self.storage = storage
        self.interval_min = max(1, int(interval_min))
        self.folder = folder
        self._lock = threading.Lock()
        self._last_at: Optional[dt.datetime] = None
        self.last_path: Optional[str] = None
        self.last_error: str = ""

    def due(self, now: Optional[dt.datetime] = None) -> bool:
        """Yedek vaqti kelganmi?"""
        moment = now or dt.datetime.now()
        if self._last_at is None:
            return True
        return (moment - self._last_at).total_seconds() >= self.interval_min * 60

    def run(self, force: bool = False) -> Optional[str]:
        """Yedek oladi. `force=True` bo'lsa vaqtni tekshirmaydi."""
        with self._lock:
            if not force and not self.due():
                return None
            try:
                path = make_backup(self.storage, folder=self.folder)
            except Exception as exc:  # noqa: BLE001 — yedeklash hech qachon dasturni to'xtatmasin
                self.last_error = str(exc)
                return None
            if path is None:
                self.last_error = "Nusxa yaratib bo'lmadi."
                return None
            self._last_at = dt.datetime.now()
            self.last_path = path
            self.last_error = ""
            return path


__all__ = [
    "BackupService",
    "backup_name",
    "list_backups",
    "make_backup",
    "prune_backups",
]
