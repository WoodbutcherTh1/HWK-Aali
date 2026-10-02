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

# --- icon ------------------------------------------------------------------
# The owner's first Mac build died HERE, at BUNDLE, with
#   FileNotFoundError: Icon input file .../build-desktop/icon.icns not found
# after 14 seconds of successful analysis and collection — because the spec
# passed icon= unconditionally and PyInstaller does NOT fall back to a default.
# So the icon is now generated from the repo's own PNGs, and the spec is
# defensive on top of that (icon=None when absent). A missing icon must never
# cost the build again.
if [ ! -f build-desktop/icon.icns ]; then
  say "[studio] no icon.icns — generating it from the repo PNGs..."
  if python3 scripts/make_icon_icns.py; then
    say "[studio] icon ready"
  else
    say "[studio] WARNING: could not build icon.icns. The spec now tolerates a"
    say "         missing icon, so the build continues without one."
  fi
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