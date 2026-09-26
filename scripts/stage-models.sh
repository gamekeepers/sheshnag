#!/usr/bin/env bash
# Copy GGUF files to workers that cannot download them, through a jump host.
#
#   scripts/stage-models.sh manifest.txt worker1 worker2
#
# Manifest lines are `name<space>path[<space>source]`, blank lines and #
# comments ignored:
#
#   gpt-oss-20b       /usr/share/ollama/.ollama/models/blobs/sha256-e7b273f9…
#   llama3-2-3b       /var/models/llama3.2-3b-q4_k_m.gguf   unsloth/Llama-3.2-3B-GGUF
#
# `source` is the public repo the bytes came from. With it the worker's
# catalogue entry is confirmed against that repo and adopted automatically;
# without it the entry is reported but has to be claimed by hand.
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
#
# Each file is accompanied by a `<name>.gguf.json` sidecar holding its sha256
# and source. The hash is computed here, where the file already is, so a worker
# never reads back a multi-gigabyte model to learn what it received.
set -uo pipefail

MANIFEST="${1:?usage: stage-models.sh <manifest> <worker>...}"
shift
[ $# -gt 0 ] || { echo "name at least one worker"; exit 1; }

: "${JUMP:?set JUMP to the ssh destination that can reach the workers}"
DEST="${DEST:-/tmp/gguf}"
ONLY="${ONLY:-}"
CM="$HOME/.ssh/cm-stage-$$"

SHA_CACHE=$(mktemp -d)
cleanup() {
  ssh -O exit -o ControlPath="$CM" "$JUMP" 2>/dev/null
  rm -rf "$SHA_CACHE"
}
trap cleanup EXIT

echo "Opening one connection to $JUMP (credentials asked once)..."
ssh -fNM -o ControlPath="$CM" -o ControlPersist=4h "$JUMP" || exit 1
J() { ssh -o ControlPath="$CM" "$JUMP" "$@"; }

local_sha() {   # $1 source path — hashed once per run, not once per worker
  # The key is a hash of the path, not the path with its punctuation folded:
  # `a-b.gguf` and `a_b.gguf` fold to the same string, and the second file
  # would then be staged under the first one's hash.
  local src="$1" key
  key=$(printf '%s' "$src" | sha256sum | cut -d' ' -f1)
  if [ ! -s "$SHA_CACHE/$key" ]; then
    sha256sum "$src" | cut -d' ' -f1 > "$SHA_CACHE/$key"
  fi
  cat "$SHA_CACHE/$key"
}

send() {   # $1 worker, $2 name, $3 source path, $4 source ref (may be empty)
  local worker="$1" name="$2" src="$3" ref="${4:-}" dest="$DEST/$2.gguf" want have
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

# The sidecar asserts what a file IS, and the daemon adopts a model on that
# assertion — so it is written only once the worker's own bytes have been
# read back and agreed. A size match is not agreement: two builds of one
# model at one quantization can be the same length.
write_sidecar() {   # $1 worker, $2 name, $3 sha, $4 size, $5 ref
  local worker="$1" name="$2" sha="$3" size="$4" ref="${5:-}"
  J "ssh $worker 'cat > $DEST/$2.gguf.json'" <<SIDECAR
{"sha256": "$sha", "size": $size, "source_ref": "$ref"}
SIDECAR
}

drop_sidecar() {   # $1 worker, $2 name
  J "ssh $1 'rm -f $DEST/$2.gguf.json'"
}

manifest_field() {   # $1 name, $2 field index — compared literally
  awk -v want="$1" -v col="$2" '$1 == want { print $col; exit }' "$MANIFEST"
}

for worker in "$@"; do
  echo
  echo "=== $worker ==="
  while read -r name src ref _rest; do
    case "$name" in ''|'#'*) continue ;; esac
    if [ -n "$ONLY" ]; then
      case " $ONLY " in *" $name "*) ;; *) continue ;; esac
    fi
    send "$worker" "$name" "$src" "$ref"
  done < "$MANIFEST"
  echo "  verifying..."
  # Read the worker's own bytes back, then write or withdraw each identity.
  # A pipeline would run this in a subshell; the file keeps it in this one.
  J "ssh $worker 'cd $DEST && sha256sum *.gguf 2>/dev/null'" | tr -d '\r' \
    > "$SHA_CACHE/remote.$$"
  while read -r remote_sha file; do
    name="${file%.gguf}"
    src=$(manifest_field "$name" 2)
    ref=$(manifest_field "$name" 3)
    if [ -z "$src" ]; then
      echo "    $file: not in the manifest — left alone"
    elif [ "$remote_sha" = "$(local_sha "$src")" ]; then
      write_sidecar "$worker" "$name" "$remote_sha" "$(stat -c %s "$src")" "$ref"
      echo "    $file: ok"
    else
      drop_sidecar "$worker" "$name"
      echo "    $file: MISMATCH — identity withdrawn; delete it on the worker and re-run"
    fi
  done < "$SHA_CACHE/remote.$$"
done
