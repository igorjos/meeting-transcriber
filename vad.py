"""Voice Activity Detection.

Consumes 16 kHz mono PCM16 chunks from a queue.Queue (as produced by
capture.py), runs Silero VAD in streaming mode, and emits complete speech
utterances onto an output queue for the ASR stage to transcribe.

Runs on its own thread, decoupled from capture, so VAD/model inference never
blocks (or is blocked by) the audio capture thread.
"""
from __future__ import annotations

import argparse
import collections
import math
import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
from silero_vad import VADIterator, load_silero_vad

SAMPLE_RATE = 16000
WINDOW_SAMPLES = 512  # fixed window size required by the 16 kHz Silero model
WINDOW_BYTES = WINDOW_SAMPLES * 2  # PCM16 -> 2 bytes/sample


@dataclass
class SpeechSegment:
    """One detected speech utterance, ready for ASR."""

    audio: np.ndarray  # float32 mono PCM in [-1, 1], 16 kHz
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return len(self.audio) / SAMPLE_RATE


class VoiceActivityDetector:
    """Streams PCM16 chunks through Silero VAD and emits SpeechSegments."""

    def __init__(
        self,
        in_queue: "queue.Queue[bytes]",
        out_queue: "queue.Queue[SpeechSegment]",
        threshold: float = 0.5,
        min_silence_duration_ms: int = 400,
        speech_pad_ms: int = 200,
        max_segment_s: float = 28.0,
    ):
        self.in_queue = in_queue
        self.out_queue = out_queue
        self.max_segment_s = max_segment_s

        self._model = load_silero_vad()
        self._iterator = VADIterator(
            self._model,
            threshold=threshold,
            sampling_rate=SAMPLE_RATE,
            min_silence_duration_ms=min_silence_duration_ms,
            speech_pad_ms=speech_pad_ms,
        )

        pre_roll_windows = max(1, math.ceil(speech_pad_ms / (WINDOW_SAMPLES / SAMPLE_RATE * 1000)))
        self._pre_roll: "collections.deque[np.ndarray]" = collections.deque(maxlen=pre_roll_windows)

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._byte_buffer = bytearray()  # PCM bytes not yet aligned to a full window
        self._speech_frames: list[np.ndarray] = []
        self._speech_start_sample: Optional[int] = None
        self._samples_seen = 0

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="vad", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        # The thread is joined (no longer touching this state), so flushing
        # here is race-free: whatever utterance was still in progress when
        # stopped is emitted as a final segment instead of silently dropped.
        if self._speech_start_sample is not None:
            self._emit_segment()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                chunk = self.in_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            self._byte_buffer.extend(chunk)

            while len(self._byte_buffer) >= WINDOW_BYTES:
                window_bytes = bytes(self._byte_buffer[:WINDOW_BYTES])
                del self._byte_buffer[:WINDOW_BYTES]
                self._process_window(window_bytes)

    def _process_window(self, window_bytes: bytes) -> None:
        pcm16 = np.frombuffer(window_bytes, dtype=np.int16)
        audio_f32 = pcm16.astype(np.float32) / 32768.0

        event = self._iterator(audio_f32, return_seconds=False)

        if event is not None and "start" in event:
            # New utterance: prepend recent pre-roll so we don't clip the onset.
            self._speech_frames = list(self._pre_roll) + [audio_f32]
            self._speech_start_sample = self._samples_seen - len(self._pre_roll) * WINDOW_SAMPLES
        elif self._speech_start_sample is not None:
            self._speech_frames.append(audio_f32)

        if event is not None and "end" in event and self._speech_start_sample is not None:
            self._emit_segment()

        self._pre_roll.append(audio_f32)
        self._samples_seen += WINDOW_SAMPLES

        if self._speech_start_sample is not None:
            duration_s = (self._samples_seen - self._speech_start_sample) / SAMPLE_RATE
            if duration_s >= self.max_segment_s:
                # Force-flush overlong utterances (e.g. a long monologue) so ASR
                # keeps getting bounded-length segments and stdout stays live.
                self._emit_segment()
                self._iterator.reset_states()

    def _emit_segment(self) -> None:
        if not self._speech_frames:
            self._speech_start_sample = None
            return
        audio = np.concatenate(self._speech_frames)
        start_s = max(0, self._speech_start_sample) / SAMPLE_RATE
        end_s = self._samples_seen / SAMPLE_RATE
        segment = SpeechSegment(audio=audio, start_s=start_s, end_s=end_s)

        self._speech_frames = []
        self._speech_start_sample = None

        try:
            self.out_queue.put(segment, timeout=1.0)
        except queue.Full:
            pass


# --------------------------------------------------------------------------
# Standalone CLI: run capture + VAD together and print detected segments,
# to verify speech is actually being segmented before wiring up ASR.
# --------------------------------------------------------------------------


def _cmd_test(device_index: Optional[int], seconds: float) -> None:
    import capture as capture_mod

    cap_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=512)
    seg_queue: "queue.Queue[SpeechSegment]" = queue.Queue()

    cap = capture_mod.LoopbackCapture(cap_queue, device_index=device_index)
    vad = VoiceActivityDetector(cap_queue, seg_queue)

    cap.start()
    vad.start()

    info = cap.device_info
    print(f"Listening on: {info['name']} for {seconds:.1f}s (speak or play speech now)...")

    segments_found = 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            seg = seg_queue.get(timeout=0.5)
        except queue.Empty:
            cap.raise_if_failed()
            continue
        segments_found += 1
        print(f"  segment {segments_found}: {seg.start_s:.2f}s -> {seg.end_s:.2f}s ({seg.duration_s:.2f}s of speech)")

    vad.stop()
    cap.stop()
    cap.raise_if_failed()

    if segments_found == 0:
        print(
            "\nNo speech segments detected. Either nothing was said/played, or --device is "
            "wrong. Run 'python capture.py --list-devices' to check the device index."
        )
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Silero VAD segmentation (test/debug utility).")
    parser.add_argument("--list-devices", action="store_true", help="List loopback devices and exit.")
    parser.add_argument("--device", type=int, default=None, help="Loopback device index to use.")
    parser.add_argument("--seconds", type=float, default=10.0, help="Seconds to listen in test mode.")
    args = parser.parse_args()

    if args.list_devices:
        import capture as capture_mod

        for dev in capture_mod.list_loopback_devices():
            print(f"  {dev}")
        return

    try:
        _cmd_test(args.device, args.seconds)
    except Exception as exc:  # NoAudioError, ValueError, etc.
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
