#!/usr/bin/env bash
# Interactive launcher for macOS: prompts for model, streaming vs log-file
# mode, and the URL/filename that mode needs, then runs transcribe.py in
# the background until interrupted (Ctrl+C).
set -euo pipefail
cd "$(dirname "$0")"

VENV_DIR=".venv"
PY="$VENV_DIR/bin/python"

if [ ! -x "$PY" ]; then
    echo "No virtualenv found at $VENV_DIR yet - let's create one."
    echo "macOS often aliases 'python3'/'pip' to an old system Python (e.g. 3.9)"
    echo "even when a newer one (e.g. python3.11) is installed and required."
    echo

    PY_CANDIDATES=()
    for name in python3.13 python3.12 python3.11 python3.10 python3.9 python3 python; do
        if command -v "$name" >/dev/null 2>&1; then
            PY_CANDIDATES+=("$name ($(command -v "$name"))")
        fi
    done
    PY_CANDIDATES+=("Other (enter a command or path)")

    echo "Select the Python interpreter to create the virtualenv with:"
    PS3="Enter number: "
    select PY_CHOICE_LABEL in "${PY_CANDIDATES[@]}"; do
        if [ -n "${PY_CHOICE_LABEL:-}" ]; then
            break
        fi
        echo "Invalid choice, try again."
    done

    if [ "$PY_CHOICE_LABEL" = "Other (enter a command or path)" ]; then
        read -r -p "Python command or path to use: " PY_CMD
    else
        PY_CMD="${PY_CHOICE_LABEL%% (*}"
    fi

    if ! command -v "$PY_CMD" >/dev/null 2>&1 && [ ! -x "$PY_CMD" ]; then
        echo "ERROR: '$PY_CMD' is not a runnable command or path." >&2
        exit 1
    fi

    echo "Using: $("$PY_CMD" --version 2>&1) ($PY_CMD)"
    "$PY_CMD" -m venv "$VENV_DIR"

    echo "Installing dependencies into $VENV_DIR (this can take a few minutes)..."
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install -r requirements.txt
    echo
fi

# A venv can exist (pass the -x check above) but still be unusable - e.g. it
# was created with the wrong interpreter, or dependencies were never
# actually installed into it (both happened here: `python -m venv .venv`
# silently picked an old system Python, and a mistyped `python3.11 install
# -r requirements.txt` - missing `-m pip` - never installed anything). Catch
# that now instead of launching transcribe.py and having it die instantly.
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
    echo "ERROR: $PY is $("$PY" --version 2>&1), but this project requires Python 3.11+." >&2
    echo "Fix: rm -rf $VENV_DIR && ./$(basename "$0")  (then pick a 3.11+ interpreter)" >&2
    exit 1
fi

if ! "$PY" -c 'import numpy' 2>/dev/null; then
    echo "$VENV_DIR exists but required packages aren't installed in it - installing now..."
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install -r requirements.txt
    echo
    if ! "$PY" -c 'import numpy' 2>/dev/null; then
        echo "ERROR: dependency install into $VENV_DIR failed. Re-run manually to see why:" >&2
        echo "  $PY -m pip install -r requirements.txt" >&2
        exit 1
    fi
fi

MODELS=(
    "tiny.en" "tiny.en-q5_1" "tiny.en-q8_0"
    "base.en" "base.en-q5_1" "base.en-q8_0"
    "small.en" "small.en-q5_1" "small.en-q8_0"
    "medium.en" "medium.en-q5_0" "medium.en-q8_0"
    "large-v3" "large-v3-q5_0" "large-v3-turbo" "large-v3-turbo-q5_0"
)

echo "Select a whisper.cpp model:"
PS3="Enter number: "
select MODEL in "${MODELS[@]}"; do
    if [ -n "${MODEL:-}" ]; then
        break
    fi
    echo "Invalid choice, try again."
done

echo
echo "Available capture devices:"
"$PY" -m transcribe --list-devices
echo
read -r -p "Device index from the list above (leave blank for whole-system default): " DEVICE

echo
echo "Select mode:"
PS3="Enter number: "
select MODE_LABEL in "Streaming (POST each sentence to a URL)" "Log file (append to a transcript file)"; do
    case "$REPLY" in
        1) STREAMING=1; break ;;
        2) STREAMING=0; break ;;
        *) echo "Invalid choice, try again." ;;
    esac
done

TRANSCRIBE_ARGS=(-m transcribe --model "$MODEL")
if [ -n "$DEVICE" ]; then
    TRANSCRIBE_ARGS+=(--device "$DEVICE")
fi

if [ "$STREAMING" -eq 1 ]; then
    read -r -p "URL to POST each transcribed sentence to (must start with http:// or https://): " URL
    while [ -z "$URL" ]; do
        read -r -p "URL cannot be empty. URL to POST each transcribed sentence to: " URL
    done
    TRANSCRIBE_ARGS+=(--streaming --url "$URL")
else
    read -r -p "Transcript log filename [transcripts/transcript.log]: " OUTPUT
    OUTPUT="${OUTPUT:-transcripts/transcript.log}"
    TRANSCRIBE_ARGS+=(--output "$OUTPUT")
fi

echo
echo "Starting: $PY ${TRANSCRIBE_ARGS[*]}"
"$PY" "${TRANSCRIBE_ARGS[@]}" &
PID=$!
echo "Running in background (PID $PID). Press Ctrl+C to stop."

trap 'kill -INT "$PID" 2>/dev/null || true' INT TERM
wait "$PID"
