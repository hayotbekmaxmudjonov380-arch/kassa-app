"""Dastur sozlamalari: Google Sheets, papkalar, variantlar.

Ma'lumotlar (SQLite, settings.json, service_account.json) dastur papkasidan
TASHQARIDA — `%APPDATA%\\GoldKassa` ichida saqlanadi. Shu tufayli dastur
yangilanganda (exe almashtirilganda) ma'lumotlar hech qachon o'chmaydi.
"""

import os
import shutil
import sys

APP_NAME = "GOLD restobar — Kassa jurnali"
APP_ORG = "GOLD"
WINDOW_TITLE = APP_NAME
APP_VERSION = "1.1.0"

# GitHub Releases orqali avtomatik yangilanish (server kerak emas).
GITHUB_OWNER = "hayotbekmaxmudjonov380-arch"
GITHUB_REPO = "kassa-app"
UPDATE_CHECK_INTERVAL_HOURS = 24

SPREADSHEET_ID = "1sbpmCrvkzJ3gRpXNPcjNZtEWYJkb8CJiALJjeRs-Vrg"
WORKSHEET_NAME = "Kassa jurnali"

# Yangi jurnal formati (A..J). 1-qator — sarlavha, ma'lumotlar 2-qatordan.
SHEET_HEADERS = [
    "#",
    "Vaqt",
    "KIRIM — Naqd",
    "KIRIM — Click",
    "KIRIM — Terminal",
    "KIRIM — Comment",
    "CHIQIM — Naqd",
    "CHIQIM — Click",
    "CHIQIM — Comment",
    "Kassir",
]
SHEET_FIRST_DATA_ROW = 2

# Maydon kaliti -> Google Sheets ustuni (faqat shu 7 katak yoziladi).
CELL_COLUMN = {
    "kn": "C",
    "kc": "D",
    "kt": "E",
    "ki": "F",
    "cn": "G",
    "cc": "H",
    "ci": "I",
}
COLUMN_NUMBER = "A"
COLUMN_TIME = "B"
COLUMN_KASSIR = "J"
AMOUNT_FIELDS = {"kn", "kc", "kt", "cn", "cc"}

# Eski (1.0) format — faqat eski testlar/moslik uchun qoldirilgan.
LEGACY_HEADERS = ["Sana", "Vaqt", "Kassir", "Turi", "To'lov usuli", "Summa", "Izoh", "UID"]
HEADERS = LEGACY_HEADERS

KIRIM = "Kirim"
CHIQIM = "Chiqim"
PAYMENTS = {
    KIRIM: ["Naqd", "Click", "Terminal"],
    CHIQIM: ["Naqd", "Click"],
}

SHEET_ORDER = [KIRIM, CHIQIM]

# Tarmoq amallari uchun kutish chegarasi (soniya). Uzun kutish kassirni
# bloklab qo'ymasligi uchun qisqa: xato bo'lsa yozuv navbatda qoladi.
HTTP_TIMEOUT_SECONDS = 20

# Katak yozuvini kechiktirish (ms) va navbatni qayta urinish oralig'i (s).
CELL_DEBOUNCE_MS = 600
CELL_RETRY_SECONDS = 25


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def base_dir() -> str:
    """.exe yoki skript qaysi papkada turgani shu papka."""
    if _frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = base_dir()


def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".yozish_testi.tmp")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def data_dir() -> str:
    """Foydalanuvchi ma'lumotlari papkasi.

    Windows: %APPDATA%\\GoldKassa (Roaming). Dastur papkasi emas — shuning
    uchun exe yangilanganda baza/sozlamalar/kalit saqlanib qoladi.
    """
    override = os.environ.get("KASSA_DATA_DIR")
    if override:
        os.makedirs(override, exist_ok=True)
        return override

    roaming = os.environ.get("APPDATA")
    candidates = []
    if roaming:
        candidates.append(os.path.join(roaming, "GoldKassa"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "GoldKassa"))
    candidates.append(os.path.join(BASE_DIR, "data"))

    for candidate in candidates:
        if _writable(candidate):
            return candidate
    return BASE_DIR


def resource_path(*parts: str) -> str:
    """Dastur bilan birga keladigan fayl (rasm, shablon) yo'li.

    .exe ichida (onefile) fayllar `sys._MEIPASS` ga chiqariladi — shu sabab
    oddiy `BASE_DIR` yetarli emas.
    """
    root = getattr(sys, "_MEIPASS", None) or BASE_DIR
    return os.path.join(root, *parts)


DATA_DIR = data_dir()
IMAGE_DIR = resource_path("image")
LOGO_PATH = os.path.join(IMAGE_DIR, "logotip.jpg")
JOURNAL_TEMPLATE_PATH = resource_path("journal.html")
SERVICE_ACCOUNT_FILE = os.path.join(DATA_DIR, "service_account.json")
LOG_PATH = os.path.join(DATA_DIR, "xato.log")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
DB_PATH = os.path.join(DATA_DIR, "kassa.db")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
BACKUP_PREFIX = "kassa_"
BACKUP_KEEP = 30          # nechta eski nusxa saqlanadi
BACKUP_INTERVAL_MIN = 30  # har necha daqiqada nusxa olinadi
DRAFTS_PATH = os.path.join(DATA_DIR, "drafts.json")

# Eski joylashuvlar (ko'chirish uchun).
LEGACY_DIRS = [BASE_DIR]
_local = os.environ.get("LOCALAPPDATA")
if _local:
    LEGACY_DIRS.append(os.path.join(_local, "GOLD_Kassa"))
    LEGACY_DIRS.append(os.path.join(_local, "GOLD_Kassa", "state"))
LEGACY_DB_PATH = (
    os.path.join(_local, "GOLD_Kassa", "state", "kassa.db")
    if _local
    else os.path.join(BASE_DIR, "kassa.db")
)


def _copy_if_missing(source: str, target: str) -> bool:
    """Fayl nusxasini faqat target hali yo'q bo'lsa ko'chiradi."""
    try:
        if not os.path.isfile(source) or os.path.exists(target):
            return False
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        shutil.copy2(source, target)
        return True
    except OSError:
        return False


def migrate_legacy_files(target_dir: str = DATA_DIR) -> list:
    """Eski joylashuvdagi ma'lumotlarni yangi papkaga ko'chiradi.

    Hech narsani o'chirmaydi — faqat target'da yo'q fayllarni nusxalaydi.
    """
    if target_dir == BASE_DIR and os.environ.get("KASSA_DATA_DIR"):
        return []
    moved = []

    def try_copy(name, target_name=None):
        target = os.path.join(target_dir, target_name or name)
        for folder in LEGACY_DIRS:
            source = os.path.join(folder, name)
            if os.path.abspath(source) == os.path.abspath(target):
                continue
            if _copy_if_missing(source, target):
                moved.append(target)
                return

    try_copy("settings.json")
    try_copy("service_account.json")
    try_copy("drafts.json")
    try_copy("kassa.db")
    # WAL/SHM ham ko'chiriladi — aks holda eng oxirgi yozuvlar yo'qolishi mumkin.
    try_copy("kassa.db-wal")
    try_copy("kassa.db-shm")
    return moved


# Dastur ishga tushishida bir marta ko'chiramiz (override bo'lsa — testlarda
# haqiqiy foydalanuvchi bazasini test papkasiga ko'chirmaymiz).
if not os.environ.get("KASSA_DATA_DIR"):
    migrate_legacy_files(DATA_DIR)

# Qulaylik uchun eski nom (boshqa modullar ishlatadi).
LOCAL_STATE_DIR = DATA_DIR
