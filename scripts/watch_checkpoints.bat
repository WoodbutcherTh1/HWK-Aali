@echo off
rem Archive every Phase A checkpoint save to D:/hwk-models/scratch-backups/step_<N>
rem as soon as it lands. Keeps the newest 12 snapshots; exits when the run
rem finishes or after 1h of log silence. Close this window to stop it.
set ROOT=%~dp0..
cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" scripts\checkpoint_watchdog.py --keep 12
pause
