"""Web API: qator ochish, katak yuborish va /api/add integratsiyasi."""

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import web_app
from cell_sync import CellSyncService
from drafts import DraftStore
from helpers import today_str
from settings import AppSettings
from storage import Storage
from tests.test_cell_sync import FakeClient


class WebCellApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(os.path.join(self.tmp.name, "web.db"))
        self.fake = FakeClient(last_row=4)
        self.ctx = SimpleNamespace(
            settings=AppSettings(os.path.join(self.tmp.name, "settings.json")),
            storage=self.storage,
            drafts=DraftStore(os.path.join(self.tmp.name, "drafts.json")),
            cells=CellSyncService(
                self.storage,
                client_factory=lambda: self.fake,
                seed_timeout=0.05,
            ),
            sync=SimpleNamespace(request=lambda *args: None),
        )
        self._old_context = web_app._context
        web_app._context = self.ctx
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web_app.RequestHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        web_app._context = self._old_context
        self.ctx.cells.stop()
        self.storage.close()
        self.tmp.cleanup()

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def _post(self, path, payload):
        request = urllib.request.Request(
            self._url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get(self, path):
        with urllib.request.urlopen(self._url(path)) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_row_open_then_cell_then_add(self):
        opened = self._post("/api/row/open", {"kassir": "Alisher"})
        self.assertTrue(opened["ok"])
        self.assertEqual(opened["sheet_row"], 5)
        self.assertEqual(opened["number"], 1)
        row_id = opened["row_id"]

        cell = self._post(
            "/api/cell", {"row_id": row_id, "field": "kn", "value": "12 000"}
        )
        self.assertTrue(cell["ok"])
        self.assertTrue(cell["queued"])
        # 1 katak + hali ochilmagan qator = 2 ta navbatdagi birlik.
        self.assertEqual(cell["pending"], 2)

        empty = self._post(
            "/api/cell", {"row_id": row_id, "field": "kc", "value": ""}
        )
        self.assertTrue(empty["ok"])
        self.assertFalse(empty["queued"])

        added = self._post(
            "/api/add",
            {
                "kassir": "Alisher",
                "turi": "Kirim",
                "tolov_usuli": "Naqd",
                "summa": 12000,
                "vaqt": "12:30",
                "izoh": "",
                "row_id": row_id,
            },
        )
        self.assertTrue(added["ok"])
        self.assertEqual(added["sheet_row"], 5)

        records = self.storage.today_records(today_str())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "sent")
        self.assertEqual(records[0]["sheet_row"], 5)

        data = self._get("/api/data")
        self.assertEqual(data["cell_pending"], 2)
        # Seed muvaffaqiyatli o'tdi (soxta klient), lekin kataklar hali navbatda.
        self.assertTrue(data["sheets_connected"])

        # Fon oqimi (bu yerda qo'lda) qatorni ochib, katakni yozadi.
        self.ctx.cells.process_once()
        self.assertEqual(self.fake.opened[0][0], 5)
        self.assertEqual(self.fake.cells, [("C", 5, "12000")])

        data = self._get("/api/data")
        self.assertEqual(data["cell_pending"], 0)
        self.assertTrue(data["sheets_connected"])

    def test_cell_for_unknown_row_is_rejected(self):
        result = self._post(
            "/api/cell", {"row_id": 999, "field": "kn", "value": "100"}
        )
        self.assertFalse(result["ok"])

    def test_unknown_field_is_rejected(self):
        opened = self._post("/api/row/open", {"kassir": "Alisher"})
        result = self._post(
            "/api/cell",
            {"row_id": opened["row_id"], "field": "hacker", "value": "1"},
        )
        self.assertFalse(result["ok"])

    def test_row_open_requires_kassir(self):
        result = self._post("/api/row/open", {"kassir": ""})
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
