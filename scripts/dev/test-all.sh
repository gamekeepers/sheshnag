#!/usr/bin/env bash
# Run every suite the repo has, each with the venv it actually needs.
#
#   scripts/dev/test-all.sh              # backend + daemon + frontend lint
#   scripts/dev/test-all.sh backend      # just one
#
# None of these need a running server: the backend suite uses in-memory
# SQLite, the daemon suite uses the mock backend and mock runtimes in
# daemon/tests/. So this is safe to run while the dev stack is up.
#
# Suites are per CONTRIBUTING.md § Testing.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
declare -a FAILED=() PASSED=()

info() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }

run_suite() {
  local name="$1"; shift
  local dir="$1"; shift
  info "$name"
  if (cd "$REPO/$dir" && "$@"); then
    PASSED+=("$name")
  else
    FAILED+=("$name")
  fi
}

# want <suite> [requested...] — true if nothing was requested, or this is.
want() { local n="$1"; shift; [[ $# -eq 0 || " $* " == *" $n "* ]]; }

TARGETS=("$@")
for target in "${TARGETS[@]}"; do
  case "$target" in
    backend|daemon|frontend) ;;
    *) printf '\033[31merror:\033[0m unknown suite: %s\n' "$target" >&2; exit 2 ;;
  esac
done

if want backend "${TARGETS[@]}"; then
  run_suite backend backend "$REPO/.venv/bin/python" -m pytest tests/ -q
fi

if want daemon "${TARGETS[@]}"; then
  # pytest-asyncio ships in the [dev] extra, not daemon/requirements.txt.
  # Without it the async tests are skipped silently rather than failing —
  # check up front so a green run means what it looks like.
  if ! "$REPO/daemon/.venv/bin/python" -c "import pytest_asyncio" 2>/dev/null; then
    printf '\033[31merror:\033[0m pytest-asyncio missing in daemon/.venv — async tests cannot run.\n' >&2
    printf '       fix: cd daemon && .venv/bin/pip install -e ".[dev]"\n' >&2
    FAILED+=("daemon (missing pytest-asyncio)")
  else
    run_suite daemon daemon "$REPO/daemon/.venv/bin/python" -m pytest tests/ -q
  fi
fi

if want frontend "${TARGETS[@]}"; then
  # No test framework on the frontend yet; lint is the whole gate.
  run_suite "frontend (lint)" . npm run lint
fi

echo
for s in "${PASSED[@]:-}"; do [[ -n "$s" ]] && printf '\033[32mPASS\033[0m %s\n' "$s"; done
for s in "${FAILED[@]:-}"; do [[ -n "$s" ]] && printf '\033[31mFAIL\033[0m %s\n' "$s"; done
[[ ${#FAILED[@]} -eq 0 ]] || exit 1
