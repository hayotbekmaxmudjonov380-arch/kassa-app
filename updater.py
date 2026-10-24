"""Avtomatik yangilanish (GitHub Releases, serverless).

Dastur ishga tushganda GitHub'dagi oxirgi relizni tekshiradi. Yangi versiya
bo'lsa — foydalanuvchidan so'raydi ("Yangilash" / "Keyinroq"). Majburiy
yangilash yo'q. Internet bo'lmasa — jim o'tadi.

Muhim: ma'lumotlar `%APPDATA%\\GoldKassa` da (exe yonida emas), shuning
uchun exe almashtirilganda baza/sozlamalar/kalit saqlanib qoladi.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from typing import Optional
from urllib.parse import urlparse

from config import APP_VERSION, DB_PATH, GITHUB_OWNER, GITHUB_REPO

RELEASE_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
UPDATE_DIR_NAME = "GoldKassa_update"


class UpdateInfo:
    def __init__(self, version: str, tag: str, url: str, notes: str = ""):
        self.version = version
        self.tag = tag
        self.url = url
        self.notes = notes

    def __repr__(self) -> str:  # pragma: no cover - qulaylik uchun
        return f"UpdateInfo({self.version!r}, {self.url!r})"


def parse_version(text: str) -> tuple:
    """'v1.2.3' / '1.2.3-beta' -> (1, 2, 3). Bo'lmasa — bo'sh tuple."""
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(text or ""))
    if not match:
        return ()
    return tuple(int(part or 0) for part in match.groups())


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    new = parse_version(latest)
    old = parse_version(current)
    if not new or not old:
        return False
    return new > old


def _fetch_json(url: str, timeout: float, opener=None) -> dict:
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"GoldKassa/{APP_VERSION}",
            "Accept": "application/vnd.github+json",
        },
    )
    with opener(request, timeout=timeout) as response:
        payload = response.read()
    data = json.loads(payload.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def check_for_update(
    current: str = APP_VERSION,
    api_url: Optional[str] = None,
    timeout: float = 10.0,
    opener=None,
) -> Optional[UpdateInfo]:
    """Yangi reliz bo'lsa UpdateInfo qaytaradi; aks holda None.

    Xato (internet yo'q, 404 va h.k.) — jim None. Dastur hech qachon
    yangilanish tekshiruvi sabab to'xtamaydi.
    """
    url = api_url or os.environ.get("KASSA_UPDATE_API") or RELEASE_URL
    try:
        data = _fetch_json(url, timeout, opener=opener)
    except Exception:  # noqa: BLE001
        return None

    tag = str(data.get("tag_name") or "").strip()
    if not is_newer(tag, current):
        return None

    exe_url = ""
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.lower().endswith(".exe"):
            exe_url = str(asset.get("browser_download_url") or "")
            break
    if not exe_url:
        return None

    return UpdateInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        url=exe_url,
        notes=str(data.get("body") or "")[:2000],
    )


def update_dir() -> str:
    override = os.environ.get("KASSA_UPDATE_DIR")
    path = override or os.path.join(tempfile.gettempdir(), UPDATE_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def download_update(info: UpdateInfo, timeout: float = 120.0, opener=None) -> str:
    """Yangi exe'ni vaqtinchalik papkaga yuklaydi va yo'lini qaytaradi."""
    opener = opener or urllib.request.urlopen
    name = os.path.basename(urlparse(info.url).path) or "GOLD_Kassa.exe"
    if not name.lower().endswith(".exe"):
        name = "GOLD_Kassa.exe"
    target = os.path.join(update_dir(), name)
    partial = target + ".part"
    request = urllib.request.Request(
        info.url, headers={"User-Agent": f"GoldKassa/{APP_VERSION}"}
    )
    with opener(request, timeout=timeout) as response, open(partial, "wb") as fh:
        shutil.copyfileobj(response, fh)
    os.replace(partial, target)
    return target


def write_update_script(
    new_exe: str,
    target_exe: str,
    db_path: str = DB_PATH,
    pid: Optional[int] = None,
) -> str:
    """Eski dastur yopilishini kutib, exe'ni almashtiradigan .bat yozadi."""
    pid = os.getpid() if pid is None else int(pid)
    script_path = os.path.join(update_dir(), "update.bat")
    content = f"""@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
set "PID={pid}"
set "NEW={new_exe}"
set "TARGET={target_exe}"
set "DB={db_path}"
:wait
tasklist /FI "PID eq %PID%" 2>nul | findstr /C:"%PID%" >nul
if not errorlevel 1 (
    timeout /t 2 /nobreak >nul
    goto wait
)
if exist "%DB%" copy /Y "%DB%" "%DB%.preupdate" >nul
set /a tries=0
:replace
move /Y "%NEW%" "%TARGET%" >nul 2>&1
if errorlevel 1 (
    set /a tries+=1
    if !tries! lss 10 (
        timeout /t 2 /nobreak >nul
        goto replace
    )
)
if exist "%TARGET%" start "" "%TARGET%"
del "%~f0"
"""
    with open(script_path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(content)
    return script_path


def launch_update_script(script_path: str, pid: Optional[int] = None) -> bool:
    """Updater'ni alohida jarayonda ishga tushiradi (dastur yopiladi)."""
    pid = os.getpid() if pid is None else int(pid)
    try:
        flags = 0
        for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
            flags |= int(getattr(subprocess, name, 0))
        subprocess.Popen(
            ["cmd", "/c", script_path, str(pid)],
            creationflags=flags,
            close_fds=True,
        )
        return True
    except OSError:
        return False


def can_self_update() -> bool:
    """Faqat .exe sifatida ishga tushganda o'zini almashtira oladi."""
    import sys

    return bool(getattr(sys, "frozen", False)) and os.name == "nt"


def prepare_update(info: UpdateInfo, target_exe: Optional[str] = None) -> Optional[str]:
    """Yuklab oladi va updater skriptini tayyorlaydi. Yo'l yoki None."""
    import sys

    target = target_exe or os.path.abspath(sys.executable)
    try:
        new_exe = download_update(info)
        return write_update_script(new_exe, target)
    except Exception:  # noqa: BLE001 — yangilash xatosi dasturni to'xtatmaydi
        return None


__all__ = [
    "UpdateInfo",
    "check_for_update",
    "download_update",
    "is_newer",
    "parse_version",
    "prepare_update",
    "launch_update_script",
    "write_update_script",
    "can_self_update",
]
