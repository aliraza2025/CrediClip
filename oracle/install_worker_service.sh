#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_SRC="${1:-$ROOT_DIR/oracle/crediclip-worker.service}"
ENV_SRC="${2:-$ROOT_DIR/oracle/crediclip-worker.env.example}"
SERVICE_DST="/etc/systemd/system/crediclip-worker.service"
ENV_DST="/etc/default/crediclip-worker"

if [[ ! -f "$SERVICE_SRC" ]]; then
  echo "Missing service template: $SERVICE_SRC" >&2
  exit 1
fi

if [[ ! -f "$ENV_SRC" ]]; then
  echo "Missing env template: $ENV_SRC" >&2
  exit 1
fi

echo "[1/5] Installing systemd unit..."
sudo cp "$SERVICE_SRC" "$SERVICE_DST"
sudo chown root:root "$SERVICE_DST"
sudo chmod 644 "$SERVICE_DST"

echo "[2/5] Installing worker env file..."
if [[ -f "$ENV_DST" ]]; then
  echo "Existing $ENV_DST kept as-is."
  echo "Edit it manually if you need different values."
else
  sudo cp "$ENV_SRC" "$ENV_DST"
  sudo chown root:root "$ENV_DST"
  sudo chmod 644 "$ENV_DST"
  echo "Created $ENV_DST from template."
fi

echo "[3/5] Reloading systemd..."
sudo systemctl daemon-reload

echo "[4/5] Enabling + restarting worker service..."
sudo systemctl enable crediclip-worker
sudo systemctl restart crediclip-worker

echo "[5/5] Service status:"
sudo systemctl --no-pager status crediclip-worker | sed -n '1,80p'
echo
echo "Tail logs:"
echo "  sudo journalctl -u crediclip-worker -f"
