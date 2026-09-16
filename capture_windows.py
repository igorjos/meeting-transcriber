"""WASAPI loopback audio capture (Windows backend).

Captures system/speaker output (not the microphone) via PyAudioWPatch's WASAPI
loopback support, normalizes it to 16 kHz mono PCM16, and pushes fixed-size
chunks onto a queue.Queue from a dedicated background thread.

This module is the Windows half of `capture.py`'s platform dispatch - see
`capture_macos.py` for the macOS (ScreenCaptureKit) equivalent. Both expose
the same public shape (`list_loopback_devices`, `LoopbackCapture`) so
`capture.py`/`vad.py`/`transcribe.py` don't need to know which platform
they're on.
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from typing import Optional

import pyaudiowpatch as pyaudio

from audio_common import CHUNK_MS, SAMPLE_WIDTH, TARGET_RATE, LoopbackDevice, NoAudioError, downmix_to_mono, resample_to_target


def _default_loopback_index(pa: "pyaudio.PyAudio") -> Optional[int]:
    """Best-effort match of the current default output device to its loopback device."""
    try:
        wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_speakers = pa.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
    except Exception:
        return None

    if default_speakers.get("isLoopbackDevice"):
        return default_speakers["index"]

    for loopback in pa.get_loopback_device_info_generator():
        if default_speakers["name"] in loopback["name"]:
            return loopback["index"]
    return None


def list_loopback_devices() -> list[LoopbackDevice]:
    """Enumerate every WASAPI loopback-capable ("record what you hear") device."""
    pa = pyaudio.PyAudio()
    try:
        default_index = _default_loopback_index(pa)
        devices = []
        for info in pa.get_loopback_device_info_generator():
            devices.append(
                LoopbackDevice(
                    index=info["index"],
                    name=info["name"],
                    sample_rate=int(info["defaultSampleRate"]),
                    channels=int(info["maxInputChannels"]),
                    is_default=(info["index"] == default_index),
                )
            )
        return devices
    finally:
        pa.terminate()


def _resolve_device(pa: "pyaudio.PyAudio", device_index: Optional[int]) -> dict:
    if device_index is not None:
        info = pa.get_device_info_by_index(device_index)
        if not info.get("isLoopbackDevice"):
            raise ValueError(
                f"Device {device_index} ({info['name']!r}) is not a WASAPI loopback device. "
                "Run with --list-devices to see valid choices."
            )
        return info

    default_index = _default_loopback_index(pa)
    if default_index is None:
        raise NoAudioError(
            "Could not determine a default loopback device. Run with --list-devices and "
            "pass --device <index> explicitly."
        )
    return pa.get_device_info_by_index(default_index)


class LoopbackCapture:
    """Captures system audio via WASAPI loopback on a dedicated thread.

    Produces ~chunk_ms chunks of 16 kHz mono PCM16 bytes onto `out_queue`.
    """

    def __init__(
        self,
        out_queue: "queue.Queue[bytes]",
        device_index: Optional[int] = None,
        chunk_ms: int = CHUNK_MS,
        silence_timeout: float = 8.0,
    ):
        self.out_queue = out_queue
        self.device_index = device_index
        self.chunk_ms = chunk_ms
        self.silence_timeout = silence_timeout

        self._pa: Optional["pyaudio.PyAudio"] = None
        self._stream = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._device_info: Optional[dict] = None
        self._error: Optional[BaseException] = None
        self._frames_captured = 0
        self._bytes_captured = 0

    @property
    def device_info(self) -> Optional[dict]:
        return self._device_info

    @property
    def frames_captured(self) -> int:
        return self._frames_captured

    @property
    def bytes_captured(self) -> int:
        return self._bytes_captured

    def start(self) -> None:
        self._pa = pyaudio.PyAudio()
        self._device_info = _resolve_device(self._pa, self.device_index)

        in_rate = int(self._device_info["defaultSampleRate"])
        in_channels = int(self._device_info["maxInputChannels"])
        frames_per_chunk = max(1, int(in_rate * self.chunk_ms / 1000))

        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=in_channels,
            rate=in_rate,
            input=True,
            input_device_index=self._device_info["index"],
            frames_per_buffer=frames_per_chunk,
        )

        self._stop_event.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._run,
            args=(in_rate, in_channels, frames_per_chunk),
            name="loopback-capture",
            daemon=True,
        )
        self._thread.start()

    def _run(self, in_rate: int, in_channels: int, frames_per_chunk: int) -> None:
        resample_state = None
        last_data_time = time.monotonic()
        warned_silent = False
        try:
            while not self._stop_event.is_set():
                try:
                    raw = self._stream.read(frames_per_chunk, exception_on_overflow=False)
                except Exception as exc:  # PortAudio/device errors
                    self._error = exc
                    break

                if not raw:
                    silent_for = time.monotonic() - last_data_time
                    if self._frames_captured == 0:
                        # Nothing has ever come through - almost always the
                        # wrong --device. Fail fast instead of hanging forever.
                        if silent_for > self.silence_timeout:
                            self._error = NoAudioError(
                                f"No audio received from device {self._device_info['index']} "
                                f"({self._device_info['name']!r}) for "
                                f"{self.silence_timeout:.0f}s. Is anything actually playing "
                                "through that output device? Run --list-devices and double-check "
                                "you picked the device matching your active speakers/headset."
                            )
                            break
                    elif silent_for > self.silence_timeout and not warned_silent:
                        # Audio has flowed before - a later quiet stretch (e.g.
                        # a muted meeting) is normal, not a device problem, so
                        # it's only ever a warning, never fatal.
                        print(
                            f"WARNING: no audio from device {self._device_info['index']} for "
                            f"{silent_for:.0f}s (nothing playing?).",
                            file=sys.stderr,
                        )
                        warned_silent = True
                    continue

                last_data_time = time.monotonic()
                warned_silent = False
                self._frames_captured += 1
                self._bytes_captured += len(raw)

                mono = downmix_to_mono(raw, in_channels)

                if in_rate != TARGET_RATE:
                    mono, resample_state = resample_to_target(mono, in_rate, resample_state)

                try:
                    self.out_queue.put(mono, timeout=1.0)
                except queue.Full:
                    # Consumer is stalled; drop this chunk rather than block capture.
                    pass
        finally:
            self._close_stream()

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        # Closing the stream first is what actually unblocks a capture thread
        # parked inside a blocking stream.read() (e.g. while the render
        # endpoint is idle and delivering nothing) - join()ing before closing
        # would just wait out the full timeout for no reason.
        self._close_stream()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None

    def raise_if_failed(self) -> None:
        if self._error is not None:
            err, self._error = self._error, None
            raise err
