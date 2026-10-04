@echo off
REM ---------------------------------------------------------------------------
REM Voice server for Jarvis: XTTS-v2 voice-clone TTS on :5082, LAN reachable.
REM
REM WHY THIS FILE EXISTS: Jarvis runs on the Pi, which has 905 MB of RAM and no
REM torch, so it cannot host XTTS. The PC can. This puts the PC voice server on
REM the LAN behind a shared-secret token so the Pi can render replies in the
REM owner's cloned voice (arabic_male).
REM
REM The model takes ~140s to load on CPU, so this MUST stay warm: a cold start
REM would make the first reply wait over two minutes.
REM
REM Token: D:\hwk-data\jarvis_secrets\voice_token.txt (never in the repo). It is
REM read into the environment here and NEVER echoed. Do not start
REM studio_api.py by hand for the LAN case: without this token the endpoint
REM would let any device on the WiFi speak in the owner's voice.
REM
REM Loopback unless AALI_VOICE_BIND is set. This file sets it deliberately,
REM because the whole point is to let the Pi reach it.
REM ---------------------------------------------------------------------------
setlocal
set "AALI_VOICE_BIND=1"
set "VOICE_TOKEN_FILE=D:\hwk-data\jarvis_secrets\voice_token.txt"

if not exist "%VOICE_TOKEN_FILE%" (
  echo [voice] token file missing: %VOICE_TOKEN_FILE%
  exit /b 2
)

REM read the token into the env without echoing it
set /p AALI_VOICE_TOKEN=<"%VOICE_TOKEN_FILE%"

cd /d "%~dp0.."
"D:\hwk-tools\voice-venv\Scripts\python.exe" scripts\voice\studio_api.py --port 5082
endlocal