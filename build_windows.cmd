@echo off
setlocal
cd /d "%~dp0"
py -3.13 -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install --requirement requirements-build.txt
if errorlevel 1 exit /b 1
set WINSLIM_REQUIRE_GUI=1
.venv\Scripts\python.exe -m unittest discover -s tests -v
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin --name WinSlim_Studio_4_0 tools\windows_entry.py
if errorlevel 1 exit /b 1
echo Eseguibile creato: dist\WinSlim_Studio_4_0.exe
