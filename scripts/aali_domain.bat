@echo off
chcp 65001 >nul
rem ============================================================
rem  آلي — Internet (دائم) : دومين خاص + HTTPS دائم
rem  مرة واحدة فقط، بعدها رابطك ثابت يعمل تلقائياً مع تشغيل
rem  الويندوز (خدمة في الخلفية). يتطلب: نطاقك مضاف في Cloudflare.
rem  الشرح الكامل: docs/custom_domain.md
rem ============================================================
setlocal
title Aali - Permanent Domain
cd /d "%~dp0"

set "CF=%~dp0cloudflared.exe"
if not exist "%CF%" (
  echo   [آلي] تنزيل النفق لمرة واحدة...
  curl -L -o "%CF%" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -s -S
)
if not exist "%CF%" (
  echo   ❌ تعذر تنزيل النفق. تحقق من الاتصال ثم أعد المحاولة.
  pause & exit /b 1
)

set "CFDIR=%USERPROFILE%\.cloudflared"

echo.
echo   ✦ آلي — Internet (دائم)
echo   ─────────────────────────────────────────────
echo   سيعطيك رابطاً مثل:  https://aali.mdomain.com
echo   HTTPS تلقائي، يعمل بعد إعادة التشغيل، مجاناً.
echo.

rem — الخطوة 1: تسجيل الدخول إلى Cloudflare (يفتح المتصفح) —
if not exist "%CFDIR%\cert.pem" (
  echo   [١/٤] سيفتح المتصفح الآن — سجّل دخولك واختر نطاقك ثم اضغط Authorize...
  "%CF%" tunnel login
)
if not exist "%CFDIR%\cert.pem" (
  echo   ❌ لم يكتمل تسجيل الدخول. أعد تشغيل هذا الملف.
  pause & exit /b 1
)
echo   ✅ تم ربط الحساب.

rem — الخطوة 2: اسم النطاق —
set /p HOST=  [٢/٤] اكتب عنوانك الكامل (مثال: aali.mydomain.com) :

rem — الخطوة 3: إنشاء النفق الدائم —
echo   [٣/٤] إنشاء النفق "aali"...
"%CF%" tunnel create aali 2>nul
if errorlevel 1 echo        ^(النفق موجود مسبقاً — سنستخدمه^)

rem — إيجاد ملف بيانات النفق (الأحدث) —
set "CREDS="
for /f "delims=" %%i in ('dir /b /o-d "%CFDIR%\*.json" 2^>nul') do if not defined CREDS set "CREDS=%%i"
if not defined CREDS (
  echo   ❌ لم أجد ملف بيانات النفق — أعد تشغيل السكربت.
  pause & exit /b 1
)
set "UUID=%CREDS:.json=%"

rem — الخطوة 4: ملف الإعداد + سجل DNS —
>  "%CFDIR%\config.yml" echo tunnel: %UUID%
>> "%CFDIR%\config.yml" echo credentials-file: %CFDIR%\%CREDS%
>> "%CFDIR%\config.yml" echo ingress:
>> "%CFDIR%\config.yml" echo   - hostname: %HOST%
>> "%CFDIR%\config.yml" echo     service: http://localhost:5055
>> "%CFDIR%\config.yml" echo   - service: http_status:404

echo   [٤/٤] ربط النطاق %HOST% بالنفق...
"%CF%" tunnel route dns -f aali %HOST%

rem — تثبيت كخدمة ويندوز (يحتاج صلاحيات المسؤول) —
set ADMIN=0
net session >nul 2>&1
if not errorlevel 1 set ADMIN=1

if "%ADMIN%"=="1" (
  echo.
  echo   ✅ تثبيت الخدمة ^(تعمل تلقائياً مع تشغيل الويندوز^)...
  "%CF%" service install
  net start cloudflared >nul 2>&1
  echo.
  echo   🎉 جاهز! رابطك الدائم:  https://%HOST%/ui/
  echo      الخدمة تعمل الآن وستعمل تلقائياً بعد كل إعادة تشغيل.
) else (
  echo.
  echo   ⚠ لتشغيل الرابط ٢٤/٧ كخدمة: انقر يميناً على هذا الملف
  echo     واختر "تشغيل كمسؤول" ثم أعد الخطوات ^(ستتجاوز السريعة^).
  echo.
  echo   ⏳ حالياً: سأشغّل النفق في نافذة مؤقتة...
  start "Aali Tunnel" "%CF%" tunnel run aali
  timeout /t 5 >nul
  start "" "https://%HOST%/ui/"
)

echo.
pause
