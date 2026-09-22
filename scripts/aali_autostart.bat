@echo off
rem ============================================================
rem  Aali AutoStart guard - runs at logon via Task Scheduler.
rem  Boots the server ONLY if /api/health is not answering, so
rem  it is safe to fire repeatedly: if Aali is already alive
rem  this exits in about one second.
rem  Guard log : D:\hwk-data\aali_autostart.log
rem  Also ensures the mid-session watchdog is running (step 0).
rem  Remove    : schtasks /Delete /TN "Aali AutoStart" /F
rem ============================================================
setlocal
set "ROOT=%~dp0.."
set "HEALTH_URL=http://127.0.0.1:5055/api/health"
set "GUARDLOG=D:\hwk-data\aali_autostart.log"
if not exist "D:\hwk-data" mkdir "D:\hwk-data"

rem 0) ensure the mid-session watchdog is alive on EVERY guard run
rem    (single-instance via pidlock: a duplicate exits in <1s, harmless;
rem     absolute child paths - relative ones die WinError 2 when the CWD
rem     is not the repo root, e.g. when fired from the Startup folder)
"%ROOT%\.venv\Scripts\python.exe" "%ROOT%\scripts\launch_detached.py" --log aali_server_watchdog -- "%ROOT%\.venv\Scripts\python.exe" "%ROOT%\scripts\aali_server_watchdog.py" >>"%GUARDLOG%" 2>&1

rem 1) alive already? (--fail: any HTTP error also counts as down)
curl -s --fail -m 3 "%HEALTH_URL%" >nul 2>&1
if not errorlevel 1 (
  echo [%date% %time%] alive - nothing to do>>"%GUARDLOG%"
  exit /b 0
)

rem 2) down -> boot detached-ish (minimized) via start_app.bat, which
rem    sets AGENT_WORKSPACE, HWK_ALLOW_COMMANDS and PYTHONIOENCODING.
echo [%date% %time%] down - booting server>>"%GUARDLOG%"
powershell -NoProfile -Command "Start-Process -FilePath '%ROOT%\scripts\start_app.bat' -WorkingDirectory '%ROOT%' -WindowStyle Minimized"

exit /b 0
