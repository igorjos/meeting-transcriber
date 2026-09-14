"""System/speaker audio capture - platform dispatch.

Captures system/speaker output (not the microphone), normalizes it to
16 kHz mono PCM16, and pushes fixed-size chunks onto a queue.Queue from a
dedicated background thread. Downstream consumers (VAD, ASR) pull from that
queue on their own thread(s), so slow inference never blocks or drops audio
capture.

The actual capture mechanism is platform-specific and lives in
`capture_windows.py` (WASAPI loopback via PyAudioWPatch) or `capture_macos.py`
(ScreenCaptureKit) - both expose the same shape (`list_loopback_devices`,
`LoopbackCapture`, `NoAudioError`), so this module and everything downstream
of it (`vad.py`, `transcribe.py`) stay platform-agnostic.

Run standalone to enumerate devices and sanity-check that capture actually
works before wiring up VAD/ASR:

    python capture.py --list-devices
    python capture.py --device 3 --seconds 5 --wav test.wav
"""
from __future__ import annotations

import argparse
import queue
import sys
import time
import wave
from typing import Optional

from audio_common import SAMPLE_WIDTH, TARGET_RATE, LoopbackDevice, NoAudioError  # noqa: F401 (re-exported)

if sys.platform == "win32":
    from capture_windows import LoopbackCapture, list_loopback_devices
elif sys.platform == "darwin":
    from capture_macos import LoopbackCapture, list_loopback_devices
else:
    raise NotImplementedError(
        f"No system-audio capture backend for platform {sys.platform!r}. "
        "Supported: Windows (WASAPI loopback) and macOS (ScreenCaptureKit)."
    )

_NO_DEVICES_HINT = {
    "win32": "Is this Windows with an active audio output?",
    "darwin": "Is Screen Recording permission granted (System Settings -> Privacy & Security)?",
}.get(sys.platform, "")


# --------------------------------------------------------------------------
# Standalone CLI: enumerate devices / smoke-test capture before wiring up
# VAD and ASR on top of it.
# --------------------------------------------------------------------------


def _cmd_list_devices() -> None:
    devices = list_loopback_devices()
    if not devices:
        print(f"No loopback/capture devices found. {_NO_DEVICES_HINT}")
        return
    print("Available loopback ('record what you hear') devices:")
    for dev in devices:
        print(f"  {dev}")


def _cmd_test(device_index: Optional[int], seconds: float, wav_path: Optional[str]) -> None:
    import numpy as np

    q: "queue.Queue[bytes]" = queue.Queue(maxsize=512)
    capture = LoopbackCapture(q, device_index=device_index)
    capture.start()

    info = capture.device_info
    print(f"Recording from: {info['name']} ({info['defaultSampleRate']:.0f} Hz -> {TARGET_RATE} Hz)")
    print(f"Capturing {seconds:.1f}s of audio... play something now.")

    chunks: list[bytes] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            chunks.append(q.get(timeout=0.5))
        except queue.Empty:
            capture.raise_if_failed()
            continue

    capture.stop()
    capture.raise_if_failed()

    if not chunks:
        print(
            "\nNo audio captured. This is almost always the wrong device index.\n"
            "Run 'python capture.py --list-devices' and pass --device <index> for the "
            "device that matches the output you want to capture (e.g. your headset, "
            "not a disabled/disconnected device)."
        )
        sys.exit(1)

    pcm = b"".join(chunks)
    samples = np.frombuffer(pcm, dtype=np.int16)
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) if len(samples) else 0.0
    peak = int(np.max(np.abs(samples))) if len(samples) else 0
    duration = len(samples) / TARGET_RATE

    print(f"\nCaptured {duration:.2f}s of audio ({len(pcm)} bytes, {capture.frames_captured} reads).")
    print(f"RMS level: {rms:.1f} / 32768   Peak: {peak} / 32768")
    if rms < 5:
        print(
            "WARNING: audio level is near-zero. The stream opened but nothing audible "
            "was captured (wrong device, or nothing was playing)."
        )
    else:
        print("Looks good - non-silent audio was captured.")

    if wav_path:
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(TARGET_RATE)
            wf.writeframes(pcm)
        print(f"Saved test recording to: {wav_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="System-audio loopback capture (test/debug utility).")
    parser.add_argument("--list-devices", action="store_true", help="List loopback devices and exit.")
    parser.add_argument("--device", type=int, default=None, help="Loopback device index to use.")
    parser.add_argument("--seconds", type=float, default=5.0, help="Seconds to record in test mode.")
    parser.add_argument("--wav", type=str, default=None, help="Optional path to save the test capture as a WAV.")
    args = parser.parse_args()

    if args.list_devices:
        _cmd_list_devices()
        return

    try:
        _cmd_test(args.device, args.seconds, args.wav)
    except NoAudioError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
