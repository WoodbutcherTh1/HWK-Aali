@echo off
chcp 65001 >nul
rem ============================================================
rem  آلي — Internet  (افتح آلي للإنترنت برابط عام مؤقت)
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
  echo   [آلي] جاري تنزيل النفق لمرة واحدة...
  curl -L -o "%CF%" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -s -S
)

if not exist "%CF%" (
  echo   ❌ تعذر تنزيل النفق. تحقق من الاتصال ثم أعد المحاولة.
  pause
  exit /b 1
)

rem ---- safety gate: never expose an UNauthenticated brain to the world ----
set "KEYFILE=D:\hwk-data\aali_master_key.txt"
if not exist "%KEYFILE%" (
  echo   ❌ لا يوجد مفتاح مدير. شغّل scripts\aali_share.bat أولاً لإنشائه،
  echo      ثم أعد هذه النافذة. رابط عام بلا مفاتيح = أي شخص في العالم
  echo      يتحدث مع آلي الذي يشغّل جهازك — مرفوض.
  pause
  exit /b 1
)
set /p MASTER=<"%KEYFILE%"
curl -s -m 3 -H "X-API-Key: %MASTER%" http://127.0.0.1:5055/api/health | findstr /c:"ok" >nul
if errorlevel 1 (
  echo   ❌ الخادم لا يعمل بوضع المفاتيح. شغّل scripts\aali_share.bat أولاً
  echo      (يعيد تشغيل آلي بوضع multi-user مع جدار ناري محلي)، ثم أعد المحاولة.
  pause
  exit /b 1
)

echo.
echo   ✦ آلي — Internet Access
echo   ─────────────────────────────────────
echo   سينشئ الآن رابطاً عاماً مؤقتاً لخادم آلي المحلي.
rem   SAFE: the server verified above requires X-API-Key, and non-admin keys
rem   arriving over the tunnel are force-gated to the guest policy
rem   (no run_command / delete / machine_ops) in file-agent/app.py.
echo   أرسل الرابط لأصدقائك — يحتاج كل منهم مفتاحاً من /signup أو /admin.
echo   (الرابط صالح ما دامت هذه النافذة مفتوحة)
echo.
"%CF%" tunnel --url http://127.0.0.1:5055 --no-autoupdate
pause
