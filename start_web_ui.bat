@echo off
cd /d "%~dp0"
echo Starting service...
start "Landscaping service - close this window to stop" cmd /k python -m uvicorn pipeline.web.app:app --host 127.0.0.1 --port 8000
echo Waiting for the service to become ready...
powershell -NoProfile -Command "$ok=$false; for ($i=0; $i -lt 40; $i++) { try { Invoke-WebRequest -Uri 'http://127.0.0.1:8000/docs' -UseBasicParsing -TimeoutSec 1 | Out-Null; $ok=$true; break } catch { Start-Sleep -Milliseconds 500 } }; if (-not $ok) { Write-Host 'Service did not respond in time - check the service window for an error.' }"
start "" http://127.0.0.1:8000
