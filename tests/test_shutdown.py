"""End-to-end tests of the real transcribe.run() shutdown paths (first Ctrl+C,
second Ctrl+C, a hung upload, failing segments) in a subprocess, with capture,
VAD and whisper faked - see tests/shutdown_harness.py."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HARNESS = Path(__file__).resolve().parent / "shutdown_harness.py"


class _Store:
    def __init__(self, hang: bool = False):
        self.bodies: list = []
        self.release = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                outer.bodies.append(self.rfile.read(int(self.headers["Content-Length"])))
                if hang:
                    outer.release.wait(60)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_a):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/store"

    def __enter__(self):
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_exc):
        self.release.set()
        self._server.shutdown()
        self._server.server_close()


@unittest.skipIf(sys.platform == "win32", "sends POSIX SIGINT to the child")
class ShutdownTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = Path(self._tmp.name) / "meeting.log"
        self.out_path = Path(self._tmp.name) / "stdout.txt"

    def _start(self, mode: str, store_url: str | None = None) -> subprocess.Popen:
        args = [sys.executable, "-u", str(HARNESS), mode, str(self.log)] + ([store_url] if store_url else [])
        out = open(self.out_path, "w")
        self.addCleanup(out.close)
        proc = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    def _output(self) -> str:
        return self.out_path.read_text() if self.out_path.exists() else ""

    def _wait_for(self, text: str, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while text not in self._output():
            if time.monotonic() > deadline:
                self.fail(f"timed out waiting for {text!r}; output so far:\n{self._output()}")
            time.sleep(0.05)

    def test_first_ctrl_c_stops_gracefully_and_uploads_only_this_session_once(self):
        self.log.write_text("# Transcript started EARLIER\n[09:00:00 - Person 1] said: OLD MEETING\n")
        with _Store() as store:
            proc = self._start("graceful", store.url)
            self._wait_for("Listening")
            proc.send_signal(2)
            self.assertEqual(proc.wait(timeout=20), 0, self._output())

        self.assertEqual(len(store.bodies), 1)
        body = store.bodies[0].decode()
        self.assertIn("# Transcript started", body)
        self.assertIn("spoken text number 1", body)  # the trailing segment flushed at shutdown
        self.assertNotIn("OLD MEETING", body)

    def test_first_line_of_the_log_and_the_upload_carries_topic_and_email(self):
        self.log.write_text("# Transcript started EARLIER | model=old | language=en\n[09:00:00 - Person 1] said: OLD\n")
        with _Store() as store:
            proc = self._start("graceful", store.url)
            self._wait_for("Listening")
            proc.send_signal(2)
            self.assertEqual(proc.wait(timeout=20), 0, self._output())

        pattern = r"^# Transcript started \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d \| model=base\.en-q5_1 \| language=en \| topic=Acme kickoff \| email=me@example\.com$"
        session_lines = self.log.read_text().splitlines()[2:]  # after the earlier session's two lines
        self.assertRegex(session_lines[0], pattern)
        self.assertRegex(store.bodies[0].decode().split("\r\n\r\n", 1)[1].splitlines()[0], pattern)

    def test_second_ctrl_c_forces_exit_but_still_uploads_once(self):
        with _Store() as store:
            proc = self._start("stuck", store.url)
            self._wait_for("Listening")
            proc.send_signal(2)
            self._wait_for("Stopping...")
            time.sleep(0.5)  # the drain thread is now stuck inside transcribe()
            proc.send_signal(2)
            self.assertEqual(proc.wait(timeout=20), 1, self._output())

        self.assertEqual(len(store.bodies), 1)
        self.assertIn("# Transcript started", store.bodies[0].decode())
        self.assertIn("Force-stopping", self._output())

    def test_force_exit_is_time_boxed_when_the_store_hangs(self):
        with _Store(hang=True) as store:
            proc = self._start("stuck", store.url)
            self._wait_for("Listening")
            proc.send_signal(2)
            self._wait_for("Stopping...")
            time.sleep(0.5)
            proc.send_signal(2)
            started = time.monotonic()
            code = proc.wait(timeout=30)
            elapsed = time.monotonic() - started

        self.assertEqual(code, 1, self._output())
        self.assertLess(elapsed, 12.0)  # FORCE_UPLOAD_MAX_WAIT_S (4s) plus interpreter shutdown
        # The single short attempt (3s socket timeout) gives up on its own
        # before the 4s hard limit; either way it is reported, not silent.
        self.assertRegex(self._output(), r"failed to upload transcript log.*timed out|giving up")
        self.assertEqual(len(store.bodies), 1)

    def test_third_ctrl_c_escapes_a_hanging_upload_without_starting_another(self):
        with _Store(hang=True) as store:
            proc = self._start("stuck", store.url)
            self._wait_for("Listening")
            proc.send_signal(2)
            self._wait_for("Stopping...")
            time.sleep(0.5)
            proc.send_signal(2)
            self._wait_for("Force-stopping")
            time.sleep(1.0)  # now inside the (hanging) upload
            proc.send_signal(2)
            started = time.monotonic()
            code = proc.wait(timeout=30)
            elapsed = time.monotonic() - started

        self.assertEqual(code, 1, self._output())
        self.assertLess(elapsed, 3.0)
        self.assertEqual(len(store.bodies), 1)

    def test_a_failing_segment_does_not_end_the_session(self):
        proc = self._start("flaky")
        self._wait_for("Listening")
        self._wait_for("spoken text number 8")  # all 8 segments processed; 2 failed, 6 succeeded
        proc.send_signal(2)
        self.assertEqual(proc.wait(timeout=20), 0, self._output())

        out = self._output()
        self.assertIn("(1/5)", out)
        self.assertIn("(2/5)", out)
        self.assertEqual(self.log.read_text().count("said: spoken text"), 6)

    def test_repeated_segment_failures_stop_the_session_with_an_error(self):
        proc = self._start("broken")
        self.assertEqual(proc.wait(timeout=30), 1, self._output())
        self.assertIn("giving up after repeated failures", self._output())


if __name__ == "__main__":
    unittest.main()
