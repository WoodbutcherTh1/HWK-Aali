@echo off
rem ============================================================
rem  Aali Voice Studio - console + API on :5082
rem  Arabic-first RTL console: voices, script-to-voice, batch.
rem  Runs in the ISOLATED voice venv (XTTS lives there).
rem  Open http://127.0.0.1:5082/ after it starts.
rem  (ASCII + CRLF on purpose: cmd.exe garbles anything else.)
rem ============================================================
setlocal
chcp 65001 >nul
title Aali Voice Studio Console
cd /d "%~dp0.."

set PYTHONIOENCODING=utf-8
set "VOICE_PY=D:\hwk-tools\voice-venv\Scripts\python.exe"
if not exist "%VOICE_PY%" (
  echo voice venv not found at %VOICE_PY% - see tasks\voice-agent-phase1.md
  pause
  exit /b 1
)

echo Studio console: http://127.0.0.1:5082/
"%VOICE_PY%" scripts\voice\studio_api.py %*
pause