#!/bin/bash
# MoonKnight Worker Daemon — Rootless Linux Installer
#
# Installs everything under ~/.gpu-daemon for a non-privileged user —
# no sudo required at any step. Services run via `systemctl --user`.
#
# Usage (interactive):
#   curl -fsSL https://platform.example.com/install.sh | bash
#
# Usage (non-interactive, e.g. automation / no TTY):
#   BACKEND_URL=http://api.example.com API_KEY=gk-... bash install.sh

set -euo pipefail

echo "==========================================="
echo "   Sheshnag GPU Worker Daemon Installer"
echo "==========================================="

if [ "$EUID" -eq 0 ]; then
  echo "Do NOT run as root — this installs into your home directory."
  echo "Re-run as the (non-sudo) user that will host the daemon."
  exit 1
fi

if [ "$(uname)" != "Linux" ]; then
    echo "Error: This installer is for Linux only."
    exit 1
fi

# Under `curl ... | bash`, stdin is the script itself — reattach to the
# terminal so prompts work. Only needed when we will actually prompt
# (env vars not set), and /dev/tty must be *openable*, not just present.
if [ ! -t 0 ] && { [ -z "${BACKEND_URL:-}" ] || [ -z "${API_KEY:-}" ]; }; then
  if { exec < /dev/tty; } 2>/dev/null; then
    :  # prompts now read from the terminal
  else
    echo "No TTY available. Re-run with BACKEND_URL=... API_KEY=... (and optional WORKER_ID=...) set."
    exit 1
  fi
fi

DAEMON_DIR="$HOME/.gpu-daemon"
REPO_URL="${REPO_URL:-https://github.com/gamekeepers/moonknight.git}"

# 1. Prerequisites — check only; installing them needs an admin.
echo "[1/6] Checking prerequisites..."
missing=""
for cmd in python3 git curl; do
  command -v "$cmd" >/dev/null 2>&1 || missing="$missing $cmd"
done
if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PY' || missing="$missing python3>=3.10"
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
fi
if [ -n "$missing" ]; then
  echo "Missing:$missing"
  echo "Ask an admin to run: sudo apt-get install -y python3 python3-venv python3-pip git curl"
  exit 1
fi

# GPU check — advisory only
GPU_TOOLING=""
if command -v nvidia-smi >/dev/null 2>&1; then
    echo "NVIDIA GPU tooling detected (nvidia-smi)."
    GPU_TOOLING="nvidia"
fi
if command -v rocm-smi >/dev/null 2>&1; then
    ROCM_VER="$(sed -n '1s/^\([0-9.]*\).*/\1/p' /opt/rocm/.info/version 2>/dev/null)"
    echo "AMD GPU tooling detected (rocm-smi${ROCM_VER:+, ROCm $ROCM_VER})."
    GPU_TOOLING="${GPU_TOOLING:+$GPU_TOOLING+}amd"
fi
if [ -z "$GPU_TOOLING" ]; then
    echo "WARNING: neither nvidia-smi nor rocm-smi found. The daemon will run but report no GPU."
fi

# 2. Ollama — user-local install when not already on PATH.
echo "[2/6] Checking Ollama..."
mkdir -p "$DAEMON_DIR/bin"
if command -v ollama >/dev/null 2>&1; then
    echo "Ollama already installed (system)."
elif [ -x "$DAEMON_DIR/bin/ollama" ]; then
    echo "Ollama already installed (user-local)."
else
    echo "Installing Ollama into $DAEMON_DIR (no root needed)..."
    # The tarball ships bin/ollama plus lib/ (needed for GPU support);
    # extract the whole tree under ~/.gpu-daemon.
    if curl -fL "https://ollama.com/download/ollama-linux-amd64.tgz" -o "$DAEMON_DIR/ollama.tgz"; then
        tar -xzf "$DAEMON_DIR/ollama.tgz" -C "$DAEMON_DIR"
        rm -f "$DAEMON_DIR/ollama.tgz"
        chmod +x "$DAEMON_DIR/bin/ollama"
    else
        echo "ERROR: Ollama download failed — install it manually, then re-run."
        exit 1
    fi
fi

# 3. Configuration
echo ""
echo "[3/6] Configuration"
[ -z "${BACKEND_URL:-}" ] && read -rp "Enter Platform URL (e.g. http://api.example.com): " BACKEND_URL
if [ -z "${API_KEY:-}" ]; then
  echo "Create an org worker API key in the platform dashboard (Provider portal → API keys)."
  read -rp "Enter your org worker API key (gk-...): " API_KEY
fi
if [ -z "${WORKER_ID+x}" ]; then
  read -rp "Enter unique Worker ID [leave blank to auto-generate]: " WORKER_ID
fi

{
  echo "backend_url: \"$BACKEND_URL\""
  echo "api_key: \"$API_KEY\""
  [ -n "${WORKER_ID:-}" ] && echo "worker_id: \"$WORKER_ID\""
  echo "runtime: \"ollama\""
} > "$DAEMON_DIR/config.yaml"
chmod 600 "$DAEMON_DIR/config.yaml"   # contains the API key

# 4. Daemon code + Python environment
echo "[4/6] Installing daemon code..."
if [ ! -d "$DAEMON_DIR/src/.git" ]; then
    git clone --depth 1 "$REPO_URL" "$DAEMON_DIR/src"
else
    git -C "$DAEMON_DIR/src" pull --ff-only
fi

echo "[5/6] Creating Python virtual environment..."
python3 -m venv "$DAEMON_DIR/venv"
"$DAEMON_DIR/venv/bin/pip" install -q --upgrade pip
"$DAEMON_DIR/venv/bin/pip" install -q -r "$DAEMON_DIR/src/daemon/requirements.txt"

# 5. systemd user services (no root; survives logout via linger)
echo "[6/6] Setting up user services..."
if systemctl --user show-environment >/dev/null 2>&1; then
    mkdir -p "$HOME/.config/systemd/user"
    cp "$DAEMON_DIR/src/scripts/gpu-daemon.service" "$HOME/.config/systemd/user/"
    cp "$DAEMON_DIR/src/scripts/ollama.service"     "$HOME/.config/systemd/user/"
    systemctl --user daemon-reload
    systemctl --user enable --now ollama gpu-daemon

    # Keep services running after logout. May require auth on some
    # distros — harmless if it fails, the daemon then runs only while
    # this user has a session.
    if ! loginctl enable-linger "$USER" 2>/dev/null; then
        echo "NOTE: enable-linger failed — daemon stops at logout."
        echo "      Ask an admin to run: sudo loginctl enable-linger $USER"
    fi

    echo "==========================================="
    echo "Installation complete!"
    echo "Status: systemctl --user status gpu-daemon"
    echo "Logs:   journalctl --user -u gpu-daemon -f"
    echo "==========================================="
else
    echo "No systemd user session available — start the daemon manually:"
    echo "  cd $DAEMON_DIR/src/daemon && nohup $DAEMON_DIR/venv/bin/python -m daemon.main --config $DAEMON_DIR/config.yaml >> $DAEMON_DIR/daemon.log 2>&1 &"
fi
