"""Lokal ombor (SQLite).

Internet uzilib qolsa ham yozuv yo'qolmaydi: har bir yozuv avval shu
yerda saqlanadi, keyin fon oqimida Google Sheets'ga yuboriladi.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from typing import Dict, List, Optional, Sequence

from config import DB_PATH, LEGACY_DB_PATH
from helpers import now_iso

STATUS_PENDING = "pending"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"

# `records` jadvalining to'liq ta'rifi — bitta joyda (takrorlanmaydi).
_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL,
    sana        TEXT    NOT NULL,
    vaqt        TEXT    NOT NULL,
    kassir      TEXT    NOT NULL,
    turi        TEXT    NOT NULL,
    tolov_usuli TEXT    NOT NULL,
    summa       REAL    NOT NULL,
    izoh        TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT    NOT NULL DEFAULT '',
    uid         TEXT    NOT NULL DEFAULT ''
);
"""

# Indekslar jadval yaratilgandan SO'NG qo'shiladi (migratsiyadan keyin).
_INDEX_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);
CREATE INDEX IF NOT EXISTS idx_records_sana   ON records(sana);
CREATE INDEX IF NOT EXISTS idx_records_uid    ON records(uid);
"""

# Uzilishdan qolgan yozuvlar — alohida jadvalda saqlanadi, chunki ularning
# "sending" holati tiklanganda "pending"ga o'tadi va holat yo'qoladi.
# Bu ro'yxat bazada saqlanadi, shuning uchun dastur qayta ishga tushsa ham
# ma'lumot saqlanib qoladi.
_RECOVERED_SCHEMA = """
CREATE TABLE IF NOT EXISTS recovered_records (
    id         INTEGER PRIMARY KEY,
    recovered_at TEXT NOT NULL
);
"""

# Kalit/qiymat ombori: `next_sheet_row` kabi global qiymatlar shu yerda.
_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Jurnal qatorlari — Google Sheets'dagi qatorga bog'lanish.
_JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_rows (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_row      INTEGER,
    number         INTEGER NOT NULL DEFAULT 0,
    row_date       TEXT    NOT NULL,
    opened_at      TEXT    NOT NULL,
    timestamp_text TEXT    NOT NULL,
    kassir         TEXT    NOT NULL DEFAULT '',
    sheet_opened   INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_journal_opened ON journal_rows(sheet_opened);
"""

# Katak darajasidagi navbat — har bir katak alohida yuboriladi.
_CELL_SCHEMA = """
CREATE TABLE IF NOT EXISTS cell_updates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id        INTEGER NOT NULL,
    sheet_row     INTEGER NOT NULL,
    column_letter TEXT    NOT NULL,
    value         TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL DEFAULT 'pending',
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    error         TEXT    NOT NULL DEFAULT '',
    UNIQUE(sheet_row, column_letter)
);
CREATE INDEX IF NOT EXISTS idx_cell_status ON cell_updates(status, updated_at);
"""

CELL_PENDING = "pending"
CELL_SYNCED = "synced"


class Storage:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        folder = os.path.dirname(os.path.abspath(path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        self._migrate_legacy_db()
        self._lock = threading.RLock()
        self._conn = self._connect()
        with self._lock:
            # Eski bazada `records` jadvali `uid` ustunisiz bo'lishi mumkin —
            # shuning uchun avval jadvalni yaratamiz, keyin ustunlarni
            # migratsiya qilamiz va faqat shundan keyin indeks qo'shamiz.
            self._conn.executescript(_TABLE_SCHEMA)
            self._conn.commit()
            self._conn.executescript(_RECOVERED_SCHEMA)
            self._conn.commit()
            self._conn.executescript(_META_SCHEMA)
            self._conn.executescript(_JOURNAL_SCHEMA)
            self._conn.executescript(_CELL_SCHEMA)
            self._conn.commit()
            self._migrate_columns()
            self._conn.executescript(_INDEX_SCHEMA)
            self._conn.commit()

    # --------------------------------------------------------- ulanish (WAL)
    def _connect(self) -> sqlite3.Connection:
        """Bazaga ulanadi — eskirgan '-shm' hosila faylni tozalab.

        SQLite WAL rejimida `-shm` fayl umumiy xotira indeksini saqlaydi.
        Agar dastur g'ajarib qolsa yoki tizim to'xtab qolsa, bu fayl
        "eskirgan" holatda qoladi va SQLite `disk I/O error` xatosini beradi.
        `-shm` — vaqtinchalik hosila fayl, uni o'chirish ma'lumotga tegmaydi
        (haqiqiy ma'lumot `-wal` va `.db` fayllarida saqlanadi).
        """
        last_error: Exception | None = None
        for _attempt in range(2):
            conn = None
            try:
                conn = sqlite3.connect(self.path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute("PRAGMA busy_timeout=10000")
                return conn
            except sqlite3.Error as exc:
                last_error = exc
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
                # Noto'g'ri qoldirilgan WAL indeksini tozalab, qayta urinib ko'ramiz.
                self._remove_stale_shared_memory()
        raise last_error  # type: ignore[misc]

    def _remove_stale_shared_memory(self) -> None:
        """`path-shm` hosila faylini o'chish (ma'lumotga tegmaydi)."""
        for suffix in ("-shm",):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    # ------------------------------------------------------------- sxema
    def _migrate_columns(self) -> None:
        """Eski bazalarga yangi ustunlarni qo'shadi (ALTER TABLE)."""
        try:
            cur = self._conn.execute("PRAGMA table_info(records)")
            existing = {str(row[1]) for row in cur.fetchall()}
        except sqlite3.Error:
            return
        if not existing:
            return
        # Yangi ustunlar va ularning SQL ta'rifi.
        new_columns = {
            "status": "TEXT    NOT NULL DEFAULT 'pending'",
            "attempts": "INTEGER NOT NULL DEFAULT 0",
            "last_error": "TEXT    NOT NULL DEFAULT ''",
            "uid": "TEXT    NOT NULL DEFAULT ''",
            "sheet_row": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, ddl in new_columns.items():
            if column in existing:
                continue
            try:
                self._conn.execute(
                    f"ALTER TABLE records ADD COLUMN {column} {ddl}"
                )
                self._conn.commit()
            except sqlite3.Error:
                pass
        # Bo'sh UID'larga yangi identifikator beramiz — eski yozuvlar ham
        # dedupe tekshiruvidan o'tsin.
        try:
            rows = self._conn.execute(
                "SELECT id FROM records WHERE uid IS NULL OR uid = ''"
            ).fetchall()
            if rows:
                self._conn.executemany(
                    "UPDATE records SET uid = ? WHERE id = ?",
                    [(uuid.uuid4().hex, row[0]) for row in rows],
                )
                self._conn.commit()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------- migratsiya
    def _migrate_legacy_db(self) -> None:
        """Eski bazani (dastur papkasida) yangi joyga ko'chiradi.

        Eski versiya bazani dastur papkasida saqlardi — u OneDrive ichida
        bo'lishi mumkin edi va chiroq uzilganda buzilishi mumkin edi.
        Birinchi ishga tushirishda ma'lumotlar yangi (xavfsiz) joyga ko'chiriladi.

        Ko'chirish SQLite'ning o'z `backup()` API'si orqali qilinadi — bu
        faylni bloklamaydi va WAL ma'lumotlarini ham to'liq olib ketadi.
        """
        legacy = LEGACY_DB_PATH
        if os.path.abspath(self.path) == os.path.abspath(legacy):
            return
        # Migratsiya faqat asosiy bazaga qo'llaniladi — test va vaqtinchalik
        # bazalarga legacy faylni ko'chirish mantiqan noto'g'ri.
        if os.path.abspath(self.path) != os.path.abspath(DB_PATH):
            return
        if not os.path.isfile(legacy):
            return
        # Yangi baz allaqon mavjud bo'lsa — ko'chirmaymiz (ikki bazani aralashtirmaymiz).
        if os.path.exists(self.path):
            return

        try:
            src = sqlite3.connect(legacy)
            try:
                dst = sqlite3.connect(self.path)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
        except sqlite3.Error:
            # Ko'chirish mumkin bo'lmasa — asl fayl joyida qoladi, yangi baz bo'sh.
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(self.path + suffix)
                except OSError:
                    pass
            # Legacy bazaning eskirgan WAL indeksi bo'lishi mumkin — uni
            # tozalab, ko'chirishni yana bir marta urinib ko'ramiz.
            legacy_shm = legacy + "-shm"
            if os.path.exists(legacy_shm):
                try:
                    os.remove(legacy_shm)
                except OSError:
                    pass
                try:
                    src = sqlite3.connect(legacy)
                    try:
                        dst = sqlite3.connect(self.path)
                        try:
                            src.backup(dst)
                        finally:
                            dst.close()
                    finally:
                        src.close()
                except sqlite3.Error:
                    for suffix in ("", "-wal", "-shm"):
                        try:
                            os.remove(self.path + suffix)
                        except OSError:
                            pass

    # ------------------------------------------------------------------ yozish
    def add(
        self,
        *,
        sana: str,
        vaqt: str,
        kassir: str,
        turi: str,
        tolov_usuli: str,
        summa: float,
        izoh: str = "",
        created_at: Optional[str] = None,
        uid: Optional[str] = None,
        sheet_row: int = 0,
        status: Optional[str] = None,
    ) -> int:
        return self.add_many(
            [
                {
                    "sana": sana,
                    "vaqt": vaqt,
                    "kassir": kassir,
                    "turi": turi,
                    "tolov_usuli": tolov_usuli,
                    "summa": summa,
                    "izoh": izoh,
                    "created_at": created_at,
                    "uid": uid,
                    "sheet_row": sheet_row,
                    "status": status,
                }
            ]
        )[0]

    def add_many(self, records: Sequence[Dict]) -> List[int]:
        """Bir nechta yozuvni bitta tranzaksiyada saqlaydi (atomik).

        Yarim saqlash bo'lmaydi: yoki hammasi yoziladi, yoki hech biri.
        `add()` shu metod orqali ishlaydi.
        """
        if not records:
            return []
        with self._lock:
            cur = self._conn.cursor()
            ids: List[int] = []
            for record in records:
                status = str(record.get("status") or STATUS_PENDING)
                if status not in (STATUS_PENDING, STATUS_SENDING, STATUS_SENT):
                    status = STATUS_PENDING
                cur.execute(
                    """
                    INSERT INTO records
                        (created_at, sana, vaqt, kassir, turi, tolov_usuli, summa, izoh,
                         status, uid, sheet_row)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.get("created_at") or now_iso(),
                        str(record.get("sana") or ""),
                        str(record.get("vaqt") or ""),
                        str(record.get("kassir") or ""),
                        str(record.get("turi") or ""),
                        str(record.get("tolov_usuli") or ""),
                        float(record.get("summa") or 0),
                        str(record.get("izoh") or ""),
                        status,
                        record.get("uid") or uuid.uuid4().hex,
                        int(record.get("sheet_row") or 0),
                    ),
                )
                ids.append(int(cur.lastrowid))
            self._conn.commit()
            return ids

    def mark_sending(self, ids: Sequence[int]) -> None:
        """Yuborish boshlandi deb belgilaydi.

        Bu holat 'pending' va 'sent' orasidagi o'tish holati. Agar jarayon
        shu yerda to'xtasa (chiroq uzilishi), yozuv 'sending' holatida qoladi
        va tiklashda Sheets'dan tekshiriladi — qayta yuborilmaydi.
        """
        if not ids:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE records SET status = ? WHERE id = ?",
                [(STATUS_SENDING, i) for i in ids],
            )
            self._conn.commit()

    def mark_sent(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE records SET status = ?, last_error = '' WHERE id = ?",
                [(STATUS_SENT, i) for i in ids],
            )
            self._conn.commit()

    def set_record_sheet_row(self, record_id: int, sheet_row: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE records SET sheet_row = ? WHERE id = ?",
                (int(sheet_row), int(record_id)),
            )
            self._conn.commit()

    def mark_pending(self, ids: Sequence[int], error: str = "") -> None:
        """Yuborilmagan yozuvlarni qayta navbatga qo'yadi (urinishlar sanaladi)."""
        if not ids:
            return
        with self._lock:
            self._conn.executemany(
                """
                UPDATE records
                   SET status = ?, attempts = attempts + 1, last_error = ?
                 WHERE id = ?
                """,
                [(STATUS_PENDING, (error or "")[:500], i) for i in ids],
            )
            self._conn.commit()

    def hold_pending(self, ids: Sequence[int], error: str = "") -> None:
        """Yozuvni navbatda ushlab turadi (urinishlar soni oshmasligi kerak).

        Bu holatda yozuv yuborilmadi, lekin "muvaffaqiyatsiz urinish" ham
        sanalmaydi — dedupe tekshiruvi keyinroq bajarilishi mumkin.
        """
        if not ids:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE records SET status = ?, last_error = ? WHERE id = ?",
                [(STATUS_PENDING, (error or "")[:500], i) for i in ids],
            )
            self._conn.commit()

    def sending_records(self) -> List[Dict]:
        """Yuborilayotgan, lekin tasdiqlanmagan yozuvlar (chiroq uzilishi holati)."""
        return self._rows(
            "SELECT * FROM records WHERE status = ? ORDER BY id ASC", (STATUS_SENDING,)
        )

    def reset_stuck_sending(self) -> int:
        """'sending' holatida qolgan yozuvlarni 'pending'ga qaytaradi.

        Dastur ishga tushganda chaqiriladi — o'tkazilmagan yozuvlar
        yana navbatga tushadi (lekin UID orqali duplikat tekshiruvidan o'tadi).
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE records SET status = ? WHERE status = ?",
                (STATUS_PENDING, STATUS_SENDING),
            )
            self._conn.commit()
            return cur.rowcount or 0

    # --------------------------------------------------- tiklangan yozuvlar
    def note_recovered(self, ids: Sequence[int]) -> None:
        """Uzilishdan qolgan yozuvlarni belgilaydi.

        Bu yozuvlar Sheets'ga ehtimol yuborilgan. Dedupe tekshiruvi
        ishlamagan holatda ularni qayta yuborish xavfsiz emas — shuning
        uchun ular navbatda ushlab turiladi.
        """
        if not ids:
            return
        with self._lock:
            stamp = now_iso()
            self._conn.executemany(
                "INSERT OR REPLACE INTO recovered_records (id, recovered_at) VALUES (?, ?)",
                [(int(i), stamp) for i in ids],
            )
            self._conn.commit()

    def recovered_ids(self) -> set:
        """Uzilishdan qolgan, hali tasdiqlanmagan yozuvlar ID'lar to'plami."""
        with self._lock:
            rows = self._rows("SELECT id FROM recovered_records")
            return {int(r["id"]) for r in rows}

    def clear_recovered(self, ids: Sequence[int]) -> None:
        """Yozuvlar tasdiqlandi — ular tiklanganlar ro'yxatidan chiqadi."""
        if not ids:
            return
        with self._lock:
            self._conn.executemany(
                "DELETE FROM recovered_records WHERE id = ?",
                [(int(i),) for i in ids],
            )
            self._conn.commit()

    def uids(self, ids: Sequence[int]) -> List[str]:
        """Ko'rsatilgan yozuvlarning UID ro'yxati."""
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self._rows(
            f"SELECT uid FROM records WHERE id IN ({placeholders})", tuple(ids)
        )
        return [str(r.get("uid") or "") for r in rows]

    # ------------------------------------------------------------------ o'qish
    def _rows(self, sql: str, params: Sequence = ()) -> List[Dict]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
        return rows

    def pending_records(self) -> List[Dict]:
        return self._rows(
            "SELECT * FROM records WHERE status = ? ORDER BY id ASC", (STATUS_PENDING,)
        )

    def pending_count(self) -> int:
        rows = self._rows("SELECT COUNT(*) AS n FROM records WHERE status = ?", (STATUS_PENDING,))
        return int(rows[0]["n"])

    def last_error(self) -> str:
        rows = self._rows(
            "SELECT last_error FROM records WHERE status = ? AND last_error <> '' ORDER BY id DESC LIMIT 1",
            (STATUS_PENDING,),
        )
        return rows[0]["last_error"] if rows else ""

    def today_stats(self, sana: str) -> Dict[str, float]:
        rows = self._rows(
            """
            SELECT turi, COALESCE(SUM(summa), 0) AS jami
              FROM records
             WHERE sana = ?
             GROUP BY turi
            """,
            (sana,),
        )
        stats = {"Kirim": 0.0, "Chiqim": 0.0}
        for row in rows:
            if row["turi"] in stats:
                stats[row["turi"]] = float(row["jami"])
        return stats

    def today_records(self, sana: str, since_iso: str = "") -> List[Dict]:
        if since_iso:
            return self._rows(
                """
                SELECT id, created_at, sana, vaqt, kassir, turi, tolov_usuli, summa, izoh,
                       status, last_error, attempts, sheet_row
                  FROM records
                 WHERE sana = ? AND created_at > ?
                 ORDER BY id ASC
                """,
                (sana, since_iso),
            )
        return self._rows(
            """
            SELECT id, created_at, sana, vaqt, kassir, turi, tolov_usuli, summa, izoh,
                   status, last_error, attempts, sheet_row
              FROM records
             WHERE sana = ?
             ORDER BY id ASC
            """,
            (sana,),
        )

    def today_detailed_stats(self, sana: str, since_iso: str = "") -> Dict[str, float]:
        if since_iso:
            rows = self._rows(
                """
                SELECT turi, tolov_usuli, COALESCE(SUM(summa), 0) AS jami
                  FROM records
                 WHERE sana = ? AND created_at > ?
                 GROUP BY turi, tolov_usuli
                """,
                (sana, since_iso),
            )
        else:
            rows = self._rows(
                """
                SELECT turi, tolov_usuli, COALESCE(SUM(summa), 0) AS jami
                  FROM records
                 WHERE sana = ?
                 GROUP BY turi, tolov_usuli
                """,
                (sana,),
            )
        stats = {
            "Kirim_Naqd": 0.0,
            "Kirim_Click": 0.0,
            "Kirim_Terminal": 0.0,
            "Chiqim_Naqd": 0.0,
            "Chiqim_Click": 0.0,
        }
        for row in rows:
            key = f"{row['turi']}_{row['tolov_usuli']}"
            if key in stats:
                stats[key] = float(row["jami"])
        return stats

    def recent(self, limit: int = 15) -> List[Dict]:
        return self._rows(
            """
            SELECT sana, vaqt, kassir, turi, tolov_usuli, summa, izoh,
                   status, last_error, attempts, created_at, uid, sheet_row
              FROM records
             ORDER BY id DESC
             LIMIT ?
            """,
            (int(limit),),
        )

    def count(self) -> int:
        return int(self._rows("SELECT COUNT(*) AS n FROM records")[0]["n"])

    # ------------------------------------------------------- meta (kalit/qiymat)
    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        rows = self._rows("SELECT value FROM meta WHERE key = ?", (key,))
        return str(rows[0]["value"]) if rows else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(value)),
            )
            self._conn.commit()

    def next_sheet_row(self) -> int:
        """Keyingi bo'sh qator raqami. 0 — hali aniqlanmagan (seed kerak)."""
        try:
            return int(self.get_meta("next_sheet_row", "0") or 0)
        except (TypeError, ValueError):
            return 0

    def set_next_sheet_row(self, value: int) -> None:
        self.set_meta("next_sheet_row", str(int(value)))

    # ------------------------------------------------------ jurnal qatorlari
    def allocate_sheet_row(
        self, row_date: str, timestamp_text: str, kassir: str, number: int = 0
    ) -> Dict:
        """Yangi qatorga keyingi Sheets qator raqamini beradi (atomik)."""
        with self._lock:
            row = self.next_sheet_row()
            uncertain = row < 2
            if uncertain:
                from config import SHEET_FIRST_DATA_ROW

                row = SHEET_FIRST_DATA_ROW
                self.set_meta("seed_uncertain", "1")
            cur = self._conn.execute(
                """
                INSERT INTO journal_rows
                    (sheet_row, number, row_date, opened_at, timestamp_text, kassir, sheet_opened)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (row, int(number or 0), row_date, now_iso(), timestamp_text, kassir),
            )
            self._conn.commit()
            row_id = int(cur.lastrowid)
            self.set_next_sheet_row(row + 1)
            return {
                "id": row_id,
                "sheet_row": row,
                "number": int(number or 0),
                "row_date": row_date,
                "timestamp_text": timestamp_text,
                "kassir": kassir,
                "sheet_opened": 0,
                "seed_uncertain": uncertain,
            }

    def journal_row(self, row_id: int) -> Optional[Dict]:
        rows = self._rows(
            """
            SELECT id, sheet_row, number, row_date, opened_at, timestamp_text, kassir,
                   sheet_opened, last_error
              FROM journal_rows WHERE id = ?
            """,
            (int(row_id),),
        )
        return rows[0] if rows else None

    def journal_row_by_sheet_row(self, sheet_row: int) -> Optional[Dict]:
        """Jadval qator raqami bo'yicha jurnal qatorini topadi."""
        rows = self._rows(
            """
            SELECT id, sheet_row, number, row_date, opened_at, timestamp_text, kassir,
                   sheet_opened, last_error
              FROM journal_rows WHERE sheet_row = ?
             ORDER BY id DESC LIMIT 1
            """,
            (int(sheet_row),),
        )
        return rows[0] if rows else None

    def records_for_sheet_row(self, sheet_row: int) -> List[Dict]:
        """Bitta jurnal qatoriga bog'langan yozuvlar (tahrirlash uchun)."""
        return self._rows(
            """
            SELECT id, sana, vaqt, kassir, turi, tolov_usuli, summa, izoh, status
              FROM records WHERE sheet_row = ?
             ORDER BY id ASC
            """,
            (int(sheet_row),),
        )

    def update_record(
        self,
        record_id: int,
        *,
        summa: Optional[float] = None,
        izoh: Optional[str] = None,
    ) -> None:
        """Yozuvning summasi/izohini tuzatadi (jurnal qatorini tahrirlash)."""
        sets = []
        params: List = []
        if summa is not None:
            sets.append("summa = ?")
            params.append(float(summa))
        if izoh is not None:
            sets.append("izoh = ?")
            params.append(str(izoh))
        if not sets:
            return
        params.append(int(record_id))
        with self._lock:
            self._conn.execute(
                f"UPDATE records SET {', '.join(sets)} WHERE id = ?", params
            )
            self._conn.commit()

    def delete_record(self, record_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM records WHERE id = ?", (int(record_id),))
            self._conn.commit()

    def mark_row_opened(self, row_id: int, error: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE journal_rows SET sheet_opened = 1, last_error = ? WHERE id = ?",
                (error, int(row_id)),
            )
            self._conn.commit()

    def mark_row_open_error(self, row_id: int, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE journal_rows SET last_error = ? WHERE id = ?",
                (error, int(row_id)),
            )
            self._conn.commit()

    def unopened_rows(self, limit: int = 20) -> List[Dict]:
        return self._rows(
            """
            SELECT id, sheet_row, number, row_date, timestamp_text, kassir, last_error
              FROM journal_rows
             WHERE sheet_opened = 0
             ORDER BY id ASC
             LIMIT ?
            """,
            (int(limit),),
        )

    def journal_rows_count(self, row_date: str) -> int:
        return int(
            self._rows(
                "SELECT COUNT(*) AS n FROM journal_rows WHERE row_date = ?",
                (str(row_date),),
            )[0]["n"]
        )

    def unopened_row_count(self) -> int:
        return int(
            self._rows(
                "SELECT COUNT(*) AS n FROM journal_rows WHERE sheet_opened = 0"
            )[0]["n"]
        )

    def reassign_unopened_rows(self, start_row: int) -> int:
        """Seed kechikganda qator raqamlarini haqiqiy jadvalga moslaydi.

        Faqat hali jadvalda ochilmagan qatorlarga qo'llanadi (ma'lumot
        yo'qolmaydi, chunki ular hali yozilmagan).
        """
        with self._lock:
            opened = self._rows(
                "SELECT COUNT(*) AS n FROM journal_rows WHERE sheet_opened = 1"
            )[0]["n"]
            if int(opened):
                return 0
            rows = self._rows("SELECT id FROM journal_rows ORDER BY id ASC")
            if not rows:
                self.set_meta("seed_uncertain", "0")
                return 0
            # Vaqtinchalik siljish — UNIQUE(sheet_row, column) to'qnashmasin.
            self._conn.execute("UPDATE cell_updates SET sheet_row = sheet_row + 100000")
            next_row = max(int(start_row), 2)
            for offset, row in enumerate(rows):
                target = next_row + offset
                self._conn.execute(
                    "UPDATE journal_rows SET sheet_row = ? WHERE id = ?",
                    (target, row["id"]),
                )
                self._conn.execute(
                    "UPDATE cell_updates SET sheet_row = ? WHERE row_id = ?",
                    (target, row["id"]),
                )
            self._conn.commit()
            self.set_next_sheet_row(next_row + len(rows))
            self.set_meta("seed_uncertain", "0")
            return len(rows)

    # --------------------------------------------------------- katak navbati
    def queue_cell(
        self, row_id: int, sheet_row: int, column_letter: str, value: str
    ) -> bool:
        """Katakni navbatga qo'yadi. Bo'sh qiymat yangi katakni yaratmaydi.

        Qoida: hali to'ldirilmagan katak ustiga bo'sh satr yozilmaydi.
        """
        value = "" if value is None else str(value)
        column_letter = str(column_letter).upper()
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM cell_updates WHERE sheet_row = ? AND column_letter = ?",
                (int(sheet_row), column_letter),
            ).fetchone()
            if value == "" and existing is None:
                return False
            now = now_iso()
            self._conn.execute(
                """
                INSERT INTO cell_updates
                    (row_id, sheet_row, column_letter, value, status, created_at, updated_at, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, '')
                ON CONFLICT(sheet_row, column_letter) DO UPDATE SET
                    value = excluded.value,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    error = ''
                """,
                (
                    int(row_id),
                    int(sheet_row),
                    column_letter,
                    value,
                    CELL_PENDING,
                    now,
                    now,
                ),
            )
            self._conn.commit()
            return True

    def pending_cells(self, limit: int = 50) -> List[Dict]:
        """Yuborilishi kerak kataklar — eng eski o'zgarish birinchi."""
        return self._rows(
            """
            SELECT c.id, c.row_id, c.sheet_row, c.column_letter, c.value,
                   c.status, c.updated_at, c.error
              FROM cell_updates c
              JOIN journal_rows r ON r.id = c.row_id
             WHERE c.status = ? AND r.sheet_opened = 1
             ORDER BY c.updated_at ASC, c.id ASC
             LIMIT ?
            """,
            (CELL_PENDING, int(limit)),
        )

    def mark_cells_synced(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE cell_updates SET status = ?, error = '' WHERE id = ?",
                [(CELL_SYNCED, int(i)) for i in ids],
            )
            self._conn.commit()

    def mark_cell_error(self, cell_id: int, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE cell_updates SET status = ?, error = ? WHERE id = ?",
                (CELL_PENDING, str(error)[:500], int(cell_id)),
            )
            self._conn.commit()

    def pending_cell_count(self) -> int:
        """Barcha navbatdagi kataklar (qatori hali ochilmagan bo'lsa ham)."""
        return int(
            self._rows(
                "SELECT COUNT(*) AS n FROM cell_updates WHERE status = ?",
                (CELL_PENDING,),
            )[0]["n"]
        )

    def last_cell_error(self) -> str:
        rows = self._rows(
            """
            SELECT error FROM cell_updates
             WHERE status = ? AND error <> ''
             ORDER BY updated_at DESC LIMIT 1
            """,
            (CELL_PENDING,),
        )
        return str(rows[0]["error"]) if rows else ""

    def cell_status(self, sheet_row: int) -> List[Dict]:
        return self._rows(
            """
            SELECT column_letter, value, status FROM cell_updates
             WHERE sheet_row = ? ORDER BY column_letter
            """,
            (int(sheet_row),),
        )

    # ---------------------------------------------------------------- yedek
    def backup_to(self, dest_path: str) -> bool:
        """Bazani xavfsiz nusxa qiladi (SQLite `backup()` orqali).

        Oddiy fayl ko'chirishdan farqli: bu faylni bloklamaydi va WAL
        ma'lumotlarini ham to'liq olib ketadi — chiroq uzilganda ham
        nusxa butun bo'ladi.
        """
        try:
            os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        except OSError:
            pass
        try:
            with self._lock:
                dst = sqlite3.connect(dest_path)
                try:
                    self._conn.backup(dst)
                finally:
                    dst.close()
            return True
        except sqlite3.Error:
            return False

    def close(self) -> None:
        with self._lock:
            try:
                # WAL'dagi barcha commit'larni .db fayliga ko'chiramiz — shunda
                # qo'lda fayl nusxalash yoki OneDrive sinxronizatsiyasida
                # WAL ma'lumotlari yo'qolmaydi.
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
            except Exception:  # noqa: BLE001
                pass
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def checkpoint(self) -> None:
        """WAL'ni .db fayliga to'liq ko'chiradi (quvvat uzilishidan himoya)."""
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._conn.commit()
            except sqlite3.Error:
                pass


__all__ = ["Storage", "STATUS_PENDING", "STATUS_SENT"]
