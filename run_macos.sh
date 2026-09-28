#!/usr/bin/env bash
# Interactive launcher for macOS: prompts for email, model, client/topic,
# streaming vs log-file mode, and the URL/filename that mode needs (reusing
# what was saved in .env last time), then runs transcribe.py until
# interrupted (Ctrl+C).
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

# Email, model, the streaming endpoint URL and the API key header are
# remembered in .env (managed by settings.py, which also validates them); the
# client/topic and the store (upload) URL are asked every session and never
# saved. A saved value is used as-is unless the user says they want to change
# saved settings; a missing one is asked for.
env_get() { "$PY" settings.py get "$1"; }
env_set() { SETTINGS_VALUE="$2" "$PY" settings.py set "$1"; }

# resolve_setting KEY LABEL PROMPT -> RESOLVED
resolve_setting() {
    local key="$1" label="$2" prompt="$3" saved="" answer
    if saved=$(env_get "$key"); then
        if [ "$CHANGE_SAVED" -eq 0 ]; then
            RESOLVED="$saved"
            echo "Using saved $label: $saved"
            return
        fi
        read -r -p "$prompt [$saved]: " answer
        answer="${answer:-$saved}"
        if [ "$answer" = "$saved" ]; then
            RESOLVED="$saved"
            return
        fi
    else
        read -r -p "$prompt: " answer
    fi
    until env_set "$key" "$answer"; do
        read -r -p "$prompt: " answer
    done
    RESOLVED="$(env_get "$key")"
}

# choose_model -> MODEL
choose_model() {
    local saved="" answer i
    if saved=$(env_get TRANSCRIBER_MODEL); then
        if [ "$CHANGE_SAVED" -eq 0 ]; then
            MODEL="$saved"
            echo "Using saved model: $MODEL"
            return
        fi
    else
        saved=""
    fi
    echo "Select a whisper.cpp model:"
    for i in "${!MODELS[@]}"; do
        echo "  [$((i + 1))] ${MODELS[$i]}"
    done
    while true; do
        if [ -n "$saved" ]; then
            read -r -p "Enter number [Enter keeps '$saved']: " answer
        else
            read -r -p "Enter number: " answer
        fi
        if [ -z "$answer" ] && [ -n "$saved" ]; then
            MODEL="$saved"
            return
        fi
        if [[ "$answer" =~ ^[1-9][0-9]*$ ]] && [ "$answer" -le "${#MODELS[@]}" ]; then
            MODEL="${MODELS[$((answer - 1))]}"
            env_set TRANSCRIBER_MODEL "$MODEL"
            return
        fi
        echo "Invalid choice, try again."
    done
}

# resolve_api_key -> API_KEY_HEADER ('Name: value', empty when the endpoint needs none)
resolve_api_key() {
    local saved="" have_saved=0 answer
    if saved=$(env_get TRANSCRIBER_AUTH_HEADER); then
        have_saved=1
        if [ "$CHANGE_SAVED" -eq 0 ]; then
            API_KEY_HEADER="$saved"
            if [ -n "$saved" ]; then echo "Using saved API key."; else echo "No API key saved (the endpoint needs none)."; fi
            return
        fi
    fi
    echo "API key header sent with every POST, as 'Name: value' (e.g. 'Authorization: Bearer <token>')."
    if [ "$have_saved" -eq 1 ]; then
        echo "Press Enter to keep the saved one, or type 'none' to remove it."
    else
        echo "Leave blank if the endpoint needs no key."
    fi
    while true; do
        read -r -p "API key header: " answer
        if [ -z "$answer" ] && [ "$have_saved" -eq 1 ]; then
            API_KEY_HEADER="$saved"
            return
        fi
        if [ "$answer" = "none" ]; then
            answer=""
        fi
        if env_set TRANSCRIBER_AUTH_HEADER "$answer"; then
            API_KEY_HEADER="$(env_get TRANSCRIBER_AUTH_HEADER)"
            return
        fi
    done
}

CHANGE_SAVED=0
if "$PY" settings.py show; then
    echo
    read -r -p "Change any of the saved settings? [y/N]: " CHANGE_ANSWER
    if [[ "$CHANGE_ANSWER" =~ ^[Yy] ]]; then
        CHANGE_SAVED=1
    fi
fi
echo

resolve_setting TRANSCRIBER_EMAIL "email" "Your email address"
EMAIL="$RESOLVED"
choose_model

read -r -p "Client name / meeting topic (asked every session, never saved): " TOPIC
while [[ -z "${TOPIC//[[:space:]]/}" ]]; do
    echo "Client name / meeting topic cannot be empty."
    read -r -p "Client name / meeting topic: " TOPIC
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

TRANSCRIBE_ARGS=(-m transcribe --model "$MODEL" "--email=$EMAIL" "--topic=$TOPIC")
if [ -n "$DEVICE" ]; then
    TRANSCRIBE_ARGS+=(--device "$DEVICE")
fi

if [ "$STREAMING" -eq 1 ]; then
    resolve_setting TRANSCRIBER_URL "stream URL" "URL to POST each transcribed sentence to (must start with http:// or https://)"
    TRANSCRIBE_ARGS+=(--streaming --url "$RESOLVED")
    resolve_api_key
else
    read -r -p "Upload the log file to a remote location when the session ends? [y/N]: " UPLOAD
    if [[ "$UPLOAD" =~ ^[Yy] ]]; then
        read -r -p "Store URL to POST the log file to (must start with http:// or https://): " STORE_URL
        while [ -z "$STORE_URL" ]; do
            read -r -p "Store URL cannot be empty. Store URL to POST the log file to: " STORE_URL
        done
        TRANSCRIBE_ARGS+=(--store-url "$STORE_URL")
        resolve_api_key
    fi
    read -r -p "Transcript log filename [transcripts/transcript.log]: " OUTPUT
    OUTPUT="${OUTPUT:-transcripts/transcript.log}"
    TRANSCRIBE_ARGS+=(--output "$OUTPUT")
fi

# The key goes to the app through the environment, not argv, so it never shows
# up in the process list or in the "Starting:" line below.
if [ -n "${API_KEY_HEADER:-}" ]; then
    export TRANSCRIBER_AUTH_HEADER="$API_KEY_HEADER"
fi

echo
echo "Starting: $PY ${TRANSCRIBE_ARGS[*]}"
echo "Running (PID $$). Press Ctrl+C to stop (press it again to force-quit if shutdown stalls)."

# exec, not `&` + wait: a job started with `&` from a non-interactive script
# inherits "ignore SIGINT", so Python never installs its Ctrl+C handler and
# keeps running after the launcher exits. exec makes the app the foreground
# process, so the terminal's Ctrl+C reaches it directly.
exec "$PY" "${TRANSCRIBE_ARGS[@]}"
