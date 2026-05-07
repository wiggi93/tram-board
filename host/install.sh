#!/usr/bin/env bash
# Installs the host-side LED driver for tram-board.
#
# What it does:
#   1. Builds and installs the rpi-rgb-led-matrix Python bindings (pinned
#      to a known-good commit) — only if rgbmatrix isn't already importable.
#   2. Copies led-driver.py + fonts/ to /opt/tram-board-led/.
#   3. Installs and enables the tram-board-led systemd service.
#
# Usage:  sudo ./install.sh
#
# After running, the panel should come alive within a few seconds of the
# Docker container being up. Edit /etc/systemd/system/tram-board-led.service
# (or use `systemctl edit tram-board-led`) to tune the panel env vars,
# then `systemctl restart tram-board-led`.

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "Please run with sudo." >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="/opt/tram-board-led"
RGBMATRIX_REF="2f72a32b3deea16d2b8e9b281d0475ef3b1d0d72"   # last upstream commit before the Pillow shim

echo "▶ Installing rpi-rgb-led-matrix (if not already)..."
if python3 -c "import rgbmatrix" 2>/dev/null; then
  echo "  ✓ rgbmatrix already importable, skipping build."
else
  apt-get update
  apt-get install -y --no-install-recommends git build-essential python3-dev python3-pip cython3
  TMP="$(mktemp -d)"
  git clone https://github.com/hzeller/rpi-rgb-led-matrix.git "$TMP/matrix"
  ( cd "$TMP/matrix" && git checkout "$RGBMATRIX_REF" \
                     && make build-python PYTHON="$(which python3)" \
                     && make install-python PYTHON="$(which python3)" )
  rm -rf "$TMP"
fi

echo "▶ Installing python-requests (for /api/state polling)..."
apt-get install -y python3-requests

echo "▶ Copying driver + fonts to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
install -m 0755 "$REPO_DIR/host/led-driver.py" "$INSTALL_DIR/led-driver.py"
mkdir -p "$INSTALL_DIR/fonts"
install -m 0644 "$REPO_DIR/fonts/4x6.bdf"  "$INSTALL_DIR/fonts/"
install -m 0644 "$REPO_DIR/fonts/7x14.bdf" "$INSTALL_DIR/fonts/"

echo "▶ Installing systemd unit..."
install -m 0644 "$REPO_DIR/host/tram-board-led.service" /etc/systemd/system/tram-board-led.service
systemctl daemon-reload
systemctl enable --now tram-board-led.service

echo
echo "✓ Done. The LED driver is running and will start on every boot."
echo "  Check it with:   systemctl status tram-board-led"
echo "  Tail the logs:   journalctl -u tram-board-led -f"
