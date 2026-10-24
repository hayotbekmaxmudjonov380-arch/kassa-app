"""sync.py uchun testlar — tarmoqsiz, soxta klient bilan."""

import os
import tempfile
import unittest

from PyQt6.QtCore import QCoreApplication

from storage import Storage
from sync import REASON_MANUAL, SyncService


class FakeClient:
    def __init__(self, fail_headers: int = 0, fail_append: bool = False, fail_uids: bool = False):
        self.rows = []
        self.fail_headers = fail_headers
        self.fail_append = fail_append
        self.fail_uids = fail_uids
        self.known_uids = set()

    def ensure_headers(self):
        if self.fail_headers > 0:
            self.fail_headers -= 1
            raise ConnectionError("internet yo'q")

    def existing_uids(self):
        if self.fail_uids:
            raise ConnectionError("internet yo'q")
        return set(self.known_uids)

    def append(self, row):
        if self.fail_append:
            raise ConnectionError("internet yo'q")
        self.rows.append(list(row))
        if len(row) > 7 and row[7]:
            self.known_uids.add(str(row[7]))


class SyncServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(os.path.join(self.tmp.name, "test.db"))
        self.messages = []

    def tearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    def _add(self, summa=1000):
        return self.storage.add(
            sana="22.10.2026",
            vaqt="10:00:00",
            kassir="Alisher",
            turi="Kirim",
            tolov_usuli="Naqd",
            summa=summa,
            izoh="",
        )

    def _service(self, client):
        service = SyncService(self.storage, client)
        service.finished.connect(lambda sent, msg, ok: self.messages.append((sent, msg, ok)))
        return service

    def _drain(self):
        """Sig'llar boshqa oqimdan keladi — navbatdagi hodisalarni ishlatamiz."""
        QCoreApplication.processEvents()

    def test_sends_all_pending_records(self):
        self._add(1000)
        self._add(2000)
        client = FakeClient()
        service = self._service(client)

        self.assertTrue(service.request(REASON_MANUAL))
        service.wait(5)
        self._drain()

        self.assertEqual(len(client.rows), 2)
        self.assertEqual(self.storage.pending_count(), 0)
        self.assertTrue(self.messages)
        self.assertEqual(self.messages[-1][0], 2)
        self.assertTrue(self.messages[-1][2])

    def test_connection_failure_keeps_records_pending(self):
        self._add(1000)
        client = FakeClient(fail_headers=1)
        service = self._service(client)

        service.request(REASON_MANUAL)
        service.wait(5)
        self._drain()

        self.assertEqual(self.storage.pending_count(), 1)
        self.assertTrue(self.messages)
        sent, message, ok = self.messages[-1]
        self.assertEqual(sent, 0)
        self.assertFalse(ok)
        self.assertIn("Internet", message)

    def test_retry_after_failure_sends_everything(self):
        self._add(1000)
        failing = FakeClient(fail_headers=1)
        first = self._service(failing)
        first.request(REASON_MANUAL)
        first.wait(5)
        self._drain()
        self.assertEqual(self.storage.pending_count(), 1)

        working = FakeClient()
        second = self._service(working)
        second.request(REASON_MANUAL)
        second.wait(5)
        self._drain()

        self.assertEqual(len(working.rows), 1)
        self.assertEqual(self.storage.pending_count(), 0)

    def test_empty_queue_is_reported_without_thread(self):
        service = self._service(FakeClient())
        self.assertFalse(service.request(REASON_MANUAL))
        self.assertTrue(self.messages)
        self.assertEqual(self.messages[-1][0], 0)
        self.assertTrue(self.messages[-1][2])

    # ------------------------------------------------- takroriy qator himoyasi
    def test_record_gets_unique_uid(self):
        first = self._add(1000)
        second = self._add(2000)
        uids = self.storage.uids([first, second])
        self.assertEqual(len(uids), 2)
        self.assertTrue(all(uids))
        self.assertNotEqual(uids[0], uids[1])

    def test_already_synced_record_is_not_sent_twice(self):
        """Yuborilgan, lekin 'sent' deb belgilanmagan yozuv qayta yuborilmaydi."""
        rec_id = self._add(1000)
        uid = self.storage.uids([rec_id])[0]

        client = FakeClient()
        client.known_uids.add(uid)  # Sheets'da allaqon bor
        service = self._service(client)
        service.request(REASON_MANUAL)
        service.wait(5)
        self._drain()

        # Qator yuborilmadi, lekin yozuv 'sent' deb belgilanadi.
        self.assertEqual(len(client.rows), 0)
        self.assertEqual(self.storage.pending_count(), 0)

    def test_stuck_sending_record_is_recovered_without_duplicate(self):
        """'sending' holatida qolgan yozuv tiklanadi va takroriy qator yaratmaydi."""
        rec_id = self._add(1000)
        uid = self.storage.uids([rec_id])[0]

        # Jarayon yuborish paytida to'xtagan holat
        self.storage.mark_sending([rec_id])
        self.assertEqual(len(self.storage.sending_records()), 1)

        service = self._service(FakeClient())
        recovered = service.recover_stuck()
        self.assertEqual(recovered, 1)
        self.assertEqual(len(self.storage.sending_records()), 0)
        self.assertEqual(self.storage.pending_count(), 1)

        # Endi Sheets'da allaqon bor (chiroq uzildi, lekin yuborilgan edi)
        client = FakeClient()
        client.known_uids.add(uid)
        service = self._service(client)
        service.request(REASON_MANUAL)
        service.wait(5)
        self._drain()

        self.assertEqual(len(client.rows), 0)
        self.assertEqual(self.storage.pending_count(), 0)

    def test_failed_append_returns_record_to_pending(self):
        """Yuborish xatosi yozuvni 'pending' holatida qoldiradi (takroriy emas)."""
        self._add(1000)
        client = FakeClient(fail_append=True)
        service = self._service(client)
        service.request(REASON_MANUAL)
        service.wait(5)
        self._drain()

        self.assertEqual(self.storage.pending_count(), 1)
        self.assertEqual(len(self.storage.sending_records()), 0)

    def test_recovered_record_is_held_when_uid_check_fails(self):
        """Dedupe tekshiruvi ishlamasa, tiklangan yozuv yuborilmaydi."""
        rec_id = self._add(1000)
        self.storage.mark_sending([rec_id])

        service = self._service(FakeClient())
        self.assertEqual(service.recover_stuck(), 1)

        # Sheets'dan UID o'qish ishlamaydi.
        client = FakeClient(fail_uids=True)
        service = self._service(client)
        service.request(REASON_MANUAL)
        service.wait(5)
        self._drain()

        # Yozuv yuborilmadi, navbatda qoladi va urinishlar soni oshmaydi.
        self.assertEqual(len(client.rows), 0)
        self.assertEqual(self.storage.pending_count(), 1)
        self.assertEqual(len(self.storage.sending_records()), 0)
        sent, message, ok = self.messages[-1]
        self.assertEqual(sent, 0)
        self.assertFalse(ok)
        self.assertIn("Duplikat", message)

    def test_new_record_is_sent_even_if_uid_check_fails(self):
        """Dedupe tekshiruvi ishlamasa ham yangi yozuv yuboriladi."""
        self._add(1000)
        client = FakeClient(fail_uids=True)
        service = self._service(client)
        service.request(REASON_MANUAL)
        service.wait(5)
        self._drain()

        self.assertEqual(len(client.rows), 1)
        self.assertEqual(self.storage.pending_count(), 0)

    def test_uid_is_sent_as_last_column(self):
        rec_id = self._add(1000)
        uid = self.storage.uids([rec_id])[0]
        client = FakeClient()
        service = self._service(client)
        service.request(REASON_MANUAL)
        service.wait(5)
        self._drain()

        self.assertEqual(len(client.rows), 1)
        row = client.rows[0]
        self.assertEqual(len(row), 8)
        self.assertEqual(str(row[7]), uid)


if __name__ == "__main__":
    unittest.main()
