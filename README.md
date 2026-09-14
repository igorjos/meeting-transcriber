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
transcribe.py        main entrypoint: whisper.cpp (pywhispercpp) -> stdout + log file
```

Capture and inference run on separate threads connected by `queue.Queue`, so
a slow transcription pass never drops or blocks audio capture.

## Setup

Requires Python 3.11.

### Windows

Also needs the
[Microsoft Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe)
(needed for torch's native DLLs — `silero-vad` depends on torch).

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

### macOS

macOS 13 (Ventura) or later, for ScreenCaptureKit's audio-capture API.

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

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
```

Stop with Ctrl+C. The transcript is flushed to disk after every line, so
killing the process never loses a completed segment.

### Flags

| Flag | Default | Description |
|---|---|---|
| `--list-devices` | — | Enumerate capture targets and exit (Windows: WASAPI loopback devices; macOS: system audio + one entry per running app). |
| `--device N` | auto-detected default output (Windows) / whole system (macOS) | Capture target index from `--list-devices`. |
| `--model NAME` | `base.en-q5_1` | whisper.cpp model name (`tiny.en`, `tiny.en-q5_1`, `base.en-q5_1`, ...) or a path to a `.bin` file. Auto-downloaded into `models/` on first use. |
| `--output PATH` | `transcripts\transcript.log` | Transcript log file (appended to, flushed after every line). |
| `--threads N` | `4` | CPU threads for whisper.cpp inference. |

## Model choice

Default is `base.en-q5_1`: English-only, quantized, ~59 MB — enough accuracy
for meeting speech while staying light on CPU/RAM. Use `tiny.en-q5_1` for an
even smaller/faster (less accurate) option. Multilingual and unquantized
models work too via `--model`, just bigger downloads.

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
