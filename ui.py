"""Asosiy oyna: Qog'oz kassa jurnali (spreadsheet) ko'rinishidagi interfeys.

Xodim summani to'g'ridan-to'g'ri tegishli katakchaga kiritadi va Enter bosadi.
Backend (Google Sheets'ga yuborish, SQLite lokal navbat) o'zgarmasdan ishlaydi.
"""

from __future__ import annotations

import datetime as dt
import os
import threading
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QKeySequence, QShortcut
from PyQt6.QtPrintSupport import QPrinter
from PyQt6.QtGui import QTextDocument
from PyQt6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import updater
from branding import logo_pixmap
from cell_sync import CellSyncService, migrate_legacy_records
from config import AMOUNT_FIELDS, APP_NAME, APP_VERSION, CELL_COLUMN
from backup import BackupService
from drafts import DraftStore
from helpers import fmt_money, list_time, now_time_str, parse_summa, today_str
from settings import AppSettings
from sheets import SheetsClient
from storage import Storage
from style import COLORS
from sync import REASON_AUTO, REASON_MANUAL, REASON_STARTUP, SyncService

# Google Sheets va web-versiyadagi bilan bir xil 9 ta ustun — desktop,
# web va jadval doim bir xil ko'rinadi (avval desktopda 2 ta chiqim
# ustuni yo'q edi, chiqim Click noto'g'ri ustunga tushar edi).
JOURNAL_HEADERS = [
    "#",
    "Vaqt",
    "KIRIM — Naqd",
    "KIRIM — Click",
    "KIRIM — Terminal",
    "KIRIM — Comment",
    "CHIQIM — Naqd",
    "CHIQIM — Click",
    "CHIQIM — Comment",
]

# Draft (kiritilgan, lekin Enter bosilmagan) qatorlarning ustunlari.
# Har bir maydon: (nom, jadval ustun indeksi)
DRAFT_FIELDS = (
    ("vt", 1),   # Vaqt
    ("kn", 2),   # Kirim — Naqd
    ("kc", 3),   # Kirim — Click
    ("kt", 4),   # Kirim — Terminal
    ("ki", 5),   # Kirim — Comment (izoh)
    ("cn", 6),   # Chiqim — Naqd
    ("cc", 7),   # Chiqim — Click
    ("ci", 8),   # Chiqim — Comment (izoh)
)

# Ustun indeksi -> maydon nomi (katak sinxronizatsiyasi uchun).
DRAFT_FIELD_BY_COL = {col: name for name, col in DRAFT_FIELDS}
DRAFT_COLUMN = {name: col for name, col in DRAFT_FIELDS}

# Summa maydoni -> (turi, to'lov usuli) — saqlangan qatorni tahrirlash uchun.
FIELD_PAYMENT = {
    "kn": ("Kirim", "Naqd"),
    "kc": ("Kirim", "Click"),
    "kt": ("Kirim", "Terminal"),
    "cn": ("Chiqim", "Naqd"),
    "cc": ("Chiqim", "Click"),
}


class JournalTableWidget(QTableWidget):
    """Qog'oz kassa jurnali jadvali: Enter bosilganda yangi yozuvni saqlaydi."""

    entry_submitted = pyqtSignal()

    def keyPressEvent(self, event) -> None:  # noqa: D102 - Qt API
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.state() == QAbstractItemView.State.EditingState:
                editor = self.focusWidget()
                if editor and self.currentIndex().isValid():
                    # Avval tahrir natijasini modelga yozamiz (aks holda
                    # kiritilgan summa yo'qolib qoladi), keyin yopamiz.
                    self.itemDelegate().setModelData(
                        editor, self.model(), self.currentIndex()
                    )
                    self.closeEditor(
                        editor, QAbstractItemDelegate.EndEditHint.SubmitModelCache
                    )
            self.entry_submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class JournalHeader(QHeaderView):
    """Jadval sarlavhasi: KIRIM ustunlari yashil, CHIQIM ustunlari qizil."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(Qt.Orientation.Horizontal, parent)

    COLORS = {
        2: ("#E8F5E9", "#1B5E20"),
        3: ("#E8F5E9", "#1B5E20"),
        4: ("#E8F5E9", "#1B5E20"),
        5: ("#E8F5E9", "#1B5E20"),
        6: ("#FDECEA", "#7F1D1D"),
        7: ("#FDECEA", "#7F1D1D"),
        8: ("#FDECEA", "#7F1D1D"),
    }

    def paintSection(self, painter, rect, logical_index) -> None:  # noqa: N802
        colors = self.COLORS.get(logical_index)
        if not colors:
            super().paintSection(painter, rect, logical_index)
            return
        background, foreground = colors
        painter.save()
        painter.fillRect(rect, QColor(background))
        painter.setPen(QColor(COLORS["border"]))
        painter.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())
        painter.setPen(QColor(COLORS["border_strong"]))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        text = str(self.model().headerData(logical_index, Qt.Orientation.Horizontal) or "")
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(foreground))
        painter.drawText(
            rect.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignCenter, text
        )
        painter.restore()


class AmountDelegate(QStyledItemDelegate):
    """Summa katagi: yozayotganda minglik/millionlik ajratgich qo'yiladi."""

    def createEditor(self, parent, option, index):  # noqa: N802 - Qt API
        editor = QLineEdit(parent)
        editor.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        editor.textChanged.connect(
            lambda text, widget=editor: self._reformat(widget, text)
        )
        return editor

    def setEditorData(self, editor, index):  # noqa: N802 - Qt API
        text = str(index.data(Qt.ItemDataRole.EditRole) or "")
        value = parse_summa(text)
        editor.setText("" if value is None else str(int(round(value))))

    def setModelData(self, editor, model, index):  # noqa: N802 - Qt API
        value = parse_summa(editor.text())
        model.setData(
            index,
            "" if value is None else fmt_money(value),
            Qt.ItemDataRole.EditRole,
        )

    @staticmethod
    def _reformat(editor: QLineEdit, text: str) -> None:
        digits = "".join(ch for ch in text if ch.isdigit())
        formatted = fmt_money(int(digits)) if digits else ""
        if formatted == text:
            return
        # Kursorni raqamlar orasidagi o'rniga moslab saqlaymiz.
        digits_before = sum(1 for ch in text[: editor.cursorPosition()] if ch.isdigit())
        editor.blockSignals(True)
        editor.setText(formatted)
        if digits:
            seen = 0
            position = len(formatted)
            for pos, ch in enumerate(formatted):
                if ch.isdigit():
                    seen += 1
                    if seen == digits_before:
                        position = pos + 1
                        break
            editor.setCursorPosition(min(position, len(formatted)))
        else:
            editor.setCursorPosition(0)
        editor.blockSignals(False)


class HeaderWidget(QWidget):
    """Sarlavha paneli: soat oynaning markazida, sana esa o'z joyida turadi."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        # QWidget subklassida QSS fon (#header) ishlashi uchun shart.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.clock: Optional[QLabel] = None

    def set_clock(self, clock: QLabel) -> None:
        self.clock = clock
        clock.setParent(self)
        clock.raise_()
        self.place_clock()

    def place_clock(self) -> None:
        if self.clock is None:
            return
        self.clock.adjustSize()
        x = (self.width() - self.clock.width()) // 2
        y = (self.height() - self.clock.height()) // 2
        self.clock.move(x, y)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self.place_clock()


class ElidedLabel(QLabel):
    """Uzun matnni '…' bilan qisqartiradi; to'liq matn tooltip'da qoladi."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None):
        super().__init__("", parent)
        self._full_text = ""
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._full_text = text or ""
        self.setToolTip(self._full_text)
        self._apply_elide()

    def resizeEvent(self, event) -> None:  # noqa: D102 - Qt API
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        if not self._full_text:
            super().setText("")
            return
        available = self.width() - 2
        if available <= 10:
            super().setText(self._full_text)
            return
        super().setText(
            self.fontMetrics().elidedText(
                self._full_text, Qt.TextElideMode.ElideRight, available
            )
        )


def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("fieldLabel")
    return label


class UpdateChecker(QObject):
    """GitHub relizlarini fonda tekshiradi (oynani bloklamaydi)."""

    available = pyqtSignal(object)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="update-check", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        info = updater.check_for_update()
        if info is not None:
            self.available.emit(info)


class MainWindow(QMainWindow):
    """Qog'oz kassa jurnali oynasi."""

    def __init__(
        self,
        storage: Storage,
        settings: AppSettings,
        client: SheetsClient,
        parent: Optional[QWidget] = None,
        drafts: Optional[DraftStore] = None,
        backup_service: Optional[BackupService] = None,
    ):
        super().__init__(parent)
        self.storage = storage
        self.settings = settings
        self.client = client

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1100, 720)
        self.resize(1200, 820)

        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._hide_toast)

        self.sync = SyncService(storage, client, parent=self)
        self.sync.started.connect(self._on_sync_started)
        self.sync.finished.connect(self._on_sync_finished)
        self.sync.rejected.connect(lambda msg: self.show_toast(msg, "warn"))

        # Katak darajasidagi sinxronizatsiya (web-versiya bilan bir xil):
        # har bir o'zgargan katak alohida, 600 ms debounce bilan yuboriladi.
        self.cells = CellSyncService(
            storage, client_factory=lambda: self.client, seed_timeout=0.5
        )
        self._dirty_cells = set()
        self._silent_cells = False
        self._migration_thread: Optional[threading.Thread] = None
        # Testlar o'z draft/yedek joyini beradi — haqiqiy foydalanuvchi
        # papkalari (LOCALAPPDATA, backups/) ifloslanmaydi.
        self.drafts = drafts if drafts is not None else DraftStore()
        self.backup_service = (
            backup_service if backup_service is not None else BackupService(storage)
        )
        self._quiet = False
        self._closing = False
        self._last_date = today_str()
        self._updating_table = False
        self.extra_draft_rows = 1
        self._draft_restore_pending = False

        self._build_ui()
        self._install_shortcuts()

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick)
        self._clock_timer.start()

        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(60_000)
        self._retry_timer.timeout.connect(self._auto_retry)
        self._retry_timer.start()

        # Yedeklash va draft saqlash — muntazam.
        self._backup_timer = QTimer(self)
        self._backup_timer.setInterval(60_000)
        self._backup_timer.timeout.connect(self._on_backup_tick)
        self._backup_timer.start()

        # Katak navbati: 600 ms jim turgandan keyin yuboriladi.
        self._cell_timer = QTimer(self)
        self._cell_timer.setSingleShot(True)
        self._cell_timer.setInterval(600)
        self._cell_timer.timeout.connect(self._flush_cells)

        # Google Sheets holati (footer badge) — har 3 soniyada yangilanadi.
        self._sheet_timer = QTimer(self)
        self._sheet_timer.setInterval(3000)
        self._sheet_timer.timeout.connect(self._refresh_sheet_status)
        self._sheet_timer.start()

        self.refresh_all()
        QTimer.singleShot(600, self._startup)

        # Avtomatik yangilanish — faqat .exe sifatida ishlaganda (dev'da yo'q).
        self._update_checker = UpdateChecker(self)
        self._update_checker.available.connect(self._on_update_available)
        if updater.can_self_update():
            QTimer.singleShot(4000, self._update_checker.start)
            self._update_timer = QTimer(self)
            self._update_timer.setInterval(24 * 60 * 60 * 1000)
            self._update_timer.timeout.connect(self._update_checker.start)
            self._update_timer.start()

    # ------------------------------------------------------------------ qurilma
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())

        body = QWidget()
        body.setObjectName("root")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 10, 14, 10)
        body_layout.setSpacing(10)

        body_layout.addWidget(self._build_journal_card(), 1)
        body_layout.addWidget(self._build_summary_card())

        self.toast_label = QLabel("")
        self.toast_label.setObjectName("toast")
        self.toast_label.setProperty("level", "info")
        self.toast_label.setWordWrap(True)
        self.toast_label.hide()
        body_layout.addWidget(self.toast_label)

        body_layout.addWidget(self._build_footer())
        outer.addWidget(body, 1)

        self.setCentralWidget(root)

    # ------------------------------------------------------------ sarlavha paneli
    def _build_header(self) -> QWidget:
        header = HeaderWidget()
        self.header = header
        header.setObjectName("header")
        header.setFixedHeight(64)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(18, 8, 14, 8)
        layout.setSpacing(16)

        # Chapda: logotip + brand title + Kassir ismi
        logo = QLabel()
        logo.setObjectName("brandLogo")
        pixmap = logo_pixmap(46, radius=10)
        if not pixmap.isNull():
            logo.setPixmap(pixmap)
            logo.setFixedSize(46, 46)
            logo.setToolTip(APP_NAME)
            layout.addWidget(logo)

        brand = QVBoxLayout()
        brand.setSpacing(1)
        title = QLabel("GOLD restobar")
        title.setObjectName("brandTitle")
        subtitle = QLabel("Kassa jurnali")
        subtitle.setObjectName("brandSubtitle")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        layout.addLayout(brand)

        kassir_box = QHBoxLayout()
        kassir_box.setSpacing(6)
        lbl_kassir = QLabel("Kassir:")
        lbl_kassir.setObjectName("headerLabel")
        self.kassir_edit = QLineEdit(self.settings.last_kassir)
        self.kassir_edit.setObjectName("kassirEdit")
        self.kassir_edit.setPlaceholderText("Kassir ismini kiriting")
        self.kassir_edit.setFixedWidth(220)
        kassir_box.addWidget(lbl_kassir)
        kassir_box.addWidget(self.kassir_edit)
        layout.addLayout(kassir_box)

        layout.addStretch(1)

        # O'ngda: Kun (sana) + Sozlamalar
        date_box = QHBoxLayout()
        date_box.setSpacing(6)
        lbl_kun = QLabel("Kun:")
        lbl_kun.setObjectName("headerLabel")
        self.date_edit = QLineEdit(today_str())
        self.date_edit.setObjectName("dateEdit")
        self.date_edit.setStyleSheet("color: %s; font-size: 16px; font-weight: 700; background: #0F172A;" % COLORS["gold"])
        self.date_edit.setReadOnly(True)  # foydalanuvchi sanani qo'lda o'zgartira olmasin
        self.date_edit.setFixedWidth(110)
        self.clock_label = QLabel("")
        self.clock_label.setObjectName("headerClock")

        # Soat sarlavhaning markazida turadi (sana o'z joyida qoladi).
        header.set_clock(self.clock_label)

        date_box.addWidget(lbl_kun)
        date_box.addWidget(self.date_edit)
        layout.addLayout(date_box)

        self.settings_button = QPushButton("Sozlamalar")
        self.settings_button.setObjectName("headerButton")
        self.settings_button.clicked.connect(self.open_settings)
        layout.addWidget(self.settings_button)

        return header

    # ------------------------------------------------------------- asosiy jadval
    def _build_journal_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        card_title = QLabel("QOG'OZ KASSA JURNALI (SPREADSHEET)")
        card_title.setObjectName("cardTitle")
        layout.addWidget(card_title)

        self.table = JournalTableWidget(0, len(JOURNAL_HEADERS))
        self.table.setObjectName("journalTable")
        self.table.setHorizontalHeader(JournalHeader(self.table))
        self.table.setHorizontalHeaderLabels(JOURNAL_HEADERS)
        self.amount_delegate = AmountDelegate(self.table)
        for column in (2, 3, 4, 6, 7):
            self.table.setItemDelegateForColumn(column, self.amount_delegate)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.entry_submitted.connect(self.add_record_from_row)
        self.table.cellChanged.connect(self._on_cell_changed)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for col in range(2, len(JOURNAL_HEADERS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        header.setHighlightSections(False)

        layout.addWidget(self.table, 1)

        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)
        bottom_bar.addStretch(1)

        self.add_row_button = QPushButton("➕ Yangi qator qo'shish")
        self.add_row_button.setObjectName("secondaryButton")
        self.add_row_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_row_button.clicked.connect(self.add_record_from_row)
        bottom_bar.addWidget(self.add_row_button)

        layout.addLayout(bottom_bar)

        return card

    # ----------------------------------------------------------- summary jadval
    def _build_summary_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(6)

        card_title = QLabel("JAMI HISOB-KITOB (BUGUNGI NATIJA)")
        card_title.setObjectName("cardTitle")
        layout.addWidget(card_title)

        self.summary_table = QTableWidget(3, 3)
        self.summary_table.setObjectName("summaryTable")
        self.summary_table.setHorizontalHeaderLabels(["Naqd", "Click", "Terminal"])
        self.summary_table.setVerticalHeaderLabels(["Kirim", "Chiqim", "Qoldiq"])
        self.summary_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.summary_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.summary_table.setShowGrid(True)
        self.summary_table.setFixedHeight(120)

        s_header = self.summary_table.horizontalHeader()
        s_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        v_header = self.summary_table.verticalHeader()
        v_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        layout.addWidget(self.summary_table)
        return card

    # --------------------------------------------------------------------- footer
    def _build_footer(self) -> QWidget:
        footer = QWidget()
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(12)

        self.close_day_button = QPushButton("Kunni yopish")
        self.close_day_button.setObjectName("closeDayButton")
        self.close_day_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_day_button.clicked.connect(self.close_day)
        layout.addWidget(self.close_day_button)

        layout.addStretch(1)

        self.status_label = ElidedLabel("Tayyor")
        self.status_label.setObjectName("statusText")
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.status_label)

        # Holat: BITTA matn (ulangan YOKI navbatda N ta). Tugma/undov belgisi yo'q.
        self.sheet_label = ElidedLabel("Sheets: ulanmoqda…")
        self.sheet_label.setObjectName("statusText")
        self.sheet_label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        self.sheet_label.setMinimumWidth(240)
        layout.addWidget(self.sheet_label)

        return footer

    def _install_shortcuts(self) -> None:
        for sequence in ("Ctrl+Return", "Ctrl+Enter"):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(self.add_record_from_row)
        retry = QShortcut(QKeySequence("Ctrl+R"), self)
        retry.activated.connect(self.retry_sync)

    # ------------------------------------------------------------------ hodisa
    def _on_backup_tick(self) -> None:
        """Har minut: kiritilmagan qatorlarni saqlash va yedek nusxa olish.

        Ikkalasi ham chiroq uzilishi/kompyuter o'chishidan himoya qiladi.
        """
        try:
            self.save_drafts()
        except Exception:  # noqa: BLE001 — saqlash dasturni to'xtatmasin
            pass
        try:
            self.storage.checkpoint()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.backup_service.run()
        except Exception:  # noqa: BLE001 — yedeklash dasturni to'xtatmasin
            pass

    def _tick(self) -> None:
        now = dt.datetime.now()
        today = today_str(now)
        if not self.date_edit.hasFocus() and not self.date_edit.text():
            self.date_edit.setText(today)
        self.clock_label.setText(now.strftime("%H:%M:%S"))
        self.header.place_clock()

        if today != self._last_date:
            self._last_date = today
            self.refresh_all()

        # Davriy yedek nusxa (chiroq uzilishidan himoya).
        try:
            self.backup_service.run()
        except Exception:  # noqa: BLE001 — yedeklash dasturni to'xtatmasin
            pass

    def _startup(self) -> None:
        if self._closing:
            return
        self._tick()
        # O'ggan kiringan ma'lumotni tiklash (chiroq uzilishi holati).
        try:
            self.restore_drafts()
        except Exception:  # noqa: BLE001 — tiklash dasturni to'xtatmasin
            pass
        # 'sending' holatida qolgan yozuvlarni tiklash (jarayon to'xtab qolgan).
        try:
            self.sync.recover_stuck()
        except Exception:  # noqa: BLE001 — tiklash dasturni to'xtatmasin
            pass
        credentials = self.settings.credentials_file
        if not credentials or not os.path.exists(credentials):
            self.show_toast(
                "Kalit fayl topilmadi — 'Sozlamalar' orqali Google Sheets'ni ulang.",
                "warn",
            )
        # Katak sinxronizatsiyasi: jadvaldagi oxirgi qatorni aniqlab,
        # navbatni fon oqimida bo'shatadi.
        try:
            self.cells.start()
        except Exception:  # noqa: BLE001 - sinxronizatsiya dasturni to'xtatmasin
            pass
        # Eski (butun qator) navbatda qolgan yozuvlarni yangi jurnalga
        # o'tkazamiz — ular ham A..J formatida yuboriladi.
        self._migration_thread = threading.Thread(
            target=self._migrate_legacy, name="legacy-migrate", daemon=True
        )
        self._migration_thread.start()

        self._quiet = False
        self.sync.request(REASON_STARTUP)

    def _migrate_legacy(self) -> None:
        try:
            migrate_legacy_records(self.storage, self.cells)
            self.cells.notify()
        except Exception:  # noqa: BLE001 - migratsiya dasturni to'xtatmasin
            pass

    def _auto_retry(self) -> None:
        if self.storage.pending_count() and not self.sync.busy:
            self._quiet = self.sync.request(REASON_AUTO)

    # ------------------------------------------------------------ yangilanish
    def _on_update_available(self, info) -> None:
        if self._closing:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Yangi versiya mavjud")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            f"Yangi versiya: v{info.version}\nHozirgi versiya: v{APP_VERSION}"
        )
        box.setInformativeText(
            "Yangilash uchun dastur yopiladi, yangi versiya o'rnatiladi va "
            "qayta ishga tushadi. Ma'lumotlaringiz saqlanib qoladi."
        )
        update_button = box.addButton(
            "Yangilash", QMessageBox.ButtonRole.AcceptRole
        )
        later_button = box.addButton("Keyinroq", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(later_button)
        box.exec()
        if box.clickedButton() is not update_button:
            return

        self.status_label.setText("Yangi versiya yuklab olinmoqda…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            script = updater.prepare_update(info)
        finally:
            QApplication.restoreOverrideCursor()

        if not script:
            self.show_toast("Yangilanishni yuklab bo'lmadi — keyinroq urinib ko'ring.", "error")
            return
        self.status_label.setText("Yangilanish tayyor — dastur qayta ishga tushadi…")
        if updater.launch_update_script(script):
            self._closing = True
            self.close()
            QApplication.quit()
        else:
            self.show_toast("Yangilanishni ishga tushirib bo'lmadi.", "error")

    # --------------------------------------------------- katak sinxronizatsiyasi
    def _draft_index(self, row: int) -> Optional[int]:
        """Jadval qatori draft qatorimi? Bo'lsa — draft indeksini qaytaradi."""
        total = self.table.rowCount()
        start = max(0, total - self.extra_draft_rows)
        if start <= row < total:
            return row - start
        return None

    def _draft_table_row(self, index: int) -> int:
        return max(0, self.table.rowCount() - self.extra_draft_rows) + index

    def _row_meta(self, table_row: int) -> Dict:
        """Qatorning jurnal ma'lumoti (rid/sr/ts) — `#` katagida saqlanadi."""
        item = self.table.item(table_row, 0)
        meta = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return dict(meta) if isinstance(meta, dict) else {}

    def _set_row_meta(self, table_row: int, meta: Dict) -> None:
        item = self.table.item(table_row, 0)
        if item is None:
            item = QTableWidgetItem(str(table_row + 1))
            item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(table_row, 0, item)
        item.setData(Qt.ItemDataRole.UserRole, dict(meta))

    def _ensure_row_opened(self, draft_index: int) -> Optional[Dict]:
        """Birinchi tahrirda jurnal qatorini ochadi (A/B/J keyin yoziladi)."""
        table_row = self._draft_table_row(draft_index)
        meta = self._row_meta(table_row)
        if int(meta.get("sr") or 0) > 0:
            return meta

        kassir = self.kassir_edit.text().strip()
        if not kassir:
            self.show_toast("Kassir ismini yozing.", "error")
            self.kassir_edit.setFocus()
            return None

        today = today_str()
        timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        number = self.storage.journal_rows_count(today) + 1
        row = self.cells.allocate_row(today, timestamp, kassir, number)
        meta = {
            "rid": int(row["id"]),
            "sr": int(row["sheet_row"]),
            "ts": timestamp,
        }
        self._set_row_meta(table_row, meta)

        # Vaqtni dastur qo'yadi — kassir o'zgartira olmaydi (audit uchun).
        self._silent_cells = True
        try:
            time_item = self.table.item(table_row, DRAFT_COLUMN["vt"])
            if time_item is not None:
                time_item.setText(timestamp[11:16])
        finally:
            self._silent_cells = False
        self.save_drafts()
        return meta

    def _cell_text(self, field: str, text: str) -> Optional[str]:
        """Katakka yuboriladigan qiymat (summalar — faqat raqam, RAW)."""
        value = (text or "").strip()
        if field in AMOUNT_FIELDS:
            if not value:
                return ""
            number = parse_summa(value)
            if number is None or number <= 0:
                return None
            return str(int(number)) if float(number).is_integer() else ("%g" % number)
        return value

    def _queue_cell(self, meta: Dict, field: str, text: str) -> bool:
        value = self._cell_text(field, text)
        if value is None:
            return False
        return self.cells.queue_cell(
            {"id": meta["rid"], "sheet_row": meta["sr"]}, CELL_COLUMN[field], value
        )

    def _queue_row_cells(self, table_row: int, meta: Dict) -> None:
        """Qatordagi to'ldirilgan kataklarni navbatga qo'yadi (takrorisiz)."""
        existing = {
            str(cell["column_letter"]): str(cell["value"])
            for cell in self.storage.cell_status(int(meta["sr"]))
        }
        for name, col in DRAFT_FIELDS:
            if name == "vt":
                continue
            item = self.table.item(table_row, col)
            value = self._cell_text(name, item.text() if item else "")
            if value is None:
                continue
            letter = CELL_COLUMN[name]
            if existing.get(letter) == value:
                continue  # allaqachon bir xil qiymat yuborilgan/navbatda
            self.cells.queue_cell(
                {"id": meta["rid"], "sheet_row": meta["sr"]}, letter, value
            )

    def _flush_cells(self) -> None:
        """Debounce tugagach: qatorni ochib, faqat o'zgargan kataklarni yuboradi."""
        dirty = sorted(self._dirty_cells)
        self._dirty_cells.clear()
        if not dirty:
            return

        changed = False
        for draft_index, field in dirty:
            table_row = self._draft_table_row(draft_index)
            if table_row < 0 or table_row >= self.table.rowCount():
                continue
            item = self.table.item(table_row, DRAFT_COLUMN[field])
            text = item.text() if item else ""
            value = self._cell_text(field, text)
            if value is None:
                continue  # noto'g'ri summa qator ochmaydi
            meta = self._row_meta(table_row)
            if not value and not int(meta.get("sr") or 0):
                continue  # bo'sh katak yangi qator ochmaydi
            meta = self._ensure_row_opened(draft_index)
            if not meta:
                continue
            if self.cells.queue_cell(
                {"id": meta["rid"], "sheet_row": meta["sr"]},
                CELL_COLUMN[field],
                value,
            ):
                changed = True

        self.save_drafts()
        if changed:
            self.cells.notify()
            self._refresh_sheet_status()

    def _refresh_sheet_status(self) -> None:
        if not hasattr(self, "sheet_label") or self._closing:
            return
        status = self.cells.status()
        pending = int(status.get("pending") or 0) + int(self.storage.pending_count())
        if pending:
            text = f"{pending} ta yozuv navbatda"
            color = COLORS["gold_dark"]
        elif status.get("connected"):
            text = "Sheets bilan ulangan"
            color = COLORS["green_dark"]
        else:
            text = "Sheets bilan ulanmagan"
            color = COLORS["gold_dark"]
        self.sheet_label.setText(text)
        self.sheet_label.setStyleSheet(f"color: {color};")

    # ---------------------------------------------------------------- yozuv qo'shish
    def add_record_from_row(self) -> None:
        """Kiritilgan summani saqlaydi; summa yo'q bo'lsa erkin yangi bo'sh qator qo'shadi."""
        if self._updating_table:
            return

        kassir = self.kassir_edit.text().strip()
        if not kassir:
            self.show_toast("Kassir ismini yozing.", "error")
            self.kassir_edit.setFocus()
            return

        # Enter bosilganda debounce'ni kutmasdan navbatni bo'shatamiz.
        self._flush_cells()

        # Saqlangan qatorda Enter bosilgan bo'lsa — yangi yozuv qo'shilmaydi,
        # faqat katak tahriri saqlanadi (yangi bo'sh qator ham ochilmaydi).
        current_row = self.table.currentRow()
        if current_row >= 0 and self._draft_index(current_row) is None:
            return

        total_rows = self.table.rowCount()
        if total_rows <= 0:
            return

        amount_cols = [
            (2, "Kirim", "Naqd", 5),
            (3, "Kirim", "Click", 5),
            (4, "Kirim", "Terminal", 5),
            (6, "Chiqim", "Naqd", 8),
            (7, "Chiqim", "Click", 8),
        ]

        # Bo'sh qatorlar orasidan birida summa yozilganini qidiramiz
        start_draft_row = max(0, total_rows - self.extra_draft_rows)
        for r_idx in range(start_draft_row, total_rows):
            # Qatordagi barcha to'ldirilgan summa katakchalari — bitta
            # qatorda bir nechta to'lov bo'lsa ham birortasi yo'qolmaydi.
            entries = []
            for col_idx, turi, tolov, comment_col in amount_cols:
                item = self.table.item(r_idx, col_idx)
                text = item.text().strip() if item else ""
                if not text:
                    continue
                val = parse_summa(text)
                if val is not None and val > 0:
                    entries.append((turi, tolov, comment_col, val))
            if not entries:
                continue

            draft_index = self._draft_index(r_idx)
            meta = (
                self._ensure_row_opened(draft_index)
                if draft_index is not None
                else None
            )
            time_item = self.table.item(r_idx, 1)
            custom_vaqt = time_item.text().strip() if time_item else ""
            if meta and meta.get("ts"):
                custom_vaqt = str(meta["ts"])[11:16]
            custom_sana = self.date_edit.text().strip() or today_str()
            now = dt.datetime.now()
            sheet_row = int(meta["sr"]) if meta else 0
            payload = []
            for turi, tolov, comment_col, val in entries:
                c_item = self.table.item(r_idx, comment_col)
                payload.append(
                    {
                        "sana": custom_sana,
                        "vaqt": custom_vaqt or now_time_str(now),
                        "kassir": kassir,
                        "turi": turi,
                        "tolov_usuli": tolov,
                        "summa": val,
                        "izoh": c_item.text().strip() if c_item else "",
                        "sheet_row": sheet_row,
                        "status": "sent" if meta else None,
                    }
                )
            try:
                # Bitta tranzaksiyada saqlanadi — qisman saqlash bo'lmaydi.
                self.storage.add_many(payload)
            except Exception as exc:  # noqa: BLE001
                self.show_toast(f"Yozuvni saqlab bo'lmadi: {exc}", "error")
                return

            # Qatordagi barcha to'ldirilgan kataklar navbatga qo'yiladi
            # (debounce ulgurmagan bo'lsa ham hech narsa yo'qolmaydi).
            if meta:
                self._queue_row_cells(r_idx, meta)
                self.cells.notify()

            if self.settings.get("last_kassir") != kassir:
                self.settings.set("last_kassir", kassir)
                self.settings.save()

            # Faqat shu qator draftdan chiqadi — boshqa kiritilgan
            # qatorlar joyida qoladi (avval esa hammasi o'chib ketar edi).
            self._drop_draft_row(r_idx, total_rows)
            self.refresh_all()
            self._refresh_sheet_status()
            return

        # Summa bo'lmasa erkin yangi bo'sh qator qo'shamiz
        self.extra_draft_rows += 1
        self.refresh_table()
        self.show_toast("Yangi bo'sh qator qo'shildi", "info")

    def add_record(self) -> None:
        self.add_record_from_row()

    def _drop_draft_row(self, row_index: int, total_rows: int) -> None:
        """Saqlangan qatorni draftdan chiqaradi, qolganlarini saqlaydi."""
        draft_start = max(0, total_rows - self.extra_draft_rows)
        submitted = max(0, row_index - draft_start)
        remaining = [
            row
            for i, row in enumerate(self._draft_rows_from_table())
            if i != submitted
        ]
        self.drafts.save(
            remaining,
            kassir=self.kassir_edit.text().strip(),
            sana=self.date_edit.text().strip(),
        )
        self.extra_draft_rows = len(remaining) + 1

    # ---------------------------------------------------------------------- sync
    def retry_sync(self) -> None:
        self._quiet = False
        self.sync.request(REASON_MANUAL)

    def _on_sync_started(self, count: int) -> None:
        self.status_label.setText(f"Google Sheets'ga yuborilmoqda… ({count} ta)")

    def _on_sync_finished(self, sent: int, message: str, ok: bool) -> None:
        if self._closing:
            return
        quiet = self._quiet
        self._quiet = False
        self.status_label.setText(message)
        self.refresh_stats()
        self.refresh_table()
        self.refresh_pending()
        if not ok:
            if not quiet:
                self.show_toast(message, "error")
        elif sent:
            self.show_toast(message, "ok")
        self._refresh_sheet_status()

        if ok and self.storage.pending_count() and not self.sync.busy:
            self._quiet = self.sync.request(REASON_AUTO)

    # ------------------------------------------------------------------- ko'rsatish
    def refresh_all(self) -> None:
        self.refresh_stats()
        self.refresh_table()
        self.refresh_pending()

    def refresh_stats(self) -> None:
        since_iso = self.settings.get("day_closed_at") or ""
        stats = self.storage.today_detailed_stats(today_str(), since_iso=since_iso)

        k_naqd = stats.get("Kirim_Naqd", 0.0)
        k_click = stats.get("Kirim_Click", 0.0)
        k_term = stats.get("Kirim_Terminal", 0.0)

        c_naqd = stats.get("Chiqim_Naqd", 0.0)
        c_click = stats.get("Chiqim_Click", 0.0)

        q_naqd = k_naqd - c_naqd
        q_click = k_click - c_click
        q_term = k_term

        # Summary Table: 3 rows x 3 cols
        # Row 0: Kirim
        self._set_summary_cell(0, 0, fmt_money(k_naqd), COLORS["green_dark"], bold=True)
        self._set_summary_cell(0, 1, fmt_money(k_click), COLORS["green_dark"], bold=True)
        self._set_summary_cell(0, 2, fmt_money(k_term), COLORS["green_dark"], bold=True)

        # Row 1: Chiqim
        self._set_summary_cell(1, 0, fmt_money(c_naqd), COLORS["red_dark"], bold=True)
        self._set_summary_cell(1, 1, fmt_money(c_click), COLORS["red_dark"], bold=True)
        self._set_summary_cell(1, 2, "—", COLORS["text_faint"], bold=False)

        # Row 2: Qoldiq
        self._set_summary_cell(2, 0, fmt_money(q_naqd), COLORS["text"], bold=True)
        self._set_summary_cell(2, 1, fmt_money(q_click), COLORS["text"], bold=True)
        self._set_summary_cell(2, 2, fmt_money(q_term), COLORS["text"], bold=True)

    def _set_summary_cell(
        self, row: int, col: int, text: str, color: str, bold: bool = False
    ) -> None:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont(self.summary_table.font())
        font.setBold(bold)
        item.setFont(font)
        item.setForeground(QBrush(QColor(color)))
        self.summary_table.setItem(row, col, item)

    def refresh_pending(self) -> None:
        self._refresh_sheet_status()

    def refresh_table(self) -> None:
        self._updating_table = True
        try:
            since_iso = self.settings.get("day_closed_at") or ""
            records: List[Dict] = self.storage.today_records(today_str(), since_iso=since_iso)

            total_rows = len(records) + self.extra_draft_rows
            self.table.setRowCount(total_rows)

            # Saqlangan yozuvlarni chiqarish
            for row_idx, record in enumerate(records):
                turi = record["turi"]
                tolov = record["tolov_usuli"]
                summa_str = fmt_money(record["summa"])
                izoh = record.get("izoh", "") or ""

                # #
                item_num = QTableWidgetItem(str(row_idx + 1))
                item_num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item_num.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                # Saqlangan qator ham tahrirlanadi — jurnal qatori ma'lumoti shu yerda.
                try:
                    sheet_row = int(record.get("sheet_row") or 0)
                except (TypeError, ValueError):
                    sheet_row = 0
                if sheet_row > 0:
                    item_num.setData(
                        Qt.ItemDataRole.UserRole,
                        {"rid": 0, "sr": sheet_row, "ts": ""},
                    )

                # Vaqt
                item_time = QTableWidgetItem(list_time(record["vaqt"]))
                item_time.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item_time.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)

                self.table.setItem(row_idx, 0, item_num)
                self.table.setItem(row_idx, 1, item_time)

                # Bo'sh katakchalar (2..8 — barcha summa/izoh ustunlari).
                # Saqlangan qator ham tahrirlanadi (katak yangilanishi Sheets'ga
                # alohida yuboriladi), shuning uchun ItemIsEditable qo'shiladi.
                for c in range(2, len(JOURNAL_HEADERS)):
                    item = QTableWidgetItem("")
                    item.setFlags(
                        Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsEditable
                    )
                    self.table.setItem(row_idx, c, item)

                if turi == "Kirim":
                    if tolov == "Naqd":
                        col_idx = 2
                    elif tolov == "Click":
                        col_idx = 3
                    else:  # Terminal
                        col_idx = 4
                    color = COLORS["green_dark"]
                else:  # Chiqim — Click ham o'z ustuniga tushadi (Naqd'ga emas)
                    col_idx = 7 if tolov == "Click" else 6
                    color = COLORS["red_dark"]

                item_sum = QTableWidgetItem(summa_str)
                item_sum.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                item_sum.setForeground(QBrush(QColor(color)))
                font = item_sum.font()
                font.setBold(True)
                item_sum.setFont(font)
                item_sum.setFlags(
                    Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsEditable
                )
                self.table.setItem(row_idx, col_idx, item_sum)

                if izoh:
                    # Kirim izohi "KIRIM — Comment"da, chiqim izohi
                    # "CHIQIM — Comment"da (avval ikkalasi ham 5-ustunda edi).
                    comment_col = 5 if turi == "Kirim" else 8
                    item_izoh = QTableWidgetItem(izoh)
                    item_izoh.setFlags(
                        Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsEditable
                    )
                    self.table.setItem(row_idx, comment_col, item_izoh)

            # Yangi bo'sh qatorlar (extra_draft_rows ta)
            # Kiritilgan, lekin Enter bosilmagan qatorlar saqlanadi va
            # qayta chizilganda ham o'z joyini saqlab qoladi (chiroq uzilsa ham).
            saved_drafts = self.drafts.rows()
            draft_start = len(records)

            for i in range(self.extra_draft_rows):
                draft_row_idx = draft_start + i
                saved = saved_drafts[i] if i < len(saved_drafts) else {}

                item_num = QTableWidgetItem(str(draft_row_idx + 1))
                item_num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item_num.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                item_num.setForeground(QBrush(QColor(COLORS["gold_dark"])))
                # Ochiq jurnal qatorining ma'lumoti (rid/sr/ts) — `#` katagida.
                try:
                    has_row = int(saved.get("sr") or 0) > 0
                except (TypeError, ValueError):
                    has_row = False
                if has_row:
                    item_num.setData(
                        Qt.ItemDataRole.UserRole,
                        {
                            "rid": saved.get("rid"),
                            "sr": saved.get("sr"),
                            "ts": str(saved.get("ts") or ""),
                        },
                    )

                # Vaqt dastur tomonidan qo'yiladi — kassir uni o'zgartira
                # olmaydi (web-versiya bilan bir xil, audit uchun).
                # Bo'sh qatorda Vaqt BO'SH — birinchi qiymat kiritilganda
                # (jurnal qatori ochilganda) to'ldiriladi.
                saved_time = str(saved.get("vt") or "").strip()
                if not saved_time and saved.get("ts"):
                    saved_time = str(saved["ts"])[11:16]
                item_time = QTableWidgetItem(saved_time)
                item_time.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item_time.setFlags(
                    Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                )

                self.table.setItem(draft_row_idx, 0, item_num)
                self.table.setItem(draft_row_idx, 1, item_time)

                for c in range(2, len(JOURNAL_HEADERS)):
                    value = ""
                    for name, col in DRAFT_FIELDS:
                        if col == c:
                            value = str(saved.get(name) or "").strip()
                            break
                    if c in (2, 3, 4, 6, 7) and value:
                        parsed = parse_summa(value)
                        if parsed is not None:
                            value = fmt_money(parsed)
                    item = QTableWidgetItem(value)
                    item.setFlags(
                        Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsEditable
                    )
                    if c in (2, 3, 4, 6, 7):
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                        )
                    self.table.setItem(draft_row_idx, c, item)

        finally:
            self._updating_table = False

    # ------------------------------------------------------------------- draft
    def _on_cell_changed(self, row: int, col: int) -> None:
        """Jadval katakchasi o'zgarganda draft'ni diskka saqlaydi.

        Bu chiroq uzilishi/jarayon to'xtashi holatida kiritilgan summani
        yo'qotmaslik uchun kerak: foydalanuvchi Enter bosmagan bo'lsa ham
        kiritilgan ma'lumot diskda qoladi.
        """
        if self._updating_table or self._silent_cells or self._closing:
            return
        field = DRAFT_FIELD_BY_COL.get(col)
        if not field or field == "vt":
            return

        draft_index = self._draft_index(row)
        if draft_index is None:
            # Saqlangan qator — bazadagi yozuvni va Sheets katagini yangilaymiz.
            self._on_record_cell_changed(row, col, field)
            return

        self.save_drafts()
        self._dirty_cells.add((draft_index, field))
        self._cell_timer.start()

    def _on_record_cell_changed(self, row: int, col: int, field: str) -> None:
        """Saqlangan qatordagi katak o'zgartirildi — yozuv va Sheets yangilanadi."""
        meta = self._row_meta(row)
        sheet_row = int(meta.get("sr") or 0)
        if sheet_row <= 0:
            return
        item = self.table.item(row, col)
        text = item.text().strip() if item is not None else ""
        if field in AMOUNT_FIELDS:
            self._apply_record_amount(row, col, field, sheet_row, text)
        else:
            self._apply_record_note(sheet_row, field, text)

    def _apply_record_amount(
        self, row: int, col: int, field: str, sheet_row: int, text: str
    ) -> None:
        turi, tolov = FIELD_PAYMENT[field]
        value = parse_summa(text) if text else 0
        if value is None:
            self._restore_record_cell(row, col, field, sheet_row)
            self.show_toast("Summa noto'g'ri — o'zgarish qabul qilinmadi.", "warn")
            return

        value = float(value)
        records = [
            rec
            for rec in self.storage.records_for_sheet_row(sheet_row)
            if rec["turi"] == turi and rec["tolov_usuli"] == tolov
        ]
        if value <= 0:
            # Katak tozalandi — yozuv ham o'chiriladi (hisob-kitob to'g'ri qolsin).
            for rec in records:
                self.storage.delete_record(int(rec["id"]))
            cell_value = ""
        else:
            if records:
                self.storage.update_record(int(records[0]["id"]), summa=value)
            else:
                jrow = self.storage.journal_row_by_sheet_row(sheet_row) or {}
                comment_col = 5 if turi == "Kirim" else 8
                c_item = self.table.item(row, comment_col)
                self.storage.add(
                    sana=str(
                        jrow.get("row_date")
                        or self.date_edit.text().strip()
                        or today_str()
                    ),
                    vaqt=str(jrow.get("timestamp_text") or "")[11:16]
                    or now_time_str(),
                    kassir=str(jrow.get("kassir") or self.kassir_edit.text().strip()),
                    turi=turi,
                    tolov_usuli=tolov,
                    summa=value,
                    izoh=c_item.text().strip() if c_item is not None else "",
                    sheet_row=sheet_row,
                    status="sent",
                )
            cell_value = self._cell_text(field, text) or ""

        self._queue_record_cell(sheet_row, field, cell_value)
        self._silent_cells = True
        try:
            item = self.table.item(row, col)
            if item is not None:
                item.setText(fmt_money(value) if value > 0 else "")
        finally:
            self._silent_cells = False
        self.refresh_stats()

    def _apply_record_note(self, sheet_row: int, field: str, text: str) -> None:
        """Izoh katagi — shu qatordagi barcha mos yozuvlarga yoziladi."""
        turi = "Kirim" if field == "ki" else "Chiqim"
        for rec in self.storage.records_for_sheet_row(sheet_row):
            if rec["turi"] == turi:
                self.storage.update_record(int(rec["id"]), izoh=text)
        self._queue_record_cell(sheet_row, field, text)

    def _restore_record_cell(
        self, row: int, col: int, field: str, sheet_row: int
    ) -> None:
        """Noto'g'ri kiritilgan summani bazadagi qiymatga qaytaradi."""
        turi, tolov = FIELD_PAYMENT[field]
        value = 0.0
        for rec in self.storage.records_for_sheet_row(sheet_row):
            if rec["turi"] == turi and rec["tolov_usuli"] == tolov:
                value = float(rec["summa"])
                break
        self._silent_cells = True
        try:
            item = self.table.item(row, col)
            if item is not None:
                item.setText(fmt_money(value) if value > 0 else "")
        finally:
            self._silent_cells = False

    def _queue_record_cell(self, sheet_row: int, field: str, value: str) -> None:
        jrow = self.storage.journal_row_by_sheet_row(sheet_row)
        if not jrow:
            return
        if self.cells.queue_cell(
            {"id": int(jrow["id"]), "sheet_row": int(sheet_row)},
            CELL_COLUMN[field],
            value,
        ):
            self._refresh_sheet_status()

    def _draft_rows_from_table(self) -> List[Dict]:
        """Jadvaldagi kiritilmagan qatorlarni lug'atlar ro'yxatiga aylantiradi."""
        rows: List[Dict] = []
        total_rows = self.table.rowCount()
        start = max(0, total_rows - self.extra_draft_rows)
        for r_idx in range(start, total_rows):
            row: Dict[str, str] = {}
            for name, col in DRAFT_FIELDS:
                item = self.table.item(r_idx, col)
                row[name] = item.text().strip() if item else ""
            meta = self._row_meta(r_idx)
            if int(meta.get("sr") or 0) > 0:
                row["rid"] = str(meta["rid"])
                row["sr"] = str(meta["sr"])
                row["ts"] = str(meta.get("ts") or "")
            rows.append(row)
        return rows

    def save_drafts(self) -> bool:
        """Kiritilmagan qatorlarni diskga saqlaydi (atomik)."""
        try:
            rows = self._draft_rows_from_table()
        except RuntimeError:
            return False
        kassir = self.kassir_edit.text().strip() if hasattr(self, "kassir_edit") else ""
        sana = self.date_edit.text().strip() if hasattr(self, "date_edit") else ""
        return self.drafts.save(rows, kassir=kassir, sana=sana)

    def restore_drafts(self) -> bool:
        """Saqlangan draftlarni jadvalga qaytaradi.

        Dastur qayta ishga tushganda chaqiriladi — kiritilgan lekin Enter
        bosilmagan ma'lumot foydalanuvchiga ko'rinadi.
        """
        saved = self.drafts.rows()
        if not saved:
            return False
        if self.extra_draft_rows < len(saved):
            self.extra_draft_rows = len(saved)
        self.refresh_table()
        self.show_toast(
            "Tiklangan kiritilmagan ma'lumot bor — Enter bosing.", "warn"
        )
        return True

    # ---------------------------------------------------------------- Kunni yopish
    def close_day(self) -> None:
        reply = QMessageBox.question(
            self,
            "Kunni yopish",
            "Joriy kun kassa jurnalini yopishni va yangi kunni boshlashni tasdiqlaysizmi?\n\n"
            "(Barcha saqlangan yozuvlar Google Sheets va bazada xavfsiz saqlanadi).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            from helpers import now_iso
            # Yopishdan oldin barcha kiritilgan ma'lumotni diskka saqlaymiz.
            try:
                self._flush_cells()
            except Exception:  # noqa: BLE001
                pass
            try:
                self.save_drafts()
            except Exception:  # noqa: BLE001
                pass

            # PDF hujjat tayyorlash (dastur interfeysi bilan bir xil jadval).
            pdf_path = self.export_day_pdf()

            self.settings.set("day_closed_at", now_iso())
            self.settings.save()
            self.refresh_all()
            if pdf_path:
                self.show_toast(f"Kun yopildi. PDF: {pdf_path}", "ok")
            else:
                self.show_toast("Kun yopildi. Yangi kun jurnali boshlandi.", "ok")

    def export_day_pdf(self) -> str:
        """Joriy kun jurnalini PDF faylga eksport qiladi (jadval interfeys bilan bir xil)."""
        import os
        from config import DATA_DIR
        from helpers import today_str
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
        except OSError:
            pass
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(DATA_DIR, f"kassa_{stamp}.pdf")

        # Jadval sarlavhasi
        header_cells = "".join(
            f"<th>{h}</th>" for h in JOURNAL_HEADERS
        )
        rows_html = ""
        for r in range(self.table.rowCount()):
            cells = ""
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                text = item.text() if item is not None else ""
                cells += f"<td>{text}</td>"
            rows_html += f"<tr>{cells}</tr>"

        kassir = self.kassir_edit.text().strip() if hasattr(self, "kassir_edit") else ""
        sana = self.date_edit.text().strip() if hasattr(self, "date_edit") else today_str()

        html = f"""
        <html><head><meta charset='utf-8'>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; font-size: 10pt; }}
            h2 {{ color: #1E293B; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #94A3B8; padding: 4px 6px; font-size: 9pt; }}
            th {{ background-color: #E2E8F0; font-weight: bold; }}
            .summary {{ margin-top: 12px; font-weight: bold; }}
        </style></head><body>
        <h2>GOLD restobar — Kassa jurnali</h2>
        <p>Sana: {sana} &nbsp;|&nbsp; Kassir: {kassir}</p>
        <table>
            <thead><tr>{header_cells}</tr></thead>
            <tbody>{rows_html}</tbody>
        </table>
        </body></html>
        """

        try:
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(path)
            printer.setPageSize(QPrinter.PageSize.A4)
            doc = QTextDocument()
            doc.setHtml(html)
            doc.print(printer)
            return path
        except Exception:  # noqa: BLE001
            return ""

    # --------------------------------------------------------------------- xabar
    def show_toast(self, message: str, level: str = "info") -> None:
        self.toast_label.setText(message)
        self.toast_label.setProperty("level", level)
        self.toast_label.style().unpolish(self.toast_label)
        self.toast_label.style().polish(self.toast_label)
        self.toast_label.show()
        self._toast_timer.start(7000)

    def _hide_toast(self) -> None:
        self.toast_label.hide()

    # ------------------------------------------------------------------- sozlamalar
    def open_settings(self) -> None:
        from settings_dialog import SettingsDialog

        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.client = SheetsClient(
            credentials_file=self.settings.credentials_file,
            spreadsheet_id=self.settings.spreadsheet_id,
            worksheet_name=self.settings.worksheet_name,
            auto_create_sheet=self.settings.auto_create_sheet,
        )
        self.sync.client = self.client
        self.cells.invalidate_client()
        self.show_toast("Sozlamalar saqlandi.", "ok")
        self.refresh_pending()
        self._quiet = False
        self.sync.request(REASON_MANUAL)

    # ----------------------------------------------------------------------- yopish
    def closeEvent(self, event) -> None:  # noqa: D102 - Qt API
        self._closing = True
        self._clock_timer.stop()
        self._retry_timer.stop()
        self._backup_timer.stop()
        self._toast_timer.stop()
        # Kiritilmagan ma'lumotni oxirgi marta diskka yozamiz va navbatga
        # qo'yamiz (kataklar SQLite'da qoladi — keyingi ishga tushishda
        # avtomatik yuboriladi).
        self._cell_timer.stop()
        try:
            self._flush_cells()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.save_drafts()
        except Exception:  # noqa: BLE001
            pass
        # Yopishdan oldin oxirgi yedek nusxa.
        try:
            self.backup_service.run(force=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.sync.finished.disconnect()
        except TypeError:
            pass
        self.sync.wait(4000)
        self.cells.stop()
        self.cells.join(2000)
        if self._migration_thread is not None:
            self._migration_thread.join(2000)
        self.storage.close()
        super().closeEvent(event)


__all__ = ["MainWindow", "JournalTableWidget", "JOURNAL_HEADERS"]
