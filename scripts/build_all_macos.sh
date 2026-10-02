#!/usr/bin/env bash
# ============================================================
#  Build EVERY Aali client for macOS (and Linux) in one go.
#
#    ./scripts/build_all_macos.sh            # build all three
#    ./scripts/build_all_macos.sh studio     # only Aali Studio
#    ./scripts/build_all_macos.sh desktop    # only آلي Desktop
#    ./scripts/build_all_macos.sh cli        # only the terminal client
#    ./scripts/build_all_macos.sh check      # no build: the portability self-test
#
#  Outputs land in build-desktop/dist/:
#    Aali-Studio.app   آلي Studio (the IDE)
#    Aali-Desktop.app  آلي Desktop (chat window)
#    aali-cli          the terminal client
#
#  Honest scope: this builds the CLIENTS. The brain (:5055 + the promoted
#  model on :20129) stays on the owner's Windows PC — that is where the GPU,
#  the trained checkpoints and D:\hwk-data live. On the Mac the clients talk
#  to it over the LAN (http://<pc-ip>:5055 with the master key) or over the
#  tunnel. See docs/PORTING.md.
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WHAT="${1:-all}"
DIST="build-desktop/dist"
say() { printf '%s\n' "$*"; }

have() { command -v "$1" >/dev/null 2>&1; }

PY_BOOT=""
if [ -x .venv/Scripts/python.exe ]; then PY_BOOT=".venv/Scripts/python.exe"
elif [ -x .venv/bin/python ]; then PY_BOOT=".venv/bin/python"
elif have python3; then PY_BOOT="python3"
else say "[build] no python found — install python3 (3.10+) first"; exit 1; fi

say "[build] host   : $(uname -srm)"
say "[build] python : $($PY_BOOT --version 2>&1)"

# ---------------------------------------------------------------- studio
build_studio() {
  say ""; say "=== آلي ستوديو (Aali Studio) ==="
  bash build-desktop/aali-studio/build-mac.sh
}

# --------------------------------------------------------------- desktop
build_desktop() {
  say ""; say "=== آلي Desktop ==="
  local venv=".venv-desktop"
  if [ ! -d "$venv" ]; then
    say "[desktop] creating the isolated venv ($venv)..."
    "$PY_BOOT" -m venv "$venv"
    "$venv/bin/python" -m pip install --upgrade pip
    "$venv/bin/python" -m pip install pyinstaller pywebview
  fi
  say "[desktop] compiling..."
  "$venv/bin/pyinstaller" --noconfirm --clean \
    --distpath "$DIST" --workpath build-desktop/work Aali-Desktop.spec
  say "[desktop] done: $DIST/Aali-Desktop.app"
}

# ------------------------------------------------------------------- cli
build_cli() {
  say ""; say "=== آلي CLI ==="
  local venv=".venv-desktop"
  [ -d "$venv" ] || build_desktop >/dev/null 2>&1 || true
  if [ ! -d "$venv" ]; then
    "$PY_BOOT" -m venv "$venv"
    "$venv/bin/python" -m pip install pyinstaller
  fi
  say "[cli] compiling (a single-file binary, no install needed)..."
  "$venv/bin/pyinstaller" --noconfirm --clean --onefile \
    --name aali-cli --distpath "$DIST" \
    --workpath build-desktop/work-cli --specpath build-desktop \
    scripts/aali_cli.py
  say "[cli] done: $DIST/aali-cli"
  say "[cli] run it with:  ./$DIST/aali-cli --base http://<pc-ip>:5055"
}

# ----------------------------------------------------------------- check
# The check MUST run on an interpreter that can actually import the clients.
# The owner's first Mac run proved the trap: PY_BOOT fell through to the system
# python3, which has no flask, and the report honestly said
# "studio_server imports: No module named 'flask'" — a verdict about the wrong
# interpreter, not about macOS. Any repo venv with flask wins over system python.
VENV_STUDIO=".venv-studio"

check_python() {
  for candidate in "$VENV_STUDIO/bin/python" ".venv-desktop/bin/python" \
                   ".venv/bin/python" ".venv/Scripts/python.exe"; do
    [ -x "$candidate" ] || continue
    if "$candidate" -c "import flask" >/dev/null 2>&1; then
      printf '%s' "$candidate"; return 0
    fi
  done
  # Nothing has flask yet. Build the studio venv rather than report a verdict
  # from an interpreter that cannot import half the thing under test.
  say "[check] no interpreter has flask — creating $VENV_STUDIO (one time)..."
  if [ ! -d "$VENV_STUDIO" ]; then
    "$PY_BOOT" -m venv "$VENV_STUDIO" >/dev/null
  fi
  "$VENV_STUDIO/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
  "$VENV_STUDIO/bin/python" -m pip install --quiet flask pywebview >/dev/null 2>&1 || true
  if "$VENV_STUDIO/bin/python" -c "import flask" >/dev/null 2>&1; then
    printf '%s' "$VENV_STUDIO/bin/python"; return 0
  fi
  printf '%s' "$PY_BOOT"
}

run_check() {
  say ""; say "=== portability self-test (no build) ==="
  local check_py; check_py="$(check_python)"
  say "[check] interpreter: $check_py  ($("$check_py" --version 2>&1))"
  "$check_py" scripts/portability_check.py "$@"
}

case "$WHAT" in
  studio)  build_studio ;;
  desktop) build_desktop ;;
  cli)     build_cli ;;
  check)   shift || true; run_check "$@" ;;
  all)
    build_studio
    build_desktop
    build_cli
    say ""
    say "[build] ALL DONE -> $DIST"
    ls -la "$DIST" | sed 's/^/    /'
    ;;
  *)
    say "usage: $0 [all|studio|desktop|cli|check]"
    exit 2 ;;
esac