"""sheets.py uchun testlar (tarmoqqa ulanmasdan)."""

import unittest

from sheets import SheetsError, build_row, friendly_error


class BuildRowTest(unittest.TestCase):
    def test_row_has_eight_columns_in_order(self):
        record = {
            "sana": "22.10.2026",
            "vaqt": "10:15:00",
            "kassir": "Alisher",
            "turi": "Chiqim",
            "tolov_usuli": "Click",
            "summa": 125000,
            "izoh": "yetkazib beruvchi",
            "uid": "abc123",
        }
        row = build_row(record)
        self.assertEqual(len(row), 8)
        self.assertEqual(row, ["22.10.2026", "2026-10-22 10:15:00", "Alisher", "Chiqim", "Click", 125000.0, "yetkazib beruvchi", "abc123"])

    def test_missing_values_are_safe(self):
        row = build_row({})
        self.assertEqual(row[:5], ["", "", "", "", ""])
        self.assertEqual(row[5], 0.0)
        self.assertEqual(row[6], "")
        self.assertEqual(row[7], "")


class FriendlyErrorTest(unittest.TestCase):
    def test_missing_credentials_file(self):
        message = friendly_error(FileNotFoundError("service_account.json"))
        self.assertIn("service_account.json", message)

    def test_connection_error_is_explained(self):
        message = friendly_error(ConnectionError("no route to host"))
        self.assertIn("Internet", message)

    def test_sheets_error_is_passed_through(self):
        message = friendly_error(SheetsError("Varaq topilmadi"))
        self.assertEqual(message, "Varaq topilmadi")

    def test_requests_connection_error(self):
        import requests

        message = friendly_error(requests.exceptions.ConnectionError("dns fail"))
        self.assertIn("Internet", message)


if __name__ == "__main__":
    unittest.main()
