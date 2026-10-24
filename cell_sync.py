"""Katak darajasidagi fon sinxronizatsiyasi (Google Sheets).

Qoidalar:
  - Har bir o'zgargan katak alohida yoziladi (valueInputOption=RAW).
  - Qator birinchi marta tahrirlanganda A (#), B (Vaqt), J (Kassir) bir
    so'rovda yoziladi; shundan keyingina kataklar yuboriladi.
  - Internet yo'q bo'lsa katak SQLite navbatida qoladi va 25 soniyada
    qayta uriniladi. Ma'lumot hech qachon yo'qolmaydi.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from typing import Callable, Dict, Optional

from config import CELL_COLUMN, CELL_RETRY_SECONDS, SHEET_FIRST_DATA_ROW
from sheets import SheetsClient, friendly_error

PAYMENT_FIELD = {
    ("Kirim", "Naqd"): "kn",
    ("Kirim", "Click"): "kc",
    ("Kirim", "Terminal"): "kt",
    ("Chiqim", "Naqd"): "cn",
    ("Chiqim", "Click"): "cc",
}
NOTE_FIELD = {"Kirim": "ki", "Chiqim": "ci"}


class CellSyncService:
    def __init__(
        self,
        storage,
        client_factory: Optional[Callable[[], SheetsClient]] = None,
        retry_seconds: int = CELL_RETRY_SECONDS,
        seed_timeout: float = 3.0,
    ):
        self._storage = storage
        self._factory = client_factory or (lambda: SheetsClient())
        self._client = None
        self._retry_seconds = max(1, int(retry_seconds))
        self._seed_timeout = max(0.0, float(seed_timeout))
        self._sheet_lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._last_error = ""
        self._last_sync = ""
        self._seed_retry_at = 0.0

    # ------------------------------------------------------------- hayot davri
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="cell-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def notify(self) -> None:
        """Yangi ish bor — fon oqimini darhol uyg'otadi."""
        self._wake.set()

    def join(self, timeout: float = 2.0) -> None:
        """Fon oqimi tugashini kutadi (baza yopilishidan oldin)."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(max(0.0, float(timeout)))

    def invalidate_client(self) -> None:
        """Sozlamalar o'zgarganda klientni qayta yaratishga majbur qiladi."""
        self._client = None
        self._seed_retry_at = 0.0
        self.notify()

    def _run(self) -> None:
        try:
            self.ensure_seeded(timeout=8.0)
        except Exception as exc:  # noqa: BLE001 — baza yopilgan bo'lishi mumkin
            self._set_error(exc)
        while not self._stop.is_set():
            try:
                self.process_once()
            except Exception as exc:  # noqa: BLE001 — oqim hech qachon o'lmaydi
                self._set_error(exc)
            self._wake.wait(self._retry_seconds)
            self._wake.clear()

    # ---------------------------------------------------------------- holat
    def status(self) -> Dict:
        pending = (
            self._storage.pending_cell_count()
            + self._storage.unopened_row_count()
        )
        return {
            "connected": bool(self._connected),
            "pending": int(pending),
            "error": self._last_error or self._storage.last_cell_error(),
            "last_sync": self._last_sync,
        }

    def _set_ok(self) -> None:
        self._connected = True
        self._last_error = ""
        self._last_sync = time.strftime("%H:%M:%S")

    def _set_error(self, exc: BaseException) -> None:
        self._connected = False
        self._last_error = friendly_error(exc)

    def _has_pending(self) -> bool:
        return bool(
            self._storage.pending_cell_count()
            or self._storage.unopened_row_count()
        )

    # ---------------------------------------------------------------- ulanish
    def _get_client(self) -> SheetsClient:
        if self._client is None:
            self._client = self._factory()
        return self._client

    def _needs_seed(self) -> bool:
        try:
            return (
                self._storage.next_sheet_row() < SHEET_FIRST_DATA_ROW
                or self._storage.get_meta("seed_uncertain") == "1"
            )
        except Exception:  # noqa: BLE001 — baza yopilgan; ishlashni to'xtatamiz
            return True

    def _try_seed(self) -> bool:
        with self._sheet_lock:
            try:
                client = self._get_client()
                client.ensure_journal_headers()
                last = int(client.last_data_row() or 0)
                start = max(last + 1, SHEET_FIRST_DATA_ROW)
                if self._storage.get_meta("seed_uncertain") == "1":
                    moved = self._storage.reassign_unopened_rows(start)
                    if not moved:
                        self._storage.set_next_sheet_row(start)
                else:
                    self._storage.set_next_sheet_row(start)
                self._seed_retry_at = 0.0
                self._set_ok()
                return True
            except Exception as exc:  # noqa: BLE001
                self._set_error(exc)
                self._client = None
                return False

    def ensure_seeded(self, timeout: float = 5.0) -> bool:
        """Jadvaldagi oxirgi qatorni aniqlaydi (bir marta, keyin keshlanadi)."""
        if self._stop.is_set():
            return False
        if not self._needs_seed():
            return True
        # Xato bo'lsa 30 soniya davomida qayta urinmaymiz — kassirning
        # birinchi katagi har safar 3 soniya kutmasin.
        if time.time() < self._seed_retry_at:
            return False
        deadline = time.time() + max(0.0, timeout)
        while True:
            if self._try_seed():
                return True
            if self._stop.is_set() or time.time() >= deadline:
                self._seed_retry_at = time.time() + 30
                return False
            time.sleep(0.4)

    # ---------------------------------------------------------------- ish
    def process_once(self) -> None:
        """Bir marta navbatni bo'shatadi (fon oqimi ham, test ham chaqiradi)."""
        if self._stop.is_set():
            return
        if self._needs_seed() and not self._try_seed():
            return
        with self._sheet_lock:
            client = self._get_client()
            rows = self._storage.unopened_rows(limit=20)
            for row in rows:
                if self._stop.is_set():
                    return
                try:
                    client.open_row(
                        int(row["sheet_row"]),
                        row.get("number") or int(row["sheet_row"]) - 1,
                        row["timestamp_text"],
                        row["kassir"],
                    )
                except Exception as exc:  # noqa: BLE001
                    self._storage.mark_row_open_error(
                        int(row["id"]), friendly_error(exc)
                    )
                    self._set_error(exc)
                    return
                self._storage.mark_row_opened(int(row["id"]))
                self._set_ok()

            cells = self._storage.pending_cells(limit=50)
            for cell in cells:
                if self._stop.is_set():
                    return
                try:
                    client.update_cell(
                        cell["column_letter"],
                        int(cell["sheet_row"]),
                        cell["value"],
                    )
                except Exception as exc:  # noqa: BLE001
                    self._storage.mark_cell_error(
                        int(cell["id"]), friendly_error(exc)
                    )
                    self._set_error(exc)
                    return
                self._storage.mark_cells_synced([int(cell["id"])])
                self._set_ok()

    # ---------------------------------------------------------------- ochish
    def allocate_row(
        self, row_date: str, timestamp_text: str, kassir: str, number: int = 0
    ) -> Dict:
        """Yangi qator ochadi (fon oqimi keyin A/B/J yozadi)."""
        self.ensure_seeded(timeout=self._seed_timeout)
        row = self._storage.allocate_sheet_row(
            row_date, timestamp_text, kassir, number
        )
        self.notify()
        return row

    def queue_cell(self, row: Dict, column_letter: str, value: str) -> bool:
        queued = self._storage.queue_cell(
            int(row["id"]), int(row["sheet_row"]), column_letter, value
        )
        if queued:
            self.notify()
        return queued


def _amount_text(value) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return ""
    return str(int(number)) if number == int(number) else ("%g" % number)


def _timestamp_text(sana: str, vaqt: str, created_at: str) -> str:
    """Eski yozuvning sanasi/vaqtidan `YYYY-MM-DD HH:MM:SS` hosil qiladi."""
    try:
        day = dt.datetime.strptime(str(sana), "%d.%m.%Y")
    except (TypeError, ValueError):
        return str(created_at or dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    text = str(vaqt or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            moment = dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        return day.replace(
            hour=moment.hour, minute=moment.minute, second=moment.second
        ).strftime("%Y-%m-%d %H:%M:%S")
    return str(created_at or day.strftime("%Y-%m-%d %H:%M:%S"))


def migrate_legacy_records(storage, cells: CellSyncService) -> int:
    """Eski (butun qator) navbatdagi yozuvlarni yangi katak jurnaliga o'tkazadi.

    Har bir yozuv uchun jurnal qatori ochiladi, summa va izoh kataklari
    navbatga qo'yiladi, so'ng yozuv `sent` deb belgilanadi — eski 8 ustunli
    append hech qachon ishlamaydi. Idempotent: `sheet_row` borlar o'tkazilmaydi.
    """
    migrated = 0
    for record in storage.pending_records():
        if int(record.get("sheet_row") or 0):
            continue
        field = PAYMENT_FIELD.get((record.get("turi"), record.get("tolov_usuli")))
        if not field:
            continue
        sana = str(record.get("sana") or "")
        number = storage.journal_rows_count(sana) + 1
        row = cells.allocate_row(
            sana,
            _timestamp_text(sana, str(record.get("vaqt") or ""), str(record.get("created_at") or "")),
            str(record.get("kassir") or ""),
            number,
        )
        cells.queue_cell(row, CELL_COLUMN[field], _amount_text(record.get("summa")))
        note = str(record.get("izoh") or "").strip()
        if note:
            cells.queue_cell(row, CELL_COLUMN[NOTE_FIELD[record["turi"]]], note)
        storage.set_record_sheet_row(int(record["id"]), int(row["sheet_row"]))
        storage.mark_sent([int(record["id"])])
        migrated += 1
    return migrated


__all__ = ["CellSyncService", "migrate_legacy_records"]
