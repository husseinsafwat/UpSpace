@echo off
REM Optional: builds dist\UpSpace\UpSpace.exe so the accountant's PC doesn't need Python.
cd /d "%~dp0"
python -m pip install -q -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --windowed --name UpSpace --collect-data customtkinter --add-data "assets;assets" main.py
echo.
echo Done. Open dist\UpSpace\UpSpace.exe  (the database upspace.db is created next to it).
pause
