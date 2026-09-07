#!/usr/bin/env bash
# Aali server bootstrap — Oracle Cloud Always Free Ubuntu (aarch64).
# Run ON the VM right after you create it. Requires: nothing pre-installed.
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/WoodbutcherTh1/HWK-Aali/master/deploy/oracle/setup_aali_server.sh)
#
# What it does: installs Docker -> opens the firewall -> clones this repo ->
# generates your private AALI_API_KEY -> builds and starts the server ->
# waits for health -> prints your public URL.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/WoodbutcherTh1/HWK-Aali.git}"
SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

echo "[1/6] Installing Docker + git…"
export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null 2>&1; then
  $SUDO apt-get update -y
  $SUDO apt-get install -y docker.io docker-compose-v2 git
  $SUDO usermod -aG docker "$USER" || true
fi
$SUDO systemctl enable --now docker

echo "[2/6] Opening the firewall for Aali (SSH stays open)…"
$SUDO ufw allow OpenSSH >/dev/null 2>&1 || true
$SUDO ufw allow 5055/tcp >/dev/null 2>&1 || true
$SUDO ufw --force enable >/dev/null 2>&1 || true

echo "[3/6] Cloning the repo…"
mkdir -p "$HOME/aali" && cd "$HOME/aali"
if [ ! -d HWK-Aali ]; then
  git clone --depth 1 "$REPO_URL"
fi
cd HWK-Aali
git pull --ff-only || true

echo "[4/6] Generating your private access key…"
if [ ! -f .env ]; then
  KEY="aali-$(openssl rand -hex 16)"
  printf 'AALI_API_KEY=%s\n' "$KEY" > .env
  chmod 600 .env
else
  KEY="$(grep '^AALI_API_KEY=' .env | cut -d= -f2)"
fi

echo "[5/6] Building and starting the server (first build ~2-3 min)…"
$SUDO docker compose up -d --build

echo "[6/6] Waiting for the health check…"
for _ in $(seq 1 60); do
  if curl -sf -H "X-API-Key: $KEY" http://127.0.0.1:5055/api/health >/dev/null 2>&1; then
    PUBLIC_IP="$(curl -s --max-time 10 ifconfig.me || echo '<VM_PUBLIC_IP>')"
    echo ""
    echo "==============================================================="
    echo " Aali is LIVE"
    echo "   Web app : http://$PUBLIC_IP:5055/ui/"
    echo "   API     : http://$PUBLIC_IP:5055/api/ask   (POST JSON)"
    echo "   CLI     : python aali_cli.py --server http://$PUBLIC_IP:5055"
    echo "   Key     : $KEY   (clients send it as the X-API-Key header;"
    echo "             it is also in ~/aali/HWK-Aali/.env — keep it private)"
    echo "==============================================================="
    echo "Reminder: also add TCP 5055 ingress in the Oracle web console"
    echo "(VCN > Security Lists) — the OS firewall alone is not enough."
    exit 0
  fi
  sleep 5
done

echo "Health check timed out — watch the build with:  $SUDO docker compose logs -f"
exit 1
