#!/bin/bash
# MoonKnight Worker Daemon — Linux Installer
#
# Usage:
#   curl -fsSL https://platform.example.com/install.sh | bash

set -e

echo "==========================================="
echo "   MoonKnight Worker Daemon Installer"
echo "==========================================="

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (use sudo)"
  exit 1
fi

# 1. Check OS
if [ "$(uname)" != "Linux" ]; then
    echo "Error: This installer is for Linux only."
    exit 1
fi

# 2. Install prerequisites
echo "[1/6] Installing dependencies..."
if command -v apt-get >/dev/null; then
    apt-get update -qq
    apt-get install -y python3 python3-pip python3-venv curl pciutils
elif command -v dnf >/dev/null; then
    dnf install -y python3 python3-pip curl pciutils
else
    echo "Unsupported package manager. Please install Python 3 and curl manually."
    exit 1
fi

# 3. Check for NVIDIA GPU
if lspci | grep -i nvidia >/dev/null; then
    echo "NVIDIA GPU detected."
    if ! command -v nvidia-smi >/dev/null; then
        echo "WARNING: nvidia-smi not found. Please ensure NVIDIA drivers are installed."
    fi
else
    echo "WARNING: No NVIDIA GPU detected. The daemon will run but models will be slow."
fi

# 4. Install Ollama if not present
if ! command -v ollama >/dev/null; then
    echo "[2/6] Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "[2/6] Ollama already installed."
fi

# 5. Setup daemon directory
echo "[3/6] Setting up daemon directory..."
DAEMON_USER="gpu-daemon"
DAEMON_DIR="/opt/gpu-daemon"

if ! id -u "$DAEMON_USER" >/dev/null 2>&1; then
    useradd -r -s /bin/false "$DAEMON_USER"
fi

mkdir -p "$DAEMON_DIR"
chown "$DAEMON_USER:$DAEMON_USER" "$DAEMON_DIR"

# 6. Prompt for configuration
echo ""
echo "[4/6] Configuration"
read -p "Enter Platform URL (e.g. http://api.example.com): " BACKEND_URL
read -p "Enter your Provider ID: " PROVIDER_ID
read -p "Enter unique Worker ID [leave blank to auto-generate]: " WORKER_ID

cat <<EOF > "$DAEMON_DIR/config.yaml"
backend_url: "$BACKEND_URL"
provider_id: "$PROVIDER_ID"
worker_id: "$WORKER_ID"
runtime: "ollama"
EOF
chown "$DAEMON_USER:$DAEMON_USER" "$DAEMON_DIR/config.yaml"

echo "[5/6] Creating Python virtual environment..."
python3 -m venv "$DAEMON_DIR/venv"
# Here we'd normally pip install the package or git clone.
# For now, we assume the code will be placed in $DAEMON_DIR.

echo "[6/6] Setting up systemd service..."
# Service file would be copied here.

echo "==========================================="
echo "Installation complete!"
echo "To start the daemon: sudo systemctl start gpu-daemon"
echo "To check logs: sudo journalctl -u gpu-daemon -f"
echo "==========================================="
