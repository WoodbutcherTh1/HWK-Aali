@echo off
rem ============================================================
rem  Build the آلي Node exe (Aali Cloud STEP 5 packaging)
rem  Output: build-desktop\dist\aali-node.exe
rem
rem  Same proven pattern as scripts\build_desktop.bat: a dedicated
rem  .venv-desktop carries PyInstaller + pywebview + pillow so the
rem  training venv never gains build-only deps.
rem
rem  The exe IS the headless daemon: aali-node.exe --hub ws://... --token JWT
rem  GUI:  aali-node.exe --shell --hub ws://... --token JWT
rem  Update activation inside a frozen build is deliberately refused
rem  (activate.py) — updates swap on source deployments.
rem ============================================================
setlocal
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8

if not exist .venv-desktop (
  echo [build-node] creating build venv...
  .venv\Scripts\python.exe -m venv .venv-desktop
  .venv-desktop\Scripts\python.exe -m pip install pyinstaller pywebview pillow pystray websockets
)

rem websockets + pystray are runtime deps of the daemon/shell (hub-venv only
rem by day) — the BUILD venv needs them too or the exe ships without them.
rem cryptography is the PRODUCTION update-signature path (Ed25519; HMAC is
rem the fallback) — without it a packaged Node fails closed on every real
rem update manifest, so it ships inside the exe.
.venv-desktop\Scripts\python.exe -c "import websockets, pystray, cryptography" 2>nul
if errorlevel 1 (
  echo [build-node] installing Node runtime deps into .venv-desktop...
  .venv-desktop\Scripts\python.exe -m pip install websockets pystray cryptography
)

if not exist build-desktop\icon.ico (
  echo [build-node] generating HWK icon...
  .venv-desktop\Scripts\python.exe scripts\make_hwk_icon.py
)

echo [build-node] compiling aali-node.exe...
.venv-desktop\Scripts\pyinstaller.exe --noconfirm --clean --distpath build-desktop\dist --workpath build-desktop\work aali-node.spec
if errorlevel 1 goto :fail

echo.
echo [build-node] DONE: build-desktop\dist\aali-node.exe
echo   Try: build-desktop\dist\aali-node.exe --help
exit /b 0

:fail
echo [build-node] FAILED
exit /b 1
