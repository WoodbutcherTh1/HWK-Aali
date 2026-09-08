@echo off
rem ============================================================
rem  Build آلي Desktop:  portable exe + Inno Setup installer
rem  Output: build-desktop\dist\Aali-Desktop.exe
rem          build-desktop\installer\Aali-Desktop-Setup.exe
rem ============================================================
setlocal
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8

if not exist .venv-desktop (
  echo [build] creating build venv...
  .venv\Scripts\python.exe -m venv .venv-desktop
  .venv-desktop\Scripts\python.exe -m pip install pyinstaller pywebview pillow
)

if not exist build-desktop\icon.ico (
  echo [build] generating HWK icon...
  .venv-desktop\Scripts\python.exe scripts\make_hwk_icon.py
)

echo [build] compiling exe...
.venv-desktop\Scripts\pyinstaller.exe --noconfirm --clean --distpath build-desktop\dist --workpath build-desktop\work Aali-Desktop.spec

rem --- terminal client (aali-cli.exe) ---
.venv-desktop\Scripts\pyinstaller.exe --noconfirm --onefile --name aali-cli --icon "%~dp0..\build-desktop\icon.ico" --distpath build-desktop\dist --workpath build-desktop\work --specpath build-desktop scripts\aali_cli.py
if errorlevel 1 goto :fail

where ISCC.exe >nul 2>&1
if errorlevel 1 set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
  echo [build] compiling installer...
  "%ISCC%" build-desktop\Aali-Setup.iss
) else (
  echo [build] Inno Setup not found - skipping installer (portable exe is ready)
)

echo.
echo [build] DONE:
echo   build-desktop\dist\Aali-Desktop.exe
if exist build-desktop\installer\Aali-Desktop-Setup.exe echo   build-desktop\installer\Aali-Desktop-Setup.exe
exit /b 0

:fail
echo [build] FAILED
exit /b 1
