"""Tests for capture_windows.LoopbackCapture using a fake pyaudiowpatch, so
they run on any OS (the real library only exists on Windows)."""
from __future__ import annotations

import queue
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeStream:
    """read() blocks until close() - like a WASAPI loopback read on an idle
    endpoint - then fails, which is what closing a stream under a reader does."""

    def __init__(self, fail_immediately: bool = False):
        self.closed = threading.Event()
        self.fail_immediately = fail_immediately

    def read(self, _frames, exception_on_overflow=False):
        if self.fail_immediately:
            raise OSError("device disappeared")
        self.closed.wait(5)
        raise OSError("Stream closed")

    def stop_stream(self):
        pass

    def close(self):
        self.closed.set()


def _fake_pyaudio_module(stream: _FakeStream) -> types.ModuleType:
    class FakePyAudio:
        def get_device_info_by_index(self, index):
            return {
                "isLoopbackDevice": True,
                "index": index,
                "name": "fake loopback",
                "defaultSampleRate": 16000.0,
                "maxInputChannels": 2,
            }

        def open(self, **_kwargs):
            return stream

        def terminate(self):
            pass

    module = types.ModuleType("pyaudiowpatch")
    module.PyAudio = FakePyAudio
    module.paInt16 = 8
    module.paWASAPI = 13
    return module


class LoopbackCaptureStopTests(unittest.TestCase):
    def _capture(self, stream: _FakeStream):
        # patch.dict restores sys.modules afterwards, dropping the capture_windows
        # import that bound itself to the fake module.
        patcher = mock.patch.dict(sys.modules, {"pyaudiowpatch": _fake_pyaudio_module(stream)})
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.modules.pop("capture_windows", None)
        import capture_windows

        cap = capture_windows.LoopbackCapture(queue.Queue(), device_index=3)
        cap.start()
        self.addCleanup(cap.stop)
        return cap

    def test_stop_closing_the_stream_is_not_reported_as_a_device_error(self):
        # stop() closes the stream to unblock the capture thread's read(); the
        # read failing because of that used to be stored as a capture error, so
        # the debug CLIs' cap.stop(); cap.raise_if_failed() reported a bogus one.
        cap = self._capture(_FakeStream())
        time.sleep(0.1)  # let the thread park inside read()
        cap.stop()
        cap.raise_if_failed()  # must not raise

    def test_a_genuine_read_failure_is_still_reported(self):
        cap = self._capture(_FakeStream(fail_immediately=True))
        deadline = time.monotonic() + 3
        while cap._error is None and time.monotonic() < deadline:
            time.sleep(0.02)
        with self.assertRaises(OSError):
            cap.raise_if_failed()


if __name__ == "__main__":
    unittest.main()
