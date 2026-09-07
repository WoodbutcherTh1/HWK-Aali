@echo off
rem ============================================================
rem  after_phaseA.bat - run AFTER Phase A pretraining finishes.
rem  Waits until D:\hwk-data\training.log has been idle for 15+
rem  minutes (training ended), then starts the 1024 -> 4096
rem  context extension via extend_context.bat (which trains on a
rem  COPY in D:\hwk-models\context-4k - Phase A files are never
rem  touched). Launch this once, minimized; close the window to
rem  cancel before it fires.
rem ============================================================
set LOG=D:\hwk-data\training.log
echo Watching for Phase A to finish (checking every 5 min)...
:wait
timeout /t 300 /nobreak >nul
powershell -NoProfile -Command "if (((Get-Date) - (Get-Item '%LOG%').LastWriteTime).TotalMinutes -lt 15) { exit 1 }"
if errorlevel 1 goto wait
echo.
echo Phase A appears finished (log idle 15+ minutes at %date% %time%).
echo Starting context extension to 4096 tokens on a COPY...
call "%~dp0extend_context.bat"
