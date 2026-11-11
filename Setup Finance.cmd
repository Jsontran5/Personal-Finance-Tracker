@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
call npm.cmd ci --prefer-offline
if errorlevel 1 goto fail
call npm.cmd run build
if errorlevel 1 goto fail
echo Setup complete. Open Start Finance.cmd to launch.
pause
exit /b 0

:fail
echo Setup failed. See the error above.
pause
exit /b 1

