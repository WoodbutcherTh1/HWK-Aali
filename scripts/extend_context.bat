@echo off
rem Stage A.5: extend Aali's context from 1024 (Phase A) to 4096 tokens.
rem Safe because the model uses RoPE, not learned position embeddings (see
rem file-agent/hwk_model/model.py) - context length is a training-time
rem choice, not baked into the weights' shapes. Copies the Phase A
rem checkpoint into a separate context-4k dir (Phase A's own files are never
rem touched) and continues training at the larger context with:
rem   --grad-checkpoint   trades compute for memory (recomputes activations
rem                       on backward) so the longer context fits in 8GB
rem   --batch-size 1 --gradient-accumulation 8   8 sequences/step, matching
rem                       Phase A's ~32k tokens/optimizer-step cadence
rem   lower LR (1e-4) + short warmup (200 steps) since this continues an
rem                       already-trained model rather than starting fresh
set ROOT=%~dp0..
cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8

if not exist "D:\hwk-models\context-4k" mkdir "D:\hwk-models\context-4k"
if not exist "D:\hwk-models\context-4k\checkpoint.pt" copy /Y "D:\hwk-models\scratch\checkpoint.pt" "D:\hwk-models\context-4k\checkpoint.pt"
if not exist "D:\hwk-models\context-4k\trainer-state.pt" copy /Y "D:\hwk-models\scratch\trainer-state.pt" "D:\hwk-models\context-4k\trainer-state.pt"

".venv\Scripts\python.exe" train_scratch.py ^
  --data D:/hwk-data/tokens ^
  --corpora pile,arabic ^
  --tokenizer D:/hwk-data/tokenizer/hwk_spm.model ^
  --output-dir D:/hwk-models/context-4k ^
  --mirror-dir X:/hwk-backups/context-4k ^
  --context 4096 --d-model 768 --heads 12 --layers 12 ^
  --batch-size 1 --gradient-accumulation 8 --grad-checkpoint ^
  --max-steps 100000 --save-steps 500 --log-steps 25 ^
  --learning-rate 1e-4 --warmup-steps 200 --dtype fp16 ^
  --resume > D:\hwk-data\context_training.log 2>&1
