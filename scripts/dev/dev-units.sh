#!/usr/bin/env bash
# Install and drive the Sheshnag dev stack as systemd --user units.
#
#   scripts/dev/dev-units.sh install     # template + install units
#   scripts/dev/dev-units.sh up          # start all three
#   scripts/dev/dev-units.sh down        # stop all three
#   scripts/dev/dev-units.sh restart     # bounce all three
#   scripts/dev/dev-units.sh status      # what is up, and on which ports
#   scripts/dev/dev-units.sh logs        # follow all three, interleaved
#   scripts/dev/dev-units.sh logs backend
#   scripts/dev/dev-units.sh uninstall   # stop + remove units
#
# `up`/`down`/etc. are thin wrappers — plain systemctl works just as well:
#   systemctl --user start sheshnag-dev.target
#
# Rootless by design, matching scripts/install.sh: no sudo at any step.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNITS=(
  sheshnag-dev.target
  sheshnag-backend-dev.service
  sheshnag-frontend-dev.service
  sheshnag-daemon-dev.service
)
SERVICES=(
  sheshnag-backend-dev.service
  sheshnag-frontend-dev.service
  sheshnag-daemon-dev.service
)

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
warn() { printf '\033[33mwarn:\033[0m %s\n' "$*" >&2; }
info() { printf '\033[32m==>\033[0m %s\n' "$*"; }

cmd_install() {
  # Fail loudly at install time rather than with an opaque status=203 later.
  [[ -x "$REPO/.venv/bin/python" ]] \
    || die "missing $REPO/.venv — create it and 'pip install -r backend/requirements.txt'"
  [[ -x "$REPO/daemon/.venv/bin/python" ]] \
    || die "missing $REPO/daemon/.venv — create it and 'pip install -r daemon/requirements.txt'"
  [[ -d "$REPO/node_modules" ]] \
    || warn "no node_modules/ — run 'npm install' or the frontend unit will fail"

  local npm_bin node_dir
  npm_bin="$(command -v npm)" || die "npm not on PATH"
  node_dir="$(dirname "$npm_bin")"

  [[ -f "$REPO/backend/.env" ]] \
    || warn "no backend/.env — copy backend/.env.example and edit it"
  if [[ ! -f "$REPO/daemon/.env" && ! -f "$HOME/.gpu-daemon/credentials" ]]; then
    warn "no daemon/.env and no saved ~/.gpu-daemon/credentials — the daemon"
    warn "  needs DAEMON_API_KEY to register."
    warn "  cp daemon/.env.example daemon/.env  # then paste your gk-... key"
  fi

  # Documented in CONTRIBUTING.md: pytest-asyncio lives in the [dev] extra,
  # so a daemon venv built from requirements.txt alone cannot run the suite.
  if [[ -x "$REPO/daemon/.venv/bin/python" ]] \
     && ! "$REPO/daemon/.venv/bin/python" -c "import pytest_asyncio" 2>/dev/null; then
    warn "daemon/.venv cannot run tests (no pytest-asyncio)."
    warn "  fix: cd daemon && .venv/bin/pip install -e \".[dev]\""
  fi

  mkdir -p "$UNIT_DIR"
  local u
  for u in "${UNITS[@]}"; do
    sed -e "s|@REPO@|$REPO|g" -e "s|@NODE_BIN@|$node_dir|g" \
      "$REPO/scripts/dev/$u" > "$UNIT_DIR/$u"
  done
  systemctl --user daemon-reload

  info "installed ${#UNITS[@]} units into $UNIT_DIR"
  info "  repo: $REPO"
  info "  node: $node_dir"
  echo
  info "start with: scripts/dev/dev-units.sh up"
  if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]]; then
    warn "linger is off — services stop when you log out. Fine for a laptop;"
    warn "  on a shared dev box run: loginctl enable-linger \"\$USER\""
  fi
}

cmd_uninstall() {
  systemctl --user stop "${SERVICES[@]}" sheshnag-dev.target 2>/dev/null || true
  systemctl --user disable sheshnag-dev.target 2>/dev/null || true
  local u
  for u in "${UNITS[@]}"; do rm -f "$UNIT_DIR/$u"; done
  systemctl --user daemon-reload
  info "removed units from $UNIT_DIR"
}

require_installed() {
  [[ -f "$UNIT_DIR/sheshnag-dev.target" ]] \
    || die "units not installed — run: scripts/dev/dev-units.sh install"
}

cmd_up() { require_installed; systemctl --user start sheshnag-dev.target; cmd_status; }

# Stop the services by name, not just the target. `systemctl stop <target>`
# returns as soon as the *target* job finishes — the PartOf= propagation to
# the three services is queued separately, so it would report "stopped" while
# uvicorn still held port 8005, and a following `up` would race it.
cmd_down() {
  require_installed
  systemctl --user stop "${SERVICES[@]}" sheshnag-dev.target
  info "stopped"
}

cmd_restart() { require_installed; cmd_down; cmd_up; }

cmd_status() {
  require_installed
  local svc state
  printf '\n%-32s %-10s %s\n' SERVICE STATE DETAIL
  for svc in "${SERVICES[@]}"; do
    state="$(systemctl --user is-active "$svc" 2>/dev/null || true)"
    printf '%-32s %-10s %s\n' "$svc" "$state" \
      "$(systemctl --user show "$svc" -p MainPID --value 2>/dev/null | sed 's/^0$/-/;s/^/pid /')"
  done
  echo
  # Ports are the check that matters — "active" only means the process started.
  local p
  for p in 8005:backend 3005:frontend; do
    if (exec 3<>"/dev/tcp/127.0.0.1/${p%%:*}") 2>/dev/null; then
      printf '  port %-5s %s listening\n' "${p%%:*}" "${p##*:}"
    else
      printf '  port %-5s %s not listening\n' "${p%%:*}" "${p##*:}"
    fi
  done
}

cmd_logs() {
  require_installed
  if [[ $# -gt 0 ]]; then
    journalctl --user -u "sheshnag-$1-dev.service" -f -n 100
  else
    journalctl --user -f -n 50 \
      -u sheshnag-backend-dev.service \
      -u sheshnag-frontend-dev.service \
      -u sheshnag-daemon-dev.service
  fi
}

case "${1:-}" in
  install)   cmd_install ;;
  uninstall) cmd_uninstall ;;
  up|start)  cmd_up ;;
  down|stop) cmd_down ;;
  restart)   cmd_restart ;;
  status)    cmd_status ;;
  logs)      shift; cmd_logs "$@" ;;
  *)
    sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    exit 1 ;;
esac
