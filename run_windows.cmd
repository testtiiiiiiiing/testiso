@echo off
cd /d "%~dp0"
py -3.13 -m winslim
if errorlevel 1 pause
