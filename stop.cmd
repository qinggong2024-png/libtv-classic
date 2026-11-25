@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "scripts\classic_lan.py" stop
exit /b %errorlevel%
