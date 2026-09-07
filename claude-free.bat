@echo off
rem ============================================================
rem  claude-free.bat - Claude Code routed through the local
rem  OmniRoute gateway (localhost:20128), which serves free-tier
rem  providers (GLM, DeepSeek, GPT-OSS, Groq, ...). Use this when
rem  your Claude subscription quota runs out - or any time.
rem
rem  Your normal `claude` command and subscription login are NOT
rem  touched: they keep working exactly as before.
rem ============================================================
setlocal
set "PATH=C:\Program Files\nodejs;%APPDATA%\npm;%PATH%"

rem Make sure the OmniRoute gateway is running (starts it hidden if not)
netstat -ano | findstr ":20128" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
  echo Starting OmniRoute gateway on localhost:20128 ...
  powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath 'C:\Users\HmamK\AppData\Roaming\npm\omniroute.cmd' -WorkingDirectory 'C:\Users\HmamK' -RedirectStandardOutput 'D:\hwk-data\omniroute.log' -RedirectStandardError 'D:\hwk-data\omniroute.err.log'"
  timeout /t 12 /nobreak >nul
)

rem Route Claude Code through the gateway
set "ANTHROPIC_BASE_URL=http://127.0.0.1:20128"
set "ANTHROPIC_AUTH_TOKEN=local-omniroute"
set "ANTHROPIC_MODEL=auto"

echo Claude Code is running through OmniRoute (free tiers). Model: %ANTHROPIC_MODEL%
"%APPDATA%\npm\claude.cmd" %*
endlocal
