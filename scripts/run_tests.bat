@echo off
rem Run the HWK test suite (model, tokenizers, tools, agent) with pytest.
set ROOT=%~dp0..
cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8
set PYTHONPATH=file-agent
".venv\Scripts\python.exe" -m pytest tests -q
pause
