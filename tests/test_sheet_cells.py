"""Yangi jurnal kataklari uchun SheetsClient testlari (tarmoqsiz)."""

import unittest

from config import SHEET_HEADERS
from sheets import SheetsClient


class FakeWorksheet:
    def __init__(self, header=None, column=None):
        self.header = list(header if header is not None else SHEET_HEADERS)
        self.column = list(column if column is not None else ["#", "1", "2", "3"])
        self.updated = []
        self.batched = []
        self.columns_added = 0

    def row_values(self, index):
        return list(self.header) if int(index) == 1 else []

    def col_values(self, index):
        return list(self.column)

    def update(self, range_name, values, **kwargs):
        self.updated.append((range_name, values, kwargs.get("value_input_option")))

    def batch_update(self, data, **kwargs):
        self.batched.append((data, kwargs.get("value_input_option")))

    def add_columns(self, count):
        self.columns_added += int(count)


def make_client(ws):
    client = SheetsClient()
    client._ws = ws
    return client


class JournalSheetTest(unittest.TestCase):
    def test_update_cell_writes_only_that_cell_as_raw(self):
        ws = FakeWorksheet()
        make_client(ws).update_cell("C", 7, "12000")
        self.assertEqual(ws.updated, [("C7", [["12000"]], "RAW")])

    def test_open_row_is_one_batch_with_a_b_j_raw(self):
        ws = FakeWorksheet()
        make_client(ws).open_row(7, 3, "2026-10-23 12:30:00", "Alisher")

        self.assertEqual(len(ws.batched), 1)
        data, option = ws.batched[0]
        self.assertEqual(option, "RAW")
        self.assertEqual(
            data,
            [
                {"range": "A7", "values": [[3]]},
                {"range": "B7", "values": [["2026-10-23 12:30:00"]]},
                {"range": "J7", "values": [["Alisher"]]},
            ],
        )
        # Butun qator qayta yozilmaydi.
        self.assertEqual(ws.updated, [])

    def test_headers_are_written_when_row_one_is_empty(self):
        ws = FakeWorksheet(header=[])
        make_client(ws).ensure_journal_headers()
        self.assertEqual(len(ws.updated), 1)
        range_name, values, option = ws.updated[0]
        self.assertEqual(range_name, "A1:J1")
        self.assertEqual(values, [SHEET_HEADERS])
        self.assertEqual(option, "RAW")

    def test_headers_are_not_rewritten_when_they_match(self):
        ws = FakeWorksheet()
        make_client(ws).ensure_journal_headers()
        self.assertEqual(ws.updated, [])

    def test_last_data_row_ignores_empty_tail(self):
        ws = FakeWorksheet(column=["#", "1", "2", "", ""])
        self.assertEqual(make_client(ws).last_data_row(), 3)

    def test_last_data_row_on_empty_sheet(self):
        ws = FakeWorksheet(column=[])
        self.assertEqual(make_client(ws).last_data_row(), 0)

    def test_old_append_refuses_to_touch_new_journal(self):
        """Eski 8 ustunli append yangi A..J jadvalini buzmasligi kerak."""
        from sheets import SheetsError

        ws = FakeWorksheet()
        with self.assertRaises(SheetsError):
            make_client(ws).ensure_headers()
        self.assertEqual(ws.updated, [])


if __name__ == "__main__":
    unittest.main()
