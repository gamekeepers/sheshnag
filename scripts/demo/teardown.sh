#!/usr/bin/env bash
# Stop everything the demo started. Leaves $DEMO_DIR on disk for inspection.
DEMO=${DEMO_DIR:-/srv/forge/runs/${USER#forge-}/demo}
PORT=${DEMO_PORT:-8800}
if docker ps -a --format '{{.Names}}' | grep -qx sheshnag-demo-vllm; then
    docker rm -f sheshnag-demo-vllm >/dev/null && echo "stopped vLLM"
fi
for c in $(docker ps --format '{{.Names}}' | grep '^forge-ftjob-'); do
    docker stop "$c" >/dev/null && echo "stopped training container $c"
done
pkill -f "[f]orge-runner serve" && echo "stopped forge-runner"
pkill -f "[u]vicorn main:app .*--port $PORT" && echo "stopped backend"
echo "done (state kept in $DEMO)"
