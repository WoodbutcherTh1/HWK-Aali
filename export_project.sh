#!/usr/bin/env bash
set -euo pipefail

BACKUP="${1:-project_backup.tar.gz}"
tar -czf "$BACKUP" \
  --exclude='./.git' \
  --exclude='./.local' \
  --exclude='./.agents' \
  --exclude='./artifacts' \
  --exclude='./.cache' \
  --exclude='./.venv' \
  --exclude='./venv' \
  --exclude='./env' \
  --exclude='./__pycache__' \
  --exclude='*/__pycache__' \
  --exclude='./.pythonlibs' \
  --exclude="./$BACKUP" \
  .
echo "Created $BACKUP"