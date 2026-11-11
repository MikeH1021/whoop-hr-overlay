@echo off
REM WHOOP Heart Rate Overlay launcher for Windows.
REM First run creates a local virtual environment and installs dependencies;
REM later runs just start the overlay. Pass any main.py flags straight through,
REM e.g.  run-overlay.bat --simulate   or   run-overlay.bat --name WHOOP
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment and installing dependencies...
    py -3 -m venv .venv || python -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call ".venv\Scripts\activate.bat"
)

python main.py %*
