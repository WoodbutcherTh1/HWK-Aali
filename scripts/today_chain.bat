@echo off
rem Morning chain (2026-09-09): graduation pipeline -> Phase B resume.
rem Launch minimized; logs: D:\hwk-data\morning_chain.log
set ROOT=%~dp0..
cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" scripts\today_chain.py
pause
