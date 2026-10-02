@echo off
rem ============================================================
rem  Install the Aali Reach reading venv (OWN venv, never .venv).
rem
rem  Playwright pulls in a browser, and AGENTS.md forbids heavy
rem  deps in the training venv - so it gets its own home:
rem      D:\hwk-tools\reach-venv   (override with AALI_REACH_VENV)
rem
rem  (ASCII + CRLF on purpose: tests/test_bat_hygiene.py checks this)
rem ============================================================
setlocal
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8

if defined AALI_REACH_VENV (set VENV=%AALI_REACH_VENV%) else (set VENV=D:\hwk-tools\reach-venv)

if not exist "%VENV%\Scripts\python.exe" (
  echo [reach] creating %VENV% ...
  .venv\Scripts\python.exe -m venv "%VENV%"
  if errorlevel 1 goto :fail
)

echo [reach] installing playwright + camoufox ...
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install playwright camoufox
if errorlevel 1 goto :fail

echo [reach] downloading the chromium build playwright uses ...
"%VENV%\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto :fail

rem camoufox ships its own firefox build; it is the anti-detect escalation
rem for hosts that serve a challenge page. Optional on purpose: it is a
rem large download and most links never need it.
echo [reach] downloading the camoufox engine (optional) ...
"%VENV%\Scripts\python.exe" -m camoufox fetch || echo [reach] camoufox unavailable - static + chromium still work

echo [reach] self-test: reading https://example.com with the browser ...
"%VENV%\Scripts\python.exe" scripts\reach_fetch.py --url https://example.com --engine chromium
if errorlevel 1 goto :fail

echo.
echo [reach] READY - venv: %VENV%
echo [reach] engine: auto (chromium first, camoufox if a page looks like a challenge)
exit /b 0

:fail
echo [reach] SETUP FAILED
exit /b 1