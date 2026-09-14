"""Real-time transcription of system/speaker audio (Teams, Zoom, etc.).

Pipeline: capture.py (WASAPI loopback on Windows / ScreenCaptureKit on macOS
-> PCM) -> vad.py (Silero VAD -> speech segments) -> whisper.cpp
(pywhispercpp) -> timestamped stdout lines, appended to a transcript log
flushed after every segment.

Usage:
    python -m transcribe --list-devices
    python -m transcribe --device 10
    python -m transcribe --device 10 --model tiny.en-q5_1 --output transcripts\\meeting.log
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import queue
import sys
import time
from pathlib import Path
from typing import List, Optional

from capture import LoopbackCapture, NoAudioError, list_loopback_devices
from vad import SpeechSegment, VoiceActivityDetector

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "base.en-q5_1"  # quantized, English-only, lightweight footprint
DEFAULT_MODELS_DIR = PROJECT_DIR / "models"
DEFAULT_OUTPUT = PROJECT_DIR / "transcripts" / "transcript.log"

NO_AUDIO_WARNING_S = 15.0  # how long to wait before nudging the user about --device

# whisper.cpp's stock tiny/base models sometimes emit bracketed filler tokens
# on non-speech audio (breathing, background noise the VAD let through). Drop
# lines that are just that, so the transcript stays readable.
_FILLER_TOKENS = {
    "[BLANK_AUDIO]", "[SILENCE]", "(silence)", "[NOISE]", "[MUSIC]", "[APPLAUSE]",
}


class TranscriptWriter:
    """Prints timestamped lines to stdout and appends them to a log file,
    flushing (and fsyncing) after every line so nothing is lost if the
    process is killed mid-session."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def write(self, line: str) -> None:
        print(line, flush=True)
        self._fh.write(line + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        self._fh.close()


def _clean_text(segments) -> str:
    parts = []
    for seg in segments:
        text = seg.text.strip()
        if not text or text.upper() in _FILLER_TOKENS:
            continue
        parts.append(text)
    return " ".join(parts).strip()


def run(args: argparse.Namespace) -> int:
    output_path = Path(args.output)
    writer = TranscriptWriter(output_path)
    session_start = dt.datetime.now()
    writer.write(f"# Transcript started {session_start.isoformat(timespec='seconds')} | model={args.model}")

    cap_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=1024)
    seg_queue: "queue.Queue[SpeechSegment]" = queue.Queue(maxsize=64)

    cap = LoopbackCapture(cap_queue, device_index=args.device)
    try:
        cap.start()
    except (NoAudioError, ValueError, OSError) as exc:
        print(f"ERROR: could not start audio capture: {exc}", file=sys.stderr)
        print("Run 'python -m transcribe --list-devices' to see available devices.", file=sys.stderr)
        writer.close()
        return 1

    print(f"Capturing from: {cap.device_info['name']} ({cap.device_info['defaultSampleRate']:.0f} Hz)")

    detector = VoiceActivityDetector(cap_queue, seg_queue)
    detector.start()

    print(f"Loading whisper.cpp model '{args.model}'...")
    try:
        from pywhispercpp.model import Model

        model = Model(args.model, models_dir=str(DEFAULT_MODELS_DIR), n_threads=args.threads)
    except Exception as exc:
        print(f"ERROR: could not load whisper.cpp model {args.model!r}: {exc}", file=sys.stderr)
        detector.stop()
        cap.stop()
        writer.close()
        return 1

    print("Model loaded. Listening... (Ctrl+C to stop)\n")

    got_first_segment = False
    next_warning_at = time.monotonic() + NO_AUDIO_WARNING_S
    exit_code = 0

    try:
        while True:
            try:
                segment: SpeechSegment = seg_queue.get(timeout=0.5)
            except queue.Empty:
                try:
                    cap.raise_if_failed()
                except NoAudioError as exc:
                    print(f"\nERROR: {exc}", file=sys.stderr)
                    exit_code = 1
                    break
                if not got_first_segment and time.monotonic() > next_warning_at:
                    print(
                        "WARNING: no speech detected yet. If this is unexpected, confirm "
                        "--device matches the output that's actually playing audio "
                        "(run 'python -m transcribe --list-devices').",
                        file=sys.stderr,
                    )
                    next_warning_at = time.monotonic() + NO_AUDIO_WARNING_S * 2
                continue

            got_first_segment = True
            segments = model.transcribe(segment.audio)
            text = _clean_text(segments)
            if not text:
                continue

            line_ts = (session_start + dt.timedelta(seconds=segment.start_s)).strftime("%H:%M:%S")
            writer.write(f"[{line_ts}] {text}")
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopping...")
        detector.stop()
        cap.stop()
        writer.close()

    return exit_code


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m transcribe",
        description="Real-time transcription of system/speaker audio (Teams, Zoom, etc.).",
    )
    parser.add_argument("--list-devices", action="store_true", help="List loopback devices and exit.")
    parser.add_argument("--device", type=int, default=None, help="Loopback device index (see --list-devices).")
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"whisper.cpp model name (e.g. tiny.en, tiny.en-q5_1, base.en-q5_1) or a path to a .bin file (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(DEFAULT_OUTPUT),
        help=f"Transcript log file path, appended to (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument("--threads", type=int, default=4, help="CPU threads for whisper.cpp inference (default: 4).")
    args = parser.parse_args(argv)

    if args.list_devices:
        devices = list_loopback_devices()
        if not devices:
            hint = (
                "Is this Windows with an active audio output?"
                if sys.platform == "win32"
                else "Is Screen Recording permission granted (System Settings -> Privacy & Security)?"
            )
            print(f"No loopback/capture devices found. {hint}")
            return
        print("Available loopback ('record what you hear') devices:")
        for dev in devices:
            print(f"  {dev}")
        return

    sys.exit(run(args))


if __name__ == "__main__":
    main()
