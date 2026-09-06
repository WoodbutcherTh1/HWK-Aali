@echo off
rem Watches Aali's full pipeline and runs each stage automatically as the
rem previous one finishes: Phase A (running already) -> context extension
rem to 4096 tokens -> tool-calling/instruction SFT. Run this in its own
rem window, alongside resume_training.bat. It only reads log files, so it
rem never competes for the GPU.
set ROOT=%~dp0..
cd /d "%ROOT%"
".venv\Scripts\python.exe" scripts\queue_sft.py
pause
