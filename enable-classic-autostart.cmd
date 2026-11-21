@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "scripts\classic_autostart.py" install
if errorlevel 1 (pause & exit /b 1)
