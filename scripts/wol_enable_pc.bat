@echo off
rem ============================================================
rem  Aali Wake-on-LAN arming for the home PC - RUN AS ADMIN.
rem
rem  One-time setup that lets the Raspberry Pi wake this PC with
rem  a magic packet (Pi sentinel design, 2026-09-27):
rem    1) NIC: Wake on Magic Packet = Enabled   (already on here,
rem       verified via Get-NetAdapterAdvancedProperty - re-arming
rem       is idempotent and survives driver updates)
rem    2) Firewall: allow inbound UDP 9 from the LAN only, so the
rem       wake packet reaches the NIC stack in every power state
rem    3) powercfg /a reminder: S3 must be listed as available
rem
rem  OPTIONAL sleep-on-idle (opt-in, NOT done by this script):
rem    review D:\hwk-data\sleep_guard.log from
rem      .venv\Scripts\python.exe scripts\idle_sleep_guard.py --loop
rem    for a few days; when happy, set an idle timeout, e.g.
rem      powercfg /change standby-timeout-ac 15     (15 minutes)
rem    The guard's contract: it only CHECKS; the power plan sleeps.
rem ============================================================
setlocal
set "IFACE=Ethernet"
set "LAN=192.168.1.0/24"

net session >nul 2>&1
if errorlevel 1 (
  echo [X] Not elevated. Right-click this .bat ^> Run as administrator.
  exit /b 1
)

echo [1/3] Arming Wake on Magic Packet on "%IFACE%" ...
powershell -NoProfile -Command "Set-NetAdapterPowerManagement -Name '%IFACE%' -WakeOnMagicPacket Enabled -WakeOnPattern Enabled" 2>nul
if errorlevel 1 echo     (warning: cmdlet refused on this driver - verify manually with Get-NetAdapterAdvancedProperty)

echo [2/3] Firewall rule for inbound UDP 9 (wake port) from the LAN ...
netsh advfirewall firewall delete rule name="Aali WoL UDP9 in" >nul 2>&1
netsh advfirewall firewall add rule name="Aali WoL UDP9 in" dir=in action=allow protocol=UDP localport=9 remoteip=%LAN% profile=private >nul
if errorlevel 1 (
  echo [X] netsh failed - firewall rule not added.
  exit /b 1
)

echo [3/3] Sleep states (S3 must be in the AVAILABLE list) ...
powercfg /a

echo.
echo Done - the Pi can now wake this PC:
echo   MAC F4:B5:20-46-44-27 on %IFACE%, wake port 9, LAN-only rule.
echo The PC may now be put to sleep manually anytime; the Pi sentinel
echo will wake it on incoming requests to aali.dpdns.org.
exit /b 0
