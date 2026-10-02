@echo off
rem Start the Aali server: chat UI on http://127.0.0.1:5055/ui/
rem Admin dashboard: http://127.0.0.1:5055/admin   Signup: /signup
rem The agent works on D:\hwk-projects (change AGENT_WORKSPACE to any folder
rem you want it to build/edit in). File changes stay inside that folder;
rem allow-listed build/test commands may run there. Set HWK_ALLOW_COMMANDS=0
rem to disable command execution entirely.
rem
rem 2026-10-02 (brain answering "direct-command mode" on every ask):
rem   The server used to inherit its whole runtime config from WHATEVER shell
rem   happened to launch it, and a 2026-09-14 debug shell left
rem   AALI_OWN_MODEL=0 + a stale AALI_REMOTE_BRAIN_URL in that chain. The
rem   consequence was silent and total: promoted_own_model() returned None, so
rem   the LIVE promoted brain on :20129 (checkpoint-3933) was skipped; Ollama
rem   was gone (its models were deleted on 2026-10-01); model/scratch/final.pt
rem   does not exist -> _local_agent_loop's deterministic command mode, i.e.
rem   that card, for EVERY ask. The config is now restored HERE, from files on
rem   disk, so it can never depend on a stale shell again.
set ROOT=%~dp0..
cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8
if not exist "D:\hwk-projects" mkdir "D:\hwk-projects"
set AGENT_WORKSPACE=D:\hwk-projects
set HWK_ALLOW_COMMANDS=1

rem --- the owner's own brain: serve the promoted adapter on :20129 ---------
rem (AALI_OWN_MODEL=0 is a debugging kill-switch; 1 is the shipped default)
set AALI_OWN_MODEL=1
rem --- remote brain OFF ---------------------------------------------------
rem It means ANOTHER Aali being this machine's brain (the Pi setup). The stale
rem value pointed at a host that is gone; and after the 2026-09-27 tunnel
rem cut-over a URL that resolves back to THIS box would make the server ask
rem itself. Unset it here instead of trusting the inherited value.
set AALI_REMOTE_BRAIN_URL=
set AALI_REMOTE_BRAIN=0

rem --- key mode (multi-user: the tunnel and friends need it) --------------
rem Read from the master-key file WITHOUT echoing it (set /p prints nothing).
rem With a key present app.py binds 0.0.0.0; without it, 127.0.0.1 only.
set "KEYFILE=D:\hwk-data\aali_master_key.txt"
if exist "%KEYFILE%" set /p AALI_API_KEY=<"%KEYFILE%"

".venv\Scripts\python.exe" file-agent\app.py
pause