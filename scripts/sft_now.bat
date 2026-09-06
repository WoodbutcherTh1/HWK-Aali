@echo off
rem Fast path: start SFT RIGHT NOW on Aali's current Phase A checkpoint,
rem at context=1024 (no context extension yet - that still happens later,
rem automatically, via queue_sft.bat's full pipeline once Phase A finishes).
rem Use this to get a testable tool-calling model sooner, at the cost of
rem sharing the 8GB card with Phase A pretraining (both will be slower).
rem
rem Does NOT touch Phase A's own checkpoint (D:\hwk-models\scratch) or
rem interfere with queue_sft.bat's later, separate pipeline - this writes to
rem its own output dir (sft-now) and its own log (sft_now_training.log).
set ROOT=%~dp0..
cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8

if not exist "D:\hwk-models\sft-now" mkdir "D:\hwk-models\sft-now"
if not exist "D:\hwk-models\sft-now\checkpoint.pt" copy /Y "D:\hwk-models\scratch\checkpoint.pt" "D:\hwk-models\sft-now\checkpoint.pt"
if not exist "D:\hwk-models\sft-now\trainer-state.pt" copy /Y "D:\hwk-models\scratch\trainer-state.pt" "D:\hwk-models\sft-now\trainer-state.pt"

set MAXSTEPS=0
for /f %%i in ('".venv\Scripts\python.exe" scripts\_read_step.py "D:\hwk-models\sft-now\trainer-state.pt" 3000') do set MAXSTEPS=%%i

echo Current Phase A step captured. Training sft-now up to step %MAXSTEPS% (+3000 SFT steps).

".venv\Scripts\python.exe" train_scratch.py ^
  --data data/sft_mix.jsonl ^
  --tokenizer D:/hwk-data/tokenizer/hwk_spm.model ^
  --output-dir D:/hwk-models/sft-now ^
  --mirror-dir X:/hwk-backups/sft-now ^
  --context 1024 --d-model 768 --heads 12 --layers 12 ^
  --batch-size 2 --gradient-accumulation 16 ^
  --max-steps %MAXSTEPS% --save-steps 250 --log-steps 25 ^
  --learning-rate 5e-5 --warmup-steps 100 --dtype fp16 ^
  --resume > D:\hwk-data\sft_now_training.log 2>&1
pause
