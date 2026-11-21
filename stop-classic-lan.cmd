@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "scripts\classic_autostart.py" stop
if errorlevel 1 pause
