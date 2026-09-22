@echo off
chcp 65001 >nul
rem ============================================================
rem    Connect  (     )
rem  Send this ONE file to a friend  no install, no Python.
rem  It just opens Aali's web app in their browser and saves
rem  their key locally in that browser.
rem ============================================================
setlocal
title Aali Connect

echo.
echo      Connect
echo
echo.

if "%~1"=="" (
  set /p AALI_URL=        (: https://xxx.trycloudflare.com) :
) else (
  set "AALI_URL=%~1"
)

set "AALI_URL=%AALI_URL:/=%"
echo.
echo     ...
start "" "%AALI_URL%/signup"
timeout /t 2 >nul
start "" "%AALI_URL%/ui/"
echo.
echo    !        .
echo      (    )
echo.
timeout /t 6 >nul
endlocal
