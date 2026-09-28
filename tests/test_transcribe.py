"""Tests for the pure helpers in transcribe.py - no audio hardware needed."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import transcribe


class _Server:
    """Real local HTTP server recording every POST (body + headers). With
    hang=True it holds each request open until the context exits, standing in
    for an unresponsive endpoint."""

    def __init__(self, hang: bool = False):
        self.requests: list = []
        self.release = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append({"body": body, "headers": self.headers})
                if hang:
                    outer.release.wait(30)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/store"

    def __enter__(self) -> "_Server":
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_exc) -> None:
        self.release.set()
        self._server.shutdown()
        self._server.server_close()


class _FixedResponseServer:
    """Real local HTTP server that always answers every request with a fixed
    status code (and headers), and counts how many requests it received - for
    testing that a client error isn't retried and a redirect isn't followed."""

    def __init__(self, status: int, headers: "dict[str, str]" = None):
        self.request_count = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.request_count += 1
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()

            def log_message(self, *_args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/endpoint"

    def __enter__(self) -> "_FixedResponseServer":
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()


def _quiet():
    """Capture stdout+stderr so expected warnings don't clutter test output."""
    stack = contextlib.ExitStack()
    out, err = io.StringIO(), io.StringIO()
    stack.enter_context(contextlib.redirect_stdout(out))
    stack.enter_context(contextlib.redirect_stderr(err))
    return stack, out, err


class FakeSegment:
    def __init__(self, text: str):
        self.text = text


class CleanTextTests(unittest.TestCase):
    def test_keeps_real_speech(self):
        segments = [FakeSegment(" Hello there. ")]
        self.assertEqual(transcribe._clean_text(segments), "Hello there.")

    def test_joins_multiple_segments_with_spaces(self):
        segments = [FakeSegment("Hello"), FakeSegment("world.")]
        self.assertEqual(transcribe._clean_text(segments), "Hello world.")

    def test_drops_known_filler_tokens(self):
        for token in ["[BLANK_AUDIO]", "[SILENCE]", "(silence)", "[NOISE]", "[MUSIC]", "[APPLAUSE]"]:
            self.assertEqual(transcribe._clean_text([FakeSegment(token)]), "", msg=token)

    def test_drops_filler_variants_exact_token_list_would_miss(self):
        # Regression coverage: an exact-match token set (even case-insensitive)
        # misses whitespace/wording variants that whisper.cpp also emits.
        for token in ["[ Silence ]", "(speaking in foreign language)", "[ Music ]", "[laughs]", "[SILENCE]"]:
            self.assertEqual(transcribe._clean_text([FakeSegment(token)]), "", msg=token)

    def test_drops_empty_and_whitespace_only_segments(self):
        self.assertEqual(transcribe._clean_text([FakeSegment("   ")]), "")
        self.assertEqual(transcribe._clean_text([FakeSegment("")]), "")

    def test_keeps_real_speech_containing_parens(self):
        segments = [FakeSegment("The weather (allegedly) is nice.")]
        self.assertEqual(transcribe._clean_text(segments), "The weather (allegedly) is nice.")

    def test_keeps_speech_that_merely_starts_with_a_bracket(self):
        segments = [FakeSegment("[Music] then someone started talking")]
        self.assertEqual(transcribe._clean_text(segments), "[Music] then someone started talking")


class HeaderValueTests(unittest.TestCase):
    def test_plain_text_is_unchanged(self):
        self.assertEqual(transcribe._header_value("Acme kickoff"), "Acme kickoff")

    def test_none_and_blank_become_empty(self):
        self.assertEqual(transcribe._header_value(None), "")
        self.assertEqual(transcribe._header_value("  \t "), "")

    def test_line_breaks_cannot_start_a_fake_transcript_line(self):
        cleaned = transcribe._header_value("Acme\n[10:00:00 - Person 9] said: forged\r\nmore")
        self.assertNotIn("\n", cleaned)
        self.assertNotIn("\r", cleaned)
        self.assertEqual(cleaned, "Acme [10:00:00 - Person 9] said: forged more")

    def test_control_characters_are_dropped(self):
        self.assertEqual(transcribe._header_value("a\x1b[31mred\x00b"), "a [31mred b")

    def test_field_separator_is_replaced(self):
        self.assertEqual(transcribe._header_value("Acme | Q3 | review"), "Acme / Q3 / review")

    def test_whitespace_runs_collapse(self):
        self.assertEqual(transcribe._header_value("  a   b\u00a0c  "), "a b c")


class SessionHeaderTests(unittest.TestCase):
    START = dt.datetime(2026, 9, 25, 10, 30, 5)

    def args(self, **overrides) -> argparse.Namespace:
        values = dict(model="base.en-q5_1", language="en", topic=None, email=None)
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_without_topic_or_email_only_time_model_and_language(self):
        self.assertEqual(
            transcribe._session_header(self.START, self.args()),
            "# Transcript started 2026-09-25T10:30:05 | model=base.en-q5_1 | language=en",
        )

    def test_includes_topic_and_email(self):
        self.assertEqual(
            transcribe._session_header(self.START, self.args(topic="Acme kickoff", email="me@example.com")),
            "# Transcript started 2026-09-25T10:30:05 | model=base.en-q5_1 | language=en"
            " | topic=Acme kickoff | email=me@example.com",
        )

    def test_uses_the_selected_language(self):
        self.assertIn("language=de", transcribe._session_header(self.START, self.args(language="de")))

    def test_hostile_topic_stays_on_the_first_line_and_in_its_own_field(self):
        header = transcribe._session_header(self.START, self.args(topic="x | email=evil@example.com\nFORGED"))
        self.assertEqual(len(header.splitlines()), 1)
        self.assertEqual(header.count(" | email="), 0)
        self.assertIn("topic=x / email=evil@example.com FORGED", header)

    def test_blank_topic_is_omitted(self):
        self.assertNotIn("topic=", transcribe._session_header(self.START, self.args(topic="   ")))


class MainArgumentTests(unittest.TestCase):
    def parse_error(self, *argv: str) -> str:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            transcribe.main(list(argv))
        self.assertEqual(raised.exception.code, 2)
        return err.getvalue()

    def test_rejects_a_malformed_email(self):
        self.assertIn("--email must look like", self.parse_error("--email", "not-an-email"))

    def test_topic_and_email_reach_run(self):
        seen = {}
        with mock.patch.object(transcribe, "run", lambda args: seen.update(vars(args)) or 0), self.assertRaises(SystemExit):
            transcribe.main(["--topic=-leading dash", "--email", "me@example.com"])
        self.assertEqual(seen["topic"], "-leading dash")
        self.assertEqual(seen["email"], "me@example.com")

    def test_run_wires_the_topic_into_the_streaming_poster(self):
        # A real call to run() (not run itself mocked away, unlike the test above) -
        # only SentencePoster's constructor is intercepted, and capture is made to
        # fail immediately (NoAudioError, the real "no such device" exception) so
        # this never touches real audio hardware/VAD/whisper. SentencePoster is
        # constructed before cap.start() in run(), so this still exercises the real
        # wiring for the one thing this test cares about.
        args = argparse.Namespace(
            streaming=True, url="http://localhost:1/ingest", model="m", language="en",
            headers={}, topic="Acme kickoff", email=None, store_url=None, output="unused.log",
            device=None, threads=4, speaker_threshold=0.84,
        )
        captured = {}

        class _FakePoster:
            def __init__(self, *a, **kw):
                captured.update(kw)

            def stop(self):
                pass

        with mock.patch.object(transcribe, "SentencePoster", _FakePoster), \
             mock.patch.object(transcribe, "LoopbackCapture") as fake_cap_cls:
            fake_cap_cls.return_value.start.side_effect = transcribe.NoAudioError("no device")
            stack, _out, _err = _quiet()
            with stack:
                transcribe.run(args)
        self.assertEqual(captured.get("topic"), "Acme kickoff")


class TranscriptWriterTests(unittest.TestCase):
    def test_start_offset_is_zero_for_a_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = transcribe.TranscriptWriter(Path(tmp) / "new.log")
            writer.close()
            self.assertEqual(writer.start_offset, 0)

    def test_start_offset_skips_content_from_earlier_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meeting.log"
            path.write_text("# Transcript started EARLIER\n[10:00:00 - Person 1] said: old\n", encoding="utf-8")
            old_size = path.stat().st_size

            with contextlib.redirect_stdout(io.StringIO()):
                writer = transcribe.TranscriptWriter(path)
                writer.write("# Transcript started NOW")
                writer.write("[11:00:00 - Person 1] said: new")
            writer.close()

            self.assertEqual(writer.start_offset, old_size)
            session = path.read_bytes()[writer.start_offset :].decode("utf-8")
            self.assertIn("NOW", session)
            self.assertIn("said: new", session)
            self.assertNotIn("old", session)


class InsecureTransportWarningTests(unittest.TestCase):
    def test_warns_for_plain_http_to_a_remote_host(self):
        self.assertIn("--store-url", transcribe._insecure_transport_warning("http://example.com/x", "--store-url"))

    def test_no_warning_for_https_loopback_or_unset(self):
        for url in ["https://example.com/x", "http://localhost:8000/x", "http://127.0.0.1:1/x", "http://[::1]:1/x", None, ""]:
            self.assertIsNone(transcribe._insecure_transport_warning(url, "--url"), msg=url)


class BuildMultipartBodyTests(unittest.TestCase):
    def test_body_contains_field_name_filename_and_content(self):
        body, content_type = transcribe._build_multipart_body("file", "transcript.log", b"hello world")
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        boundary = content_type.split("boundary=", 1)[1]
        self.assertIn(f"--{boundary}".encode(), body)
        self.assertIn(b'name="file"; filename="transcript.log"', body)
        self.assertIn(b"hello world", body)
        self.assertTrue(body.rstrip(b"\r\n").endswith(f"--{boundary}--".encode()))

    def test_filename_cannot_break_out_of_the_quoted_parameter(self):
        body, _ = transcribe._build_multipart_body("file", 'evil".log\r\nX-Injected: 1', b"x")
        header_line = body.split(b"\r\n")[1]
        self.assertEqual(header_line.count(b'"'), 4)  # only the delimiting quotes around name and filename
        self.assertIn(b"%22", header_line)
        self.assertNotIn(b"X-Injected", body.split(b"\r\n")[2])


class UploadLogFileTests(unittest.TestCase):
    """Live smoke test against a real local http.server - not mocked -
    matching this project's existing convention for testing POST wiring."""

    def _start_server(self, received: dict) -> HTTPServer:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                received["body"] = self.rfile.read(length)
                received["content_type"] = self.headers.get("Content-Type", "")
                received["session_id"] = self.headers.get("X-Session-Id", "")
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args):
                pass  # keep test output quiet

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def test_uploads_real_file_content_as_multipart_field(self):
        received: dict = {}
        server = self._start_server(received)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                log_path = Path(tmpdir) / "transcript.log"
                log_path.write_text("[10:00:00] Person 1 said: hello\n", encoding="utf-8")

                with contextlib.redirect_stdout(io.StringIO()):
                    transcribe._upload_log_file(
                        f"http://127.0.0.1:{server.server_port}/store", log_path, "session-abc"
                    )
        finally:
            server.shutdown()
            server.server_close()

        self.assertIn("multipart/form-data; boundary=", received["content_type"])
        self.assertIn(b'name="log"; filename="transcript.log"', received["body"])
        self.assertIn(b"Person 1 said: hello", received["body"])
        self.assertEqual(received["session_id"], "session-abc")

    def test_missing_file_warns_instead_of_raising(self):
        # Force-exit calls this with whatever's on disk; a missing/unreadable
        # file must not itself crash the shutdown path.
        stack, _out, err = _quiet()
        with stack:
            ok = transcribe._upload_log_file(
                "http://127.0.0.1:1/store", Path("/nonexistent/does-not-exist.log"), "session-x"
            )
        self.assertFalse(ok)
        self.assertIn("could not read", err.getvalue())

    def test_a_redirect_is_reported_and_not_silently_followed(self):
        # Left to urllib's default, a 302 to a POST is followed as a body-less
        # GET - the upload would vanish while _upload_log_file still reported
        # success, and our headers (including any --auth-header) would be
        # resent to whatever host the redirect names.
        with _FixedResponseServer(302, {"Location": "http://example.invalid/elsewhere"}) as server:
            with tempfile.TemporaryDirectory() as tmpdir:
                log_path = Path(tmpdir) / "x.log"
                log_path.write_text("hello\n", encoding="utf-8")
                stack, _out, err = _quiet()
                with stack:
                    ok = transcribe._upload_log_file(server.url, log_path, "sid", max_retries=2)
            self.assertFalse(ok)
            self.assertEqual(server.request_count, 1)  # not retried, not followed
        self.assertIn("redirected", err.getvalue())
        self.assertIn("http://example.invalid/elsewhere", err.getvalue())

    def test_a_client_error_is_reported_and_not_retried(self):
        with _FixedResponseServer(401) as server:
            with tempfile.TemporaryDirectory() as tmpdir:
                log_path = Path(tmpdir) / "x.log"
                log_path.write_text("hello\n", encoding="utf-8")
                stack, _out, err = _quiet()
                with stack:
                    ok = transcribe._upload_log_file(server.url, log_path, "sid", max_retries=2)
            self.assertFalse(ok)
            self.assertEqual(server.request_count, 1)
        self.assertIn("401", err.getvalue())
        self.assertIn("not retrying", err.getvalue())

    def test_a_server_error_is_still_retried(self):
        with _FixedResponseServer(503) as server:
            with tempfile.TemporaryDirectory() as tmpdir:
                log_path = Path(tmpdir) / "x.log"
                log_path.write_text("hello\n", encoding="utf-8")
                stack, _out, err = _quiet()
                with stack:
                    ok = transcribe._upload_log_file(server.url, log_path, "sid", max_retries=2)
            self.assertFalse(ok)
            self.assertEqual(server.request_count, 3)  # 1 + 2 retries, unlike a 4xx


class SessionUploadTests(unittest.TestCase):
    def test_uploads_only_this_sessions_part_of_an_append_only_log(self):
        with _Server() as server, tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meeting.log"
            path.write_text("# Transcript started EARLIER\n[10:00:00 - Person 1] said: old meeting\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                writer = transcribe.TranscriptWriter(path)
                writer.write("# Transcript started NOW")
                writer.write("[11:00:00 - Person 2] said: new meeting")
            writer.close()

            with contextlib.redirect_stdout(io.StringIO()):
                ok = transcribe._upload_log_file(server.url, path, "sess", writer.start_offset)

        self.assertTrue(ok)
        body = server.requests[0]["body"]
        self.assertIn(b"new meeting", body)
        self.assertNotIn(b"old meeting", body)

    def test_falls_back_to_the_whole_file_if_it_shrank_below_the_offset(self):
        with _Server() as server, tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rotated.log"
            path.write_text("short", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                transcribe._upload_log_file(server.url, path, "sess", start_offset=10_000)
        self.assertIn(b"short", server.requests[0]["body"])

    def test_sends_auth_header_but_cannot_override_content_type(self):
        with _Server() as server, tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.log"
            path.write_text("x", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                transcribe._upload_log_file(
                    server.url, path, "sess", extra_headers={"Authorization": "Bearer t0k", "Content-Type": "text/evil"}
                )
        headers = server.requests[0]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer t0k")
        self.assertTrue(headers["Content-Type"].startswith("multipart/form-data"))


class LogUploaderTests(unittest.TestCase):
    def _log(self, tmp: str) -> Path:
        path = Path(tmp) / "t.log"
        path.write_text("hello", encoding="utf-8")
        return path

    def test_uploads_at_most_once_however_often_it_is_called(self):
        with _Server() as server, tempfile.TemporaryDirectory() as tmp:
            uploader = transcribe.LogUploader(server.url, self._log(tmp), "sess")
            with contextlib.redirect_stdout(io.StringIO()):
                first = uploader.upload(max_wait=5)
                second = uploader.upload(max_wait=5)
                third = uploader.upload(max_wait=5)
        self.assertEqual((first, second, third), (True, False, False))
        self.assertEqual(len(server.requests), 1)

    def test_a_second_call_during_an_in_flight_upload_returns_immediately(self):
        # The second-Ctrl+C handler can fire while the normal-shutdown upload
        # is still running; it must not start another upload or wait on it.
        stack, _out, _err = _quiet()
        with stack, _Server(hang=True) as server, tempfile.TemporaryDirectory() as tmp:
            uploader = transcribe.LogUploader(server.url, self._log(tmp), "sess")
            first = threading.Thread(target=lambda: uploader.upload(max_wait=3, timeout=10, max_retries=0), daemon=True)
            first.start()
            deadline = time.monotonic() + 3
            while not server.requests and time.monotonic() < deadline:
                time.sleep(0.02)
            started = time.monotonic()
            second = uploader.upload(max_wait=30)
            elapsed = time.monotonic() - started
            first.join(timeout=6)
        self.assertFalse(second)
        self.assertLess(elapsed, 1.0)
        self.assertEqual(len(server.requests), 1)

    def test_gives_up_after_max_wait_when_the_endpoint_hangs(self):
        stack, _out, err = _quiet()
        with stack, _Server(hang=True) as server, tempfile.TemporaryDirectory() as tmp:
            uploader = transcribe.LogUploader(server.url, self._log(tmp), "sess")
            started = time.monotonic()
            ok = uploader.upload(max_wait=0.5, timeout=20, max_retries=0)
            elapsed = time.monotonic() - started
        self.assertFalse(ok)
        self.assertLess(elapsed, 3.0)
        self.assertIn("giving up", err.getvalue())


class SentencePosterTests(unittest.TestCase):
    def test_posts_json_with_the_auth_header(self):
        with _Server() as server:
            poster = transcribe.SentencePoster(
                server.url, "m", "en", "sess", extra_headers={"Authorization": "Bearer t0k"}
            )
            poster.post("hello there", 2)
            poster.stop()
        request = server.requests[0]
        self.assertEqual(json.loads(request["body"])["sentence"], "hello there")
        self.assertEqual(request["headers"]["Authorization"], "Bearer t0k")
        self.assertEqual(request["headers"]["Content-Type"], "application/json")

    def test_posts_the_topic(self):
        with _Server() as server:
            poster = transcribe.SentencePoster(server.url, "m", "en", "sess", topic="Acme kickoff")
            poster.post("hello", 1)
            poster.stop()
        self.assertEqual(json.loads(server.requests[0]["body"])["topic"], "Acme kickoff")

    def test_topic_defaults_to_an_empty_string_not_null(self):
        with _Server() as server:
            poster = transcribe.SentencePoster(server.url, "m", "en", "sess")
            poster.post("hello", 1)
            poster.stop()
        self.assertEqual(json.loads(server.requests[0]["body"])["topic"], "")

    def test_a_redirect_is_reported_and_not_silently_followed(self):
        with _FixedResponseServer(302, {"Location": "http://example.invalid/elsewhere"}) as server:
            poster = transcribe.SentencePoster(server.url, "m", "en", "sess", max_retries=2)
            stack, _out, err = _quiet()
            with stack:
                poster.post("hello", 1)
                poster.stop()
        self.assertEqual(server.request_count, 1)
        self.assertIn("redirected", err.getvalue())
        self.assertIn("http://example.invalid/elsewhere", err.getvalue())

    def test_a_client_error_is_reported_and_not_retried(self):
        with _FixedResponseServer(404) as server:
            poster = transcribe.SentencePoster(server.url, "m", "en", "sess", max_retries=2)
            stack, _out, err = _quiet()
            with stack:
                poster.post("hello", 1)
                poster.stop()
        self.assertEqual(server.request_count, 1)
        self.assertIn("404", err.getvalue())
        self.assertIn("not retrying", err.getvalue())

    def test_a_server_error_is_still_retried(self):
        with _FixedResponseServer(500) as server:
            poster = transcribe.SentencePoster(server.url, "m", "en", "sess", max_retries=2)
            stack, _out, err = _quiet()
            with stack:
                poster.post("hello", 1)
                poster.stop(timeout=5)
        self.assertEqual(server.request_count, 3)

    def test_stop_does_not_hang_and_warns_when_the_endpoint_is_stuck_with_a_full_backlog(self):
        stack, _out, err = _quiet()
        with stack, _Server(hang=True) as server:
            poster = transcribe.SentencePoster(server.url, "m", "en", "sess", max_retries=0, timeout=20)
            for i in range(300):  # more than the 256-slot queue: the worker is stuck on #1
                poster.post(f"sentence {i}", 1)
            started = time.monotonic()
            poster.stop(timeout=0.5)
            elapsed = time.monotonic() - started
            # Let the worker exit cleanly when the server releases it, instead
            # of grinding through the remaining backlog after the test ends.
            while not poster._queue.empty():
                poster._queue.get_nowait()
            poster._queue.put_nowait(None)
        self.assertLess(elapsed, 4.0)
        self.assertIn("not yet delivered", err.getvalue())
        self.assertIn("dropping sentence", err.getvalue())  # the overflow itself is reported too


if __name__ == "__main__":
    unittest.main()
