@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "scripts\classic_stack.py" stop
if errorlevel 1 pause
