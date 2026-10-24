@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [XATO] .venv topilmadi.
    echo Avval quyidagini bajaring:  python -m venv .venv
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo.
echo [1/2] Kutubxonalar o'rnatilmoqda...
python -m pip install --disable-pip-version-check -r requirements-dev.txt
if errorlevel 1 goto :xato

echo.
echo [2/2] .exe yasalmoqda...
python -m PyInstaller --noconfirm --clean kassa.spec
if errorlevel 1 goto :xato

echo.
echo ============================================
echo  Tayyor: dist\GOLD_Kassa.exe
echo ============================================
echo  Muhim: service_account.json faylini ham
echo  dist papkasiga nusxalang (exe yoniga).
echo.
pause
exit /b 0

:xato
echo.
echo [XATO] Build bajarilmadi. Yuqoridagi xabarni o'qing.
pause
exit /b 1
