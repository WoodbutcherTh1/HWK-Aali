@echo off
chcp 65001 >nul
rem ============================================================
rem  آلي — Desktop  (نافذة تطبيق مستقلة بدون تثبيت)
rem  Download this ONE file, double-click it, and Aali opens in
rem  its own window (Chrome/Edge app mode) — like a native app.
rem  First run asks for Aali's address; it is remembered.
rem ============================================================
setlocal EnableDelayedExpansion
title Aali Desktop

set "CFG=%APPDATA%\AaliDesktop\url.txt"
if exist "%CFG%" set /p LAST_URL=<"%CFG%"

if "%~1"=="" (
  if defined LAST_URL (
    set "AALI_URL=!LAST_URL!"
  ) else (
    echo.
    echo   ✦ آلي — Desktop
    echo   ─────────────────────────────
    echo   أول تشغيل فقط: الصق رابط خادم آلي
    echo   (مثال: http://192.168.1.10:5055 أو رابط from the owner)
    echo.
    set /p AALI_URL=  الرابط :
  )
) else (
  set "AALI_URL=%~1"
)

set "AALI_URL=!AALI_URL:/=!"
if not exist "%APPDATA%\AaliDesktop" mkdir "%APPDATA%\AaliDesktop"
echo !AALI_URL!>"%CFG%"

set "BROWSER="
for %%E in (
  "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
  "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
  "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
  "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
) do (
  if exist %%E if not defined BROWSER set "BROWSER=%%~E"
)

if defined BROWSER (
  start "" "!BROWSER!" --app="!AALI_URL!/ui/" --window-size=1180,840
) else (
  start "" "!AALI_URL!/ui/"
)
exit
