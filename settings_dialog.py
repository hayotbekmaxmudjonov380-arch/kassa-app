"""Sozlamalar oynasi: spreadsheet, varaq nomi va service account kaliti."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QThread, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config import DATA_DIR
from settings import AppSettings
from sheets import SheetsClient, friendly_error
from style import COLORS


class ConnectionTester(QThread):
    """Aloqani fonda tekshiradi (UI bloklanmasligi uchun)."""

    finished_ok = pyqtSignal(bool, str)

    def __init__(self, client: SheetsClient, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._client = client

    def run(self) -> None:  # noqa: D102 - QThread API
        try:
            info = self._client.ping()
            self.finished_ok.emit(True, info)
        except Exception as exc:  # noqa: BLE001 — xato foydalanuvchiga ko'rsatiladi
            self.finished_ok.emit(False, friendly_error(exc))


class SettingsDialog(QDialog):
    """Bitta oynada: Spreadsheet ID, varaq nomi, kalit fayli."""

    def __init__(self, settings: AppSettings, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.settings = settings
        self._chosen_credentials: Optional[str] = None
        self._tester: Optional[ConnectionTester] = None

        self.setWindowTitle("Sozlamalar — GOLD Kassa")
        self.setMinimumWidth(760)
        self.setStyleSheet("QDialog { background-color: %s; }" % COLORS["bg"])

        self.sheet_id_edit = QLineEdit(settings.spreadsheet_id)
        self.sheet_id_edit.setPlaceholderText("1g3sb3swmuWLHGuMt0_hoYwIf0myHH8q-pKRF5l9vAn8")

        self.worksheet_edit = QLineEdit(settings.worksheet_name)

        self.credentials_edit = QLineEdit(settings.credentials_file)
        self.credentials_edit.setReadOnly(True)
        self.credentials_edit.setToolTip(settings.credentials_file)
        self.credentials_edit.setMinimumWidth(200)

        self.auto_create_check = QCheckBox("Varaq topilmasa avtomatik yaratish")
        self.auto_create_check.setChecked(settings.auto_create_sheet)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("hint")

        self.test_button = QPushButton("Test ulanish")
        self.test_button.setObjectName("secondaryButton")
        self.test_button.setMinimumHeight(46)
        self.test_button.clicked.connect(self.test_connection)

        self.browse_button = QPushButton("Tanlash…")
        self.browse_button.setObjectName("secondaryButton")
        self.browse_button.clicked.connect(self.choose_credentials)

        self.folder_button = QPushButton("Papkani ochish")
        self.folder_button.setObjectName("secondaryButton")
        self.folder_button.clicked.connect(self.open_data_folder)

        self.save_button = QPushButton("Saqlash")
        self.save_button.setObjectName("addButton")
        self.save_button.setMinimumHeight(46)
        self.save_button.clicked.connect(self.save_and_close)

        self.cancel_button = QPushButton("Bekor qilish")
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.setMinimumHeight(46)
        self.cancel_button.clicked.connect(self.reject)

        self._build()

    # ------------------------------------------------------------------ qurilma
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        title = QLabel("Dastur sozlamalari")
        title.setStyleSheet(
            "font-size: 19px; font-weight: 700; color: %s;" % COLORS["text"]
        )
        root.addWidget(title)

        card = QFrame()
        card.setObjectName("card")
        form = QFormLayout(card)
        form.setContentsMargins(16, 14, 16, 16)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        form.addRow(self._label("Google Spreadsheet ID"), self.sheet_id_edit)
        form.addRow(self._label("Varaq nomi"), self.worksheet_edit)

        cred_row = QHBoxLayout()
        cred_row.setSpacing(8)
        cred_row.addWidget(self.credentials_edit, 1)
        cred_row.addWidget(self.browse_button)
        form.addRow(self._label("Service account kaliti"), cred_row)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        folder_row.addWidget(self.folder_button)
        folder_row.addStretch(1)
        form.addRow("", folder_row)

        form.addRow(self.auto_create_check)
        root.addWidget(card)

        root.addWidget(self.status_label)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addWidget(self.test_button)
        bottom.addStretch(1)
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(self.save_button)
        root.addLayout(bottom)

        hint = QLabel(
            "Kalit fayl — Google Cloud'dan yaratilgan service account JSON kaliti. "
            "Uni Excel/Sheets fayli bilan 'Ulashish' orqali 'Editor' huquqi bilan bog'lang."
        )
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        root.addWidget(hint)

    def _label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    # ------------------------------------------------------------------ amallar
    def choose_credentials(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Service account kalit faylini tanlang", "", "JSON fayl (*.json)"
        )
        if not path:
            return
        self._chosen_credentials = path
        self.credentials_edit.setText(path)
        self.status_label.setText("")
        self.status_label.setStyleSheet("color: %s;" % COLORS["text_muted"])

    def open_data_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(DATA_DIR))

    def _stop_tester(self) -> None:
        """Oyna yopilayotganda fon oqimini kutib turadi (qulamasligi uchun)."""
        if self._tester is not None and self._tester.isRunning():
            self._tester.wait(3000)

    def done(self, result: int) -> None:  # noqa: D102 - Qt API
        self._stop_tester()
        super().done(result)

    def test_connection(self) -> None:
        sheet_id = self.sheet_id_edit.text().strip()
        worksheet = self.worksheet_edit.text().strip()
        if not sheet_id or not worksheet:
            self._set_status("Spreadsheet ID va varaq nomini to'ldiring.", "error")
            return

        credentials = self._chosen_credentials or self.credentials_edit.text().strip()
        client = SheetsClient(
            credentials_file=credentials,
            spreadsheet_id=sheet_id,
            worksheet_name=worksheet,
            auto_create_sheet=self.auto_create_check.isChecked(),
        )

        self.test_button.setEnabled(False)
        self._set_status("Ulanish tekshirilmoqda…", "info")

        self._tester = ConnectionTester(client, self)
        self._tester.finished_ok.connect(self._on_test_done)
        self._tester.start()

    def _on_test_done(self, ok: bool, message: str) -> None:
        self.test_button.setEnabled(True)
        if ok:
            self._set_status("Ulanish muvaffaqiyatli. " + message, "ok")
        else:
            self._set_status(message, "error")

    def _set_status(self, message: str, level: str) -> None:
        color = {
            "ok": COLORS["green_dark"],
            "error": COLORS["red_dark"],
            "info": COLORS["text_muted"],
        }.get(level, COLORS["text_muted"])
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: %s; font-weight: 600;" % color)

    def save_and_close(self) -> None:
        sheet_id = self.sheet_id_edit.text().strip()
        worksheet = self.worksheet_edit.text().strip()
        if not sheet_id or not worksheet:
            QMessageBox.warning(self, "Sozlamalar", "Spreadsheet ID va varaq nomi majburiy.")
            return

        if self._chosen_credentials:
            try:
                self.settings.set_credentials_from(self._chosen_credentials)
            except OSError as exc:
                QMessageBox.critical(self, "Kalit fayli", f"Kalit faylni ko'chirib bo'lmadi:\n{exc}")
                return
        else:
            self.settings.set("credentials_file", self.credentials_edit.text().strip())

        self.settings.set("spreadsheet_id", sheet_id)
        self.settings.set("worksheet_name", worksheet)
        self.settings.set("auto_create_sheet", self.auto_create_check.isChecked())

        if not self.settings.save():
            QMessageBox.critical(
                self, "Sozlamalar", "Sozlamalarni saqlab bo'lmadi (papkaga yozish huquqi yo'q)."
            )
            return

        self.accept()


__all__ = ["SettingsDialog", "ConnectionTester"]

