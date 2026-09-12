@echo off
rem Mission clock - a colorful watch-party dashboard for Aali's long jobs.
rem Live clock, per-process cards, progress bars and countdown ETAs,
rem all parsed from the real D:/hwk-data logs. Fun to stare at.
set ROOT=%~dp0..
cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" "%~dp0mission_clock.py" %*
