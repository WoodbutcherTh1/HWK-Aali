@echo off
REM ---------------------------------------------------------------------------
REM PC control agent for Jarvis: :5083, LAN reachable, token-gated.
REM
REM The hands behind Telegram Jarvis' "resources / logs / power" capabilities.
REM Jarvis runs on the Pi and calls this over plain HTTP with a shared token -
REM the same transport as the brain (:5055) and the voice server (:5082).
REM
REM Token: D:\hwk-data\jarvis_secrets\pc_agent_token.txt (never in the repo).
REM It is read into the environment here and NEVER echoed. Do not start
REM pc_agent.py by hand for the LAN case: without the token the agent refuses
REM to start (fail closed) - an unauthenticated control endpoint on the WiFi
REM would let any device read logs or power-cycle this PC.
REM
REM Loopback unless AALI_AGENT_BIND is set. This file sets it deliberately,
REM because the whole point is to let the Pi reach it.
REM ---------------------------------------------------------------------------
setlocal
set "AALI_AGENT_BIND=1"
set "AGENT_TOKEN_FILE=D:\hwk-data\jarvis_secrets\pc_agent_token.txt"

if not exist "%AGENT_TOKEN_FILE%" (
  echo [pc_agent] token file missing: %AGENT_TOKEN_FILE%
  echo [pc_agent] generate one with:  python -c "import secrets;open(r'%AGENT_TOKEN_FILE%','w').write(secrets.token_hex(24))"
  exit /b 2
)

REM read the token into the env without echoing it
set /p AALI_AGENT_TOKEN=<"%AGENT_TOKEN_FILE%"

cd /d "%~dp0.."
".venv\Scripts\python.exe" scripts\pc_agent.py --port 5083
endlocal
