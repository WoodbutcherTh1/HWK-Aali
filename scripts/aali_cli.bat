@echo off
rem ============================================================
rem  آلي في الترمينال — Claude Code-style client
rem  Double-click me: colorful animated chat with Aali.
rem ============================================================
setlocal
chcp 65001 >nul
title Aali CLI
cd /d "%~dp0.."

rem --- pretty terminal: 120x34, dark background ---
rem (colors the user can change: Properties > Colors)
if not "%AALI_NO_RESIZE%"=="1" (
  mode con: cols=120 lines=34 >nul 2>&1
)
rem Best experience: Windows Terminal (truecolor). Auto-used if present.
where wt.exe >nul 2>&1
if %errorlevel%==0 if not "%AALI_IN_WT%"=="1" (
  set AALI_IN_WT=1
  start "" wt.exe -p "Windows PowerShell" cmd /c "%~f0"
  exit /b
)

set PYTHONIOENCODING=utf-8
set FORCE_COLOR=1
".venv\Scripts\python.exe" scripts\aali_cli.py
pause
