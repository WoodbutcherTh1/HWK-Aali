@echo off
rem ============================================================
rem  Launch Aali Studio (the desktop IDE).
rem  Builds nothing: it starts the backend on :5070 and opens
rem  the pywebview window (falls back to the browser).
rem  (ASCII + CRLF on purpose: tests/test_bat_hygiene.py checks this)
rem ============================================================
setlocal
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8

if exist build-desktop\dist\Aali-Studio.exe (
  start "" build-desktop\dist\Aali-Studio.exe %*
  exit /b 0
)

if not exist .venv-studio (
  echo [studio] creating the isolated venv...
  .venv\Scripts\python.exe -m venv .venv-studio
  .venv-studio\Scripts\python.exe -m pip install flask pywebview
)

.venv-studio\Scripts\python.exe build-desktop\aali-studio\studio_app.py %*
