@echo off
REM آلي — start everything: brain server (+ n8n if present)
REM Web UI:      http://127.0.0.1:5055
REM API:         POST http://127.0.0.1:5055/api/ask  {"message": "..."}
REM n8n editor:  http://127.0.0.1:5678
setlocal
cd /d "%~dp0.."

echo [آلي] starting brain server on port 5055 ...
set PORT=5055
set HWK_ALLOW_COMMANDS=1
set PYTHONIOENCODING=utf-8
set PYTHONPATH=file-agent
start "Aali Server" /min .venv\Scripts\python.exe file-agent\app.py

timeout /t 3 /nobreak >nul

where node >nul 2>nul
if %errorlevel%==0 (
  netstat -ano | findstr /r /c":5678 .*LISTENING" >nul 2>nul
  if %errorlevel%==0 (
    echo [آلي] n8n already running on 5678 - skipping
  ) else (
    echo [آلي] starting n8n on port 5678 ...
    start "n8n" /min cmd /c "set PATH=D:\hwk-tools\node-v24.20.0-win-x64;%PATH% && n8n start --host 127.0.0.1 --port 5678"
  )
) else (
  echo [آلي] node not found - skipping n8n
)

timeout /t 2 /nobreak >nul
rem When the desktop app auto-boots us (AALI_NO_BROWSER=1) it opens the UI
rem itself — a second browser tab would just duplicate the window.
if not defined AALI_NO_BROWSER start "" http://127.0.0.1:5055
echo [آلي] ready: web UI + API on 5055. Desktop shell: .venv\Scripts\python desktop_app.py
endlocal
