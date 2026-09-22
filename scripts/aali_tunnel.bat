@echo off
chcp 65001 >nul
rem ============================================================
rem    Internet  (     )
rem  Uses a free Cloudflare quick-tunnel: no router setup,
rem  no ports, no account. Works while this window stays open.
rem  Optional: put cloudflared.exe next to this script, or it
rem  downloads it automatically on first run.
rem ============================================================
setlocal
title Aali Internet Access
cd /d "%~dp0"

set "CF=%~dp0cloudflared.exe"
if not exist "%CF%" (
  echo   []     ...
  curl -L -o "%CF%" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -s -S
)

if not exist "%CF%" (
  echo      .      .
  pause
  exit /b 1
)

rem ---- safety gate: never expose an UNauthenticated brain to the world ----
set "KEYFILE=D:\hwk-data\aali_master_key.txt"
if not exist "%KEYFILE%" (
  echo       .  scripts\aali_share.bat
  echo         .     =
  echo             .
  pause
  exit /b 1
)
set /p MASTER=<"%KEYFILE%"
curl -s -m 3 -H "X-API-Key: %MASTER%" http://127.0.0.1:5055/api/health | findstr /c:"ok" >nul
if errorlevel 1 (
  echo        .  scripts\aali_share.bat
  echo      (    multi-user    )   .
  pause
  exit /b 1
)

echo.
echo      Internet Access
echo
echo          .
rem   SAFE: the server verified above requires X-API-Key, and non-admin keys
rem   arriving over the tunnel are force-gated to the guest policy
rem   (no run_command / delete / machine_ops) in file-agent/app.py.
echo            /signup  /admin.
echo   (      )
echo.
"%CF%" tunnel --url http://127.0.0.1:5055 --no-autoupdate
pause
