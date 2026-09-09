@echo off
chcp 65001 >nul
rem ============================================================
rem  آلي — Share  (شارك آلي مع أصدقائك على شبكتك المحلية)
rem  Owner-only: turns this PC into the Aali server for friends.
rem  Does three things, safely:
rem    1. Generates (once) a master key -> D:\hwk-data\aali_master_key.txt
rem    2. Adds a firewall rule for port 5055 LIMITED TO THE LAN
rem       (localsubnet only — the internet cannot see it)
rem    3. Restarts Aali in multi-user mode (key required) so every
rem       friend needs their own key (issued via /admin or /signup),
rem       and remote guests are forced to the server-enforced guest
rem       policy: NO commands, NO deletes, NO machine control.
rem  Friends connect to:  http://192.168.1.x:5055/signup
rem ============================================================
setlocal
cd /d "%~dp0.."
title Aali Share (LAN)

set "KEYFILE=D:\hwk-data\aali_master_key.txt"
if not exist "D:\hwk-data" mkdir "D:\hwk-data"

if exist "%KEYFILE%" (
  set /p MASTER=<"%KEYFILE%"
) else (
  .venv\Scripts\python.exe -c "import secrets; open(r'%KEYFILE%','w').write('aali-' + secrets.token_urlsafe(24))" >nul
  set /p MASTER=<"%KEYFILE%"
  echo   [آلي] master key generated -^> %KEYFILE%
)

echo   [آلي] firewall rule (LAN-only, port 5055)...
netsh advfirewall firewall show rule name="Aali Server (LAN)" >nul 2>nul
if errorlevel 1 (
  netsh advfirewall firewall add rule name="Aali Server (LAN)" dir=in action=allow protocol=TCP localport=5055 remoteip=localsubnet profile=private >nul
  if errorlevel 1 (
    echo   ❌ تعذّر إضافة قاعدة الجدار الناري - شغّل هذا الملف "كمسؤول" ثم أعد المحاولة.
    pause
    exit /b 1
  )
)

echo   [آلي] restarting Aali in multi-user mode (key required)...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c":5055 .*LISTENING"') do taskkill /PID %%P /F >nul 2>nul
timeout /t 2 /nobreak >nul

set PORT=5055
set AALI_API_KEY=%MASTER%
set AALI_BIND=0.0.0.0
set AALI_NO_BROWSER=1
set PYTHONIOENCODING=utf-8
set PYTHONPATH=file-agent
set AGENT_WORKSPACE=D:\hwk-projects
set HWK_ALLOW_COMMANDS=0
start "Aali Server (shared)" /min .venv\Scripts\python.exe file-agent\app.py

ipconfig | findstr /c:"IPv4" 
timeout /t 3 /nobreak >nul
echo.
echo   ✅ آلي يعمل الآن كخادم على شبكتك المحلية.
echo      - عنوانك لأصدقائك:  http://LOCAL-IP-ABOVE:5055/signup
echo      - أنشئ مفاتيح أصدقائك من /admin (مفتاح المدير في %KEYFILE%)
echo      - الضيوف عن بُعد: بدون أوامر نظام ولا حذف — قراءة وكتابة ووسائط فقط.
echo      - أوقف المشاركة: أغلق نافذة "Aali Server (shared)" وشغّل start_all.bat
echo.
pause
endlocal
