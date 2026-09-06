@echo off
rem Stage B: instruction / tool-calling SFT on top of the Phase A checkpoint.
rem Copies the latest pretrained checkpoint into a separate output dir (so
rem Phase A's checkpoint is never touched) and fine-tunes on data\sft_mix.jsonl
rem (84 tool-calling episodes + ~12k general instructions, same {"tool": ...}
rem protocol) with a low learning rate. Output/log go to sft-v1 and
rem sft_training.log so Phase A's own files are never overwritten.
set ROOT=%~dp0..
cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8

if not exist "D:\hwk-models\sft-v1" mkdir "D:\hwk-models\sft-v1"
if not exist "D:\hwk-models\sft-v1\checkpoint.pt" copy /Y "D:\hwk-models\scratch\checkpoint.pt" "D:\hwk-models\sft-v1\checkpoint.pt"
if not exist "D:\hwk-models\sft-v1\trainer-state.pt" copy /Y "D:\hwk-models\scratch\trainer-state.pt" "D:\hwk-models\sft-v1\trainer-state.pt"

".venv\Scripts\python.exe" train_scratch.py ^
  --data data/sft_mix.jsonl ^
  --tokenizer D:/hwk-data/tokenizer/hwk_spm.model ^
  --output-dir D:/hwk-models/sft-v1 ^
  --mirror-dir X:/hwk-backups/sft-v1 ^
  --context 1024 --d-model 768 --heads 12 --layers 12 ^
  --batch-size 4 --gradient-accumulation 8 ^
  --max-steps 93000 --save-steps 250 --log-steps 25 ^
  --learning-rate 5e-5 --warmup-steps 100 --dtype fp16 ^
  --resume > D:\hwk-data\sft_training.log 2>&1
pause
