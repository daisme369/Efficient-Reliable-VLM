@echo off
setlocal
cd /d "%~dp0" || exit /b 1
set "THORAX_RUNTIME=%~dp0..\thorax-tools"
set "PATH=%THORAX_RUNTIME%;%PATH%"
curl.exe --fail --silent --output NUL --max-time 2 http://127.0.0.1:3100/login
if not errorlevel 1 (
    echo Thorax is already running at http://127.0.0.1:3100/login
    exit /b 0
)
echo Starting Thorax. Open http://127.0.0.1:3100/login when it is ready.
set "THORAX_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%THORAX_PYTHON%" set "THORAX_PYTHON=%~dp0..\backend-venv\Scripts\python.exe"
"%THORAX_PYTHON%" scripts/integration/preview.py run
if errorlevel 1 pause
