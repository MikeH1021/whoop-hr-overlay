@echo off
REM Build the single standalone whoop-hr-overlay.exe on this Windows machine
REM (overlay + bundled USB-dongle driver installer). Same steps as CI.
REM Output: dist\whoop-hr-overlay.exe
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv || python -m venv .venv
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller

if not exist "drivers\zadig-2.9.exe" (
    echo Downloading Zadig 2.9 ^(dongle driver installer^)...
    powershell -NoProfile -Command ^
      "Invoke-WebRequest https://github.com/pbatard/libwdi/releases/download/v1.5.1/zadig-2.9.exe -OutFile drivers\zadig-2.9.exe;" ^
      "if ((Get-FileHash drivers\zadig-2.9.exe -Algorithm SHA256).Hash.ToLower() -ne '4ecaa95df3da3621486a043aef8b3050b8bafe7c901402871e816229ef82039b') { Remove-Item drivers\zadig-2.9.exe; throw 'Zadig checksum mismatch' }" || exit /b 1
)

pyinstaller --noconfirm --clean --onefile --windowed ^
    --name whoop-hr-overlay ^
    --collect-all bleak --collect-all bumble --collect-all libusb_package ^
    --hidden-import usb1 --hidden-import dongle_source ^
    --add-data "drivers/zadig-2.9.exe;drivers" ^
    --add-data "drivers/zadig.ini;drivers" ^
    main.py

echo.
echo Done. Your exe is at: dist\whoop-hr-overlay.exe
