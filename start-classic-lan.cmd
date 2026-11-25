@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "scripts\classic_autostart.py" start
if errorlevel 1 (pause & exit /b 1)
if /i "%~1"=="--no-browser" exit /b 0
start "" "http://127.0.0.1:5176/"
