"""Real-time transcription of system/speaker audio (Teams, Zoom, etc.).

Pipeline: capture.py (WASAPI loopback on Windows / ScreenCaptureKit on macOS
-> PCM) -> vad.py (Silero VAD -> speech segments) -> whisper.cpp
(pywhispercpp) -> timestamped stdout lines, appended to a transcript log
flushed after every segment.

Usage:
    python -m transcribe --list-devices
    python -m transcribe --device 10
    python -m transcribe --device 10 --model tiny.en-q5_1 --output transcripts\\meeting.log
    python -m transcribe --device 10 --topic "Acme kickoff" --email me@example.com
        (first line of the transcript: start time, model, language, topic, email)
    python -m transcribe --device 10 --streaming --url http://localhost:8000/ingest
        (each POST body includes: {sentence, model, language, session_id, speaker_id, topic})
    python -m transcribe --device 10 --output transcripts\\meeting.log --store-url http://localhost:8000/store
        (uploads this session's part of the log file as multipart/form-data, field
        "log", filename = the log's own name, when the session ends - including on
        a forced/second-Ctrl+C exit)

Set TRANSCRIBER_AUTH_HEADER="Authorization: Bearer <token>" (or pass --auth-header)
to add an auth header to every POST.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import queue
import re
import signal
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
from settings import AUTH_HEADER_KEY, parse_auth_header, validate_email, validate_url
from speaker_id import DEFAULT_SPEAKER_THRESHOLD, SpeakerRegistry
from vad import SpeechSegment, VoiceActivityDetector

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "base.en-q5_1"  # quantized, English-only, lightweight footprint
DEFAULT_MODELS_DIR = PROJECT_DIR / "models"
DEFAULT_OUTPUT = PROJECT_DIR / "transcripts" / "transcript.log"

NO_AUDIO_WARNING_S = 15.0  # how long to wait before nudging the user about --device
MAX_CONSECUTIVE_SEGMENT_ERRORS = 5  # give up if this many segments in a row fail to process
UPLOAD_MAX_WAIT_S = 15.0  # normal shutdown: 2 attempts x 5s timeout + backoff
FORCE_UPLOAD_MAX_WAIT_S = 4.0  # second Ctrl+C: one short attempt, then leave regardless
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

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
        # The file is append-only and can already hold earlier sessions;
        # remember where this session starts so only its part gets uploaded.
        self.start_offset = path.stat().st_size if path.exists() else 0
        self._fh = open(self.path, "a", encoding="utf-8")

    def write(self, line: str) -> None:
        print(line, flush=True)
        self._fh.write(line + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        self._fh.close()


def _insecure_transport_warning(url: Optional[str], flag: str) -> Optional[str]:
    """Warn (not fail) when transcripts/credentials would cross the network over plain http."""
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "http" and (parsed.hostname or "") not in _LOOPBACK_HOSTS:
        return (
            f"WARNING: {flag} uses plain http:// to a non-local host - meeting transcripts "
            "(and any --auth-header) will be sent unencrypted. Use https:// unless this is a trusted network."
        )
    return None


def _escape_form_value(value: str) -> str:
    """Escape a multipart Content-Disposition parameter the way browsers do (WHATWG form-data rules)."""
    return value.replace("\r", "%0D").replace("\n", "%0A").replace('"', "%22")


def _build_multipart_body(field_name: str, filename: str, content: bytes) -> "tuple[bytes, str]":
    """Encode a single-file multipart/form-data body. Returns (body, content_type)."""
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{_escape_form_value(field_name)}"; '
        f'filename="{_escape_form_value(filename)}"\r\n'
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Blocks urllib's default of transparently following a 3xx redirect. Left alone, a
    redirect turns our POST into a body-less GET on the new URL (per HTTP semantics for
    301/302/303) - the upload/sentence would silently vanish while we still report
    success - and resends our headers, including --auth-header, to whatever host the
    redirect names. redirect_request() returning None makes urlopen()/opener.open()
    raise HTTPError(<the 3xx code>) instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _urlopen_no_redirect(req: urllib.request.Request, timeout: float):
    """Like urllib.request.urlopen(req, timeout=timeout), but raises HTTPError on a 3xx
    response instead of following it - see _NoRedirectHandler."""
    return _NO_REDIRECT_OPENER.open(req, timeout=timeout)


def _upload_log_file(
    store_url: str,
    path: Path,
    session_id: str,
    start_offset: int = 0,
    extra_headers: Optional["dict[str, str]"] = None,
    timeout: float = 5.0,
    max_retries: int = 1,
) -> bool:
    """POST the transcript log at `path` (from byte `start_offset` on, i.e. this
    session's part of an append-only file) to `store_url` as a multipart/form-data
    upload. Returns True on success.

    Called from both the normal shutdown path and the forced (second Ctrl+C)
    exit path, so failures are reported but never raised - shutdown must
    still complete either way.
    """
    try:
        with open(path, "rb") as fh:
            # Seek straight to this session's part rather than reading the whole
            # (potentially many-session-long) file into memory just to slice it -
            # unless the file is now shorter than start_offset (e.g. truncated/
            # replaced since this session started), in which case upload all of it.
            size = os.fstat(fh.fileno()).st_size
            fh.seek(start_offset if size >= start_offset else 0)
            content = fh.read()
    except OSError as exc:
        print(f"WARNING: could not read {path} to upload to {store_url}: {exc}", file=sys.stderr)
        return False

    body, content_type = _build_multipart_body("log", path.name, content)
    headers = {**(extra_headers or {}), "Content-Type": content_type, "X-Session-Id": session_id}
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(store_url, data=body, method="POST", headers=headers)
        try:
            _urlopen_no_redirect(req, timeout).close()
            print(f"Uploaded transcript log ({path}) to {store_url}")
            return True
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                location = (exc.headers.get("Location") if exc.headers else None) or "<no Location header>"
                print(
                    f"WARNING: {store_url} redirected (HTTP {exc.code}) to {location} instead of accepting the "
                    f"upload - redirects are not followed (one would silently drop the file and could resend "
                    f"--auth-header to a different host). Point --store-url at {location} directly.",
                    file=sys.stderr,
                )
                return False
            if 400 <= exc.code < 500:  # a client error (bad URL, missing/rejected auth, ...) won't fix itself on retry
                print(f"WARNING: {store_url} rejected the upload (HTTP {exc.code} {exc.reason}) - not retrying.", file=sys.stderr)
                return False
            if attempt < max_retries:
                time.sleep(0.5)
            else:
                print(f"WARNING: failed to upload transcript log to {store_url}: {exc}", file=sys.stderr)
        except (urllib.error.URLError, OSError) as exc:
            if attempt < max_retries:
                time.sleep(0.5)
            else:
                print(f"WARNING: failed to upload transcript log to {store_url}: {exc}", file=sys.stderr)
    return False


class LogUploader:
    """Uploads this session's transcript log at most once, with a hard time limit.

    Both shutdown paths (normal, and the forced second-Ctrl+C one) call
    upload(); whichever runs first claims the upload, so repeated Ctrl+C
    presses can never start a second one. The POST runs on a daemon thread
    that the caller waits on for at most `max_wait` seconds, so a hung
    connection or slow DNS lookup (neither covered by the socket timeout)
    can't hold up exiting.
    """

    def __init__(
        self,
        store_url: str,
        path: Path,
        session_id: str,
        start_offset: int = 0,
        extra_headers: Optional["dict[str, str]"] = None,
    ):
        self.store_url = store_url
        self.path = path
        self.session_id = session_id
        self.start_offset = start_offset
        self.extra_headers = extra_headers
        # Lock.acquire(blocking=False) is an atomic test-and-set - a plain
        # bool check could be split by the SIGINT handler running mid-check.
        self._claim = threading.Lock()

    def upload(self, max_wait: float, timeout: float = 5.0, max_retries: int = 1) -> bool:
        if not self._claim.acquire(blocking=False):
            return False  # already uploading/uploaded
        outcome: "dict[str, bool]" = {}

        def work() -> None:
            outcome["ok"] = _upload_log_file(
                self.store_url, self.path, self.session_id, self.start_offset,
                self.extra_headers, timeout, max_retries,
            )

        thread = threading.Thread(target=work, name="log-upload", daemon=True)
        thread.start()
        deadline = time.monotonic() + max_wait
        while thread.is_alive() and time.monotonic() < deadline:
            thread.join(timeout=0.1)
        if thread.is_alive():
            print(
                f"WARNING: transcript log upload to {self.store_url} still running after "
                f"{max_wait:.0f}s - giving up on it.",
                file=sys.stderr,
            )
            return False
        return outcome.get("ok", False)


class SentencePoster:
    """POSTs transcribed sentences to a URL on a background thread, so a slow
    or unreachable streaming endpoint never stalls the transcription loop.

    Sentences are queued (bounded; a new sentence is dropped, with a warning,
    if the queue is full rather than blocking the caller) and posted with a
    small retry on failure.
    """

    def __init__(
        self,
        url: str,
        model: str,
        language: str,
        session_id: str,
        max_retries: int = 2,
        timeout: float = 5.0,
        extra_headers: Optional["dict[str, str]"] = None,
        topic: Optional[str] = None,
    ):
        self.extra_headers = extra_headers or {}
        self.url = url
        self.model = model
        self.language = language
        self.session_id = session_id
        self.max_retries = max_retries
        self.timeout = timeout
        self.topic = topic or ""

        self._queue: "queue.Queue[Optional[tuple[str, int]]]" = queue.Queue(maxsize=256)
        self._thread = threading.Thread(target=self._run, name="sentence-poster", daemon=True)
        self._thread.start()

    def post(self, sentence: str, speaker_id: int) -> None:
        try:
            self._queue.put_nowait((sentence, speaker_id))
        except queue.Full:
            print(f"WARNING: streaming queue full, dropping sentence: {sentence!r}", file=sys.stderr)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:  # shutdown sentinel from stop()
                return
            self._post_with_retry(*item)

    def _post_with_retry(self, sentence: str, speaker_id: int) -> None:
        body = json.dumps(
            {
                "sentence": sentence,
                "model": self.model,
                "language": self.language,
                "session_id": self.session_id,
                "speaker_id": speaker_id,
                "topic": self.topic,
            }
        ).encode("utf-8")
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(
                self.url,
                data=body,
                method="POST",
                headers={**self.extra_headers, "Content-Type": "application/json"},
            )
            try:
                _urlopen_no_redirect(req, timeout=self.timeout).close()
                return
            except urllib.error.HTTPError as exc:
                if 300 <= exc.code < 400:
                    location = (exc.headers.get("Location") if exc.headers else None) or "<no Location header>"
                    print(
                        f"WARNING: {self.url} redirected (HTTP {exc.code}) to {location} instead of accepting the "
                        f"sentence - redirects are not followed. Point --url at {location} directly. Dropping: {sentence!r}",
                        file=sys.stderr,
                    )
                    return
                if 400 <= exc.code < 500:  # a client error won't fix itself on retry
                    print(
                        f"WARNING: {self.url} rejected the sentence (HTTP {exc.code} {exc.reason}) - not retrying. "
                        f"Dropping: {sentence!r}",
                        file=sys.stderr,
                    )
                    return
                if attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                else:
                    print(
                        f"WARNING: failed to POST sentence to {self.url} after "
                        f"{self.max_retries + 1} attempts: {exc}",
                        file=sys.stderr,
                    )
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
        sentinel_queued = True
        try:
            self._queue.put(None, timeout=timeout)
        except queue.Full:
            sentinel_queued = False  # worker is stuck on a slow endpoint with a full backlog
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            pending = max(0, self._queue.qsize() - (1 if sentinel_queued else 0))
            print(
                f"WARNING: stopped streaming with about {pending} sentence(s) not yet delivered to {self.url}.",
                file=sys.stderr,
            )


def _header_value(value: Optional[str]) -> str:
    """Flatten a user-supplied label onto one line for the log's first line: control
    characters and line breaks collapse to single spaces (so a value can't start a
    fake transcript line) and '|' - the field separator - becomes '/'."""
    printable = "".join(ch if ch.isprintable() else " " for ch in value or "")
    return " ".join(printable.split()).replace("|", "/")


def _session_header(session_start: dt.datetime, args: argparse.Namespace) -> str:
    """First line of every session: start time, model, language and - when given - the
    client/topic and the user's email."""
    fields = []
    for name, value in (("model", args.model), ("language", args.language), ("topic", args.topic), ("email", args.email)):
        cleaned = _header_value(value)
        if cleaned:
            fields.append(f"{name}={cleaned}")
    return f"# Transcript started {session_start.isoformat(timespec='seconds')} | " + " | ".join(fields)


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
    speakers: SpeakerRegistry,
) -> None:
    segments = model.transcribe(segment.audio)
    text = _clean_text(segments)
    if not text:
        return

    speaker_id = speakers.identify(segment.audio)

    if args.streaming:
        print(f"[{dt.datetime.now().strftime('%H:%M:%S')} - Person {speaker_id}] said: {text}")
        poster.post(text, speaker_id)
    else:
        line_ts = (session_start + dt.timedelta(seconds=segment.start_s)).strftime("%H:%M:%S")
        writer.write(f"[{line_ts} - Person {speaker_id}] said: {text}")


def run(args: argparse.Namespace) -> int:
    session_start = dt.datetime.now()
    session_id = str(uuid.uuid4())
    speakers = SpeakerRegistry(threshold=args.speaker_threshold)
    writer: Optional[TranscriptWriter] = None
    poster: Optional[SentencePoster] = None
    if args.streaming:
        print(f"{_session_header(session_start, args)} | streaming to {args.url}")
        poster = SentencePoster(args.url, args.model, args.language, session_id, extra_headers=args.headers, topic=args.topic)
    else:
        writer = TranscriptWriter(Path(args.output))
        writer.write(_session_header(session_start, args))
    uploader: Optional[LogUploader] = None
    if args.store_url and writer is not None:
        uploader = LogUploader(args.store_url, Path(args.output), session_id, writer.start_offset, args.headers)

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
    consecutive_errors = 0

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
                if cap.stopped_by_user:
                    print("\nCapture stopped (via the system's stop-sharing control).")
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
            try:
                _process_segment(segment, model, args, session_start, writer, poster, speakers)
                consecutive_errors = 0
            except Exception as exc:  # one bad segment (whisper error, full disk...) must not end the meeting
                consecutive_errors += 1
                print(
                    f"ERROR: failed to process a speech segment "
                    f"({consecutive_errors}/{MAX_CONSECUTIVE_SEGMENT_ERRORS}): {exc!r}",
                    file=sys.stderr,
                )
                if consecutive_errors >= MAX_CONSECUTIVE_SEGMENT_ERRORS:
                    print("ERROR: giving up after repeated failures.", file=sys.stderr)
                    exit_code = 1
                    break
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopping...")

        def _force_exit(signum, frame):
            # whisper.cpp's transcribe() is a blocking native call that never
            # checks for Python signals, so without the drain thread below a
            # slow trailing transcription would swallow Ctrl+C entirely until
            # it finished. This handler is armed only once graceful shutdown
            # has begun, so a second Ctrl+C always gets through instead of
            # requiring a force-kill.
            print("\nForce-stopping (second Ctrl+C) - the in-progress transcription may be lost.")
            if uploader is not None:
                # Reads straight from disk rather than going through `writer`
                # (which the still-running drain thread may be mid-write to)
                # - TranscriptWriter fsyncs after every line, so this always
                # reflects everything actually written so far. Once-only and
                # time-boxed: a further Ctrl+C while this waits finds the
                # upload already claimed and exits immediately.
                uploader.upload(max_wait=FORCE_UPLOAD_MAX_WAIT_S, timeout=3.0, max_retries=0)
            sys.exit(1)

        signal.signal(signal.SIGINT, _force_exit)

        # detector.stop() flushes whatever utterance was still in progress
        # onto seg_queue, so draining it below picks up the session's final
        # segment instead of silently dropping it.
        detector.stop()

        def _drain_trailing_segments() -> None:
            while True:
                try:
                    trailing_segment = seg_queue.get_nowait()
                except queue.Empty:
                    return
                try:
                    _process_segment(trailing_segment, model, args, session_start, writer, poster, speakers)
                except Exception as exc:
                    print(f"ERROR: failed to process a trailing speech segment: {exc!r}", file=sys.stderr)

        # Runs on a daemon thread rather than inline so _force_exit above can
        # actually preempt it: a signal handler only runs once the main
        # thread reaches a Python bytecode checkpoint, which it never does
        # while itself stuck inside transcribe()'s native call.
        drain_thread = threading.Thread(target=_drain_trailing_segments, name="shutdown-drain", daemon=True)
        drain_thread.start()
        while drain_thread.is_alive():
            drain_thread.join(timeout=0.2)

        cap.stop()
        if writer is not None:
            writer.close()
        if uploader is not None:
            uploader.upload(max_wait=UPLOAD_MAX_WAIT_S)
        if poster is not None:
            poster.stop()

    return exit_code


def _make_console_output_lenient() -> None:
    """A transcribed sentence, or a --topic/--email value, can contain a character
    that stdout's encoding can't represent (e.g. Windows with output redirected
    under a legacy console code page) - without this, that one print() call
    raises UnicodeEncodeError and crashes the session. The transcript log file
    itself is always opened as UTF-8 (see TranscriptWriter) and is unaffected -
    this only changes what the console echo falls back to instead of crashing."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass  # not a reconfigurable TextIOWrapper (e.g. redirected to something exotic) - leave it be


def main(argv: Optional[List[str]] = None) -> None:
    _make_console_output_lenient()

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
        "--topic",
        type=str,
        default=None,
        help="Client name or meeting topic, written to the first line of the transcript.",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=None,
        help="Your email address, written to the first line of the transcript.",
    )
    parser.add_argument(
        "--speaker-threshold",
        type=float,
        default=DEFAULT_SPEAKER_THRESHOLD,
        help=(
            f"Cosine-similarity threshold (0-1) for matching a segment to an existing speaker vs. "
            f"registering a new one (default: {DEFAULT_SPEAKER_THRESHOLD}). Algorithmic (MFCC + pitch, "
            f"no ML model) - tune per mic/room with 'python speaker_id.py --device N --seconds 30'."
        ),
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
        help="Endpoint to POST {sentence, model, language, session_id, speaker_id, topic} to for each sentence. Required with --streaming.",
    )
    parser.add_argument(
        "--store-url",
        type=str,
        default=None,
        help=(
            "Endpoint to POST the finished local transcript log file to when the session ends "
            "(multipart/form-data, field 'log', filename = the log's own name; sent even on a "
            "forced/second-Ctrl+C exit). "
            "Requires log-file mode - not compatible with --streaming."
        ),
    )
    parser.add_argument(
        "--auth-header",
        type=str,
        default=None,
        help=(
            "Optional 'Name: value' HTTP header (e.g. 'Authorization: Bearer <token>') added to every "
            f"POST (--url and --store-url). Prefer the {AUTH_HEADER_KEY} environment variable so the secret "
            "doesn't appear in the process list or shell history."
        ),
    )
    args = parser.parse_args(argv)

    try:
        args.headers = parse_auth_header(args.auth_header or os.environ.get(AUTH_HEADER_KEY))
    except ValueError as exc:
        parser.error(str(exc))

    email_error = validate_email(args.email)
    if email_error:
        parser.error(email_error)

    if args.streaming and not args.url:
        parser.error("--streaming requires --url")

    if args.store_url and args.streaming:
        parser.error("--store-url requires log-file mode (cannot be combined with --streaming)")

    url_error = validate_url(args.url)
    if url_error:
        parser.error(url_error)

    store_url_error = validate_url(args.store_url, "--store-url")
    if store_url_error:
        parser.error(store_url_error)

    for warning in (
        _insecure_transport_warning(args.url, "--url"),
        _insecure_transport_warning(args.store_url, "--store-url"),
    ):
        if warning:
            print(warning, file=sys.stderr)

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
