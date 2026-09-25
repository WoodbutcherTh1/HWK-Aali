@echo off
rem Aali Monitor - tray + window watcher for Aali's long jobs.
rem Reads the real D:/hwk-data logs; alerts in Arabic; content-free.
set ROOT=%~dp0..
cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8
if exist "D:\hwk-tools\monitor-venv\Scripts\python.exe" (
  "D:\hwk-tools\monitor-venv\Scripts\python.exe" -m aali_monitor %*
) else (
  ".venv\Scripts\python.exe" -m aali_monitor --headless %*
)
