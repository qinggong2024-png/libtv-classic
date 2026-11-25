@echo off
cd /d "%~dp0"
if "%~1"=="" (
  ".venv\Scripts\python.exe" "scripts\classic_lan.py" start
) else (
  ".venv\Scripts\python.exe" "scripts\classic_lan.py" start --lan-ip "%~1"
)
exit /b %errorlevel%
