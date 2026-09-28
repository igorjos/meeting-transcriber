"""Runs the REAL transcribe.main() -> run() with capture, VAD and whisper
replaced by fakes, so the actual startup/shutdown/upload code executes without
audio hardware or a model. Driven by tests/test_shutdown.py.

    python shutdown_harness.py MODE OUTPUT_LOG [STORE_URL]

MODE: graceful  - a trailing segment is flushed at shutdown and transcribes fine
      stuck     - that trailing transcribe() never returns (like a hung native call)
      flaky     - 8 live segments; the first two fail to transcribe, the rest succeed
      broken    - 8 live segments; every transcribe() fails
"""
from __future__ import annotations

import signal
import sys
import threading
import time
import types
from dataclasses import dataclass
from pathlib import Path

# A parent started with `&` (or nohup) passes SIGINT down as "ignored", which
# Python then keeps - restore what a foreground terminal run would have.
signal.signal(signal.SIGINT, signal.default_int_handler)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from audio_common import NoAudioError

MODE, OUT = sys.argv[1], sys.argv[2]
STORE_URL = sys.argv[3] if len(sys.argv) > 3 else None


@dataclass
class SpeechSegment:
    audio: np.ndarray
    start_s: float
    end_s: float


def _segment(start_s: float) -> SpeechSegment:
    return SpeechSegment(audio=np.full(16000, 0.1, dtype=np.float32), start_s=start_s, end_s=start_s + 1.0)


class FakeCapture:
    device_info = {"name": "fake", "defaultSampleRate": 16000.0}
    stopped_by_user = False

    def __init__(self, _queue, device_index=None):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def raise_if_failed(self):
        pass


class FakeVAD:
    def __init__(self, _in_queue, out_queue):
        self.out_queue = out_queue

    def start(self):
        if MODE in ("flaky", "broken"):
            def feed():
                for i in range(8):
                    time.sleep(0.15)
                    self.out_queue.put(_segment(float(i)))
            threading.Thread(target=feed, daemon=True).start()

    def stop(self):
        if MODE in ("graceful", "stuck"):
            self.out_queue.put(_segment(99.0))  # the utterance in progress at shutdown


class FakeSegmentText:
    def __init__(self, text):
        self.text = text


class FakeModel:
    calls = 0

    def __init__(self, *_a, **_k):
        pass

    def transcribe(self, _audio):
        FakeModel.calls += 1
        if MODE == "stuck":
            time.sleep(120)
        if MODE == "broken" or (MODE == "flaky" and FakeModel.calls <= 2):
            raise RuntimeError("whisper exploded")
        return [FakeSegmentText(f"spoken text number {FakeModel.calls}")]


capture_mod = types.ModuleType("capture")
capture_mod.LoopbackCapture = FakeCapture
capture_mod.NoAudioError = NoAudioError
capture_mod.list_loopback_devices = lambda: []
vad_mod = types.ModuleType("vad")
vad_mod.SpeechSegment = SpeechSegment
vad_mod.VoiceActivityDetector = FakeVAD
whisper_pkg, whisper_model = types.ModuleType("pywhispercpp"), types.ModuleType("pywhispercpp.model")
whisper_model.Model = FakeModel
sys.modules.update(
    {"capture": capture_mod, "vad": vad_mod, "pywhispercpp": whisper_pkg, "pywhispercpp.model": whisper_model}
)

import transcribe

argv = ["--output", OUT, "--topic", "Acme kickoff", "--email", "me@example.com"]
if STORE_URL:
    argv += ["--store-url", STORE_URL]
transcribe.main(argv)
