#!/usr/bin/env bash
# Sheshnag finetuning demo — "submit a dataset, get an adapter, and call it".
# Run setup.sh first. Pauses before each step; DEMO_AUTO=1 runs straight through.
#
#   1  a broken dataset is rejected, with a fix for every problem
#   2  a good dataset is validated: token counts and a length histogram
#   3  the contract guards serving: a rank vLLM can't serve is refused
#   4  forge-runner trains it: loss streams in, adapter comes back servable
#   5  the adapter is served on vLLM and answers next to the base model
set -euo pipefail

RUNS=/srv/forge/runs/${USER#forge-}   # account forge-manan -> runs/manan
DEMO=${DEMO_DIR:-$RUNS/demo}
TRAIN=${DEMO_TRAIN:-$RUNS/week2/data/train.jsonl}
VALID=${DEMO_VALID:-$RUNS/week2/data/test.jsonl}
VLLM_IMAGE=vllm/vllm-openai@sha256:c1c9f6fd5c109ba7f0546a59f5b2f15fb87f64c77782e90a27b648b42a8e67c3  # v0.31.0, G1
VLLM_PORT=${DEMO_VLLM_PORT:-8101}
# shellcheck disable=SC1091
source "$DEMO/keys.env"
WORK=$DEMO/work

bold=$'\033[1m'; cyan=$'\033[1;36m'; dim=$'\033[2m'; green=$'\033[32m'; red=$'\033[31m'; off=$'\033[0m'

pause() { [ "${DEMO_AUTO:-0}" = 1 ] && return; read -rp $'\n'"${dim}▶ Enter to continue${off} " _; }
step()  { printf '\n%s━━ %s ━━%s\n%s\n' "$cyan" "$1" "$off" "$2"; pause; }
say()   { printf '%s$ %s%s\n' "$dim" "$1" "$off" >&2; }   # stderr: callers capture stdout
api()   { curl -sS -H "Authorization: Bearer $PERSONAL_KEY" "$@"; }

upload() {  # upload <file> -> file id
    say "POST /v1/files  purpose=fine-tune  ($(wc -l < "$1") lines)"
    api -F purpose=fine-tune -F "file=@$1" "$SHESHNAG/v1/files" | jq -r .id
}

create_job() {  # create_job <json body> -> raw response
    say "POST /v1/fine_tuning/jobs  $1"
    api -H 'Content-Type: application/json' -d "$1" "$SHESHNAG/v1/fine_tuning/jobs"
}

wait_status() {  # wait_status <job> <status...> -> prints final job JSON
    local job=$1; shift
    while :; do
        local body status
        body=$(api "$SHESHNAG/v1/fine_tuning/jobs/$job")
        status=$(jq -r .status <<<"$body")
        for s in "$@"; do [ "$status" = "$s" ] && { echo "$body"; return; }; done
        sleep 1
    done
}

# ─── 1 ─────────────────────────────────────────────────────
step "1 · A broken dataset is rejected — with a fix for every problem" \
"Three of the most common wrong formats people upload. Sheshnag names each one and says how to fix it."

cat > "$WORK/broken.jsonl" <<'EOF'
{"prompt": "What is LoRA?", "completion": "A way to finetune a small set of extra weights."}
{"conversations": [{"from": "human", "value": "Hi"}, {"from": "gpt", "value": "Hello!"}]}
{"messages": [{"role": "human", "content": "Name a GPU."}, {"role": "assistant", "content": "RTX 6000 Ada."}]}
EOF
bad_file=$(upload "$WORK/broken.jsonl")
bad_job=$(create_job "{\"model\":\"qwen2-5-0-5b-instruct-hf\",\"training_file\":\"$bad_file\"}" | jq -r .id)
bad=$(wait_status "$bad_job" failed queued)
printf '\nstatus: %s%s%s\n' "$red" "$(jq -r .status <<<"$bad")" "$off"
jq -r '.error.details[] | "  line \(.line): \(.message)\n    fix → \(.hint)\n"' <<<"$bad"

# ─── 2 ─────────────────────────────────────────────────────
step "2 · A real dataset goes in" \
"$(wc -l < "$TRAIN") chat examples (week 1's data). Validation counts tokens with the base model's own tokenizer, pinned to an exact revision."

train_file=$(upload "$TRAIN")
valid_file=$(upload "$VALID")
job=$(create_job "{\"model\":\"qwen2-5-0-5b-instruct-hf\",\"training_file\":\"$train_file\",\"validation_file\":\"$valid_file\",\"suffix\":\"demo\",\"hyperparameters\":{\"n_epochs\":1}}" | jq -r .id)
body=$(wait_status "$job" queued failed)
printf '\nstatus: %s%s%s   job: %s\n' "$green" "$(jq -r .status <<<"$body")" "$off" "$job"
jq -r '.training_file_stats | "examples: \(.n_examples)   tokens: \(.total_tokens)   p50 \(.p50) / p95 \(.p95) / max \(.max)\ncounted with: \(.token_count_method)\n\nlength histogram (tokens per example):"' <<<"$body"
jq -r '.training_file_stats.histogram[] | select(.count > 0) | "  ≤ \(.le // "∞")\t\(.count)"' <<<"$body" \
  | awk -F'\t' '{ bar=""; for (i = 0; i < $2 / 8; i++) bar = bar "█"; printf "%-10s %4d %s\n", $1, $2, bar }'

# ─── 3 ─────────────────────────────────────────────────────
step "3 · The contract protects serving" \
"Week 2 found that one adapter with too high a rank stops the whole vLLM server from starting. So the API refuses it up front."

create_job "{\"model\":\"qwen2-5-0-5b-instruct-hf\",\"training_file\":\"$train_file\",\"lora\":{\"rank\":128}}" \
  | jq -r '"HTTP 422: " + (.detail[0].msg)'

# ─── 4 ─────────────────────────────────────────────────────
step "4 · forge-runner trains it" \
"forge-runner claims the queued job, writes the LLaMA-Factory config, runs the pinned engine on this GPU, and streams every logged step back as an event."

nvidia-smi --query-gpu=memory.free --format=csv,noheader | sed 's/^/GPU free before: /'
say "forge-runner serve --backend-url $SHESHNAG …"
setsid nohup ~/venvs/forge-runner/bin/forge-runner serve --backend-url "$SHESHNAG" --api-key "$WORKER_KEY" \
    --work-dir "$DEMO/jobs" --hf-cache /srv/forge/hf-cache --poll-interval 3 \
    > "$DEMO/runner.log" 2>&1 < /dev/null &
runner_pid=$!
trap 'kill $runner_pid 2>/dev/null || true' EXIT

seen=0
while :; do
    events=$(api "$SHESHNAG/v1/fine_tuning/jobs/$job/events?limit=200")
    jq -r --argjson seen "$seen" '.data | reverse | .[]
        | select((.id | ltrimstr("ftevent-") | tonumber) > $seen)
        | "  \(.type | if . == "metrics" then "📉" else "• " end) \(.message)"' <<<"$events"
    seen=$(jq -r '[.data[].id | ltrimstr("ftevent-") | tonumber] | max // 0' <<<"$events")
    status=$(api "$SHESHNAG/v1/fine_tuning/jobs/$job" | jq -r .status)
    case $status in succeeded|failed|cancelled) break ;; esac
    sleep 2
done
kill $runner_pid 2>/dev/null || true

body=$(api "$SHESHNAG/v1/fine_tuning/jobs/$job")
if [ "$(jq -r .status <<<"$body")" != succeeded ]; then
    printf '\n%sJob %s%s: %s\n' "$red" "$(jq -r .status <<<"$body")" "$off" "$(jq -r .error.message <<<"$body")"
    exit 1
fi
FT=$(jq -r .fine_tuned_model <<<"$body")
api "$SHESHNAG/v1/fine_tuning/jobs/$job/artifacts" | jq -r '.data[0] |
  "\nadapter: rank \(.lora_rank), \(.target_modules | length) target modules, sha256 \(.sha256[0:12])…",
  "base:    \(.base_model.source_ref) @ \(.base_model.revision[0:12])…",
  "engine:  \(.engine.name) \(.engine.version)   eval_loss \(.metrics.eval_loss // "n/a")",
  "servable: \(.servable)"'
printf '%sfine_tuned_model: %s%s\n' "$green" "$FT" "$off"

# ─── 5 ─────────────────────────────────────────────────────
step "5 · Call it" \
"The adapter is served on vLLM under its fine_tuned_model name, on the exact base revision it was trained on. Same prompt, base vs adapter."

adapter_dir=$DEMO/adapters/$job
mkdir -p "$adapter_dir"
say "GET /v1/files/$(jq -r '.result_files[0]' <<<"$body")/content"
api -o "$WORK/adapter.zip" "$SHESHNAG/v1/files/$(jq -r '.result_files[0]' <<<"$body")/content"
unzip -oq "$WORK/adapter.zip" -d "$adapter_dir"
revision=$(jq -r .base_model.revision <<<"$body")

say "vllm serve Qwen/Qwen2.5-0.5B-Instruct --revision ${revision:0:12}… --enable-lora --lora-modules $FT=… --max-lora-rank 64"
docker rm -f sheshnag-demo-vllm >/dev/null 2>&1 || true
docker run -d --name sheshnag-demo-vllm \
    --device nvidia.com/gpu=all --shm-size 2g --memory 16g --cpus 8 \
    -p "127.0.0.1:$VLLM_PORT:8000" \
    -v /srv/forge/hf-cache:/hf -e HF_HOME=/hf \
    -v "$adapter_dir:/adapters/demo:ro" \
    "$VLLM_IMAGE" Qwen/Qwen2.5-0.5B-Instruct --revision "$revision" \
    --enable-lora --lora-modules "$FT=/adapters/demo" --max-lora-rank 64 \
    --max-loras 1 --gpu-memory-utilization 0.06 --max-model-len 2048 \
    --max-num-seqs 4 --enforce-eager --seed 0 > /dev/null
trap 'docker rm -f sheshnag-demo-vllm >/dev/null 2>&1 || true' EXIT

printf 'waiting for vLLM'
for _ in $(seq 1 180); do
    curl -sf -o /dev/null "http://127.0.0.1:$VLLM_PORT/v1/models" && break
    printf '.'; sleep 2
done
echo
curl -sf "http://127.0.0.1:$VLLM_PORT/v1/models" | jq -r '.data[] | "  model available: \(.id)"'

prompt=$(head -1 "$VALID" | jq -r '[.messages[] | select(.role == "user")][0].content')
ask() {
    curl -sS "http://127.0.0.1:$VLLM_PORT/v1/chat/completions" -H 'Content-Type: application/json' \
      -d "$(jq -n --arg m "$1" --arg p "$prompt" \
            '{model: $m, messages: [{role: "user", content: $p}], temperature: 0, max_tokens: 120}')" \
      | jq -r '.choices[0].message.content'
}
printf '\n%sprompt:%s %s\n' "$bold" "$off" "$prompt"
printf '\n%sbase (Qwen2.5-0.5B-Instruct):%s\n%s\n' "$bold" "$off" "$(ask Qwen/Qwen2.5-0.5B-Instruct)"
printf '\n%sadapter (%s):%s\n%s\n' "$bold" "$FT" "$off" "$(ask "$FT")"

pause
docker rm -f sheshnag-demo-vllm >/dev/null 2>&1 || true
printf '\n%sDone.%s Dataset in → validated → trained → servable adapter → called by name.\n' "$green" "$off"
printf 'Next on the board: calling it through Sheshnag itself (POST /v1/batches with model=%s).\n' "$FT"
