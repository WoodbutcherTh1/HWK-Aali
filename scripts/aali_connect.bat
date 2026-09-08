@echo off
chcp 65001 >nul
rem ============================================================
rem  آلي — Connect  (اتصل بخادم آلي من أي جهاز)
rem  Send this ONE file to a friend — no install, no Python.
rem  It just opens Aali's web app in their browser and saves
rem  their key locally in that browser.
rem ============================================================
setlocal
title Aali Connect

echo.
echo   ✦ آلي — Connect
echo   ─────────────────────────────
echo.

if "%~1"=="" (
  set /p AALI_URL=  الصق رابط آلي الذي أرسله لك (مثال: https://xxx.trycloudflare.com) :
) else (
  set "AALI_URL=%~1"
)

set "AALI_URL=%AALI_URL:/=%"
echo.
echo   جاري فتح آلي...
start "" "%AALI_URL%/signup"
timeout /t 2 >nul
start "" "%AALI_URL%/ui/"
echo.
echo   ✅ تم! أنشئ مفتاحك من الصفحة الأولى ثم ادخل المحادثة.
echo      (المفتاح يُحفظ في متصفحك تلقائيًا)
echo.
timeout /t 6 >nul
endlocal
