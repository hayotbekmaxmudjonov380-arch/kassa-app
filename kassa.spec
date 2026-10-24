# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — bitta faylli Windows dastur (GOLD_Kassa.exe)."""

hiddenimports = [
    "gspread",
    "gspread.utils",
    "gspread.exceptions",
    "google.auth",
    "google.auth.transport.requests",
    "google.oauth2.service_account",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("journal.html", "."),
        ("image/logotip.jpg", "image"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="GOLD_Kassa",
    icon="image/logotip.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
