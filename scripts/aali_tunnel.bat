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

echo.
echo   ✦ آلي — Internet Access
echo   ─────────────────────────────────────
echo   سينشئ الآن رابطاً عاماً مؤقتاً لخادم آلي المحلي.
echo   أرسل الرابط لأي شخص — يعمل من أي مكان في العالم.
echo   (الرابط صالح ما دامت هذه النافذة مفتوحة)
echo.
"%CF%" tunnel --url http://127.0.0.1:5055 --no-autoupdate
pause
