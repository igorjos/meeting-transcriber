# call-transcriber

Real-time transcription of system/speaker audio (Teams, Zoom, or anything
else making noise on your machine) — a standalone CLI, not a plugin. Runs on
Windows and macOS.

```
capture.py          Platform dispatch -> 16 kHz mono PCM16 -> queue.Queue
capture_windows.py     Windows: WASAPI loopback (PyAudioWPatch)
capture_macos.py       macOS: ScreenCaptureKit audio capture
audio_common.py      Shared types/downmix/resample used by both backends
vad.py               Silero VAD, streaming -> speech-segment queue
transcribe.py        main entrypoint: whisper.cpp (pywhispercpp) -> stdout + log file (or --streaming POSTs)
run_macos.sh          Interactive launcher (macOS): prompts for model/mode, runs in background
run_windows.ps1        Interactive launcher (Windows): prompts for model/mode, runs in background
```

Capture and inference run on separate threads connected by `queue.Queue`, so
a slow transcription pass never drops or blocks audio capture.

## Setup

Requires Python 3.11.

### Windows

Also needs the
[Microsoft Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe)
(needed for torch's native DLLs — `silero-vad` depends on torch).

Run `.\run_windows.ps1` — if `.venv` doesn't exist yet, it lets you pick
which Python to create it with (it probes the `py` launcher for 3.13 down to
3.9, plus `python3`/`python` on `PATH`, plus an "Other" option for a custom
command/path) and installs `requirements.txt` into it automatically. It also
double-checks an existing `.venv` before using it — verifies the interpreter
is 3.11+ and that dependencies actually import (auto-(re)installing
`requirements.txt` if not) — so a broken/incomplete setup fails with a clear
message instead of launching and dying silently.

To set up manually instead:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

### macOS

macOS 13 (Ventura) or later, for ScreenCaptureKit's audio-capture API.

Run `./run_macos.sh` — if `.venv` doesn't exist yet, it lets you pick which
Python to create it with (it lists every `python3.x`/`python3`/`python` it
finds on your `PATH`, plus an "Other" option for a custom command/path) and
installs `requirements.txt` into it automatically. This matters because
macOS commonly aliases plain `python3`/`pip` to an old system Python (e.g.
3.9) even when a newer one (e.g. `python3.11`) is installed and required —
picking explicitly avoids silently building the venv with the wrong one.

To set up manually instead, pick your interpreter explicitly (don't rely on
a bare `python3`/`pip` unless you've confirmed what they resolve to) and use
`pip install`, not a bare `install` (a typo there — e.g. `python3.11 install
-r requirements.txt` — silently does nothing, since it runs `python3.11` on
a nonexistent file called `install` instead of invoking pip):

```bash
python3.11 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

`./run_macos.sh` also double-checks an existing `.venv` before using it —
it verifies the interpreter is 3.11+ and that dependencies actually import
(auto-(re)installing `requirements.txt` if not), so a broken/incomplete
manual setup fails with a clear message instead of launching and dying
silently.

The first time you run it, grant **Screen Recording** permission (System
Settings → Privacy & Security → Screen Recording) to whichever process is
running Python — your terminal app, or `python3.x` itself if run some other
way — then quit and reopen that app and retry. This is required by macOS for
any system-audio capture, even though no video is recorded or stored. No
virtual audio driver or Multi-Output Device setup needed.

## Usage

```powershell
# Windows - find your loopback device index first, never hardcode it, it varies per machine.
.\.venv\Scripts\python.exe -m transcribe --list-devices

# Run it
.\.venv\Scripts\python.exe -m transcribe --device 10

# Custom model / output log
.\.venv\Scripts\python.exe -m transcribe --device 10 --model tiny.en-q5_1 --output transcripts\meeting.log
```

```bash
# macOS - list capture targets (0 = whole system, N = one running app's audio only)
./.venv/bin/python -m transcribe --list-devices

# Run it (device 0, or omit --device, captures everything)
./.venv/bin/python -m transcribe --device 0

# Capture just one app's audio (e.g. isolate Zoom, skip notification dings)
./.venv/bin/python -m transcribe --device 2 --model tiny.en-q5_1 --output transcripts/meeting.log

# Streaming mode: POST each sentence to a URL instead of writing a log file
./.venv/bin/python -m transcribe --device 0 --streaming --url http://localhost:8000/ingest
```

Stop with Ctrl+C. The transcript is flushed to disk after every line, so
killing the process never loses a completed segment.

### Launcher scripts

Instead of remembering flags, run the interactive launcher — it prompts for
the model (including quantization), the capture device (lists devices for
you first, blank = auto-detected default), streaming vs. log-file mode, and
the URL/filename that mode needs, then starts `transcribe.py` in the
background until you press Ctrl+C. On first run (or if `.venv` is missing or
broken) it also walks you through creating/repairing the virtualenv:

```bash
# macOS
./run_macos.sh
```

```powershell
# Windows (if scripts are blocked, run once: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned)
.\run_windows.ps1
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `--list-devices` | — | Enumerate capture targets and exit (Windows: WASAPI loopback devices; macOS: system audio + one entry per running app). |
| `--device N` | auto-detected default output (Windows) / whole system (macOS) | Capture target index from `--list-devices`. |
| `--model NAME` | `base.en-q5_1` | whisper.cpp model name (`tiny.en`, `tiny.en-q5_1`, `base.en-q5_1`, ...) or a path to a `.bin` file. Auto-downloaded into `models/` on first use. |
| `--output PATH` | `transcripts\transcript.log` | Transcript log file (appended to, flushed after every line). Ignored with `--streaming`. |
| `--threads N` | `4` | CPU threads for whisper.cpp inference. |
| `--language CODE` | `en` | whisper.cpp language code. Also sent as `language` in `--streaming` POST bodies. |
| `--streaming` | off | POST each transcribed sentence to `--url` instead of writing a log file (log file is disabled in this mode). Requires `--url`. |
| `--url URL` | — | Endpoint to POST `{"sentence": ..., "model": ..., "language": ..., "session_id": ...}` (JSON) to for each transcribed sentence. Required with `--streaming`; must start with `http://` or `https://` (validated at startup). Session ID is a unique identifier generated when the app starts, allowing you to correlate all sentences from a single transcription session. POSTs happen on a background thread with a small retry, so a slow/unreachable endpoint never stalls transcription. |

## Model choice

Default is `base.en-q5_1`: English-only, quantized, ~59 MB — enough accuracy
for meeting speech while staying light on CPU/RAM. Use `tiny.en-q5_1` for an
even smaller/faster (less accurate) option. Multilingual and unquantized
models work too via `--model`, just bigger downloads.

Battle-tested with `base.en-q5_1` (English) capturing Google Meet in a
browser and the Teams desktop app.

## Troubleshooting

**"No audio captured" / nothing gets transcribed.** By far the most common
cause is the wrong `--device` index. On Windows, loopback device indices are
assigned by the OS and are not stable across reboots or hardware changes;
always re-run `--list-devices` and pick the entry that matches your active
output (the `[default]` tag marks the one mirroring your current default
speaker) — WASAPI loopback only delivers audio while something is actually
playing through that specific device, so also check the right app is
actually routed to it. On macOS, per-app indices (`--device N` for N≥1) are
re-numbered fresh from the currently running apps every time, so re-run
`--list-devices` right before picking one rather than reusing an old index;
`--device 0` (or omitting `--device`) always means "whole system," which
isn't affected by that renumbering.

Note: only *prolonged silence before any audio has ever arrived* is treated
as a fatal "wrong device" error (fails fast instead of hanging forever). A
quiet stretch after audio has already been flowing — e.g. a muted meeting —
is normal and only logged as a `WARNING`, never fatal.

**Torch / `c10.dll` failed to load (Windows).** Install the
[VC++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe)
linked above and retry.

**"macOS denied screen/audio capture permission" (macOS).** Grant Screen
Recording permission as described under Setup above, then quit and reopen
the app/terminal you're running Python from (a permission grant doesn't take
effect for an already-running process) and retry.

**Debugging capture/VAD in isolation.** Both modules are runnable standalone
before you ever touch `transcribe.py`:

```powershell
# Windows
.\.venv\Scripts\python.exe capture.py --device 10 --seconds 5 --wav test.wav
.\.venv\Scripts\python.exe vad.py --device 10 --seconds 10
```

```bash
# macOS
./.venv/bin/python capture.py --device 0 --seconds 5 --wav test.wav
./.venv/bin/python vad.py --device 0 --seconds 10
```

## Tests

`tests/` covers the pure, hardware-free helpers (audio downmix/resample math,
filler-line filtering, `--url` validation) with stdlib `unittest`:

```powershell
# Windows
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

```bash
# macOS
./.venv/bin/python -m unittest discover -s tests -v
```

## Changelog

Every change to this project — code, docs, or config — gets logged in
[`CHANGELOG.md`](CHANGELOG.md) before it's considered done. See that file
for the format.

## Notes

- Capture is resampled to 16 kHz mono (what both Silero VAD and whisper.cpp
  expect) via `audio_common.py`, shared by both `capture_windows.py` and
  `capture_macos.py`, regardless of the device's native rate/channel count —
  downstream modules always see a consistent format.
- `silero-vad` pulls in `torch`/`torchaudio` as a hard dependency even though
  only its small speech-detection model is used — that's the heaviest part
  of this install, unrelated to the (deliberately lightweight) ASR model.
- This is a plain `python -m transcribe` CLI — no packaging/`.exe`/`.app` step.
- macOS's ScreenCaptureKit backend has no native "loopback device" concept;
  `--device` there selects a capture *target* instead (whole system, or one
  running app) — see `capture_macos.py` for how that maps onto the same
  `--device N` flag Windows uses for an actual device index.
