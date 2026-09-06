@echo off
rem Stage B: instruction / tool-calling SFT, on top of the context-extended
rem (4096-token) checkpoint from extend_context.bat - not the original
rem 1024-token Phase A checkpoint, so the final model keeps the longer
rem context. Copies that checkpoint into its own sft-v1 dir (context-4k's
rem files are never touched) and fine-tunes at a low LR on data\sft_mix.jsonl
rem (84 tool-calling episodes + ~12k general instructions, same {"tool": ...}
rem protocol). --grad-checkpoint again, since context=4096 needs it to fit.
set ROOT=%~dp0..
cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8

if not exist "D:\hwk-models\sft-v1" mkdir "D:\hwk-models\sft-v1"
if not exist "D:\hwk-models\sft-v1\checkpoint.pt" copy /Y "D:\hwk-models\context-4k\checkpoint.pt" "D:\hwk-models\sft-v1\checkpoint.pt"
if not exist "D:\hwk-models\sft-v1\trainer-state.pt" copy /Y "D:\hwk-models\context-4k\trainer-state.pt" "D:\hwk-models\sft-v1\trainer-state.pt"

".venv\Scripts\python.exe" train_scratch.py ^
  --data data/sft_mix.jsonl ^
  --tokenizer D:/hwk-data/tokenizer/hwk_spm.model ^
  --output-dir D:/hwk-models/sft-v1 ^
  --mirror-dir X:/hwk-backups/sft-v1 ^
  --context 4096 --d-model 768 --heads 12 --layers 12 ^
  --batch-size 1 --gradient-accumulation 8 --grad-checkpoint ^
  --max-steps 103000 --save-steps 250 --log-steps 25 ^
  --learning-rate 5e-5 --warmup-steps 100 --dtype fp16 ^
  --resume > D:\hwk-data\sft_training.log 2>&1
