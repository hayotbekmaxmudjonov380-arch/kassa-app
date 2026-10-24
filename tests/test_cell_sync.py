"""Katak darajasidagi sinxronizatsiya testlari (tarmoqsiz, soxta klient)."""

import os
import tempfile
import unittest

from cell_sync import CellSyncService
from storage import Storage


class FakeClient:
    """Google Sheets klientining yozuvchi o'rinbosari (faqat xotirada)."""

    def __init__(self, last_row=0, fail_seed=False, fail_open=False, fail_cell=False):
        self.last_row = last_row
        self.fail_seed = fail_seed
        self.fail_open = fail_open
        self.fail_cell = fail_cell
        self.calls = []          # umumiy tartib: ("headers"|"seed"|"open"|"cell", ...)
        self.opened = []
        self.cells = []

    def ensure_journal_headers(self):
        if self.fail_seed:
            raise ConnectionError("internet yo'q")
        self.calls.append(("headers",))

    def last_data_row(self):
        if self.fail_seed:
            raise ConnectionError("internet yo'q")
        return self.last_row

    def open_row(self, sheet_row, number, timestamp_text, kassir):
        if self.fail_open:
            raise ConnectionError("internet yo'q")
        self.calls.append(("open", sheet_row))
        self.opened.append((sheet_row, number, timestamp_text, kassir))

    def update_cell(self, column_letter, sheet_row, value):
        if self.fail_cell:
            raise ConnectionError("internet yo'q")
        self.calls.append(("cell", column_letter, sheet_row))
        self.cells.append((column_letter, sheet_row, value))


class CellSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(os.path.join(self.tmp.name, "cell.db"))
        self.fake = FakeClient(last_row=4)
        self.service = CellSyncService(
            self.storage,
            client_factory=lambda: self.fake,
            seed_timeout=0.05,
        )

    def tearDown(self):
        self.service.stop()
        self.storage.close()
        self.tmp.cleanup()

    def _open(self, number=1, kassir="Alisher"):
        return self.service.allocate_row(
            "23.10.2026", "2026-10-23 12:30:00", kassir, number
        )

    # ------------------------------------------------------------------ seed
    def test_seed_uses_last_filled_row_plus_one(self):
        self.assertTrue(self.service.ensure_seeded())
        self.assertEqual(self.storage.next_sheet_row(), 5)
        row = self._open()
        self.assertEqual(row["sheet_row"], 5)
        self.assertEqual(self.storage.next_sheet_row(), 6)

    def test_row_open_writes_a_b_j_in_one_call_before_cells(self):
        row = self._open(number=3)
        self.service.queue_cell(row, "C", "12000")
        self.service.queue_cell(row, "F", "tushlik")

        self.service.process_once()

        self.assertEqual(len(self.fake.opened), 1)
        sheet_row, number, stamp, kassir = self.fake.opened[0]
        self.assertEqual(sheet_row, 5)
        self.assertEqual(number, 3)
        self.assertEqual(stamp, "2026-10-23 12:30:00")
        self.assertEqual(kassir, "Alisher")
        # Qator avval ochiladi, keyin kataklar yuboriladi.
        self.assertEqual(self.fake.calls[0][0], "headers")
        self.assertEqual(self.fake.calls[1][0], "open")
        self.assertEqual(
            [c[1:] for c in self.fake.calls if c[0] == "cell"],
            [("C", 5), ("F", 5)],
        )
        self.assertEqual(
            self.fake.cells, [("C", 5, "12000"), ("F", 5, "tushlik")]
        )
        self.assertEqual(self.storage.pending_cell_count(), 0)
        self.assertTrue(self.service.status()["connected"])

    def test_cells_wait_until_row_is_opened(self):
        row = self._open()
        self.service.queue_cell(row, "D", "5000")
        self.fake.fail_open = True

        self.service.process_once()
        self.assertEqual(self.storage.pending_cell_count(), 1)
        self.assertEqual(self.storage.unopened_row_count(), 1)
        self.assertEqual(self.service.status()["pending"], 2)
        self.assertFalse(self.service.status()["connected"])

        self.fake.fail_open = False
        self.service.process_once()
        self.assertEqual(self.storage.pending_cell_count(), 0)
        self.assertEqual(self.storage.unopened_row_count(), 0)
        self.assertEqual(self.fake.cells, [("D", 5, "5000")])

    def test_failed_cell_stays_pending_and_retry_sends_it(self):
        row = self._open()
        self.service.queue_cell(row, "E", "7000")
        self.fake.fail_cell = True

        self.service.process_once()
        self.assertEqual(self.storage.pending_cell_count(), 1)
        self.assertIn("Internet", self.service.status()["error"])

        self.fake.fail_cell = False
        self.service.process_once()
        self.assertEqual(self.storage.pending_cell_count(), 0)
        self.assertEqual(self.fake.cells, [("E", 5, "7000")])

    def test_empty_value_does_not_create_new_cell(self):
        row = self._open()
        self.assertFalse(self.service.queue_cell(row, "C", ""))
        self.assertEqual(self.storage.pending_cell_count(), 0)

        self.assertTrue(self.service.queue_cell(row, "C", "1000"))
        self.assertTrue(self.service.queue_cell(row, "C", ""))
        self.assertEqual(self.storage.pending_cell_count(), 1)

    def test_next_sheet_row_never_restarts(self):
        first = self._open()
        second = self._open()
        self.assertEqual((first["sheet_row"], second["sheet_row"]), (5, 6))
        self.assertEqual(self.storage.next_sheet_row(), 7)
        # Kun yopilishi qator raqamiga ta'sir qilmaydi (u faqat vizual #).
        self.assertEqual(self.storage.journal_rows_count("23.10.2026"), 2)

    def test_uncertain_seed_is_corrected_before_any_write(self):
        self.fake.fail_seed = True
        first = self._open()
        second = self._open()
        self.assertEqual((first["sheet_row"], second["sheet_row"]), (2, 3))
        self.service.queue_cell(first, "C", "100")
        self.service.queue_cell(second, "C", "200")

        self.fake.fail_seed = False
        self.fake.last_row = 10
        self.service.process_once()

        # Ikkala qator ham haqiqiy jadvaldan keyin qayta raqamlanadi.
        self.assertEqual([o[0] for o in self.fake.opened], [11, 12])
        self.assertEqual(
            sorted(self.fake.cells), [("C", 11, "100"), ("C", 12, "200")]
        )
        self.assertEqual(self.storage.next_sheet_row(), 13)

    def test_status_counts_cells_and_unopened_rows(self):
        row = self._open()
        self.service.queue_cell(row, "C", "1")
        self.service.queue_cell(row, "D", "2")
        status = self.service.status()
        self.assertEqual(status["pending"], 3)  # 1 qator + 2 katak

    # ------------------------------------------------------------ migratsiya
    def test_legacy_pending_record_moves_into_new_journal(self):
        from cell_sync import migrate_legacy_records

        record_id = self.storage.add(
            sana="22.10.2026",
            vaqt="21:41",
            kassir="hayotbek",
            turi="Kirim",
            tolov_usuli="Naqd",
            summa=22222,
            izoh="stol 3",
        )
        self.assertEqual(self.storage.pending_count(), 1)

        migrated = migrate_legacy_records(self.storage, self.service)

        self.assertEqual(migrated, 1)
        self.assertEqual(self.storage.pending_count(), 0)
        self.assertEqual(self.storage.pending_records(), [])
        self.assertEqual(record_id, 1)

        self.assertEqual(self.storage.pending_cell_count(), 2)
        unopened = self.storage.unopened_rows()
        self.assertEqual(len(unopened), 1)
        self.assertEqual(unopened[0]["sheet_row"], 5)
        self.assertEqual(unopened[0]["number"], 1)
        self.assertEqual(unopened[0]["timestamp_text"], "2026-10-22 21:41:00")
        self.assertEqual(unopened[0]["kassir"], "hayotbek")

        # Fon oqimi qatorni ochib, ikkala katakni yozadi.
        self.service.process_once()
        self.assertEqual(
            self.fake.opened, [(5, 1, "2026-10-22 21:41:00", "hayotbek")]
        )
        self.assertEqual(
            self.fake.cells, [("C", 5, "22222"), ("F", 5, "stol 3")]
        )
        self.assertEqual(self.storage.pending_cell_count(), 0)

        # Idempotent: ikkinchi marta hech narsa o'tkazilmaydi.
        self.assertEqual(migrate_legacy_records(self.storage, self.service), 0)


if __name__ == "__main__":
    unittest.main()
