#!/usr/bin/env bash
# Copy GGUF files to workers that cannot download them, through a jump host.
#
#   scripts/stage-models.sh manifest.txt worker1 worker2
#
# Manifest lines are `name<space>path`, blank lines and # comments ignored:
#
#   gpt-oss-20b       /usr/share/ollama/.ollama/models/blobs/sha256-e7b273f9…
#   llama3-2-3b       /var/models/llama3.2-3b-q4_k_m.gguf
#
# The name becomes `<name>.gguf` on the worker, and llama-server in router
# mode reports that stem as the model id — so it must equal the
# `runtime_model_id` of a llamacpp profile in the catalogue, or the worker is
# online, healthy and never dispatched to.
#
# Settings:
#   JUMP        ssh destination that can reach the workers (required)
#   DEST        directory on each worker      (default /tmp/gguf)
#   ONLY        space-separated names to send (default: all in the manifest)
#
# Transfers nest ssh inside the jump session rather than using scp or -J,
# because a jump host may refuse to forward channels. Files already present at
# the right size are skipped and partial ones resume, so re-running is cheap.
set -uo pipefail

MANIFEST="${1:?usage: stage-models.sh <manifest> <worker>...}"
shift
[ $# -gt 0 ] || { echo "name at least one worker"; exit 1; }

: "${JUMP:?set JUMP to the ssh destination that can reach the workers}"
DEST="${DEST:-/tmp/gguf}"
ONLY="${ONLY:-}"
CM="$HOME/.ssh/cm-stage-$$"

cleanup() { ssh -O exit -o ControlPath="$CM" "$JUMP" 2>/dev/null; }
trap cleanup EXIT

echo "Opening one connection to $JUMP (credentials asked once)..."
ssh -fNM -o ControlPath="$CM" -o ControlPersist=4h "$JUMP" || exit 1
J() { ssh -o ControlPath="$CM" "$JUMP" "$@"; }

send() {   # $1 worker, $2 name, $3 source path
  local worker="$1" name="$2" src="$3" dest="$DEST/$2.gguf" want have
  [ -r "$src" ] || { echo "  $name: cannot read $src — skipped"; return; }
  want=$(stat -c %s "$src")
  have=$(J "ssh $worker 'stat -c %s $dest 2>/dev/null || echo 0'" | tr -d '\r')

  if [ "$have" = "$want" ]; then
    echo "  $name: already complete ($((want / 1000000)) MB)"
    return
  fi
  J "ssh $worker 'mkdir -p $DEST'"
  if [ "${have:-0}" -gt 0 ] 2>/dev/null; then
    echo "  $name: resuming at $((have / 1000000)) of $((want / 1000000)) MB"
    tail -c +$((have + 1)) "$src" | J "ssh $worker 'cat >> $dest'"
  else
    echo "  $name: sending $((want / 1000000)) MB"
    dd if="$src" bs=1M status=none | J "ssh $worker 'cat > $dest'"
  fi
}

for worker in "$@"; do
  echo
  echo "=== $worker ==="
  while read -r name src _rest; do
    case "$name" in ''|'#'*) continue ;; esac
    if [ -n "$ONLY" ]; then
      case " $ONLY " in *" $name "*) ;; *) continue ;; esac
    fi
    send "$worker" "$name" "$src"
  done < "$MANIFEST"
  echo "  verifying..."
  J "ssh $worker 'cd $DEST && sha256sum *.gguf 2>/dev/null'" | tr -d '\r'
done
