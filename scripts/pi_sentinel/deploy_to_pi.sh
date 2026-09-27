#!/usr/bin/env bash
# One-command deploy of the Aali Pi sentinel from the PC (Git Bash).
# Idempotent: re-running refreshes files, crontab, and the binary.
#
#   bash scripts/pi_sentinel/deploy_to_pi.sh          # full deploy
#   bash scripts/pi_sentinel/deploy_to_pi.sh --no-cloudflared
#                                                      # skip binary fetch
#
# Uses the existing Pi-CI key: D:/hwk-data/pi_ci/pc_to_pi_ed25519
set -eu

PI_USER=aalici
PI_HOST=192.168.1.15
KEY=D:/hwk-data/pi_ci/pc_to_pi_ed25519
PC_IP=192.168.1.117
PC_MAC=F4:B5:20:46:44:27
TUNNEL_ID=81d534db-ad01-4861-901f-baa443014dcc
CF_VER=2025.6.1
DEST=/home/aalici/aali_sentinel
SSH="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
SCP="scp -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

here="$(cd "$(dirname "$0")" && pwd)"

echo "== 1/5: staging directory on the Pi =="
$SSH $PI_USER@$PI_HOST "mkdir -p $DEST"

echo "== 2/5: pushing catcher + runner =="
$SCP "$here/../pi_sentinel_catcher.py" "$PI_USER@$PI_HOST:$DEST/"
$SCP "$here/run_catcher.sh"            "$PI_USER@$PI_HOST:$DEST/"

echo "== 3/5: pushing tunnel credentials + config =="
$SCP "/c/Users/HmamK/.cloudflared/$TUNNEL_ID.json" "$PI_USER@$PI_HOST:$DEST/"
cat > /tmp/aali_tunnel_config.yml <<EOF
tunnel: $TUNNEL_ID
credentials-file: $DEST/$TUNNEL_ID.json

ingress:
  - hostname: aali.dpdns.org
    service: http://127.0.0.1:8766
  - service: http_status:404
protocol: http2
EOF
$SCP /tmp/aali_tunnel_config.yml "$PI_USER@$PI_HOST:$DEST/config.yml"
rm -f /tmp/aali_tunnel_config.yml

echo "== 4/5: cloudflared linux-arm64 binary (skip with --no-cloudflared) =="
if [ "${1:-}" != "--no-cloudflared" ]; then
  url="https://github.com/cloudflare/cloudflared/releases/download/$CF_VER/cloudflared-linux-arm64"
  echo "   fetching $url"
  curl -fL --retry 3 -o /tmp/cloudflared-arm64 "$url"
  $SCP /tmp/cloudflared-arm64 "$PI_USER@$PI_HOST:$DEST/cloudflared"
  rm -f /tmp/cloudflared-arm64
  $SSH $PI_USER@$PI_HOST "chmod +x $DEST/cloudflared && $DEST/cloudflared --version"
fi

echo "== 5/5: crontab (idempotent marker checks) =="
# @reboot starts both; the * * * * * guards revive them if they crash
# between boots (pgrep-gated so they never duplicate).
$SSH $PI_USER@$PI_HOST "crontab -l 2>/dev/null | grep -q 'run_catcher.sh' || (crontab -l 2>/dev/null; echo '@reboot /bin/bash $DEST/run_catcher.sh') | crontab -"
$SSH $PI_USER@$PI_HOST "crontab -l 2>/dev/null | grep -q 'tunnel --config' || (crontab -l; echo '@reboot $DEST/cloudflared tunnel --config $DEST/config.yml run aali >> $DEST/tunnel.log 2>&1') | crontab -"
$SSH $PI_USER@$PI_HOST "crontab -l 2>/dev/null | grep -q 'pi_sentinel_catcher.py >/dev/null' || (crontab -l; echo '* * * * * pgrep -f pi_sentinel_catcher.py >/dev/null || /bin/bash $DEST/run_catcher.sh') | crontab -"
$SSH $PI_USER@$PI_HOST "crontab -l 2>/dev/null | grep -q "pgrep -f 'cloudflared tunnel'" || (crontab -l; echo "* * * * * pgrep -f 'cloudflared tunnel' >/dev/null || nohup $DEST/cloudflared tunnel --config $DEST/config.yml run aali >> $DEST/tunnel.log 2>&1 &") | crontab -"

echo "== done =="
echo "Start now (no reboot needed):"
echo "  $SSH $PI_USER@$PI_HOST 'nohup /bin/bash $DEST/run_catcher.sh >/dev/null 2>&1 & nohup $DEST/cloudflared tunnel --config $DEST/config.yml run aali >> $DEST/tunnel.log 2>&1 &'"
echo "Then verify: curl -s http://$PI_HOST:8766/_sentinel/ping  (via an ssh port-forward)"
