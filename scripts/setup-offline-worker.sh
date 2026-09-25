#!/usr/bin/env bash
# Configure and supervise a worker on a host that systemd cannot manage.
#
# Run it ON the worker, after installing the daemon from an offline bundle.
# It writes the config, a supervisor loop and a control script, installs cron
# entries, and starts everything.
#
#   BACKEND_URL=https://sheshnag.example.edu API_KEY=gk-... \
#       bash setup-offline-worker.sh
#
# Settings, all optional except the two above:
#
#   INSTANCE     name this install (default: hostname -s). Several machines
#                sharing one home each need their own.
#   RUNTIME      ollama | vllm | llamacpp          (default llamacpp)
#   MODELS_DIR   directory of GGUFs for llama.cpp router mode (default /tmp/gguf)
#   MODELS_MAX   how many may be resident at once  (default 1)
#   LLAMA_BIN    path to llama-server              (default ~/opt/llama/bin/llama-server)
#   THREADS      llama-server -t                   (default: nproc, capped at 16)
#   N_CTX        llama-server -c                   (default 4096)
#   PROXY        e.g. socks5h://127.0.0.1:1080, when the host has no route out
#   TUNNEL_HOST  ssh host to open a SOCKS forward through, if PROXY is a local
#                socks5h port this script should maintain
#   PROBE_URL    what the supervisor fetches through the forward to decide it
#                works                                 (default: BACKEND_URL)
set -euo pipefail

: "${BACKEND_URL:?set BACKEND_URL}"
: "${API_KEY:?set API_KEY}"

INSTANCE="${INSTANCE:-$(hostname -s)}"
RUNTIME="${RUNTIME:-llamacpp}"
MODELS_DIR="${MODELS_DIR:-/tmp/gguf}"
MODELS_MAX="${MODELS_MAX:-1}"
LLAMA_BIN="${LLAMA_BIN:-$HOME/opt/llama/bin/llama-server}"
THREADS="${THREADS:-$(nproc 2>/dev/null || echo 8)}"
[ "$THREADS" -gt 16 ] && THREADS=16
N_CTX="${N_CTX:-4096}"
PROXY="${PROXY:-}"
TUNNEL_HOST="${TUNNEL_HOST:-}"
PROBE_URL="${PROBE_URL:-$BACKEND_URL}"
LLAMA_PORT="${LLAMA_PORT:-8080}"

DIR="$HOME/.gpu-daemon-$INSTANCE"
test -x "$DIR/venv/bin/gpu-daemon" || {
  echo "No daemon at $DIR — install the offline bundle first."; exit 1; }

echo "Configuring $INSTANCE"
echo "  install    $DIR"
echo "  runtime    $RUNTIME"
[ "$RUNTIME" = llamacpp ] && echo "  models     $MODELS_DIR (max $MODELS_MAX resident, -t $THREADS)"
[ -n "$PROXY" ] && echo "  proxy      $PROXY"

# ── config ───────────────────────────────────────────────────────────────
# credentials_path and work_dir are written explicitly because the daemon
# defaults both under ~/.gpu-daemon, which several instances on one home
# would share — and the credentials file holds the assigned worker id.
{
  echo "backend_url: \"$BACKEND_URL\""
  echo "api_key: \"$API_KEY\""
  echo "runtime: \"$RUNTIME\""
  [ "$RUNTIME" = llamacpp ] && echo "llamacpp_url: \"http://127.0.0.1:$LLAMA_PORT\""
  echo "credentials_path: \"$DIR/credentials\""
  echo "work_dir: \"$DIR/jobs\""
  echo "inference_timeout: 1800.0"
  echo "max_concurrent_prompts: 1"
} > "$DIR/config.yaml"
chmod 600 "$DIR/config.yaml"

if [ -n "$PROXY" ]; then
  {
    echo "ALL_PROXY=$PROXY"
    echo "NO_PROXY=127.0.0.1,localhost"
  } > "$DIR/.env"
fi

# ── supervisor ───────────────────────────────────────────────────────────
# systemd 219 has no user units and a container may have no init at all, so
# cron restarts this loop and flock keeps it to one copy. The lock lives on
# local disk: flock cannot take one over NFS, and a shared home would
# otherwise let two machines supervise as one worker.
{
  echo '#!/bin/bash'
  echo "DIR=\"$DIR\""
  echo "MODELS_DIR=\"$MODELS_DIR\""
  echo "INSTANCE=\"$INSTANCE\""
  echo "LLAMA_BIN=\"$LLAMA_BIN\""
  echo "LLAMA_PORT=\"$LLAMA_PORT\""
  echo "MODELS_MAX=\"$MODELS_MAX\""
  echo "THREADS=\"$THREADS\""
  echo "N_CTX=\"$N_CTX\""
  echo "RUNTIME=\"$RUNTIME\""
  echo "TUNNEL_HOST=\"$TUNNEL_HOST\""
  echo "PROBE_URL=\"$PROBE_URL\""
  cat <<'RUNNER'

exec 9>"/tmp/gpu-daemon-$INSTANCE.lock"
flock -n 9 || { echo "another supervisor holds the lock"; exit 0; }

set -a; [ -f "$DIR/.env" ] && . "$DIR/.env"; set +a

port_open() { (exec 3<>/dev/tcp/127.0.0.1/"$1") 2>/dev/null; }

# A bound port means ssh is listening, not that the forward carries anything.
# ssh answers the SOCKS greeting itself and opens the channel only afterwards,
# so a forward whose channels are refused passes a port check and fails every
# request — arriving at the daemon as `Malformed reply` from the SOCKS library,
# which names nothing the reader can act on. The supervisor therefore makes a
# request the way the daemon will.
#
# It asks through the daemon's own interpreter, not the system curl: a host old
# enough to need this script may carry a TLS stack too old to reach the control
# plane, and a probe that cannot succeed would tear down a working forward on
# every pass. The interpreter that does the real work is the only one whose
# verdict means anything. httpx reads the proxy from the environment sourced
# above, the same variables the daemon resolves.
#
# Any reply counts, 5xx included: a response of any status proves the request
# went out and came back, so the forward carries. Only a transport failure —
# the channel refused, the connection dropped, the request timed out — is
# grounds to tear the forward down, and a backend that is down or deploying is
# not that: rebuilding a healthy forward on every pass is the connection churn
# that trips a login cap.
if [ -x "$DIR/venv/bin/python" ]; then
  tunnel_ok() {
    "$DIR/venv/bin/python" -c \
      'import httpx,sys
try: httpx.get(sys.argv[1], timeout=15)
except Exception as e:
    print(f"tunnel probe failed: {e}", file=sys.stderr)
    sys.exit(1)' \
      "$PROBE_URL" 2>>"$DIR/tunnel.log"
  }
else
  tunnel_ok() { port_open 1080; }
fi

while true; do
  # The forward carries every call to the control plane, so it is rebuilt
  # before anything else that depends on it.
  if [ -n "$TUNNEL_HOST" ] && ! tunnel_ok; then
    # A forward that is up but useless still holds 1080, and the replacement
    # would fail to bind behind it.
    pkill -f "ssh .*-D 127\.0\.0\.1:1080 $TUNNEL_HOST" 2>/dev/null
    ssh -fN -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -o ExitOnForwardFailure=yes -o BatchMode=yes \
        -D 127.0.0.1:1080 "$TUNNEL_HOST" >>"$DIR/tunnel.log" 2>&1 \
      || date "+%F %T  ssh -D to $TUNNEL_HOST failed" >>"$DIR/tunnel.log"
    # Whatever refuses the forward — a login cap, a denied forward — refuses it
    # again immediately, and retrying every 10s is how an account stays capped.
    tunnel_ok || sleep 30
  fi

  # Router mode: llama-server answers for every GGUF in the directory and
  # loads on demand, reporting each file's stem as its model id.
  if [ "$RUNTIME" = llamacpp ] && ! port_open "$LLAMA_PORT"; then
    "$LLAMA_BIN" --models-dir "$MODELS_DIR" --models-max "$MODELS_MAX" \
      --host 127.0.0.1 --port "$LLAMA_PORT" \
      -c "$N_CTX" --parallel 1 -t "$THREADS" --numa distribute \
      >> "$DIR/llama.log" 2>&1 &
    sleep 30
  fi

  "$DIR/venv/bin/gpu-daemon" --config "$DIR/config.yaml" \
      >> "$DIR/daemon.log" 2>&1
  sleep 10
done
RUNNER
} > "$DIR/run.sh"
chmod +x "$DIR/run.sh"

# ── control script ───────────────────────────────────────────────────────
# Written before the cron entries: a failure installing those must not leave
# a running worker with no way to inspect or stop it.
{
  echo '#!/bin/bash'
  echo "DIR=\"$DIR\""
  echo "MODELS_DIR=\"$MODELS_DIR\""
  echo "INSTANCE=\"$INSTANCE\""
  echo "LLAMA_PORT=\"$LLAMA_PORT\""
  echo "TUNNEL_HOST=\"$TUNNEL_HOST\""
  echo "PROBE_URL=\"$PROBE_URL\""
  cat <<'CTL'

# The proxy lives in .env, and the tunnel check below is only truthful with it.
set -a; [ -f "$DIR/.env" ] && . "$DIR/.env"; set +a

case "${1:-status}" in
  status)
    pgrep -f "$DIR/run.sh"              >/dev/null && echo "supervisor   running" || echo "supervisor   stopped"
    pgrep -f "$DIR/venv/bin/gpu-daemon" >/dev/null && echo "daemon       running" || echo "daemon       stopped"
    pgrep -f "llama-server .*$MODELS_DIR" >/dev/null && echo "llama-server running" || echo "llama-server stopped"
    if [ -n "$TUNNEL_HOST" ]; then
      if "$DIR/venv/bin/python" -c \
           'import httpx,sys
try: httpx.get(sys.argv[1], timeout=15)
except Exception as e:
    print(f"tunnel probe failed: {e}", file=sys.stderr)
    sys.exit(1)' \
           "$PROBE_URL" 2>>"$DIR/tunnel.log"; then
        echo "tunnel       carrying traffic"
      else
        echo "tunnel       DOWN — see $DIR/tunnel.log"
      fi
    fi
    echo
    echo "models on disk:"
    ls -1 "$MODELS_DIR"/*.gguf 2>/dev/null | xargs -n1 basename 2>/dev/null || echo "  (none)"
    echo
    echo "resident now:"
    curl -s --max-time 5 "http://127.0.0.1:$LLAMA_PORT/v1/models" 2>/dev/null \
      | python -c "import json,sys;[print('  %-24s %s' % (m['id'], m.get('status',{}).get('value','loaded'))) for m in json.load(sys.stdin)['data']]" 2>/dev/null \
      || echo "  (model server not answering)"
    ;;
  logs)    tail -f "$DIR/daemon.log" ;;
  llama)   tail -f "$DIR/llama.log" ;;
  restart)
    pkill -f "$DIR/venv/bin/gpu-daemon" 2>/dev/null
    echo "daemon killed — the supervisor restarts it in ~10s"
    ;;
  stop)
    crontab -l 2>/dev/null \
      | grep -v "gpu-daemon-$INSTANCE/run.sh" \
      | grep -v "touch -a -m $MODELS_DIR" \
      | crontab -
    pkill -f "$DIR/run.sh" 2>/dev/null
    pkill -f "llama-server .*$MODELS_DIR" 2>/dev/null
    pkill -f "$DIR/venv/bin/gpu-daemon" 2>/dev/null
    echo "stopped and removed from cron"
    ;;
  start)   nohup "$DIR/run.sh" >/dev/null 2>&1 & echo "started" ;;
  *) echo "usage: ctl.sh {status|logs|llama|restart|start|stop}" ;;
esac
CTL
} > "$DIR/ctl.sh"
chmod +x "$DIR/ctl.sh"

# ── cron ─────────────────────────────────────────────────────────────────
# tmpwatch reaps /tmp on a ten-day window and judges by atime, mtime and
# ctime. A model staged for failover can go weeks without being loaded, and
# under noatime even one in daily use looks untouched, so the nightly touch
# keeps all three fresh for the cost of a metadata write.
{
  crontab -l 2>/dev/null \
    | grep -v "gpu-daemon-$INSTANCE/run.sh" \
    | grep -v "touch -a -m $MODELS_DIR" || true
  echo "@reboot $DIR/run.sh"
  echo "*/5 * * * * $DIR/run.sh"
  [ "$RUNTIME" = llamacpp ] && echo "0 3 * * * touch -a -m $MODELS_DIR/*.gguf >/dev/null 2>&1"
} | crontab -

pkill -f "$DIR/run.sh" 2>/dev/null || true
nohup "$DIR/run.sh" >/dev/null 2>&1 &

echo
echo "Started. Operate it with:"
echo "  $DIR/ctl.sh status"
echo "  $DIR/ctl.sh logs"
echo "  $DIR/ctl.sh stop"
