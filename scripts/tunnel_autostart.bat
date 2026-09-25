@echo off
rem ============================================================
rem  Aali tunnel autostart - boots the Cloudflare named tunnel
rem  'aali' (https://aali.dpdns.org) at logon via Task Scheduler
rem  task "HWK TunnelAutoStart". Safe to fire repeatedly: if a
rem  cloudflared process is already running it exits in ~1s.
rem
rem  LOG OWNERSHIP (2026-09-25 lessons, both hit live):
rem   - tunnel.log          : cloudflared child ONLY. The running
rem                           child holds the file open, so guard
rem                           echos redirected there fail with
rem                           "being used by another process" and
rem                           are silently lost.
rem   - tunnel_autostart.log: guard decisions + the launcher's
rem                           own pid/error line. It must never
rem                           write tunnel.log either: cmd's
rem                           redirect + launch_detached's append
rem                           on the same file = Errno 13 spawn
rem                           failure (boot was lost once).
rem
rem  One-time setup stays MANUAL on purpose: `cloudflared tunnel
rem  login` opens a browser (Authorize), then scripts\aali_domain.bat
rem  writes config.yml + routes DNS. This guard only RUNS it.
rem  Test anytime : schtasks /Run /TN "HWK TunnelAutoStart"
rem  Register (unelevated PowerShell worked on 2026-09-25):
rem    Register-ScheduledTask -TaskName 'HWK TunnelAutoStart' -Action
rem      (New-ScheduledTaskAction -Execute '<repo>\scripts\tunnel_autostart.bat')
rem      -Trigger (New-ScheduledTaskTrigger -AtLogOn -User '<you>')
rem  Remove      : schtasks /Delete /TN "HWK TunnelAutoStart" /F
rem ============================================================
setlocal
set "ROOT=%~dp0.."
set "CF=%ROOT%\scripts\cloudflared.exe"
set "LOG=D:\hwk-data\tunnel_autostart.log"
set "CFG=%USERPROFILE%\.cloudflared\config.yml"
set "CERT=%USERPROFILE%\.cloudflared\cert.pem"
if not exist "D:\hwk-data" mkdir "D:\hwk-data"

rem 1) missing binary or one-time setup -> log and leave it to the owner
if not exist "%CF%" (
  echo [%date% %time%] cloudflared.exe missing - run scripts\aali_domain.bat once>>"%LOG%"
  exit /b 1
)
if not exist "%CERT%" (
  echo [%date% %time%] no cloudflared cert.pem - run scripts\aali_domain.bat once>>"%LOG%"
  exit /b 1
)
if not exist "%CFG%" (
  echo [%date% %time%] no config.yml - run scripts\aali_domain.bat once>>"%LOG%"
  exit /b 1
)

rem 2) already running? Process check: no edge round-trip, and a DOWN
rem    SERVER must never look like a down TUNNEL (server has its own
rem    autostart guard in aali_autostart.bat).
tasklist /FI "IMAGENAME eq cloudflared.exe" /NH 2>nul | findstr /I "cloudflared" >nul
if not errorlevel 1 (
  echo [%date% %time%] tunnel already running - nothing to do>>"%LOG%"
  exit /b 0
)

rem 3) down -> boot fully detached. launch_detached resolves the exe to
rem    an absolute path, appends the child's output to D:\hwk-data\tunnel.log,
rem    and spawns with CREATE_BREAKAWAY_FROM_JOB so the Task Scheduler's
rem    job object cannot sweep the tunnel away when this .bat exits
rem    (the 2026-09-12 attempt-8 lesson).
echo [%date% %time%] tunnel down - booting cloudflared (tunnel: aali)>>"%LOG%"
"%ROOT%\.venv\Scripts\python.exe" "%ROOT%\scripts\launch_detached.py" --log tunnel -- "%CF%" tunnel run aali >>"%LOG%" 2>&1

exit /b 0
