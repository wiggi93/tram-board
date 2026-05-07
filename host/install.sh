#!/usr/bin/env bash
# Installs the host-side LED driver for tram-board.
#
# What it does:
#   1. Builds and installs the rpi-rgb-led-matrix Python bindings (pinned
#      to a known-good commit) — only if rgbmatrix isn't already importable.
#   2. Installs python3-requests.
#   3. Downloads led-driver.py + fonts to /opt/tram-board-led/.
#   4. Downloads and enables the tram-board-led systemd service.
#
# Usage (from anywhere, including curl | sudo bash):
#   curl -fsSL https://raw.githubusercontent.com/wiggi93/tram-board/master/host/install.sh | sudo bash
#
# After running, the panel comes alive within a few seconds of the Docker
# container being up. Tune panel options with `sudo systemctl edit
# tram-board-led`, then `sudo systemctl restart tram-board-led`.

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "Please run with sudo." >&2
  exit 1
fi

INSTALL_DIR="/opt/tram-board-led"
REPO_RAW="https://raw.githubusercontent.com/wiggi93/tram-board/master"
RGBMATRIX_REF="2f72a32b3deea16d2b8e9b281d0475ef3b1d0d72"   # last upstream commit before the Pillow shim

fetch() {
  # fetch <relative-path-in-repo> <destination> <mode>
  curl -fsSL "$REPO_RAW/$1" -o "$2"
  chmod "$3" "$2"
}

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

echo "▶ Installing python3-requests..."
apt-get install -y python3-requests

echo "▶ Downloading driver + fonts to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR/fonts"
fetch host/led-driver.py "$INSTALL_DIR/led-driver.py" 0755
fetch fonts/4x6.bdf      "$INSTALL_DIR/fonts/4x6.bdf"  0644
fetch fonts/7x14.bdf     "$INSTALL_DIR/fonts/7x14.bdf" 0644

echo "▶ Installing systemd unit..."
fetch host/tram-board-led.service /etc/systemd/system/tram-board-led.service 0644
systemctl daemon-reload
systemctl enable --now tram-board-led.service

echo
echo "✓ Done. The LED driver is running and will start on every boot."
echo "  Status:        systemctl status tram-board-led"
echo "  Live logs:     journalctl -u tram-board-led -f"
echo "  Tune options:  sudo systemctl edit tram-board-led   (then 'sudo systemctl restart tram-board-led')"
