#!/usr/bin/env bash
# Build one archive that installs the worker daemon on a host with no route to
# PyPI or GitHub. Run it on a machine that has both; carry the result in.
#
# Usage:
#   scripts/build-offline-bundle.sh                 # wheels only
#   WITH_PYTHON=1 scripts/build-offline-bundle.sh   # add a portable CPython
#
# The wheels are resolved for the *target*, not for this machine. A host on
# glibc 2.17 (CentOS 7, the GICS boxes) cannot load a manylinux_2_28 wheel, so
# the tags below are part of the contract, not a default worth drifting.

set -euo pipefail

PY_VERSION="${PY_VERSION:-3.12}"
ABI="${ABI:-cp312}"
PLATFORM="${PLATFORM:-manylinux2014_x86_64}"
WITH_PYTHON="${WITH_PYTHON:-0}"
CPYTHON_VERSION="${CPYTHON_VERSION:-3.12.7}"
CPYTHON_RELEASE="${CPYTHON_RELEASE:-20241016}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/dist}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "[1/4] Building the daemon wheel..."
python3 -m pip wheel --no-deps -q -w "$STAGE/wheels" "$REPO_ROOT/daemon"

echo "[2/4] Resolving dependencies for $PLATFORM / $ABI..."
# --only-binary=:all: because the target has no compiler; a source
# distribution here becomes a build failure on the far side, hours later.
python3 -m pip download -q -d "$STAGE/wheels" \
    --only-binary=:all: \
    --platform "$PLATFORM" \
    --python-version "$PY_VERSION" \
    --implementation cp \
    --abi "$ABI" \
    -r "$REPO_ROOT/daemon/requirements.txt" "httpx[socks]"

if [ "$WITH_PYTHON" = "1" ]; then
  echo "[3/4] Fetching portable CPython $CPYTHON_VERSION..."
  TARBALL="cpython-$CPYTHON_VERSION+$CPYTHON_RELEASE-x86_64-unknown-linux-gnu-install_only.tar.gz"
  curl -fsSL -o "$STAGE/$TARBALL" \
    "https://github.com/astral-sh/python-build-standalone/releases/download/$CPYTHON_RELEASE/$TARBALL"
else
  echo "[3/4] Skipping CPython (WITH_PYTHON=1 to include it)."
fi

echo "[4/4] Writing the installer..."
cat > "$STAGE/install.sh" <<'INSTALLER'
#!/usr/bin/env bash
# Install or update the worker daemon from this bundle. No network required.
#
#   ./install.sh [target-directory]
#
# Defaults to ~/.gpu-daemon, or ~/.gpu-daemon-$INSTANCE when INSTANCE is set —
# several machines sharing one home each need their own.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTANCE="${INSTANCE:-}"
DEFAULT_DIR="$HOME/.gpu-daemon${INSTANCE:+-$INSTANCE}"
TARGET="${1:-$DEFAULT_DIR}"

PYTHON="${PYTHON:-python3}"
if [ -d "$HERE/python" ]; then
  PYTHON="$HERE/python/bin/python3"
elif [ -f "$HERE"/cpython-*.tar.gz ]; then
  echo "Unpacking the bundled interpreter..."
  tar -xzf "$HERE"/cpython-*.tar.gz -C "$HERE"
  PYTHON="$HERE/python/bin/python3"
fi

echo "Interpreter: $PYTHON"
"$PYTHON" --version

mkdir -p "$TARGET"
if [ ! -x "$TARGET/venv/bin/python" ]; then
  echo "Creating $TARGET/venv..."
  "$PYTHON" -m venv "$TARGET/venv"
fi

echo "Installing from $HERE/wheels..."
# Dependencies first; unchanged ones are left alone.
"$TARGET/venv/bin/python" -m pip install -q --no-index \
    --find-links "$HERE/wheels" gpu-daemon

# Then the daemon itself, unconditionally. Its version is pinned at 0.1.0, so
# pip cannot tell two builds apart and --upgrade would silently install
# nothing over an existing install.
"$TARGET/venv/bin/python" -m pip install -q --no-index \
    --find-links "$HERE/wheels" --force-reinstall --no-deps gpu-daemon

echo
echo "Installed: $("$TARGET/venv/bin/gpu-daemon" --version)"
echo "Run it:    $TARGET/venv/bin/gpu-daemon --config $TARGET/config.yaml"
INSTALLER
chmod +x "$STAGE/install.sh"

mkdir -p "$OUT_DIR"
ARCHIVE="$OUT_DIR/sheshnag-daemon-offline-$(date +%Y%m%d).tar.gz"
tar -czf "$ARCHIVE" -C "$STAGE" .

echo
echo "Bundle: $ARCHIVE"
echo "Size:   $(du -h "$ARCHIVE" | cut -f1)"
echo "Wheels: $(ls "$STAGE/wheels" | wc -l)"
echo
echo "Ship it, then on the target:"
echo "    tar -xzf $(basename "$ARCHIVE") -C ~/bundle && ~/bundle/install.sh"
