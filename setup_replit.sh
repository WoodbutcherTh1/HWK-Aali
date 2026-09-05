#!/usr/bin/env bash
set -euo pipefail

echo "Setting up the Python environment with uv..."
if command -v uv >/dev/null 2>&1; then
  uv sync
else
  echo "uv is not available; creating an isolated virtual environment."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi
echo "Setup complete. Run: uv run python check_env.py"