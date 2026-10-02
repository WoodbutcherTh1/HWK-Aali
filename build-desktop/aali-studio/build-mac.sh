#!/usr/bin/env bash
# ============================================================
#  Build Aali Studio on macOS or Linux.
#
#    ./build-desktop/aali-studio/build-mac.sh
#
#  macOS : -> build-desktop/dist/Aali-Studio.app   (double-click it)
#  Linux : -> build-desktop/dist/Aali-Studio       (needs WebKit2GTK)
#
#  Uses its own venv (.venv-studio) so the training venv is never touched.
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

VENV=".venv-studio"
DIST="build-desktop/dist"
WORK="build-desktop/work-studio"
LOG="D:/hwk-data/studio_build.log"

say() { printf '%s\n' "$*"; }

say "[studio] host: $(uname -srm)"
say "[studio] python: $(python3 --version 2>&1)"

# --- venv ------------------------------------------------------------------
if [ ! -d "$VENV" ]; then
  say "[studio] creating the isolated venv ($VENV)..."
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install flask pywebview pyinstaller
fi
PY="$VENV/bin/python"

# --- optional icon (a missing .icns only costs the pretty icon) -----------
if [ ! -f build-desktop/icon.icns ]; then
  say "[studio] note: build-desktop/icon.icns is missing — the app gets the"
  say "         default Python icon. (It is a binary asset; generate it on a"
  say "         machine that has iconutil, or copy one in.)"
fi

# --- build -----------------------------------------------------------------
say "[studio] compiling with pyinstaller..."
"$VENV/bin/pyinstaller" --noconfirm --clean \
  --distpath "$DIST" --workpath "$WORK" \
  build-desktop/aali-studio/Aali-Studio.spec

case "$(uname -s)" in
  Darwin)
    APP="$DIST/Aali-Studio.app"
    [ -d "$APP" ] || { say "[studio] FAIL: $APP was not produced"; exit 1; }
    say "[studio] done: $APP"
    say "         open it with:  open \"$APP\""
    # Unsigned apps are quarantined by Gatekeeper; be honest about the fix
    # instead of letting the owner hit a mystery "damaged" dialog.
    say "         (first launch only, if Gatekeeper complains:"
    say "          xattr -dr com.apple.quarantine \"$APP\")"
    ;;
  *)
    BIN="$DIST/Aali-Studio"
    [ -f "$BIN" ] || { say "[studio] FAIL: $BIN was not produced"; exit 1; }
    say "[studio] done: $BIN"
    say "         run it with:  ./$BIN"
    say "         a window needs WebKit2GTK:  sudo apt install libwebkit2gtk-4.1-0"
    ;;
esac