@echo off
rem Quick status: GPU, disks, latest training loss.
nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu --format=csv
echo.
powershell -Command "Get-PSDrive C,D,X | Select-Object Name,@{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}} | Format-Table -AutoSize"
echo.
if exist D:\hwk-data\training.log (
  echo Latest training log lines:
  tail -5 D:\hwk-data\training.log
) else (
  echo No D:\hwk-data\training.log yet.
)
pause