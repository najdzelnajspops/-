@echo off
rem Starts the web UI on Windows. First run creates a local Python environment (.venv)
rem and installs requirements.txt into it, so the service never depends on whichever
rem "python" happens to be first on PATH.
setlocal
cd /d "%~dp0"
set "VENV_PY=.venv\Scripts\python.exe"

if exist "%VENV_PY%" goto run

echo First start: creating a local Python environment in .venv (one-time, a few minutes)...
set "PY="
py -3.14 -c "import sys" >nul 2>nul && set "PY=py -3.14"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python 3.14 was not found. Install it from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH" in the installer^) and run this file again.
    pause
    exit /b 1
)
%PY% -m venv .venv || goto fail
"%VENV_PY%" -m pip install --upgrade pip >nul
"%VENV_PY%" -m pip install -r requirements.txt || goto fail

:run
echo Starting service...
start "Landscaping service - close this window to stop" cmd /k .venv\Scripts\python.exe -m uvicorn pipeline.web.app:app --host 127.0.0.1 --port 8000
echo Waiting for the service to become ready...
powershell -NoProfile -Command "$ok=$false; for ($i=0; $i -lt 60; $i++) { try { Invoke-WebRequest -Uri 'http://127.0.0.1:8000/docs' -UseBasicParsing -TimeoutSec 1 | Out-Null; $ok=$true; break } catch { Start-Sleep -Milliseconds 500 } }; if (-not $ok) { Write-Host 'Service did not respond in time - check the service window for an error.' }"
start "" http://127.0.0.1:8000
exit /b 0

:fail
echo Setup failed - see the messages above. Delete the .venv folder and run this file again.
pause
exit /b 1
