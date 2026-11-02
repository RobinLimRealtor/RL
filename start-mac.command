#!/bin/bash
# Double-click this file to start Auto Video Editor on a Mac.
# Nothing to install first: the first start downloads everything it needs
# (including its own copy of Python) into this folder. No password needed.
cd "$(dirname "$0")" || exit 1
HERE="$(pwd)"
TOOLS="$HERE/.tools"
UV="$TOOLS/uv"
export UV_PYTHON_INSTALL_DIR="$TOOLS/python"
export UV_CACHE_DIR="$TOOLS/cache"

fail() {
  echo ""
  echo "  Setup didn't finish: $1"
  echo "  Check your internet connection and double-click this file again."
  echo "  If it keeps failing, delete the hidden .tools and .venv folders here and retry."
  echo ""
  read -r -p "  Press Enter to close..."
  exit 1
}

if [ ! -x "$UV" ]; then
  echo "  First start: downloading the setup helper (one time only)..."
  mkdir -p "$TOOLS"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$TOOLS" UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null \
    || fail "couldn't download the setup helper."
  [ -x "$UV" ] || fail "the setup helper is missing."
fi

# A working .venv on Python 3.10+? Otherwise (re)create it with uv's own Python.
if ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
  echo "  Setting up Python for the editor (one time only, a few minutes)..."
  rm -rf .venv
  "$UV" venv --quiet --python 3.12 --python-preference only-managed .venv || fail "couldn't set up Python."
fi

echo "  Checking everything is installed..."
"$UV" pip install --quiet --python .venv/bin/python -r requirements.txt || fail "couldn't install the editor's parts."

.venv/bin/python -m autoedit
