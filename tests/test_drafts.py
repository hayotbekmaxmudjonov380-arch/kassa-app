"""DraftStore: eski (faqat Vaqt yozilgan) qatorlarni tozalash testlari."""

import json
import os
import tempfile
import unittest

from drafts import DraftStore


class DraftStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "drafts.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rows):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"rows": rows}, fh)

    def test_time_only_rows_are_dropped_on_load(self):
        self._write(
            [
                {"vt": "11:44", "kn": "", "kc": "", "ki": "", "cn": ""},
                {"vt": "13:14", "kn": "10000", "rid": "3", "sr": "4"},
                {"vt": "11:44"},
            ]
        )
        rows = DraftStore(self.path).rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kn"], "10000")
        self.assertEqual(rows[0]["rid"], "3")

    def test_opened_row_without_values_is_kept(self):
        self._write([{"vt": "12:00", "rid": "7", "sr": "8", "kn": ""}])
        rows = DraftStore(self.path).rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sr"], "8")

    def test_note_only_row_is_kept(self):
        self._write([{"vt": "12:00", "ki": "stol 5"}])
        rows = DraftStore(self.path).rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ki"], "stol 5")

    def test_has_content_ignores_time_only_rows(self):
        self._write([{"vt": "11:44"}, {"vt": "11:44", "kn": ""}])
        self.assertFalse(DraftStore(self.path).has_content())
        self._write([{"vt": "11:44"}, {"kn": "5000"}])
        self.assertTrue(DraftStore(self.path).has_content())

    def test_save_then_load_keeps_values_in_order(self):
        store = DraftStore(self.path)
        store.save([{"kn": "1"}, {}, {"kn": "3", "vt": "12:00"}], kassir="A")
        rows = DraftStore(self.path).rows()
        self.assertEqual([row.get("kn") for row in rows], ["1", "3"])
        self.assertEqual(rows[1]["vt"], "12:00")


if __name__ == "__main__":
    unittest.main()
