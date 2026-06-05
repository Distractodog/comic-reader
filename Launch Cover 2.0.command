#!/bin/bash
cd "$(dirname "$0")" || exit 1
export DYLD_LIBRARY_PATH="/opt/homebrew/opt/expat/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
export COVER_LAUNCHED_FROM_APP=1
if [[ ! -x ./venv/bin/python ]]; then
  echo "Cover 2.0: venv not found. Run setup from the comic-reader folder first."
  read -r -p "Press Return to close…"
  exit 1
fi
exec ./venv/bin/python ./src/main.py
