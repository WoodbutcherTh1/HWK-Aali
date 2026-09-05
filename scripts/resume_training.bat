@echo off
rem Phase A: general pretraining on English (pile) + Arabic (fineweb-2).
rem Context 1024, ~23k tokens/sec on the RTX 3070 -> roughly 2-3B tokens total.
rem Close the window to pause; reopening with --resume continues from the
rem last checkpoint. Console output is mirrored to D:\hwk-data\training.log
rem (watch it with watch_training.bat).
set ROOT=%~dp0..
cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8

".venv\Scripts\python.exe" train_scratch.py ^
  --data D:/hwk-data/tokens ^
  --corpora pile,arabic ^
  --tokenizer D:/hwk-data/tokenizer/hwk_spm.model ^
  --output-dir D:/hwk-models/scratch ^
  --mirror-dir X:/hwk-backups/scratch ^
  --context 1024 --d-model 768 --heads 12 --layers 12 ^
  --batch-size 4 --gradient-accumulation 8 ^
  --max-steps 90000 --save-steps 2500 --log-steps 25 ^
  --warmup-steps 500 --dtype fp16 ^
  --resume > D:\hwk-data\training.log 2>&1
pause
