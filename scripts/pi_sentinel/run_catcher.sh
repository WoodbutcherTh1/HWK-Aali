#!/usr/bin/env bash
# Pi-side runner for the Aali sentinel catcher (launched by cron @reboot).
# Keeps its own small log; exec so the python process IS the cron job.
# Deployed to /home/aalici/aali_sentinel/ by deploy_to_pi.sh.
set -u
mkdir -p "$HOME/aali_sentinel"
LOG="$HOME/aali_sentinel/catcher.log"
PC_IP="192.168.1.117"
PC_MAC="F4:B5:20:46:44:27"
exec python3 "$HOME/aali_sentinel/pi_sentinel_catcher.py" \
  --bind 127.0.0.1 --port 8766 \
  --target "http://${PC_IP}:5055" \
  --pc-mac "$PC_MAC" \
  --log-file "$LOG"
