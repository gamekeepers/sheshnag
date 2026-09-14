#!/bin/bash
# Sheshnag Worker Daemon — Rootless Linux Installer
#
# Installs everything under ~/.gpu-daemon for a non-privileged user —
# no sudo required at any step. Services run via `systemctl --user`.
#
# Usage (interactive):
#   curl -fsSL https://platform.example.com/install.sh | bash
#
# Usage (non-interactive, e.g. automation / no TTY):
#   BACKEND_URL=http://api.example.com API_KEY=gk-... bash install.sh
#
# Usage (several machines sharing one home directory, e.g. NFS):
#   INSTANCE=$(hostname -s) bash install.sh
#
# A shared home is detected automatically and the default install directory
# becomes host-specific, so the INSTANCE form above is only needed when
# detection cannot see it. See "Shared home directories" in docs/provider.md.
#
# Everything lives inside main() on purpose — see the note above the call at
# the bottom of the file. Do not move code out of it.

set -euo pipefail

main() {
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
  # terminal so prompts work. /dev/tty must be *openable*, not just present:
  # in a container or a CI runner it often is not.
  INTERACTIVE=1
  if [ ! -t 0 ]; then
    if { exec < /dev/tty; } 2>/dev/null && [ -t 0 ]; then
      :  # prompts now read from the terminal
    else
      INTERACTIVE=0
    fi
  fi

  # Nothing to prompt with and nothing preset — say so instead of hanging or
  # reading the rest of the pipe as if it were an answer.
  if [ "$INTERACTIVE" -eq 0 ] && { [ -z "${BACKEND_URL:-}" ] || [ -z "${API_KEY:-}" ]; }; then
    echo "No TTY available. Re-run with BACKEND_URL=... API_KEY=... (and optional WORKER_ID=...) set."
    exit 1
  fi

  # ── Helpers ────────────────────────────────────────────────────────────

  # Ask a question, optionally with a default the user accepts by pressing
  # Enter. An empty answer to a question that has no default asks again:
  # accepting one used to write backend_url: "" into config.yaml and fail
  # much later, at daemon startup, with nothing pointing back to this prompt.
  ask() {   # $1=question  $2=default ("" means required)
    local question="$1" default="${2:-}" reply=""
    while :; do
      if [ -n "$default" ]; then
        read -rp "$question [$default]: " reply
        reply="${reply:-$default}"
      else
        read -rp "$question: " reply
      fi
      [ -n "$reply" ] && break
      echo "  Required — please enter a value." >&2
    done
    printf '%s' "$reply"
  }

  # Is $HOME on a filesystem several machines plausibly mount at once? Common
  # on clusters, and the reason the default install directory moves.
  home_is_shared() {
    case "$(stat -f -c %T "$HOME" 2>/dev/null)" in
      nfs|nfs4|cifs|smb|smb2|smb3|lustre|gpfs|beegfs|afs|glusterfs|ceph|ocfs2) return 0 ;;
      *) return 1 ;;
    esac
  }

  # Unit names follow the directory, so a second machine cannot collide even
  # if it picks its own path: .gpu-daemon → "", .gpu-daemon-<x> → <x>,
  # anything else → its own basename.
  instance_from_dir() {
    local base; base="$(basename "$1")"; base="${base#.}"
    case "$base" in
      gpu-daemon)   printf '' ;;
      gpu-daemon-*) printf '%s' "${base#gpu-daemon-}" ;;
      *)            printf '%s' "$base" ;;
    esac
  }

  validate_instance() {
    case "$1" in
      *[!A-Za-z0-9._-]*)
        echo "Instance name may only contain letters, digits, dot, underscore and dash (got '$1')."
        exit 1 ;;
    esac
  }

  REPO_URL="${REPO_URL:-https://github.com/gamekeepers/sheshnag.git}"

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

  # ── Where this machine installs ────────────────────────────────────────
  # Deliberately after the prerequisite check: these use hostname/stat/tr, and
  # a box missing them should get the "Missing:" message above, not a bare 127.

  HOSTTAG="$(hostname -s 2>/dev/null || uname -n | cut -d. -f1)"
  HOSTTAG="$(printf '%s' "$HOSTTAG" | tr -c 'A-Za-z0-9._-' '-')"

  INSTANCE="${INSTANCE:-}"
  SHARED_HOME_NOTE=""
  if [ -n "$INSTANCE" ]; then
    validate_instance "$INSTANCE"
    DEFAULT_DIR="$HOME/.gpu-daemon-$INSTANCE"
  elif home_is_shared; then
    # One home across several machines means one config, one virtual
    # environment, and — worst — one credentials file holding a single worker
    # id. Default to a directory this machine owns.
    DEFAULT_DIR="$HOME/.gpu-daemon-$HOSTTAG"
    SHARED_HOME_NOTE="Your home directory is on $(stat -f -c %T "$HOME" 2>/dev/null), so it is probably shared between machines."
  else
    DEFAULT_DIR="$HOME/.gpu-daemon"
  fi

  DIR_FROM_ENV=0
  [ -n "${DAEMON_DIR:-}" ] && DIR_FROM_ENV=1
  DAEMON_DIR="${DAEMON_DIR:-$DEFAULT_DIR}"

  # 2. Configuration — every question is asked here, before the long steps,
  #    so the install can be left alone once it starts.
  echo ""
  echo "[2/6] Configuration"
  [ -n "$SHARED_HOME_NOTE" ] && echo "$SHARED_HOME_NOTE"

  if [ "$INTERACTIVE" -eq 1 ] && [ "$DIR_FROM_ENV" -eq 0 ]; then
    DAEMON_DIR="$(ask "Install directory" "$DAEMON_DIR")"
    DAEMON_DIR="${DAEMON_DIR/#\~/$HOME}"   # read does not expand a leading ~
  fi

  # Keep unit names in step with whatever directory we ended up with.
  INSTANCE="$(instance_from_dir "$DAEMON_DIR")"
  if [ -n "$INSTANCE" ]; then
    validate_instance "$INSTANCE"
    DAEMON_UNIT="gpu-daemon-$INSTANCE"
    OLLAMA_UNIT="ollama-$INSTANCE"
  else
    DAEMON_UNIT="gpu-daemon"
    OLLAMA_UNIT="ollama"
  fi

  # An install directory belongs to exactly one machine. Nothing in the
  # credentials file records which, so without this marker a second machine
  # sharing the home would quietly overwrite the first one's config and its
  # assigned worker id.
  MARKER="$DAEMON_DIR/installed-by"
  if [ -f "$MARKER" ]; then
    PREV_HOST="$(head -n1 "$MARKER" 2>/dev/null || true)"
    if [ -n "$PREV_HOST" ] && [ "$PREV_HOST" != "$HOSTTAG" ]; then
      echo ""
      echo "ERROR: $DAEMON_DIR was installed by '$PREV_HOST', not this machine ('$HOSTTAG')."
      echo "Sharing one directory means sharing one worker identity, which makes"
      echo "batches sent to one machine run on the other."
      echo "Give this machine its own directory, e.g.:"
      echo "    INSTANCE=$HOSTTAG bash install.sh"
      exit 1
    fi
  fi
  mkdir -p "$DAEMON_DIR"
  printf '%s\n' "$HOSTTAG" > "$MARKER"

  echo "Installing to $DAEMON_DIR as $DAEMON_UNIT.service"

  if [ -z "${BACKEND_URL:-}" ]; then
    BACKEND_URL="$(ask "Enter Platform URL (e.g. https://sheshnag.example.edu)")"
  fi
  if [ -z "${API_KEY:-}" ]; then
    echo "Create an org worker API key in the platform dashboard (Provider portal → Worker keys)."
    API_KEY="$(ask "Enter your org worker API key (gk-...)")"
  fi
  if [ -z "${WORKER_ID+x}" ]; then
    if [ "$INTERACTIVE" -eq 1 ]; then
      read -rp "Enter unique Worker ID [leave blank to auto-generate]: " WORKER_ID
    else
      WORKER_ID=""   # non-interactive: let the control plane assign one
    fi
  fi

  # credentials_path and work_dir are written explicitly, always. The daemon
  # defaults both to ~/.gpu-daemon/... in code (config.py), and credentials_path
  # has no environment override — so on a shared home an instance install would
  # otherwise still read and write one machine's worker id for both.
  {
    echo "backend_url: \"$BACKEND_URL\""
    echo "api_key: \"$API_KEY\""
    [ -n "${WORKER_ID:-}" ] && echo "worker_id: \"$WORKER_ID\""
    echo "runtime: \"ollama\""
    echo "credentials_path: \"$DAEMON_DIR/credentials\""
    echo "work_dir: \"$DAEMON_DIR/jobs\""
  } > "$DAEMON_DIR/config.yaml"
  chmod 600 "$DAEMON_DIR/config.yaml"   # contains the API key

  # The units refer to the install directory through %h wherever they can, so
  # keep expressing it relative to home. With the default directory this
  # substitution is a no-op and the shipped unit is copied unchanged.
  case "$DAEMON_DIR" in
    "$HOME"/*) UNIT_DIR="%h/${DAEMON_DIR#"$HOME"/}" ;;
    *)         UNIT_DIR="$DAEMON_DIR" ;;
  esac

  # 3. Ollama — user-local install when not already on PATH.
  #
  # OLLAMA_USER_LOCAL decides whether we manage an ollama unit further down. A
  # system Ollama already owns 127.0.0.1:11434, and starting a second copy
  # under this user just makes the two restart-loop against each other.
  echo "[3/6] Checking Ollama..."
  mkdir -p "$DAEMON_DIR/bin"
  OLLAMA_USER_LOCAL=0
  if command -v ollama >/dev/null 2>&1; then
      echo "Ollama already installed (system) — leaving it to manage itself."
  elif [ -x "$DAEMON_DIR/bin/ollama" ]; then
      echo "Ollama already installed (user-local)."
      OLLAMA_USER_LOCAL=1
  else
      echo "Installing Ollama into $DAEMON_DIR (no root needed)..."
      # The tarball ships bin/ollama plus lib/ (needed for GPU support);
      # extract the whole tree under the install directory.
      if curl -fL "https://ollama.com/download/ollama-linux-amd64.tgz" -o "$DAEMON_DIR/ollama.tgz"; then
          tar -xzf "$DAEMON_DIR/ollama.tgz" -C "$DAEMON_DIR"
          rm -f "$DAEMON_DIR/ollama.tgz"
          chmod +x "$DAEMON_DIR/bin/ollama"
          OLLAMA_USER_LOCAL=1
      else
          echo "ERROR: Ollama download failed — install it manually, then re-run."
          exit 1
      fi
  fi

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

      # Rewrite the shipped units for this instance: install directory, the
      # ollama unit gpu-daemon orders itself after, and a Description that says
      # which machine it belongs to. With the default directory every
      # substitution is identity and the result matches the file in the
      # repository byte for byte.
      install_unit() {
          sed -e "s|%h/\.gpu-daemon|$UNIT_DIR|g" \
              -e "s|ollama\.service|$OLLAMA_UNIT.service|g" \
              -e "${INSTANCE:+s|^Description=.*|& [$INSTANCE]|}" \
              "$1" > "$HOME/.config/systemd/user/$2.service"
      }

      install_unit "$DAEMON_DIR/src/scripts/gpu-daemon.service" "$DAEMON_UNIT"

      UNITS="$DAEMON_UNIT"
      if [ "$OLLAMA_USER_LOCAL" -eq 1 ]; then
          install_unit "$DAEMON_DIR/src/scripts/ollama.service" "$OLLAMA_UNIT"
          UNITS="$OLLAMA_UNIT $DAEMON_UNIT"
      fi

      systemctl --user daemon-reload
      # shellcheck disable=SC2086  # UNITS is a deliberate word list
      systemctl --user enable --now $UNITS

      # Keep services running after logout. May require auth on some
      # distros — harmless if it fails, the daemon then runs only while
      # this user has a session.
      if ! loginctl enable-linger "$USER" 2>/dev/null; then
          echo "NOTE: enable-linger failed — daemon stops at logout."
          echo "      Ask an admin to run: sudo loginctl enable-linger $USER"
      fi

      # Report what systemd actually did rather than assuming success — a unit
      # that fails to start would otherwise be hidden behind a cheerful banner.
      sleep 2
      DAEMON_STATE="$(systemctl --user is-active "$DAEMON_UNIT" 2>/dev/null || true)"
      echo "==========================================="
      if [ "$DAEMON_STATE" = "active" ]; then
          echo "Installation complete — $DAEMON_UNIT is running."
      else
          echo "Installation finished, but $DAEMON_UNIT is '$DAEMON_STATE'."
          echo "Check the logs below before assuming the worker registered."
      fi
      echo "Status: systemctl --user status $DAEMON_UNIT"
      echo "Logs:   journalctl --user -u $DAEMON_UNIT -f"
      echo "==========================================="
  else
      echo "No systemd user session available — start the daemon manually:"
      echo "  cd $DAEMON_DIR/src/daemon && nohup $DAEMON_DIR/venv/bin/python -m daemon.main --config $DAEMON_DIR/config.yaml >> $DAEMON_DIR/daemon.log 2>&1 &"
  fi
}

# Call main on one line with the exit, and keep it last.
#
# Under `curl ... | bash` the shell reads this file from the same stdin the
# script later hands to the terminal. Because a function body must be parsed
# in full before it can run, wrapping everything above means bash has already
# consumed the script by the time `exec < /dev/tty` swaps stdin — without the
# wrapper it would sit waiting for the rest of the script to be typed, which
# is what made the piped install hang forever.
#
# The trailing `exit` matters for the same reason: once stdin is the terminal,
# anything bash still had left to read would be read from the keyboard. Having
# it on this line means it is parsed alongside the call, before main runs.
main "$@"; exit $?
