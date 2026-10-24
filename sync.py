"""Fon sincronizatsiyasi: lokal navbatdan Google Sheets'ga yuborish.

Bitta vaqtda bitta oqim ishlaydi. Xato bo'lsa yozuvlar 'pending'
holatida qoladi va avtomatik qayta urinish davom etadi.

Muhim: bu oqimdagi hech qanday xato dasturni qulatmaydi — hammasi
ushlanadi va foydalanuvchiga tushunarli xabar sifatida qaytariladi.
"""

from __future__ import annotations

import threading
from typing import List

from PyQt6.QtCore import QObject, pyqtSignal

from sheets import build_row, friendly_error
from storage import Storage

REASON_AUTO = "auto"
REASON_MANUAL = "manual"
REASON_STARTUP = "startup"


def _invalidate(client) -> None:
    """Klient sessiyasini yangilashga urinadi (bo'lmasa jim o'tadi)."""
    invalidate = getattr(client, "invalidate", None)
    if callable(invalidate):
        try:
            invalidate()
        except Exception:  # noqa: BLE001
            pass


class SyncService(QObject):
    started = pyqtSignal(int)                       # yuborish boshlandi (nechta navbatda)
    finished = pyqtSignal(int, str, bool)           # yuborilgan soni, xabar, muvaffaqiyat
    rejected = pyqtSignal(str)                      # hozir yuborib bo'lmadi (bloklandi)

    def __init__(self, storage: Storage, client, parent: QObject | None = None):
        super().__init__(parent)
        self.storage = storage
        self.client = client
        self._busy = False
        self._thread: threading.Thread | None = None
        # Tiklashda topilgan (uzilib qolgan) yozuvlar — dedupe tekshiruvi
        # ishlamasa, ularni yubormay qolamiz.
        self.recovered_ids: set = set()

    @property
    def busy(self) -> bool:
        return self._busy

    def wait(self, timeout: float = 5.0) -> None:
        """Yopishdan oldin joriy yuborish tugashini kutadi."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
            self._thread = None

    # ----------------------------------------------------------------- tiklash
    def recover_stuck(self) -> int:
        """Chiroq/jarayon uzilishidan qolgan 'sending' yozuvlarni tiklaydi.

        Bu yozuvlar Sheets'ga yuborilgan bo'lishi mumkin. Ularni 'pending'ga
        qaytarib, keyingi yuborishda UID orqali duplikat tekshiruvidan o'tkazamiz —
        shunda takroriy qator yaratilmaydi.

        Tiklangan yozuvlar ro'yxati **bazada** (`recovered_records`) saqlanadi:
        dastur yana qayta ishga tushsa ham (masalan navbatni o'qishdan oldin
        qayta qulasa) himoya yo'qolmaydi. Agar dedupe tekshiruvi ishlamasa,
        aynan shu yozuvlar xavfsizlik uchun yuborilmay qoladi.
        """
        try:
            stuck = self.storage.sending_records()
            if stuck:
                self.storage.note_recovered([int(r["id"]) for r in stuck])
            reset = self.storage.reset_stuck_sending()
            self.recovered_ids = self._recovered_from_storage()
            return reset
        except Exception:  # noqa: BLE001
            self.recovered_ids = set()
            return 0

    def _recovered_from_storage(self) -> set:
        """Bazada saqlangan tiklangan yozuv ID'lari to'plami."""
        try:
            return {int(i) for i in self.storage.recovered_ids()}
        except Exception:  # noqa: BLE001 — o'qib bo'lmasa xotiradagiga qaytamiz
            return set(self.recovered_ids)

    def request(self, reason: str = REASON_MANUAL) -> bool:
        """Yuborishni so'raydi. Haqiqiy oqim boshlangan bo'lsa True."""
        if self._busy:
            if reason == REASON_MANUAL:
                self.rejected.emit("Yuborish allaqachon ishlab turibdi…")
            return False

        pending: List[dict] = self.storage.pending_records()

        if not pending:
            if reason == REASON_MANUAL:
                self.finished.emit(
                    0,
                    "Yuboriladigan yozuv yo'q — barcha ma'lumot Google Sheets'da.",
                    True,
                )
            return False

        self._busy = True
        self.started.emit(len(pending))
        self._thread = threading.Thread(
            target=self._run, args=(pending, reason), daemon=True, name="kassa-sync"
        )
        self._thread.start()
        return True

    # ----------------------------------------------------------------- oqim
    def _run(self, pending: List[dict], reason: str) -> None:
        try:
            self._run_safe(pending, reason)
        except Exception as exc:  # noqa: BLE001 — oxirgi himoya qatlami
            _invalidate(self.client)
            try:
                self.storage.mark_pending([r["id"] for r in pending], friendly_error(exc))
            except Exception:  # noqa: BLE001
                pass
            self._finish(0, friendly_error(exc), False)

    def _run_safe(self, pending: List[dict], reason: str) -> None:
        try:
            self.client.ensure_headers()
        except Exception as exc:  # noqa: BLE001 — dastur qulamasligi shart
            _invalidate(self.client)
            self._fail_all(pending, friendly_error(exc))
            return

        # 1) Yuborilgan, lekin tasdiqlanmagan yozuvlarni aniqlash.
        #    (Chiroq uzilganda 'sending' holatida qolgan yozuvlar.)
        known_uids = set()
        uids_readable = True
        try:
            known_uids = set(self.client.existing_uids())
        except Exception:  # noqa: BLE001 — tekshiruv ishlamasa, oddiy yuborish
            uids_readable = False
            known_uids = set()

        # Dedupe ishlamasa, xavfsiz ushlab turiladigan tiklangan yozuvlar.
        # Ro'yxat bazada saqlanadi — yangi oqim (yoki qayta ishga tushgan
        # dastur) uchun ham ochiq.
        held_ids: set = set()
        if not uids_readable:
            held_ids = self._recovered_from_storage()

        sent = 0
        skipped = 0
        held = 0
        for index, record in enumerate(pending):
            uid = str(record.get("uid") or "")
            if uid and uid in known_uids:
                # Bu yozuv Sheets'da allaqon bor — qayta yuborilmaydi.
                self.storage.mark_sent([record["id"]])
                self.storage.clear_recovered([record["id"]])
                skipped += 1
                continue
            if not uids_readable and int(record["id"]) in held_ids:
                # Dedupe tekshiruvi ishlamadi va bu yozuv uzilishdan qolgan —
                # xavfsizlik uchun yuborilmay qoldiramiz (navbatda turadi).
                self.storage.hold_pending(
                    [record["id"]], "Duplikat tekshiruvi bajarilmadi — navbatga qoldirildi"
                )
                held += 1
                continue
            try:
                # Yuborishdan oldin 'sending' deb belgilaymiz — agar jarayon
                # shu yerda to'xtasa, keyingi tiklashda duplikat aniqlanadi.
                self.storage.mark_sending([record["id"]])
                self.client.append(build_row(record))
            except Exception as exc:  # noqa: BLE001
                _invalidate(self.client)
                message = friendly_error(exc)
                # Muvaffaqiyatli yuborilganlar sent, qolganlari pending qoladi
                self.storage.mark_sent([r["id"] for r in pending[:index]])
                self.storage.mark_pending([r["id"] for r in pending[index:]], message)
                self._finish(sent, message, False)
                return
            self.storage.mark_sent([record["id"]])
            self.storage.clear_recovered([record["id"]])
            # Yuborilgan UID endi "mavjud" — keyingi yozuvlar uchun ham
            # tegishli bo'ladi (bir navbatda UID takrorlanmaydi).
            if uid:
                known_uids.add(uid)
            sent += 1

        if held and not sent and not skipped:
            self._finish(
                0,
                "Duplikat tekshiruvi bajarilmadi — yozuvlar navbatda saqlandi.",
                False,
            )
            return

        if reason == REASON_STARTUP:
            self._finish(sent, "Navbatdagi yozuvlar Google Sheets'ga yuborildi.", True)
        else:
            self._finish(sent, f"{sent} ta yozuv Google Sheets'ga yuborildi.", True)

    def _fail_all(self, pending: List[dict], message: str) -> None:
        try:
            self.storage.mark_pending([r["id"] for r in pending], message)
        except Exception:  # noqa: BLE001
            pass
        self._finish(0, message, False)

    def _finish(self, sent: int, message: str, ok: bool) -> None:
        self._busy = False
        self.finished.emit(sent, message, ok)


__all__ = ["SyncService", "REASON_AUTO", "REASON_MANUAL", "REASON_STARTUP"]
