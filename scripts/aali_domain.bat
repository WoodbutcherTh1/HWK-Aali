@echo off
chcp 65001 >nul
rem ============================================================
rem  آلي — Domain  (اربط آلي باسم نطاقك الثابت aali.dpdns.org)
rem  Uses a Cloudflare NAMED tunnel so the public address is your
rem  own domain, HTTPS automatic, no router ports opened, and
rem  your PC's IP stays hidden behind Cloudflare.
rem
rem  BEFORE running this, do these two browser steps ONCE:
rem    1. dash.cloudflare.com -> Add a site -> aali.dpdns.org (Free)
rem       -> note the TWO nameservers Cloudflare assigns.
rem    2. dashboard.digitalplat.org -> your domain page ->
rem       Delegation mode = external nameservers ->
rem       paste Cloudflare's two nameservers -> save.
rem    (Wait for DNS propagation — check https://dnschecker.org
rem     until the NS answer shows Cloudflare's nameservers.)
rem
rem  First run will open a browser so you can log in to your
rem  Cloudflare account and click Authorize (once).
rem ============================================================
setlocal
title Aali Domain (aali.dpdns.org)
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
  echo      ثم أعد هذه النافذة. نطاق عام بلا مفاتيح = أي شخص في العالم
  echo      يتحدث مع آلي الذي يشغّل جهازك — مرفوض.
  pause
  exit /b 1
)
set /p MASTER=<"%KEYFILE%"
curl -s -m 3 -H "X-API-Key: %MASTER%" http://127.0.0.1:5055/api/health | findstr /c:"ok" >nul
if errorlevel 1 (
  echo   ❌ الخادم لا يعمل بوضع المفاتيح. شغّل scripts\aali_share.bat أولاً
  echo      (يعيد تشغيل آلي بوضع multi-user)، ثم أعد المحاولة.
  pause
  exit /b 1
)

set "TUNNEL=aali"
set "HOSTNAME=aali.dpdns.org"

rem ---- one-time Cloudflare login (opens browser for you to Authorize) ----
if not exist "%USERPROFILE%\.cloudflared\cert.pem" (
  echo.
  echo   ✦ أول تشغيل: ستفتح نافذة متصفح لتسجيل دخول Cloudflare.
  echo     سجّل بحسابك، ثم اضغط Authorize — ثم ارجع لهذه النافذة.
  echo.
  "%CF%" tunnel login
  if errorlevel 1 (
    echo   ❌ فشل تسجيل الدخول إلى Cloudflare.
    pause
    exit /b 1
  )
)

rem ---- create the named tunnel once ----
"%CF%" tunnel info %TUNNEL% >nul 2>nul
if errorlevel 1 (
  echo   [آلي] إنشاء النفق %TUNNEL% ...
  "%CF%" tunnel create %TUNNEL%
  if errorlevel 1 (
    echo   ❌ تعذّر إنشاء النفق.
    pause
    exit /b 1
  )
)

rem ---- find the tunnel credentials file (named by UUID, not by name) ----
set "CRED="
for /f "tokens=3" %%T in ('"%CF%" tunnel list --output json ^| findstr /c:"%TUNNEL%"') do set "TID=%%T"
if not defined TID (
  echo   ❌ لم أتعرف على معرّف النفق %TUNNEL% — أعد تشغيل النص.
  pause
  exit /b 1
)
set "CRED=%USERPROFILE%\.cloudflared\%TID%.json"
if not exist "%CRED%" (
  echo   ❌ ملف بيانات النفق غير موجود: %CRED%
  pause
  exit /b 1
)

rem ---- write the config (hostname -> local Aali) ----
set "CFG=%USERPROFILE%\.cloudflared\config.yml"
(
  echo tunnel: %TUNNEL%
  echo credentials-file: %CRED%
  echo.
  echo ingress:
  echo   - hostname: %HOSTNAME%
  echo     service: http://127.0.0.1:5055
  echo   - service: http_status:404
) > "%CFG%"

rem ---- point the DNS record at the tunnel (CNAME) ----
echo   [آلي] ربط %HOSTNAME% بالنفق...
"%CF%" tunnel route dns %TUNNEL% %HOSTNAME%
if errorlevel 1 (
  echo   ⚠️  تعذّر إنشاء سجل DNS تلقائياً — تأكد أن النطاق أصبح
  echo      على Cloudflare (انتشرت الـ nameservers) ثم أعد التشغيل.
  pause
  exit /b 1
)

echo.
echo   ✦ آلي — Domain
echo   ─────────────────────────────────────
echo   العنوان الثابت:  https://%HOSTNAME%
echo   - HTTPS تلقائي عبر Cloudflare، بدون فتح منافذ بالراوتر.
echo   - الخادم يبقى على 127.0.0.1 وغير مكشوف للإنترنت مباشرة.
echo   - المفاتيح مطلوبة: أصدقاؤك من /signup أو /admin.
echo   - الضيوف عن بُعد مجبرون على سياسة الضيف (بدون أوامر/حذف).
echo   - أغلق هذه النافذة لإيقاف النطاق (النفق يعمل ما دامت مفتوحة).
echo.
"%CF%" tunnel run %TUNNEL%
pause