#!/usr/bin/env bash
# Finish the Jarvis installation on the Pi. Run once, as the aalici user.
#
#   bash scripts/jarvis_setup_pi.sh
#
# What it does:
#   1. checks the config dir and tells you exactly what is missing
#   2. installs the systemd unit (needs sudo -- only that one step)
#   3. enables + starts jarvis and shows the log
#
# It never prints the Groq key or the bot token.
set -eu

PI_DIR="$HOME/jarvis"
CFG_DIR="$HOME/.aali/jarvis"
UNIT=jarvis.service

echo "== 1/4: config =="
mkdir -p "$CFG_DIR"
chmod 700 "$HOME/.aali" "$CFG_DIR"

missing=0
if [ -r "$CFG_DIR/groq_key.txt" ]; then
    echo "   ✅ groq_key.txt present"
else
    echo "   ❌ missing $CFG_DIR/groq_key.txt (one line: your gsk_ key)"
    missing=1
fi
if [ -r "$CFG_DIR/machines.json" ]; then
    echo "   ✅ machines.json present"
else
    echo "   ❌ missing $CFG_DIR/machines.json"
    missing=1
fi
if [ -r "$CFG_DIR/telegram.json" ]; then
    echo "   ✅ telegram.json present"
else
    echo "   ❌ missing $CFG_DIR/telegram.json  <-- THE ONE THING LEFT"
    echo "      create it with:"
    echo "        printf '{\"bot_token\":\"PUT_THE_TOKEN_HERE\",\"owner_chat_id\":6027532184}' \\"
    echo "          > $CFG_DIR/telegram.json && chmod 600 $CFG_DIR/telegram.json"
    echo "      (ask @BotFather for the token if you do not have it)"
    missing=1
fi

if [ ! -x "$PI_DIR/.venv-jarvis/bin/python" ]; then
    echo "   ❌ venv missing -- run: bash scripts/jarvis_deploy_to_pi.sh from the PC"
    exit 1
fi
echo "   ✅ venv present"

echo
echo "== 2/4: self-check =="
export AALI_JARVIS_CONFIG="$CFG_DIR"
export PATH="$PI_DIR/.venv-jarvis/bin:$HOME/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/lib"
export ESPEAK_DATA_PATH="$HOME/bin/espeak-ng-data"
export PYTHONIOENCODING=utf-8
"$PI_DIR/.venv-jarvis/bin/python" -m jarvis.main --selfcheck || true

if [ "$missing" -ne 0 ]; then
    echo
    echo "!! Config is incomplete. Fix the items above, then re-run this script."
    echo "!! (you can still run the self-check above without systemd)"
    exit 1
fi

echo
echo "== 3/4: systemd =="
if [ ! -f "$PI_DIR/$UNIT" ]; then
    echo "   ❌ $PI_DIR/$UNIT not found -- copy deploy/jarvis.service to the Pi"
    exit 1
fi
echo "   sandbox directives in the unit:"
grep -E 'ReadWritePaths|ProtectHome|User=|ExecStart' "$PI_DIR/$UNIT" | sed 's/^/     /'

sudo cp "$PI_DIR/$UNIT" /etc/systemd/system/$UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now $UNIT
sudo systemctl --no-pager --lines=8 status $UNIT || true

echo
echo "== 4/4: log =="
sudo journalctl -u $UNIT --no-pager --lines=15 || true

echo
echo "== done =="
echo "Test from Telegram:"
echo "  /start        → welcome"
echo "  /status all   → both machines"
echo "  /wake pc      → wake the PC"
echo "  /help         → all commands"
echo
echo "Watch the log live:"
echo "  sudo journalctl -u $UNIT -f"