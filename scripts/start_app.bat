@echo off
rem Start the Aali chat UI on http://127.0.0.1:5000
rem The agent works on D:\hwk-projects (change AGENT_WORKSPACE to any folder
rem you want it to build/edit in). File changes stay inside that folder;
rem allow-listed build/test commands may run there. Set HWK_ALLOW_COMMANDS=0
rem to disable command execution entirely.
set ROOT=%~dp0..
cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8
if not exist "D:\hwk-projects" mkdir "D:\hwk-projects"
set AGENT_WORKSPACE=D:\hwk-projects
set HWK_ALLOW_COMMANDS=1

".venv\Scripts\python.exe" file-agent\app.py
pause
