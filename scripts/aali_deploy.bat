@echo off
rem ============================================================
rem    DEPLOY (     )
rem  Double-click this file. YOU choose what to publish.
rem  Nothing is uploaded/pushed without you pressing y first.
rem ============================================================
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0.."
title Aali Deploy

:menu
cls
echo.
echo
echo           (Hmam Kaadna)
echo
echo.
echo   [1]          (preflight --check)
echo   [2]  +    (soup pipeline - GPU)
echo   [3]       (merge adapter)
echo   [4]   Hugging Face    (private )
echo   [5]  Ollama            (Modelfile + GGUF steps)
echo   [6]        (checklist + probe)
echo   [7]    GitHub (commit + push)
echo   [8]
echo.
set /p CHOICE=      Enter:

if "%CHOICE%"=="1" goto check
if "%CHOICE%"=="2" goto train
if "%CHOICE%"=="3" goto merge
if "%CHOICE%"=="4" goto hf
if "%CHOICE%"=="5" goto ollama
if "%CHOICE%"=="6" goto openrouter
if "%CHOICE%"=="7" goto github
if "%CHOICE%"=="8" exit /b 0
goto menu

:check
".venv\Scripts\python.exe" scripts\publish_aali.py --check
echo.
pause
goto menu

:train
echo.
echo    QLoRA +     GPU (  ).
echo    Phase A    .
set /p GO=   (y/N):
if /i "%GO%"=="y" (
  rem 2026-09-12: detached launch - the pipeline (and its hours-long train)
  rem must never live in a console window that a close/Ctrl+C would kill.
  ".venv\Scripts\python.exe" scripts\launch_detached.py --log soup_pipeline -- .venv\Scripts\python.exe scripts\soup_pipeline.py
)
echo.
pause
goto menu

:merge
echo.
set /p GO=        (y/N):
if /i "%GO%"=="y" ".venv\Scripts\python.exe" scripts\publish_aali.py --merge
echo.
pause
goto menu

:hf
echo.
set /p REPO=    (: HmamK/aali-1.5b):
if "%REPO%"=="" goto menu
echo     PRIVATE        HF.
set /p GO=    (y/N):
if /i "%GO%"=="y" ".venv\Scripts\python.exe" scripts\publish_aali.py --hf %REPO%
echo.
pause
goto menu

:ollama
echo.
set /p NAME=     Ollama (: aali):
if "%NAME%"=="" goto menu
".venv\Scripts\python.exe" scripts\publish_aali.py --ollama %NAME%
echo.
pause
goto menu

:openrouter
".venv\Scripts\python.exe" scripts\publish_aali.py --openrouter
echo.
echo   : D:\hwk-data\soup\openrouter_checklist.md
pause
goto menu

:github
echo.
echo      (git status).      .
git status --short
echo.
set /p MSG=    commit ( Enter  ):
if "%MSG%"=="" set "MSG=update: aali nightly work"
set /p GO=       (y/N):
if /i not "%GO%"=="y" goto menu
git add -A
git commit -m "%MSG%

? Generated with Codebuff
Co-Authored-By: Codebuff <noreply@codebuff.com>"
git push origin master
echo.
pause
goto menu
