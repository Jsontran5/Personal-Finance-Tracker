@echo off
cd /d "%~dp0"
if not exist "dist\index.html" (
  echo Run Setup Finance.cmd first.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Run Setup Finance.cmd first.
  pause
  exit /b 1
)
rem Hand the app its own Python console; do not leave a batch job waiting.
start "Personal Finance" /d "%~dp0" "%~dp0.venv\Scripts\python.exe" -m finance
