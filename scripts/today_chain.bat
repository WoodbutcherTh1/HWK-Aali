@echo off
rem Morning chain (2026-09-09): graduation pipeline -> Phase B resume.
rem Detached launch (2026-09-12): the chain owns hours-long GPU stages and
rem must survive this window closing (the 12:20 console event killed a run).
rem The chain's own progress goes to D:\hwk-data\morning_chain.log; the
rem console copy (stdout) lands in morning_chain_console.log.
set ROOT=%~dp0..
cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" scripts\launch_detached.py --log morning_chain_console -- ".venv\Scripts\python.exe" scripts\today_chain.py
