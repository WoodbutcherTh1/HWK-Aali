@echo off
rem Watch the training log live. Start training first (resume_training.bat).
where tail >nul 2>nul && tail -f D:\hwk-data\training.log || powershell -Command "Get-Content D:\hwk-data\training.log -Wait -Tail 40"
pause