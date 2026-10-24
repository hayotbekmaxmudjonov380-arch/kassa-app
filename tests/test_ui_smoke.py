"""Qog'oz kassa jurnali asosiy oynasi uchun smoke-test (offscreen, tarmoqsiz)."""

import os
import tempfile
import unittest

# Oyna testlari ko'rinmas (offscreen) rejimda ishlaydi — GUI'siz muhitda ham o'tadi.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QTableWidgetItem

from backup import BackupService
from drafts import DraftStore
from helpers import today_str
from settings import AppSettings
from sheets import SheetsClient
from storage import Storage
from ui import JournalHeader, MainWindow

_APP = None


def setUpModule() -> None:
    """Widget'lar uchun QApplication yaratadi (bitta marta)."""
    global _APP
    if QApplication.instance() is None:
        _APP = QApplication([])


def tearDownModule() -> None:
    global _APP
    if _APP is not None:
        _APP = None


class MainWindowSmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(os.path.join(self.tmp.name, "ui.db"))
        self.settings = AppSettings(os.path.join(self.tmp.name, "settings.json"))
        self.client = SheetsClient(
            credentials_file=os.path.join(self.tmp.name, "kalit_yoq.json")
        )
        # Draft va yedek — faqat test papkasida (real foydalanuvchi
        # papkalari: LOCALAPPDATA/state, backups/ tegilmaydi).
        self.drafts = DraftStore(os.path.join(self.tmp.name, "drafts.json"))
        self.backup = BackupService(
            self.storage, folder=os.path.join(self.tmp.name, "backups")
        )
        self.window = MainWindow(
            self.storage,
            self.settings,
            self.client,
            drafts=self.drafts,
            backup_service=self.backup,
        )
        self.window.show()

    def tearDown(self):
        self.window.sync.wait(5000)
        QCoreApplication.processEvents()
        self.window.close()
        self.tmp.cleanup()

    def test_add_kirim_naqd_record_updates_table_and_summary(self):
        self.window.kassir_edit.setText("Alisher")
        last_row = self.window.table.rowCount() - 1

        # KIRIM - Naqd (Col 2) va Comment (Col 5) ga kiritamiz
        self.window.table.item(last_row, 2).setText("1 250 000")
        self.window.table.item(last_row, 5).setText("stol 5")
        self.window.add_record()

        self.assertEqual(self.storage.count(), 1)
        # Saqlangan qator row 0 ga o'tadi, row 1 bo'sh yangi qator bo'ladi
        self.assertEqual(self.window.table.rowCount(), 2)
        self.assertEqual(self.window.table.item(0, 2).text(), "1 250 000")
        self.assertEqual(self.window.table.item(0, 5).text(), "stol 5")

        # Summary table row 0 (Kirim), col 0 (Naqd)
        self.assertEqual(self.window.summary_table.item(0, 0).text(), "1 250 000")
        # Summary table row 2 (Qoldiq), col 0 (Naqd)
        self.assertEqual(self.window.summary_table.item(2, 0).text(), "1 250 000")
        self.assertEqual(self.settings.last_kassir, "Alisher")

    def test_add_chiqim_naqd_record_updates_summary(self):
        self.window.kassir_edit.setText("Dilnoza")
        last_row = self.window.table.rowCount() - 1

        # CHIQIM - Naqd (Col 6) va CHIQIM - Comment (Col 8) ga kiritamiz
        self.window.table.item(last_row, 6).setText("300000")
        self.window.table.item(last_row, 8).setText("taksi")
        self.window.add_record()

        self.assertEqual(self.storage.count(), 1)
        self.assertEqual(self.window.table.item(0, 6).text(), "300 000")
        self.assertEqual(self.window.table.item(0, 8).text(), "taksi")

        # Summary table row 1 (Chiqim), col 0 (Naqd)
        self.assertEqual(self.window.summary_table.item(1, 0).text(), "300 000")
        # Summary table row 2 (Qoldiq), col 0 (Naqd)
        self.assertEqual(self.window.summary_table.item(2, 0).text(), "-300 000")

    def test_add_chiqim_click_record_lands_in_own_column(self):
        """Chiqim Click — o'z ustunida (Naqd'da emas) ko'rinadi va hisobga tushadi."""
        self.window.kassir_edit.setText("Alisher")
        last_row = self.window.table.rowCount() - 1

        self.window.table.item(last_row, 7).setText("120000")
        self.window.table.item(last_row, 8).setText("onlayn to'lov")
        self.window.add_record()

        self.assertEqual(self.storage.count(), 1)
        # Chiqim Click 7-ustunda, Naqd 6-ustun bo'sh qoladi
        self.assertEqual(self.window.table.item(0, 7).text(), "120 000")
        self.assertEqual(self.window.table.item(0, 6).text(), "")
        self.assertEqual(self.window.table.item(0, 8).text(), "onlayn to'lov")
        # Summary: Chiqim qatori, Click ustuni
        self.assertEqual(self.window.summary_table.item(1, 1).text(), "120 000")

    def test_other_draft_rows_survive_submitting_one(self):
        """Bitta qator Enter qilinsa, boshqa kiritilgan qatorlar yo'qolmaydi."""
        self.window.kassir_edit.setText("Alisher")
        self.window.extra_draft_rows = 2
        self.window.refresh_table()

        first = self.window.table.rowCount() - 2
        self.window.table.item(first, 2).setText("50000")
        self.window.table.item(first + 1, 3).setText("70000")

        self.window.add_record()

        self.assertEqual(self.storage.count(), 1)
        # Qolgan kiritilgan qator draftda saqlanib, jadvalda ko'rinadi
        self.assertEqual(self.window.extra_draft_rows, 2)  # 1 qolgan + 1 bo'sh
        last_draft = self.window.table.rowCount() - self.window.extra_draft_rows
        self.assertEqual(self.window.table.item(last_draft, 3).text(), "70 000")
        self.assertEqual(self.window.table.item(last_draft, 2).text(), "")

    def test_empty_amount_is_rejected(self):
        self.window.kassir_edit.setText("Alisher")
        self.window.add_record()
        self.assertEqual(self.storage.count(), 0)
        self.assertTrue(self.window.toast_label.isVisible())
        self.assertIn("qator qo'shildi", self.window.toast_label.text())

    def test_empty_cashier_is_rejected(self):
        self.window.kassir_edit.setText("")
        last_row = self.window.table.rowCount() - 1
        self.window.table.item(last_row, 2).setText("5000")
        self.window.add_record()
        self.assertEqual(self.storage.count(), 0)
        self.assertIn("Kassir", self.window.toast_label.text())

    def test_unreachable_sheet_keeps_cells_queued_not_records(self):
        """Internet/kalit yo'q bo'lsa yozuv `sent` bo'ladi, kataklar navbatda qoladi."""
        self.window.kassir_edit.setText("Alisher")
        last_row = self.window.table.rowCount() - 1
        self.window.table.item(last_row, 2).setText("5000")
        self.window.add_record()

        self.window.sync.wait(5000)
        QCoreApplication.processEvents()

        self.assertEqual(self.storage.pending_count(), 0)
        self.assertEqual(self.storage.pending_cell_count(), 1)
        # Tugma va undov belgisi yo'q — faqat bitta matnli holat.
        self.assertFalse(hasattr(self.window, "retry_button"))
        # 1 katak + 1 yuborilmagan yozuv = bitta umumiy navbat soni.
        self.assertEqual(self.window.sheet_label.toolTip(), "2 ta yozuv navbatda")

    def test_empty_row_has_no_time_and_is_never_sent(self):
        self.window.kassir_edit.setText("Alisher")
        self.window.add_record()  # bo'sh qator qo'shadi
        last = self.window.table.rowCount() - 1
        self.assertEqual(self.window.table.item(last, 1).text(), "")
        self.assertEqual(self.storage.journal_rows_count(today_str()), 0)
        self.assertEqual(self.storage.pending_cell_count(), 0)

        # Noto'g'ri summa ham qator ochmaydi.
        self.window.table.item(last, 2).setText("abc")
        self.window._flush_cells()
        self.assertEqual(self.storage.unopened_row_count(), 0)

        # Haqiqiy qiymat: Vaqt to'ladi va qator ochiladi.
        self.window.table.item(last, 2).setText("5 000")
        self.window._flush_cells()
        self.assertRegex(self.window.table.item(last, 1).text(), r"^\d{2}:\d{2}$")
        self.assertEqual(self.storage.unopened_row_count(), 1)
        self.assertEqual(self.storage.pending_cell_count(), 1)

    def test_enter_while_editing_commits_amount(self):
        """Tahrir ochiq holda Enter: summa modelga yoziladi va yozuv saqlanadi."""
        self.window.kassir_edit.setText("Alisher")
        row = self.window.table.rowCount() - 1
        self.window.table.setCurrentCell(row, 2)
        self.window.table.editItem(self.window.table.item(row, 2))
        QCoreApplication.processEvents()
        editor = self.window.table.focusWidget()
        self.assertIsNotNone(editor)
        editor.setText("7 500")

        QTest.keyClick(self.window.table, Qt.Key.Key_Return)
        QCoreApplication.processEvents()

        records = self.storage.today_records(today_str())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["summa"], 7500)

    def test_amount_cells_show_thousand_separators(self):
        """Katakka yozilganda summa '1 250 000' ko'rinishida formatlanadi."""
        self.window.kassir_edit.setText("Alisher")
        row = self.window.table.rowCount() - 1
        self.window.table.setCurrentCell(row, 2)
        self.window.table.editItem(self.window.table.item(row, 2))
        QCoreApplication.processEvents()
        editor = self.window.table.focusWidget()
        self.assertIsNotNone(editor)
        editor.setText("1250000")
        QCoreApplication.processEvents()
        self.assertEqual(editor.text(), "1 250 000")

        QTest.keyClick(editor, Qt.Key.Key_Return)
        QCoreApplication.processEvents()
        self.assertEqual(self.window.table.item(row, 2).text(), "1 250 000")

        self.window.add_record()
        records = self.storage.today_records(today_str())
        self.assertEqual(records[0]["summa"], 1250000)

    def test_restored_draft_amounts_are_formatted(self):
        self.drafts.save([{"vt": "12:00", "kn": "10000", "rid": "1", "sr": "2"}])
        self.window.restore_drafts()
        row = self.window.table.rowCount() - self.window.extra_draft_rows
        self.assertEqual(self.window.table.item(row, 2).text(), "10 000")

    def test_header_shows_logo(self):
        from PyQt6.QtWidgets import QLabel

        logo = self.window.findChild(QLabel, "brandLogo")
        self.assertIsNotNone(logo)
        pixmap = logo.pixmap()
        self.assertFalse(pixmap.isNull())
        self.assertEqual(pixmap.width(), pixmap.height())
        self.assertEqual(logo.width(), 46)

    def test_app_icon_uses_logo(self):
        from branding import app_icon

        self.assertFalse(app_icon().isNull())

    def test_clock_is_centered_in_header(self):
        header = self.window.header
        clock = self.window.clock_label
        self.assertEqual(clock.parent(), header)
        QCoreApplication.processEvents()
        center = clock.geometry().center().x()
        self.assertLessEqual(abs(center - header.width() // 2), 2)

    def test_header_kirim_green_chiqim_red(self):
        header = self.window.table.horizontalHeader()
        self.assertIsInstance(header, JournalHeader)
        self.assertEqual(header.COLORS[2], ("#E8F5E9", "#1B5E20"))
        self.assertEqual(header.COLORS[5], ("#E8F5E9", "#1B5E20"))
        self.assertEqual(header.COLORS[6], ("#FDECEA", "#7F1D1D"))
        self.assertEqual(header.COLORS[8], ("#FDECEA", "#7F1D1D"))
        self.assertNotIn(0, header.COLORS)
        self.assertNotIn(1, header.COLORS)

        QCoreApplication.processEvents()
        x = header.sectionViewportPosition(2) + 4
        y = header.height() - 6
        pixel = header.grab().toImage().pixelColor(x, y).name().lower()
        self.assertEqual(pixel, "#e8f5e9")
        x = header.sectionViewportPosition(6) + 4
        pixel = header.grab().toImage().pixelColor(x, y).name().lower()
        self.assertEqual(pixel, "#fdecea")

    def test_cell_edit_opens_one_row_and_queues_only_that_cell(self):
        self.window.kassir_edit.setText("Alisher")
        last_row = self.window.table.rowCount() - 1
        self.window.table.item(last_row, 2).setText("12 000")
        self.window._flush_cells()

        rows = self.storage.unopened_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kassir"], "Alisher")
        cells = self.storage.cell_status(rows[0]["sheet_row"])
        self.assertEqual([(c["column_letter"], c["value"]) for c in cells], [("C", "12000")])
        # Vaqt dastur tomonidan qo'yildi va qatorda saqlandi.
        self.assertRegex(
            self.window.table.item(last_row, 1).text(), r"^\d{2}:\d{2}$"
        )

        # Enter: yozuv aynan shu jurnal qatoriga bog'lanadi (yangi qator ochilmaydi).
        self.window.add_record()
        self.assertEqual(self.storage.count(), 1)
        record = self.storage.today_records(today_str())[0]
        self.assertEqual(int(record["sheet_row"]), rows[0]["sheet_row"])
        self.assertEqual(self.storage.unopened_row_count(), 1)

    def test_opened_row_survives_restart(self):
        """Dastur qayta ochilsa ham draft o'sha jurnal qatoriga bog'lanadi."""
        self.window.kassir_edit.setText("Alisher")
        last_row = self.window.table.rowCount() - 1
        self.window.table.item(last_row, 2).setText("5000")
        self.window._flush_cells()
        sheet_row = int(self.storage.unopened_rows()[0]["sheet_row"])

        second = MainWindow(
            self.storage,
            self.settings,
            self.client,
            drafts=DraftStore(self.drafts.path),
            backup_service=BackupService(
                self.storage, folder=os.path.join(self.tmp.name, "backups2")
            ),
        )
        try:
            self.assertTrue(second.restore_drafts())
            meta = second._row_meta(second.table.rowCount() - 1)
            self.assertEqual(int(meta["sr"]), sheet_row)
        finally:
            second._closing = True
            second._clock_timer.stop()
            second._retry_timer.stop()
            second._backup_timer.stop()
            second._toast_timer.stop()
            second._cell_timer.stop()
            second.sync.wait(4000)
            second.cells.stop()
            second.cells.join(2000)
            second.deleteLater()
            QCoreApplication.processEvents()

    def test_two_amounts_in_one_row_share_one_sheet_row(self):
        self.window.kassir_edit.setText("Alisher")
        last_row = self.window.table.rowCount() - 1
        self.window.table.item(last_row, 2).setText("1000")
        self.window.table.item(last_row, 6).setText("2000")
        self.window.add_record()

        self.assertEqual(self.storage.count(), 2)
        records = self.storage.today_records(today_str())
        self.assertEqual({int(r["sheet_row"]) for r in records}, {2})
        cells = self.storage.cell_status(2)
        self.assertEqual(
            sorted((c["column_letter"], c["value"]) for c in cells),
            [("C", "1000"), ("G", "2000")],
        )

    def _saved_row(self, amount: str = "100000", comment: str = "stol 1") -> int:
        """Bitta yozuvni saqlaydi va uning jadval qatorini qaytaradi."""
        self.window.kassir_edit.setText("Alisher")
        last_row = self.window.table.rowCount() - 1
        self.window.table.item(last_row, 2).setText(amount)
        self.window.table.item(last_row, 5).setText(comment)
        self.window.add_record()
        return 0

    def test_saved_row_cells_are_editable(self):
        self._saved_row()
        for col in (2, 3, 4, 5, 6, 7, 8):
            flags = self.window.table.item(0, col).flags()
            self.assertTrue(flags & Qt.ItemFlag.ItemIsEditable, f"col {col}")
        self.assertFalse(
            self.window.table.item(0, 1).flags() & Qt.ItemFlag.ItemIsEditable
        )

    def test_saved_row_amount_edit_updates_record_and_sheet_cell(self):
        self._saved_row("100000")
        self.window.table.item(0, 2).setText("250 000")

        record = self.storage.today_records(today_str())[0]
        self.assertEqual(record["summa"], 250000)
        cells = {c["column_letter"]: c["value"] for c in self.storage.cell_status(2)}
        self.assertEqual(cells["C"], "250000")
        self.assertEqual(self.window.summary_table.item(0, 0).text(), "250 000")
        self.assertEqual(self.window.table.item(0, 2).text(), "250 000")

    def test_saved_row_new_payment_adds_record(self):
        self._saved_row("100000")
        self.window.table.item(0, 3).setText("50000")

        records = self.storage.today_records(today_str())
        self.assertEqual(
            sorted((r["turi"], r["tolov_usuli"], r["summa"]) for r in records),
            [("Kirim", "Click", 50000), ("Kirim", "Naqd", 100000)],
        )
        cells = {c["column_letter"]: c["value"] for c in self.storage.cell_status(2)}
        self.assertEqual(cells["D"], "50000")

    def test_saved_row_amount_clear_removes_record(self):
        self._saved_row("100000")
        self.window.table.item(0, 2).setText("")

        self.assertEqual(self.storage.today_records(today_str()), [])
        cells = {c["column_letter"]: c["value"] for c in self.storage.cell_status(2)}
        self.assertEqual(cells["C"], "")
        self.assertEqual(self.window.table.item(0, 2).text(), "")

    def test_saved_row_comment_edit_updates_izoh(self):
        self._saved_row("100000", "stol 1")
        self.window.table.item(0, 5).setText("stol 7")

        record = self.storage.today_records(today_str())[0]
        self.assertEqual(record["izoh"], "stol 7")
        cells = {c["column_letter"]: c["value"] for c in self.storage.cell_status(2)}
        self.assertEqual(cells["F"], "stol 7")

    def test_enter_on_saved_row_does_not_add_draft_row(self):
        self._saved_row("100000")
        rows_before = self.window.table.rowCount()

        self.window.table.setCurrentCell(0, 2)
        self.window.table.editItem(self.window.table.item(0, 2))
        QCoreApplication.processEvents()
        editor = self.window.table.focusWidget()
        editor.setText("150000")
        QTest.keyClick(editor, Qt.Key.Key_Return)
        QCoreApplication.processEvents()

        self.assertEqual(self.window.table.rowCount(), rows_before)
        self.assertEqual(self.storage.count(), 1)
        self.assertEqual(self.storage.today_records(today_str())[0]["summa"], 150000)


if __name__ == "__main__":
    unittest.main()
