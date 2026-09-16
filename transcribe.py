"""Real-time transcription of system/speaker audio (Teams, Zoom, etc.).

Pipeline: capture.py (WASAPI loopback on Windows / ScreenCaptureKit on macOS
-> PCM) -> vad.py (Silero VAD -> speech segments) -> whisper.cpp
(pywhispercpp) -> timestamped stdout lines, appended to a transcript log
flushed after every segment.

Usage:
    python -m transcribe --list-devices
    python -m transcribe --device 10
    python -m transcribe --device 10 --model tiny.en-q5_1 --output transcripts\\meeting.log
    python -m transcribe --device 10 --streaming --url http://localhost:8000/ingest
        (each POST body includes: {sentence, model, language, session_id})
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import queue
import re
import sys
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional

from capture import LoopbackCapture, NoAudioError, list_loopback_devices
from vad import SpeechSegment, VoiceActivityDetector

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "base.en-q5_1"  # quantized, English-only, lightweight footprint
DEFAULT_MODELS_DIR = PROJECT_DIR / "models"
DEFAULT_OUTPUT = PROJECT_DIR / "transcripts" / "transcript.log"

NO_AUDIO_WARNING_S = 15.0  # how long to wait before nudging the user about --device

# whisper.cpp's stock tiny/base models sometimes emit bracketed filler
# annotations on non-speech audio (breathing, background noise the VAD let
# through) - e.g. [BLANK_AUDIO], (silence), [ Music ]. Rather than an exact
# token list (which misses wording/whitespace variants), drop any line that
# is *entirely* one bracketed/parenthesized annotation.
_FILLER_RE = re.compile(r"^[\[(][^\[\]()]*[\])]$")


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


def _validate_url(url: Optional[str]) -> Optional[str]:
    """Return an error message if `url` is missing an http(s) scheme, else None."""
    if url and urllib.parse.urlparse(url).scheme not in ("http", "https"):
        return f"--url must start with http:// or https:// (got {url!r})"
    return None


class SentencePoster:
    """POSTs transcribed sentences to a URL on a background thread, so a slow
    or unreachable streaming endpoint never stalls the transcription loop.

    Sentences are queued (bounded; oldest-pending are dropped with a warning
    on overflow rather than blocking the caller) and posted with a small
    retry on failure.
    """

    def __init__(
        self,
        url: str,
        model: str,
        language: str,
        session_id: str,
        max_retries: int = 2,
        timeout: float = 5.0,
    ):
        self.url = url
        self.model = model
        self.language = language
        self.session_id = session_id
        self.max_retries = max_retries
        self.timeout = timeout

        self._queue: "queue.Queue[Optional[str]]" = queue.Queue(maxsize=256)
        self._thread = threading.Thread(target=self._run, name="sentence-poster", daemon=True)
        self._thread.start()

    def post(self, sentence: str) -> None:
        try:
            self._queue.put_nowait(sentence)
        except queue.Full:
            print(f"WARNING: streaming queue full, dropping sentence: {sentence!r}", file=sys.stderr)

    def _run(self) -> None:
        while True:
            sentence = self._queue.get()
            if sentence is None:  # shutdown sentinel from stop()
                return
            self._post_with_retry(sentence)

    def _post_with_retry(self, sentence: str) -> None:
        body = json.dumps(
            {"sentence": sentence, "model": self.model, "language": self.language, "session_id": self.session_id}
        ).encode("utf-8")
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(
                self.url, data=body, method="POST", headers={"Content-Type": "application/json"}
            )
            try:
                urllib.request.urlopen(req, timeout=self.timeout).close()
                return
            except (urllib.error.URLError, OSError) as exc:
                if attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                else:
                    print(
                        f"WARNING: failed to POST sentence to {self.url} after "
                        f"{self.max_retries + 1} attempts: {exc}",
                        file=sys.stderr,
                    )

    def stop(self, timeout: float = 5.0) -> None:
        self._queue.put(None)
        self._thread.join(timeout=timeout)


def _clean_text(segments) -> str:
    parts = []
    for seg in segments:
        text = seg.text.strip()
        if not text or _FILLER_RE.match(text):
            continue
        parts.append(text)
    return " ".join(parts).strip()


def _process_segment(
    segment: SpeechSegment,
    model,
    args: argparse.Namespace,
    session_start: dt.datetime,
    writer: Optional[TranscriptWriter],
    poster: Optional[SentencePoster],
) -> None:
    segments = model.transcribe(segment.audio)
    text = _clean_text(segments)
    if not text:
        return

    if args.streaming:
        print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {text}")
        poster.post(text)
    else:
        line_ts = (session_start + dt.timedelta(seconds=segment.start_s)).strftime("%H:%M:%S")
        writer.write(f"[{line_ts}] {text}")


def run(args: argparse.Namespace) -> int:
    session_start = dt.datetime.now()
    session_id = str(uuid.uuid4())
    writer: Optional[TranscriptWriter] = None
    poster: Optional[SentencePoster] = None
    if args.streaming:
        print(f"# Transcript started {session_start.isoformat(timespec='seconds')} | model={args.model} | streaming to {args.url}")
        poster = SentencePoster(args.url, args.model, args.language, session_id)
    else:
        writer = TranscriptWriter(Path(args.output))
        writer.write(f"# Transcript started {session_start.isoformat(timespec='seconds')} | model={args.model}")

    cap_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=1024)
    seg_queue: "queue.Queue[SpeechSegment]" = queue.Queue(maxsize=64)

    cap = LoopbackCapture(cap_queue, device_index=args.device)
    try:
        cap.start()
    except (NoAudioError, ValueError, OSError) as exc:
        print(f"ERROR: could not start audio capture: {exc}", file=sys.stderr)
        print("Run 'python -m transcribe --list-devices' to see available devices.", file=sys.stderr)
        if writer is not None:
            writer.close()
        if poster is not None:
            poster.stop()
        return 1

    print(f"Capturing from: {cap.device_info['name']} ({cap.device_info['defaultSampleRate']:.0f} Hz)")

    detector = VoiceActivityDetector(cap_queue, seg_queue)
    detector.start()

    print(f"Loading whisper.cpp model '{args.model}'...")
    try:
        from pywhispercpp.model import Model

        model = Model(
            args.model, models_dir=str(DEFAULT_MODELS_DIR), n_threads=args.threads, language=args.language
        )
    except Exception as exc:
        print(f"ERROR: could not load whisper.cpp model {args.model!r}: {exc}", file=sys.stderr)
        detector.stop()
        cap.stop()
        if writer is not None:
            writer.close()
        if poster is not None:
            poster.stop()
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
            _process_segment(segment, model, args, session_start, writer, poster)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopping...")
        # detector.stop() flushes whatever utterance was still in progress
        # onto seg_queue, so draining it below picks up the session's final
        # segment instead of silently dropping it.
        detector.stop()
        while True:
            try:
                trailing_segment = seg_queue.get_nowait()
            except queue.Empty:
                break
            _process_segment(trailing_segment, model, args, session_start, writer, poster)
        cap.stop()
        if writer is not None:
            writer.close()
        if poster is not None:
            poster.stop()

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
        help=f"Transcript log file path, appended to (default: {DEFAULT_OUTPUT}). Ignored with --streaming.",
    )
    parser.add_argument("--threads", type=int, default=4, help="CPU threads for whisper.cpp inference (default: 4).")
    parser.add_argument(
        "--language",
        type=str,
        default="en",
        help="whisper.cpp language code (default: en). Also sent as 'language' in --streaming POST bodies.",
    )
    parser.add_argument(
        "--streaming",
        action="store_true",
        help="POST each transcribed sentence to --url instead of writing a log file.",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="Endpoint to POST {sentence, model, language, session_id} to for each sentence. Required with --streaming.",
    )
    args = parser.parse_args(argv)

    if args.streaming and not args.url:
        parser.error("--streaming requires --url")

    url_error = _validate_url(args.url)
    if url_error:
        parser.error(url_error)

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
