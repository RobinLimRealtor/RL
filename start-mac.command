#!/bin/bash
# Double-click this file to start Auto Video Editor on a Mac.
cd "$(dirname "$0")" || exit 1

PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done

if [ -z "$PY" ]; then
  echo ""
  echo "  Python 3.10 or newer is needed and was not found."
  echo "  Opening the download page. Install it, then double-click this file again."
  echo ""
  open "https://www.python.org/downloads/"
  read -r -p "Press Enter to close..."
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "  Setting up for the first time. This takes a few minutes..."
  "$PY" -m venv .venv || { echo "Setup failed."; read -r -p "Press Enter to close..."; exit 1; }
fi

echo "  Checking everything is installed..."
.venv/bin/python -m pip install --disable-pip-version-check -q --upgrade pip >/dev/null 2>&1
if ! .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt; then
  echo ""
  echo "  Setup failed. Check your internet connection and try again."
  echo "  If it keeps failing, delete the hidden .venv folder here and retry."
  read -r -p "Press Enter to close..."
  exit 1
fi

.venv/bin/python -m autoedit
