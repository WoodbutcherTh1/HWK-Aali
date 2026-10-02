@echo off
rem ============================================================
rem  Build Aali Studio: isolated venv + windowed exe
rem  Output: build-desktop\dist\Aali-Studio.exe
rem  (ASCII + CRLF on purpose: tests/test_bat_hygiene.py checks this)
rem ============================================================
setlocal
cd /d "%~dp0..\.."
set PYTHONIOENCODING=utf-8

if not exist .venv-studio (
  echo [studio] creating the isolated venv...
  .venv\Scripts\python.exe -m venv .venv-studio
  .venv-studio\Scripts\python.exe -m pip install --upgrade pip
  .venv-studio\Scripts\python.exe -m pip install flask pywebview pyinstaller
)

if not exist build-desktop\icon.ico (
  echo [studio] generating the HWK icon...
  .venv-desktop\Scripts\python.exe scripts\make_hwk_icon.py
)

echo [studio] compiling the exe...
.venv-studio\Scripts\pyinstaller.exe --noconfirm --clean --distpath build-desktop\dist --workpath build-desktop\work-studio build-desktop\aali-studio\Aali-Studio.spec
if errorlevel 1 goto :fail

echo [studio] done: build-desktop\dist\Aali-Studio.exe
exit /b 0

:fail
echo [studio] BUILD FAILED
exit /b 1
