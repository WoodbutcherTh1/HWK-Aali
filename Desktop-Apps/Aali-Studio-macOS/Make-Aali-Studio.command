#!/bin/bash
# ============================================================
#  Make Aali Studio — DOUBLE-CLICK THIS FILE in Finder.
#
#  It builds آلي ستوديو and installs it into /Applications,
#  so from then on it is a normal Mac app: its own icon, click
#  to open, no Terminal, no scripts.
#
#  (If macOS refuses the first time: right-click this file ->
#   Open. That one-time security prompt is normal.)
# ============================================================
set -e

say() { printf '%s\n' "$*"; }
step() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT"

say ""
say "========================================================"
say "   AALI STUDIO  -  building your Mac app"
say "========================================================"
say "This takes 1-3 minutes the first time. Please wait."

step "Checking Python 3"
if ! command -v python3 >/dev/null 2>&1; then
    say ""
    say "  Python 3 was not found."
    say "  Install it from https://www.python.org/downloads/macos/"
    say "  (the big yellow button), then double-click this file again."
    read -r -p "Press Enter to close..." _
    exit 1
fi
say "  python3: $(python3 --version 2>&1)"

step "Building آلي ستوديو"
if ! ./build-desktop/aali-studio/build-mac.sh; then
    say ""
    say "  The build did not finish. The full log is here:"
    say "  $ROOT/build-desktop/studio_build.log"
    read -r -p "Press Enter to close..." _
    exit 1
fi

APP="$ROOT/build-desktop/dist/Aali-Studio.app"
if [ ! -d "$APP" ]; then
    say ""
    say "  The app was not produced. Please send me the log above."
    read -r -p "Press Enter to close..." _
    exit 1
fi

step "Installing into your Applications folder"
rm -rf "/Applications/Aali-Studio.app"
cp -R "$APP" /Applications/
say "  Installed: /Applications/Aali-Studio.app"

# Unsigned apps are quarantined; clear it so the first click just works.
xattr -dr com.apple.quarantine "/Applications/Aali-Studio.app" 2>/dev/null || true

say ""
say "========================================================"
say "   DONE  -  آلي ستوديو is now a normal Mac app"
say "========================================================"
say ""
say "  To open it later:  click the آلي ستوديو icon in"
say "  your Applications folder (or Launchpad / Spotlight)."
say ""
say "  IMPORTANT: the brain (آلي) lives on your Windows PC."
say "  On first launch press the 🔑 keys button and enter:"
say "     http://100.94.100.57:5055"
say "  then paste the master key from aali_master_key.txt."
say ""

read -r -p "Press Enter to launch آلي ستوديو now..." _
open -a "/Applications/Aali-Studio.app"
say "  Launched. Enjoy!"
