@echo off
REM ============================================================
REM  Aali - open the brain to your phone, Mac and Tailscale
REM  Double-click this file. If Windows asks "allow changes",
REM  press YES. Done - no typing needed.
REM
REM  Why: Windows blocks other devices from reaching Aali by
REM  default. This adds TWO rules: your home Wi-Fi, and your
REM  private Tailscale network (works from anywhere).
REM ============================================================

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   Asking Windows for permission (press YES in the popup)...
    REM Relaunch this same file as administrator.
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo   [1/3] Allowing your home Wi-Fi...
netsh advfirewall firewall delete rule name="Aali Brain 5055 LAN" >nul 2>&1
netsh advfirewall firewall add rule name="Aali Brain 5055 LAN" dir=in action=allow protocol=TCP localport=5055 remoteip=localsubnet profile=private,domain

echo   [2/3] Allowing your private Tailscale network (works from anywhere)...
netsh advfirewall firewall delete rule name="Aali Brain 5055 Tailnet" >nul 2>&1
netsh advfirewall firewall add rule name="Aali Brain 5055 Tailnet" dir=in action=allow protocol=TCP localport=5055 remoteip=100.64.0.0/10

echo   [3/3] Checking that Aali is awake...
set "HEALTH="
for /f "usebackq delims=" %%H in (`curl -s -m 5 http://127.0.0.1:5055/api/health`) do set "HEALTH=%%H"

echo.
if defined HEALTH (
    echo   DONE. Aali is awake and open to your devices.
) else (
    echo   Rules added, but Aali is not running yet.
    echo   On the PC, double-click:  scripts\start_app.bat
    echo   then run this file again.
)
echo.
echo   ON YOUR PHONE OR MAC, use this address:
echo     At home:   http://192.168.1.13:5055
echo     Anywhere:  http://100.94.100.57:5055   (needs Tailscale ON)
echo.
echo   Aali asks for a KEY the first time. The key lives on the PC in the
echo   file:  D:\hwk-data\aali_master_key.txt   (open it with Notepad, copy
echo   the single line inside)
echo.
pause
