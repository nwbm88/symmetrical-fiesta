#!/usr/bin/env bash
# Live Show Archiver - Linux/macOS launcher.  Mirrors run.bat.
set -e
cd "$(dirname "$0")"

VENV=.venv
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
    echo "First run: setting up the Python environment..."
    python3 -m venv "$VENV"
    "$PY" -m pip install --upgrade pip >/dev/null
    "$PY" -m pip install -r requirements.txt
    echo "Setup complete."
fi

if ! command -v ffmpeg >/dev/null 2>&1 && [ ! -x "ffmpeg/bin/ffmpeg" ]; then
    echo "NOTE: ffmpeg not found - install it (apt install ffmpeg / brew install ffmpeg)"
    echo "      to extract and split audio."
fi

exec "$PY" -m livearchiver.cli gui "$@"
