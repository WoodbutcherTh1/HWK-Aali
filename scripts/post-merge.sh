#!/usr/bin/env bash
set -euo pipefail

# Keep post-merge setup fast and non-interactive. Heavy model training is
# intentionally never run as part of a merge hook.
python -m py_compile \
  file-agent/agent_loop.py \
  file-agent/app.py \
  file-agent/file_agent/*.py \
  file-agent/hwk_model/*.py \
  create_training_data.py \
  train_scratch.py \
  evaluate_scratch.py

python - <<'PY'
import flask
import requests
from importlib.metadata import version
print(f"runtime imports: Flask {version('flask')}, requests {version('requests')}")
PY

if [[ ! -f data/agent_instructions.jsonl ]]; then
  python create_training_data.py
fi

echo "post-merge setup: OK"