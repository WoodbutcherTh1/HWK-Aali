#!/usr/bin/env bash
# Deploy Jarvis from the PC to the Raspberry Pi.
# Idempotent: safe to re-run; it refreshes code and reinstalls deps.
#
#   bash scripts/jarvis_deploy_to_pi.sh            # full deploy
#   bash scripts/jarvis_deploy_to_pi.sh --no-deps # copy code only (skip pip)
#
# Uses the existing Pi-CI key (same one scripts/pi_sentinel/deploy_to_pi.sh uses),
# so no password prompt. Config/secrets already in ~/.aali/jarvis/ are NEVER touched.
set -eu

PI_USER=aalici
PI_HOST=192.168.1.15
KEY=D:/hwk-data/pi_ci/pc_to_pi_ed25519
DEST=/home/aalici/jarvis
VENV=$DEST/.venv-jarvis

SSH="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
SCP="scp -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"

if [ ! -f "$repo/scripts/jarvis/machines.py" ]; then
    echo "!! scripts/jarvis/ not found in $repo"
    echo "   Push the Jarvis code from the Mac first (see the runbook), then re-run."
    exit 1
fi

echo "== 1/5: staging directory on the Pi =="
$SSH $PI_USER@$PI_HOST "mkdir -p $DEST"

echo "== 2/5: copying jarvis package =="
$SCP -r "$repo/scripts/jarvis" "$PI_USER@$PI_HOST:$DEST/"

echo "== 3/5: copying requirements =="
if [ -f "$repo/requirements-jarvis.txt" ]; then
    $SCP "$repo/requirements-jarvis.txt" "$PI_USER@$PI_HOST:$DEST/"
else
    echo "   (no requirements-jarvis.txt in repo — using the package's own, if any)"
    if [ -f "$repo/scripts/jarvis/requirements.txt" ]; then
        $SCP "$repo/scripts/jarvis/requirements.txt" "$PI_USER@$PI_HOST:$DEST/requirements-jarvis.txt"
    fi
fi

echo "== 4/5: systemd unit =="
if [ -f "$repo/deploy/jarvis.service" ]; then
    $SCP "$repo/deploy/jarvis.service" "$PI_USER@$PI_HOST:$DEST/"
    # Show the unit's sandbox lines so a missing ReadWritePaths fix is caught here,
    # not hours later when the service fails to start under systemd.
    echo "   sandbox directives in the unit:"
    $SSH $PI_USER@$PI_HOST "grep -E 'ReadWritePaths|ReadOnlyPaths|User=|WorkingDirectory' $DEST/jarvis.service || echo '   (none found — check the unit)'"
else
    echo "   (no deploy/jarvis.service in repo — skipping; STEP 5 will need it)"
fi

if [ "${1:-}" = "--no-deps" ]; then
    echo "== skipped deps (--no-deps) =="
else
    echo "== 5/5: virtualenv + dependencies =="
    # python3 -m venv bootstraps pip from ensurepip, so the Pi needs no
    # system-wide pip3 (and therefore no sudo) for this to work.
    $SSH $PI_USER@$PI_HOST "cd $DEST && [ -x $VENV/bin/python ] || python3 -m venv $VENV"
    if [ -f "$repo/requirements-jarvis.txt" ]; then
        $SSH $PI_USER@$PI_HOST "cd $DEST && $VENV/bin/pip install --quiet --upgrade pip && $VENV/bin/pip install -r requirements-jarvis.txt"
    fi
    echo "   venv python: $($SSH $PI_USER@$PI_HOST "$VENV/bin/python --version 2>&1")"
    echo "   key sanity:  $($SSH $PI_USER@$PI_HOST "test -r ~/.aali/jarvis/groq_key.txt && echo 'groq_key.txt present' || echo 'MISSING groq_key.txt'")"
fi

echo
echo "== done =="
echo "Next (needs Pi sudo for systemd):"
echo "  $SSH $PI_USER@$PI_HOST 'sudo cp $DEST/jarvis.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now jarvis && sudo systemctl status jarvis --no-pager'"
echo "Then watch the log:"
echo "  $SSH $PI_USER@$PI_HOST 'sudo journalctl -u jarvis -f'"
echo
echo "Config lives in ~/.aali/jarvis/ (groq_key.txt, telegram.json, machines.json) and was not modified."