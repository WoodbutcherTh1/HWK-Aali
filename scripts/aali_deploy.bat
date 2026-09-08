@echo off
rem ============================================================
rem  آلي — DEPLOY (زر النشر — بيد المالك فقط)
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
echo   ╔══════════════════════════════════════════╗
echo   ║   آلي — زر النشر  (Hmam Kaadna)          ║
echo   ╚══════════════════════════════════════════╝
echo.
echo   [1] فحص جاهزية النشر       (preflight --check)
echo   [2] تدريب + امتحان الترقية  (soup pipeline - GPU)
echo   [3] دمج المحوّل بالنموذج    (merge adapter)
echo   [4] نشر على Hugging Face    (private أولاً)
echo   [5] تجهيز Ollama            (Modelfile + GGUF steps)
echo   [6] قائمة OpenRouter        (checklist + probe)
echo   [7] دفع التحديثات إلى GitHub (commit + push)
echo   [8] خروج
echo.
set /p CHOICE=  اختر رقمًا ثم اضغط Enter: 

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
echo  سيبدأ تدريب QLoRA + امتحان الترقية على الـ GPU (قد يستغرق ساعات).
echo  تأكد أن Phase A متوقف أو أنهى عمله.
set /p GO=  متابعة؟ (y/N): 
if /i "%GO%"=="y" (
  start "Aali Soup Pipeline" cmd /k "set PYTHONIOENCODING=utf-8 && .venv\Scripts\python.exe scripts\soup_pipeline.py"
)
goto menu

:merge
echo.
set /p GO=  دمج المحوّل المُرقَّى في نموذج مستقل؟ (y/N): 
if /i "%GO%"=="y" ".venv\Scripts\python.exe" scripts\publish_aali.py --merge
echo.
pause
goto menu

:hf
echo.
set /p REPO=  اسم المستودع (مثال: HmamK/aali-1.5b): 
if "%REPO%"=="" goto menu
echo  يتم الرفع كـ PRIVATE أولاً — النشر العام بقرارك من موقع HF.
set /p GO=  متابعة الرفع؟ (y/N): 
if /i "%GO%"=="y" ".venv\Scripts\python.exe" scripts\publish_aali.py --hf %REPO%
echo.
pause
goto menu

:ollama
echo.
set /p NAME=  اسم النموذج في Ollama (مثال: aali): 
if "%NAME%"=="" goto menu
".venv\Scripts\python.exe" scripts\publish_aali.py --ollama %NAME%
echo.
pause
goto menu

:openrouter
".venv\Scripts\python.exe" scripts\publish_aali.py --openrouter
echo.
echo  القائمة في: D:\hwk-data\soup\openrouter_checklist.md
pause
goto menu

:github
echo.
echo  سيُطبع أولًا ما سيُدفَع (git status). لا يُدفع أي شيء قبل موافقتك.
git status --short
echo.
set /p MSG=  رسالة الـ commit (أو Enter لرسالة افتراضية): 
if "%MSG%"=="" set "MSG=update: aali nightly work"
set /p GO=  إضافة كل التغييرات والدفع الآن؟ (y/N): 
if /i not "%GO%"=="y" goto menu
git add -A
git commit -m "%MSG%

🤖 Generated with Codebuff
Co-Authored-By: Codebuff <noreply@codebuff.com>"
git push origin master
echo.
pause
goto menu
