"""Google Sheets'ga yozish qatlami (gspread + service account).

Autentifikatsiya: service_account.json (service account kalit fayli).
Har qanday xato shu qatlamda ushlanib, foydalanuvchiga tushunarli
o'zbekcha xabarga aylantiriladi — dastur qulamaydi.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Dict, List, Sequence

from config import (
    HEADERS,
    HTTP_TIMEOUT_SECONDS,
    SERVICE_ACCOUNT_FILE,
    SHEET_FIRST_DATA_ROW,
    SHEET_HEADERS,
    SPREADSHEET_ID,
    WORKSHEET_NAME,
)


class SheetsError(Exception):
    """Foydalanuvchiga tushunarli xabar beriladigan xato."""


def format_full_timestamp(sana: str, vaqt: str, created_at: str = "") -> str:
    """Sana va vaqtdan Google Sheets uchun to'liq timestamp (%Y-%m-%d %H:%M:%S) hosil qiladi."""
    date_part = ""
    sana_clean = str(sana or "").strip()
    if "." in sana_clean:
        parts = sana_clean.split(".")
        if len(parts) == 3:
            day, month, year = parts[0].zfill(2), parts[1].zfill(2), parts[2]
            if len(year) == 2:
                year = f"20{year}"
            date_part = f"{year}-{month}-{day}"
    elif "-" in sana_clean:
        parts = sana_clean.split("-")
        if len(parts) == 3:
            if len(parts[0]) == 4:
                date_part = f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
            else:
                date_part = f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"

    time_part = ""
    vaqt_clean = str(vaqt or "").strip()
    if ":" in vaqt_clean:
        v_parts = vaqt_clean.split(":")
        if len(v_parts) == 2:
            time_part = f"{v_parts[0].zfill(2)}:{v_parts[1].zfill(2)}:00"
        elif len(v_parts) >= 3:
            time_part = f"{v_parts[0].zfill(2)}:{v_parts[1].zfill(2)}:{v_parts[2].zfill(2)}"

    if date_part and time_part:
        return f"{date_part} {time_part}"

    if created_at and "T" in created_at:
        parts = created_at.split("T")
        d_p = parts[0]
        t_p = parts[1].split(".")[0] if len(parts) > 1 else "00:00:00"
        if ":" not in t_p:
            t_p += ":00:00"
        elif t_p.count(":") == 1:
            t_p += ":00"
        return f"{d_p} {t_p}"

    if not sana and not vaqt and not created_at:
        return ""

    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _column_letter(index: int) -> str:
    """1 -> A, 8 -> H (Google Sheets A1 formati)."""
    letters = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def build_row(record: Dict) -> List:
    """Lokal yozuvni Google Sheets qatoriga aylantiradi (8 ta ustun).

    Oxirgi ustun — barqaror UID. U orqali yuborilgan, lekin tasdiqlanmagan
    yozuvlar takroriy qator yaratmasligi ta'minlanadi.
    """
    full_timestamp = format_full_timestamp(
        record.get("sana", ""),
        record.get("vaqt", ""),
        record.get("created_at", ""),
    )
    return [
        record.get("sana", ""),
        full_timestamp,
        record.get("kassir", ""),
        record.get("turi", ""),
        record.get("tolov_usuli", ""),
        float(record.get("summa", 0) or 0),
        record.get("izoh", "") or "",
        record.get("uid", "") or "",
    ]


def friendly_error(exc: BaseException) -> str:
    """Xatoni oddiy o'zbekcha xabarga aylantiradi."""
    if isinstance(exc, SheetsError):
        return str(exc)

    name = f"{type(exc).__module__}.{type(exc).__name__}"
    text = str(exc).strip() or type(exc).__name__
    lowered = text.lower()

    # --- service account kalit fayli
    if isinstance(exc, FileNotFoundError):
        return "service_account.json topilmadi. Faylni dastur papkasiga qo'ying."
    if isinstance(exc, json.JSONDecodeError):
        return "service_account.json buzilgan — JSON faylni to'g'ri tanlang."

    # --- google.auth xatolari
    try:
        import google.auth.exceptions as gae
    except Exception:  # pragma: no cover
        gae = None
    if gae is not None:
        if isinstance(exc, gae.DefaultCredentialsError):
            return "Google kaliti topilmadi — service_account.json faylini tekshiring."
        if isinstance(exc, gae.MalformedError):
            return "service_account.json buzilgan yoki noto'g'ri — faylni qayta tanlang."
        if isinstance(exc, gae.RefreshError):
            return "Google autentifikatsiya xatosi: kalit bekor qilingan bo'lishi mumkin."
        if isinstance(exc, gae.TransportError):
            return "Internet aloqasi yo'q — yozuv lokal saqlandi, keyinroq qayta yuboring."

    # --- eskirgan oauth2client xatolari (moslik uchun)
    if "DefaultCredentialsError" in name or "MalformedKeyError" in name:
        return "Service account kaliti o'qilmadi — service_account.json faylini tekshiring."
    if isinstance(exc, ValueError) and ("service account" in lowered or "keydata" in lowered):
        return "Service account kaliti noto'g'ri — JSON faylni almashtiring."

    # --- gspread API
    try:
        import gspread.exceptions as gse
    except Exception:  # pragma: no cover
        gse = None
    if gse is not None:
        if isinstance(exc, gse.WorksheetNotFound):
            return f"'{WORKSHEET_NAME}' nomli varaq topilmadi."
        if isinstance(exc, gse.SpreadsheetNotFound):
            return "Google Sheets topilmadi — Spreadsheet ID noto'g'ri yoki fayl bilan bo'lishilmagan."
        if isinstance(exc, gse.APIError):
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                return "Google'dan ruxsat yo'q — service account'ga Google Sheets'da 'Editor' huquqi bering."
            if status == 404:
                return "Google Sheets topilmadi — Spreadsheet ID noto'g'ri yoki fayl bilan bo'lishilmagan."
            if status == 429:
                return "Google API limiti tugadi — bir necha daqiqadan keyin qayta yuboring."
            if status and 500 <= status < 600:
                return "Google tomonida nosozlik — birozdan so'ng qayta urinib ko'ring."
            return f"Google API xatosi: {text}"

    # --- tarmoq
    try:
        import requests.exceptions as rq
    except Exception:  # pragma: no cover
        rq = None
    if rq is not None and isinstance(exc, (rq.ConnectionError, rq.Timeout, rq.SSLError)):
        return "Internet aloqasi yo'q — yozuv lokal saqlandi, keyinroq 'Qayta yuborish'ni bosing."
    if isinstance(exc, TimeoutError) or "timed out" in lowered:
        return "Internet aloqasi uzildi — yozuv lokal saqlandi, keyinroq qayta yuboring."
    if isinstance(exc, OSError):
        return "Internet aloqasi yo'q yoki tarmoq xatosi — yozuv lokal saqlandi, keyinroq qayta yuboring."

    return f"Kutilmagan xato: {text}"[:300]


class SheetsClient:
    """Google Sheets bilan ishlash uchun yupqa klient (lenk yuborish)."""

    def __init__(
        self,
        credentials_file: str = SERVICE_ACCOUNT_FILE,
        spreadsheet_id: str = SPREADSHEET_ID,
        worksheet_name: str = WORKSHEET_NAME,
        auto_create_sheet: bool = True,
    ):
        self.credentials_file = credentials_file
        self.spreadsheet_id = spreadsheet_id
        self.worksheet_name = worksheet_name
        self.auto_create_sheet = auto_create_sheet
        self._ws = None
        self._headers_ready = False
        self._journal_ready = False

    # ------------------------------------------------------------- ulanish
    def _spreadsheet(self):
        """Service account bilan spreadsheetni ochadi."""
        if not self.credentials_file or not os.path.exists(self.credentials_file):
            raise SheetsError(
                "service_account.json topilmadi. Faylni dastur papkasiga qo'ying "
                "yoki 'Sozlamalar' orqali kalit faylni tanlang."
            )

        import gspread

        try:
            client = gspread.service_account(filename=self.credentials_file)
            client.set_timeout(HTTP_TIMEOUT_SECONDS)
            return client.open_by_key(self.spreadsheet_id)
        except SheetsError:
            raise
        except Exception as exc:  # noqa: BLE001 — xatolar yuqorida tarjima qilinadi
            raise _translate(exc) from exc

    def _worksheet(self):
        if self._ws is not None:
            return self._ws

        spreadsheet = self._spreadsheet()

        try:
            ws = spreadsheet.worksheet(self.worksheet_name)
        except Exception as exc:  # noqa: BLE001
            if not _is_missing_worksheet(exc):
                raise _translate(exc) from exc
            if not self.auto_create_sheet:
                raise SheetsError(f"'{self.worksheet_name}' nomli varaq topilmadi.") from exc
            # Varaqni avtomatik yaratamiz
            try:
                spreadsheet.add_worksheet(
                    title=self.worksheet_name,
                    rows=100,
                    cols=max(len(HEADERS), len(SHEET_HEADERS)),
                )
                ws = spreadsheet.worksheet(self.worksheet_name)
            except Exception as create_exc:  # noqa: BLE001
                raise _translate(create_exc) from create_exc

        self._ws = ws
        self._headers_ready = False
        return ws

    def invalidate(self) -> None:
        """Keyingi urinishda qaytadan ulanish (kalit/yaroqsiz sessiya uchun)."""
        self._ws = None
        self._headers_ready = False
        self._journal_ready = False

    # ------------------------------------------------------------- amallar
    def ensure_headers(self) -> None:
        import gspread

        ws = self._worksheet()
        if self._headers_ready:
            return
        try:
            first = ws.row_values(1)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc
        if not first:
            try:
                ws.append_row(
                    HEADERS, value_input_option=gspread.utils.ValueInputOption.user_entered
                )
            except Exception as exc:  # noqa: BLE001
                raise _translate(exc) from exc
        elif [str(v or "").strip() for v in first[: len(SHEET_HEADERS)]] == SHEET_HEADERS:
            # Yangi jurnal (A..J, katak darajasida sinxron). Eski 8 ustunli
            # append bu jadvalni buzadi — yozmasdan to'xtaymiz.
            raise SheetsError(
                "Jadval yangi jurnal formatida (A–J). Desktop ilovasi bu "
                "formatga mos emas — brauzer versiyasidan foydalaning."
            )
        elif len(first) < len(HEADERS):
            # Eski jadvalda UID ustuni yo'q — sarlavhani kengaytiramiz.
            try:
                ws.update(
                    "A1:{}1".format(_column_letter(len(HEADERS))),
                    [HEADERS],
                    value_input_option=gspread.utils.ValueInputOption.user_entered,
                )
                self._ensure_columns(ws, len(HEADERS))
            except Exception as exc:  # noqa: BLE001
                raise _translate(exc) from exc
        self._headers_ready = True

    @staticmethod
    def _ensure_columns(ws, count: int) -> None:
        """Varaqda yetarli ustun bo'lishini ta'minlaydi."""
        try:
            current = int(ws.cols)
        except Exception:  # noqa: BLE001
            current = 0
        if current >= count:
            return
        try:
            ws.add_columns(count - current)
        except Exception:  # noqa: BLE001 — ustun kengaytirilmasligi muhim emas
            pass

    def existing_uids(self) -> set:
        """Jadvalda mavjud bo'lgan UID'lar to'plami.

        Bu orqali yuborilgan, lekin tasdiqlanmagan ("sending") yozuvlar
        aniqlanadi va ular qayta yuborilmaydi — takroriy qator hosil bo'lmaydi.
        """
        ws = self._worksheet()
        index = HEADERS.index("UID") + 1 if "UID" in HEADERS else len(HEADERS)
        try:
            values = ws.col_values(index)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc
        uids = set()
        for row in values[1:]:  # birinchi qator — sarlavha
            value = str(row or "").strip()
            if value:
                uids.add(value)
        return uids

    def append(self, row: Sequence) -> None:
        import gspread

        ws = self._worksheet()
        try:
            ws.append_row(
                list(row), value_input_option=gspread.utils.ValueInputOption.user_entered
            )
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    def append_record(self, record: Dict) -> None:
        """Sarlavha tekshiruvi + bitta yozuvni yuborish."""
        self.ensure_headers()
        self.append(build_row(record))

    # ------------------------------------------- yangi jurnal (A..J, katak)
    def ensure_journal_headers(self) -> None:
        """1-qatorga A..J sarlavhalarini yozadi (faqat farq bo'lsa)."""
        ws = self._worksheet()
        if self._journal_ready:
            return
        try:
            first = ws.row_values(1)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc
        current = [str(v or "").strip() for v in first[: len(SHEET_HEADERS)]]
        if current != SHEET_HEADERS:
            try:
                ws.update(
                    "A1:{}1".format(_column_letter(len(SHEET_HEADERS))),
                    [SHEET_HEADERS],
                    value_input_option="RAW",
                )
                self._ensure_columns(ws, len(SHEET_HEADERS))
            except Exception as exc:  # noqa: BLE001
                raise _translate(exc) from exc
        self._journal_ready = True

    def last_data_row(self) -> int:
        """A ustunidagi oxirgi to'ldirilgan qator raqami (0 — bo'sh)."""
        ws = self._worksheet()
        try:
            values = ws.col_values(1)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc
        last = 0
        for index, value in enumerate(values, start=1):
            if str(value or "").strip():
                last = index
        return last

    def open_row(
        self, sheet_row: int, number, timestamp_text: str, kassir: str
    ) -> None:
        """Yangi qatorni ochadi: A (#), B (Vaqt), J (Kassir) — BITTA so'rov."""
        self.ensure_journal_headers()
        ws = self._worksheet()
        data = [
            {"range": f"A{int(sheet_row)}", "values": [[number]]},
            {"range": f"B{int(sheet_row)}", "values": [[str(timestamp_text)]]},
            {"range": f"J{int(sheet_row)}", "values": [[str(kassir)]]},
        ]
        try:
            ws.batch_update(data, value_input_option="RAW")
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    def update_cell(self, column_letter: str, sheet_row: int, value: str) -> None:
        """Bitta katakni yozadi — RAW, faqat shu katak (butun qator emas)."""
        ws = self._worksheet()
        cell = f"{str(column_letter).upper()}{int(sheet_row)}"
        try:
            ws.update(cell, [[str(value)]], value_input_option="RAW")
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    def ping(self) -> str:
        """Aloqani tekshiradi. Muvaffaqiyatli bo'lsa qisqa tavsif qaytaradi."""
        ws = self._worksheet()
        try:
            rows = ws.row_values(1)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc
        header = ", ".join(rows) if rows else "(bo'sh)"
        return f"Varaq: {self.worksheet_name}. Birinchi qator: {header}"


def _is_missing_worksheet(exc: BaseException) -> bool:
    try:
        import gspread.exceptions as gse
    except Exception:  # pragma: no cover
        return False
    return isinstance(exc, gse.WorksheetNotFound)


def _translate(exc: BaseException) -> SheetsError:
    """Xatoni tushunarli o'zbekcha xabarga aylantiradi (original saqlanadi)."""
    if isinstance(exc, SheetsError):
        return exc
    return SheetsError(friendly_error(exc))


__all__ = ["SheetsClient", "SheetsError", "build_row", "friendly_error"]
