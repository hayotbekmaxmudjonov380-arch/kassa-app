"""storage.py uchun testlar (SQLite navbat)."""

import os
import sqlite3
import tempfile
import unittest

from storage import STATUS_PENDING, STATUS_SENT, Storage


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    def _add(self, **kwargs):
        payload = dict(
            sana="22.10.2026",
            vaqt="10:15:00",
            kassir="Alisher",
            turi="Kirim",
            tolov_usuli="Naqd",
            summa=15000,
            izoh="stol 5",
        )
        payload.update(kwargs)
        return self.storage.add(**payload)

    def test_add_stores_pending_record(self):
        record_id = self._add()
        self.assertGreater(record_id, 0)
        self.assertEqual(self.storage.pending_count(), 1)

        rows = self.storage.recent(10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kassir"], "Alisher")
        self.assertEqual(rows[0]["status"], STATUS_PENDING)

    def test_today_stats_groups_by_type_and_date(self):
        self._add(turi="Kirim", summa=15000)
        self._add(turi="Kirim", summa=5000)
        self._add(turi="Chiqim", summa=3000)
        self._add(turi="Chiqim", summa=1000, sana="21.10.2026")

        stats = self.storage.today_stats("22.10.2026")
        self.assertEqual(stats["Kirim"], 20000)
        self.assertEqual(stats["Chiqim"], 3000)

    def test_mark_sent_removes_from_queue(self):
        first = self._add()
        second = self._add()
        self.storage.mark_sent([first, second])
        self.assertEqual(self.storage.pending_count(), 0)
        self.assertEqual(self.storage.recent(10)[0]["status"], STATUS_SENT)

    def test_mark_pending_counts_attempts_and_keeps_error(self):
        record_id = self._add()
        self.storage.mark_pending([record_id], "Internet yo'q")
        row = self.storage.recent(10)[0]
        self.assertEqual(row["status"], STATUS_PENDING)
        self.assertEqual(row["last_error"], "Internet yo'q")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(self.storage.last_error(), "Internet yo'q")

    def test_recent_limit(self):
        for _ in range(20):
            self._add()
        self.assertEqual(len(self.storage.recent(15)), 15)
        self.assertEqual(self.storage.count(), 20)

    # --------------------------------------------------------------- UID
    def test_uid_is_unique_and_persisted(self):
        first = self._add()
        second = self._add()
        uids = self.storage.uids([first, second])
        self.assertEqual(len(set(uids)), 2)
        self.assertTrue(all(uids))

    def test_add_many_saves_all_records_in_one_transaction(self):
        """Bir qatordagi bir nech summa bitta tranzaksiyada saqlanadi."""
        ids = self.storage.add_many(
            [
                {
                    "sana": "22.10.2026",
                    "vaqt": "10:15:00",
                    "kassir": "Alisher",
                    "turi": "Kirim",
                    "tolov_usuli": "Naqd",
                    "summa": 50000,
                    "izoh": "",
                },
                {
                    "sana": "22.10.2026",
                    "vaqt": "10:15:00",
                    "kassir": "Alisher",
                    "turi": "Chiqim",
                    "tolov_usuli": "Click",
                    "summa": 20000,
                    "izoh": "yetkazib berish",
                },
            ]
        )
        self.assertEqual(len(ids), 2)
        self.assertEqual(self.storage.count(), 2)
        uids = self.storage.uids(ids)
        self.assertEqual(len(set(uids)), 2)
        stats = self.storage.today_detailed_stats("22.10.2026")
        self.assertEqual(stats["Kirim_Naqd"], 50000)
        self.assertEqual(stats["Chiqim_Click"], 20000)

    def test_add_many_empty_list_is_noop(self):
        self.assertEqual(self.storage.add_many([]), [])
        self.assertEqual(self.storage.count(), 0)

    def test_hold_pending_keeps_status_without_counting_attempt(self):
        record_id = self._add()
        self.storage.hold_pending([record_id], "navbatda")
        row = self.storage.recent(10)[0]
        self.assertEqual(row["status"], STATUS_PENDING)
        self.assertEqual(row["attempts"], 0)
        self.assertEqual(row["last_error"], "navbatda")

    # ----------------------------------------------------- eskirgan -shm
    def test_stale_shm_file_does_not_break_open(self):
        """WAL indeksi eskirgan bo'lsa, baza yana ochilishi kerak.

        `-shm` — vaqtinchalik hosila fayl. Dastur to'xtab qolsa, u
        "eskirgan" holatda qoladi va SQLite `disk I/O error` beradi.
        """
        self.storage.close()
        shm = self.storage.path + "-shm"
        with open(shm, "wb") as fh:
            fh.write(b"\x00" * 4096)

        storage = Storage(self.storage.path)
        try:
            record_id = storage.add(
                sana="22.10.2026",
                vaqt="10:00:00",
                kassir="Alisher",
                turi="Kirim",
                tolov_usuli="Naqd",
                summa=1000,
                izoh="",
            )
            self.assertGreater(record_id, 0)
        finally:
            storage.close()

    # --------------------------------------------------------- migratsiya
    def test_old_database_without_uid_column_is_migrated(self):
        """Eski bazadagi `records` jadvaliga `uid` ustuni qo'shiladi."""
        self.storage.close()
        db_path = self.storage.path
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("DROP INDEX IF EXISTS idx_records_uid")
            conn.execute("ALTER TABLE records DROP COLUMN uid")
            conn.commit()
        finally:
            conn.close()

        storage = Storage(db_path)
        try:
            columns = {
                row[1]
                for row in storage._conn.execute("PRAGMA table_info(records)")
            }
            self.assertIn("uid", columns)
            # Bo'sh UID'lar to'ldirilishi kerak.
            storage.add(
                sana="22.10.2026",
                vaqt="10:00:00",
                kassir="Alisher",
                turi="Kirim",
                tolov_usuli="Naqd",
                summa=1000,
                izoh="",
            )
            uids = [r["uid"] for r in storage.recent(10)]
            self.assertTrue(all(uids))
        finally:
            storage.close()


if __name__ == "__main__":
    unittest.main()
