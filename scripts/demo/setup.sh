#!/usr/bin/env bash
# Start a throwaway Sheshnag backend on watchtower for the finetuning demo.
# Run this before the audience arrives; then run demo.sh.
#
# Everything is local to the box: backend on 127.0.0.1:$DEMO_PORT with a fresh
# SQLite DB under $DEMO_DIR, and forge-runner in its own venv. Nothing touches
# the live site. Re-running setup starts from a clean database.
set -euo pipefail

SRC=$(cd "$(dirname "$0")/../.." && pwd)
RUNS=/srv/forge/runs/${USER#forge-}   # account forge-manan -> runs/manan
DEMO=${DEMO_DIR:-$RUNS/demo}
PORT=${DEMO_PORT:-8800}
STATE=$DEMO/backend-state

echo "Installing backend and forge-runner venvs (first run takes a minute)…"
[ -d ~/venvs/sheshnag-backend ] || python3 -m venv ~/venvs/sheshnag-backend
~/venvs/sheshnag-backend/bin/pip install -q -r "$SRC/backend/requirements.txt"
[ -d ~/venvs/forge-runner ] || python3 -m venv ~/venvs/forge-runner
~/venvs/forge-runner/bin/pip install -q -e "$SRC/runner"

echo "Starting a fresh backend on 127.0.0.1:$PORT…"
pkill -f "uvicorn main:app .*--port $PORT" 2>/dev/null || true
rm -rf "$STATE" "$DEMO/jobs" "$DEMO/adapters" "$DEMO/work"
mkdir -p "$STATE" "$DEMO/jobs" "$DEMO/adapters" "$DEMO/work"

export DATABASE_URL="sqlite:///$STATE/demo.db"
export SECRET_KEY=$(openssl rand -hex 32)
export HF_HOME=/srv/forge/hf-cache          # shared cache: tokenizers are already there
export PYTHONPATH="$SRC/backend"
cd "$STATE"                                  # uploaded files land in $STATE/files
~/venvs/sheshnag-backend/bin/python "$SRC/scripts/demo/seed.py" > "$DEMO/keys.env"
echo "SHESHNAG=http://127.0.0.1:$PORT" >> "$DEMO/keys.env"

setsid nohup ~/venvs/sheshnag-backend/bin/python -m uvicorn main:app --app-dir "$SRC/backend" \
    --host 127.0.0.1 --port "$PORT" > "$DEMO/backend.log" 2>&1 < /dev/null &

for _ in $(seq 1 60); do
    curl -sf -o /dev/null "http://127.0.0.1:$PORT/docs" && break
    sleep 1
done
curl -sf -o /dev/null "http://127.0.0.1:$PORT/docs" || { echo "Backend did not start; see $DEMO/backend.log"; exit 1; }

echo
echo "Ready. Keys in $DEMO/keys.env. GPU right now:"
nvidia-smi --query-gpu=memory.used,memory.free,utilization.gpu --format=csv,noheader
echo "Post in the pod chat before running demo.sh: it trains (~1 min) and serves (~2 min) on the GPU."
