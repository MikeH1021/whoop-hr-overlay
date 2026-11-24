@echo off
REM Build a standalone whoop-hr-overlay.exe on this Windows machine.
REM Output lands in dist\whoop-hr-overlay.exe. (CI uses the same command.)
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv || python -m venv .venv
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller

pyinstaller --noconfirm --clean --onefile --windowed ^
    --name whoop-hr-overlay ^
    --collect-all bleak ^
    main.py

echo.
echo Done. Your exe is at: dist\whoop-hr-overlay.exe
