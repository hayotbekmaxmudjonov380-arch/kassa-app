"""GOLD restobar — Kassa jurnali (Web server / Brauzer versiyasi).

Dasturni brauzerda (http://localhost:8000) ishga tushiradi.
Lokal SQLite (kassa.db) va Google Sheets sinxronizatsiyasi bilan integratsiya qilingan.

Sana va vaqt faqat server tomonidan qo'yiladi — ularni o'zgartirib bo'lmaydi.

Ishga tushirish:
    python web_app.py
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from PyQt6.QtCore import QCoreApplication

from backup import BackupService
from cell_sync import CellSyncService, migrate_legacy_records
from config import AMOUNT_FIELDS, CELL_COLUMN, JOURNAL_TEMPLATE_PATH, LOGO_PATH, PAYMENTS
from drafts import DraftStore
from helpers import now_iso, now_time_str, today_str
from settings import AppSettings
from sheets import SheetsClient
from storage import Storage
from sync import REASON_AUTO, REASON_MANUAL, SyncService

PORT = 8000

TEMPLATE_PATH = JOURNAL_TEMPLATE_PATH


class AppContext:
    """Serverning umumiy ob'ektlari (bitta marta yaratiladi)."""

    def __init__(self) -> None:
        self.settings = AppSettings()
        self.storage = Storage()
        self.client = SheetsClient(
            credentials_file=self.settings.credentials_file,
            spreadsheet_id=self.settings.spreadsheet_id,
            worksheet_name=self.settings.worksheet_name,
            auto_create_sheet=self.settings.auto_create_sheet,
        )
        self.sync = SyncService(self.storage, self.client)
        self.cells = CellSyncService(self.storage, client_factory=self._new_client)
        self.drafts = DraftStore()
        self.backup = BackupService(self.storage)

    def _new_client(self) -> SheetsClient:
        return SheetsClient(
            credentials_file=self.settings.credentials_file,
            spreadsheet_id=self.settings.spreadsheet_id,
            worksheet_name=self.settings.worksheet_name,
            auto_create_sheet=self.settings.auto_create_sheet,
        )


_context: Optional[AppContext] = None


def context() -> AppContext:
    """Ob'ektlarni birinchi kerak bo'lganda yaratadi (import paytida emas).

    Testlar `web_app`'ni import qilganda haqiqiy baza/sozlamalar
    yaratilmaydi — faqat `render_page` kabi toza funksiyalar ishlatiladi.
    """
    global _context
    if _context is None:
        _context = AppContext()
    return _context


def render_page(today: str | None = None) -> str:
    """Jurnal sahifasini bugungi sana bilan qaytaradi.

    Sana HTML ichiga serverda qo'yiladi, shuning uchun sahifa ochilishi bilan
    to'g'ri ko'rinadi va uni o'zgartirib bo'lmaydi (oddiy matn, input emas).
    """
    try:
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as fh:
            template = fh.read()
    except OSError:
        template = (
            "<!DOCTYPE html><html lang=\"uz\"><meta charset=\"utf-8\">"
            "<body style=\"font-family:sans-serif;padding:24px\">"
            "<h1>journal.html topilmadi</h1>"
            "<p>Dastur papkasida journal.html fayli bo'lishi kerak.</p>"
            "</body></html>"
        )
    return template.replace("{{TODAY}}", html.escape(today or today_str()))


def cross_site_blocked(headers) -> bool:
    """Boshqa saytdan kelgan so'rovni aniqlaydi (CSRF himoyasi).

    Lokal serverda autentifikatsiya yo'q — shuning uchun ufaq veb-sayt
    (localhost'ga POST yubora oladi) orqali yozuv/butunlash huquqi oldini
    olamiz: Origin/Referer localhost bo'lmasa yoki brauzer `cross-site`
    deb belgilasa — so'rov rad etiladi. So'rov brauzerdan kelmasa
    (curl, dastur) sarlavhalar bo'sh bo'ladi — ular o'tadi.
    """
    origin = headers.get("Origin") or ""
    if origin:
        host = urllib.parse.urlparse(origin).hostname or ""
        if host not in ("localhost", "127.0.0.1", "::1"):
            return True
    referer = headers.get("Referer") or ""
    if referer and not origin:
        host = urllib.parse.urlparse(referer).hostname or ""
        if host not in ("localhost", "127.0.0.1", "::1"):
            return True
    if (headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
        return True
    return False


_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d(:[0-5]\d)?$")


def normalize_cell_value(field: str, value) -> str:
    """Katak qiymatini tozalaydi: summa — butun son, izoh — matn.

    Jadvalga "12 000" kabi bo'shliqli matn tushmasligi kerak — RAW rejimda
    u matn bo'lib qoladi va yig'indi ishlamaydi.
    """
    text = str("" if value is None else value).strip()[:200]
    if field in AMOUNT_FIELDS:
        cleaned = re.sub(r"[^\d.\-]", "", text.replace(",", "."))
        try:
            number = float(cleaned)
        except ValueError:
            number = 0.0
        if number <= 0:
            return ""
        return str(int(round(number)))
    return text


def valid_time(value) -> bool:
    """Vaqt matni to'g'rimi? (HH:MM yoki HH:MM:SS)

    Kassir qatordagi vaqtni erkin o'zgartirishi mumkin (masalan 18:45),
    lekin faqat to'g'ri format qabul qilinadi. Noto'g'ri qiymatda server
    o'z joriy vaqtini qo'yadi — jurnalda hech qachon buzilgan vaqt qolmaydi.
    """
    return bool(_TIME_RE.match(str(value or "").strip()))


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Konsolga ortiqcha log yozmaymiz

    # ------------------------------------------------------------- GET
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            body = render_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path in ("/image/logotip.jpg", "/image/logotip.JPG"):
            try:
                with open(LOGO_PATH, "rb") as fh:
                    body = fh.read()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/api/data":
            ctx = context()
            since_iso = ctx.settings.get("day_closed_at") or ""
            today = today_str()
            cell = ctx.cells.status()
            payload = {
                "kassir": ctx.settings.last_kassir,
                "date": today,
                "records": ctx.storage.today_records(today, since_iso=since_iso),
                "stats": ctx.storage.today_detailed_stats(today, since_iso=since_iso),
                "pending": ctx.storage.pending_count(),
                # Katak navbati holati (Google Sheets bilan aloqa).
                "cell_pending": cell["pending"],
                "cell_error": cell["error"],
                "sheets_connected": cell["connected"],
                "last_sync": cell["last_sync"],
                # Server tomondagi draft (kiritilgan, lekin Enter bosilmagan).
                "drafts": ctx.drafts.rows(),
            }
            self._send_json(payload)

        elif parsed.path == "/api/status":
            self._send_json({"ok": True, **context().cells.status()})

        elif parsed.path == "/api/drafts":
            self._send_json({"ok": True, "drafts": context().drafts.rows()})

        else:
            self.send_error(404)

    # ------------------------------------------------------------ POST
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"

        if cross_site_blocked(self.headers):
            self._send_json(
                {"ok": False, "error": "Ruxsat etilmagan so'rov (localhost'dan tashqari)"},
                status=403,
            )
            return

        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            data = {}

        ctx = context()

        if parsed.path == "/api/add":
            kassir = str(data.get("kassir") or "").strip()
            turi = str(data.get("turi") or "").strip()
            tolov_usuli = str(data.get("tolov_usuli") or "").strip()
            izoh = str(data.get("izoh") or "").strip()
            try:
                summa = float(data.get("summa") or 0)
            except (TypeError, ValueError):
                summa = 0.0

            valid_payment = (
                turi in PAYMENTS and tolov_usuli in PAYMENTS[turi]
            )
            if not kassir or not valid_payment or summa <= 0:
                self._send_json({"ok": False, "error": "Ma'lumotlar to'liq emas"})
                return

            # Sana har doim serverdan olinadi — uni o'zgartirib bo'lmaydi.
            # Vaqt esa qatordan keladi (erkin HH:MM); format noto'g'ri
            # bo'lsa server o'z joriy vaqtini qo'yadi.
            vaqt = str(data.get("vaqt") or "").strip()
            if not valid_time(vaqt):
                vaqt = now_time_str()

            # Qator (journal_rows) allaqachon ochilgan bo'lsa — o'sha Sheets
            # qatoriga bog'laymiz. Kataklar alohida-alohida yuboriladi,
            # shuning uchun eski "butun qator append" ishlatilmaydi.
            try:
                row_id = int(data.get("row_id") or 0)
            except (TypeError, ValueError):
                row_id = 0
            journal = ctx.storage.journal_row(row_id) if row_id else None
            sheet_row = int(journal["sheet_row"]) if journal else 0

            ctx.storage.add(
                sana=today_str(),
                vaqt=vaqt,
                kassir=kassir,
                turi=turi,
                tolov_usuli=tolov_usuli,
                summa=summa,
                izoh=izoh,
                sheet_row=sheet_row,
                status="sent" if journal else None,
            )
            if ctx.settings.get("last_kassir") != kassir:
                ctx.settings.set("last_kassir", kassir)
                ctx.settings.save()

            if journal:
                ctx.cells.notify()
            else:
                ctx.sync.request(REASON_AUTO)
            self._send_json({"ok": True, "sheet_row": sheet_row})

        elif parsed.path == "/api/row/open":
            kassir = str(data.get("kassir") or "").strip()
            if not kassir:
                self._send_json({"ok": False, "error": "Kassir ismini kiriting"})
                return
            today = today_str()
            number = ctx.storage.journal_rows_count(today) + 1
            timestamp_text = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            row = ctx.cells.allocate_row(today, timestamp_text, kassir, number)
            self._send_json(
                {
                    "ok": True,
                    "row_id": row["id"],
                    "sheet_row": row["sheet_row"],
                    "number": row["number"],
                    "timestamp": timestamp_text,
                }
            )

        elif parsed.path == "/api/cell":
            try:
                row_id = int(data.get("row_id") or 0)
            except (TypeError, ValueError):
                row_id = 0
            field = str(data.get("field") or "").strip()
            value = normalize_cell_value(field, data.get("value"))
            column = CELL_COLUMN.get(field)
            if not column and field.upper() in CELL_COLUMN.values():
                column = field.upper()
            row = ctx.storage.journal_row(row_id) if row_id else None
            if not row or not column:
                self._send_json({"ok": False, "error": "Qator topilmadi"})
                return
            queued = ctx.cells.queue_cell(row, column, value)
            status = ctx.cells.status()
            self._send_json(
                {
                    "ok": True,
                    "queued": queued,
                    "pending": status["pending"],
                    "connected": status["connected"],
                    "error": status["error"],
                }
            )

        elif parsed.path == "/api/kassir":
            kassir = str(data.get("kassir") or "").strip()
            if kassir:
                ctx.settings.set("last_kassir", kassir)
                ctx.settings.save()
            self._send_json({"ok": True})

        elif parsed.path == "/api/close-day":
            ctx.settings.set("day_closed_at", now_iso())
            ctx.settings.save()
            self._send_json({"ok": True})

        elif parsed.path == "/api/retry-sync":
            ctx.sync.request(REASON_MANUAL)
            ctx.cells.notify()
            self._send_json({"ok": True})

        elif parsed.path == "/api/drafts":
            drafts = data.get("drafts")
            if not isinstance(drafts, list):
                drafts = []
            rows = [d for d in drafts if isinstance(d, dict)]
            kassir = str(data.get("kassir") or "").strip()
            sana = str(data.get("sana") or "").strip()
            ok = ctx.drafts.save(rows, kassir=kassir, sana=sana)
            self._send_json({"ok": ok})

        elif parsed.path == "/api/drafts/clear":
            ctx.drafts.clear()
            self._send_json({"ok": True})

        else:
            self.send_error(404)

    def _send_json(self, payload: dict, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _reconfigure_stdout() -> None:
    """Konsol kodlashini UTF-8'ga o'tkazadi (emoji/chevron chiqmasligi uchun)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def main():
    _reconfigure_stdout()

    # PyQT signallari uchun minimal ilova (socket ishlatilmaydi).
    if QCoreApplication.instance() is None:
        QCoreApplication([])

    print("=" * 60)
    print("🚀 GOLD restobar — Kassa jurnali (Web versiya) ishga tushdi!")
    print(f"🌐 Brauzer manzili: http://localhost:{PORT}")
    print("=" * 60)

    url = f"http://localhost:{PORT}"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    ctx = context()
    # Katak sinxronizatsiyasi: jadvaldagi oxirgi qatorni aniqlab, navbatni
    # fon oqimida bo'shatadi (internet yo'q bo'lsa kutib turadi).
    ctx.cells.start()

    # Eski (butun qator) navbatda qolgan yozuvlarni yangi katak jurnaliga
    # o'tkazamiz — aks holda ular hech qachon jadvalga tushmaydi.
    def _migrate_legacy() -> None:
        try:
            migrate_legacy_records(ctx.storage, ctx.cells)
        except Exception:  # noqa: BLE001 - migratsiya serverni to'xtatmasin
            pass

    threading.Thread(target=_migrate_legacy, daemon=True).start()

    # Davriy fon ishlari: yedeklash (30 daqiqada bir, `due()` tekshiradi)
    # va internet qaytganida avtomatik qayta yuborish (har 60 soniyada).
    def _periodic_loop() -> None:
        while True:
            time.sleep(60)
            try:
                ctx.backup.run()
            except Exception:  # noqa: BLE001 — yedeklash serverni to'xtatmasin
                pass
            try:
                if ctx.storage.pending_count() and not ctx.sync.busy:
                    ctx.sync.request(REASON_AUTO)
            except Exception:  # noqa: BLE001
                pass
            try:
                ctx.storage.checkpoint()
            except Exception: # noqa: BLE001
                pass

    threading.Thread(target=_periodic_loop, daemon=True).start()

    # Jarayon o'tkazilmagan 'sending' yozuvlarini tiklaymiz.
    try:
        ctx.sync.recover_stuck()
    except Exception:  # noqa: BLE001
        pass

    # ThreadingHTTPServer: bir vaqtda bir nechta brauzer tab'i ishlaydi
    # (oddiy HTTPServer bir navbatda bitta so'rovni kutar edi).
    server = ThreadingHTTPServer(("localhost", PORT), RequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWeb server to'xtatildi.")
    finally:
        # Server yopilganda draft va yedekni saqlaymiz.
        try:
            ctx.drafts.save(ctx.drafts.rows())
        except Exception:  # noqa: BLE001
            pass
        try:
            ctx.backup.run(force=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            ctx.cells.stop()
            ctx.cells.join(2000)
        except Exception:  # noqa: BLE001
            pass
        try:
            ctx.storage.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
