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
".venv\Scripts\python.exe" -m finance
if errorlevel 1 pause
