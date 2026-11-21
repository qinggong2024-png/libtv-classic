@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "scripts\classic_stack.py" start
if errorlevel 1 (pause & exit /b 1)
start "" "http://127.0.0.1:5176/"
