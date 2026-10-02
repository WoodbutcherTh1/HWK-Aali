@echo off
rem ============================================================
rem  Aali Voice Studio - manage voices, audition, batch scripts.
rem  Runs in the ISOLATED voice venv (XTTS lives there).
rem  Usage: scripts\voice_studio.bat voices
rem         scripts\voice_studio.bat voices check clip.wav
rem         scripts\voice_studio.bat voices add NAME --lang ar --file clip.wav
rem         scripts\voice_studio.bat audition --lang he
rem         scripts\voice_studio.bat batch my_script.txt
rem  (ASCII + CRLF on purpose: cmd.exe garbles anything else.)
rem ============================================================
setlocal
chcp 65001 >nul
title Aali Voice Studio
cd /d "%~dp0.."

set PYTHONIOENCODING=utf-8
set "VOICE_PY=D:\hwk-tools\voice-venv\Scripts\python.exe"
if not exist "%VOICE_PY%" (
  echo voice venv not found at %VOICE_PY% - see tasks\voice-agent-phase1.md
  pause
  exit /b 1
)

"%VOICE_PY%" scripts\voice\studio.py %*
pause