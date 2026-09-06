@echo off
rem Watches training.log and auto-launches Stage B SFT (sft_training.bat)
rem once Phase A pretraining reaches its target step or stops updating.
rem Run this in its own window, alongside resume_training.bat. It only
rem reads the log file, so it never competes for the GPU.
set ROOT=%~dp0..
cd /d "%ROOT%"
".venv\Scripts\python.exe" scripts\queue_sft.py
pause
