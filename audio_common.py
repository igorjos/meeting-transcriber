"""Shared types/constants/helpers for the platform capture backends.

Windows (`capture_windows.py`, WASAPI loopback via PyAudioWPatch) and macOS
(`capture_macos.py`, ScreenCaptureKit) both normalize whatever raw audio they
capture down to 16 kHz mono PCM16 chunks before handing them to `capture.py`'s
output queue - this module holds the bits that are identical either way, so
that normalization isn't duplicated per backend.
"""
from __future__ import annotations

import audioop
from dataclasses import dataclass
from typing import Optional

TARGET_RATE = 16000  # sample rate expected by Silero VAD and whisper.cpp
SAMPLE_WIDTH = 2  # bytes per sample (PCM16)
CHUNK_MS = 30  # nominal size of each chunk pushed to the queue


class NoAudioError(RuntimeError):
    """Raised when a capture stream produces no usable audio.

    Almost always means the wrong --device index was passed (e.g. a disabled
    or disconnected output device, or the mic instead of a loopback device),
    or - on macOS - that Screen Recording permission hasn't been granted.
    """


@dataclass
class LoopbackDevice:
    index: int
    name: str
    sample_rate: int
    channels: int
    is_default: bool = False

    def __str__(self) -> str:
        tag = " [default]" if self.is_default else ""
        return f"[{self.index}] {self.name} - {self.sample_rate} Hz, {self.channels} ch{tag}"


def downmix_to_mono(raw: bytes, channels: int) -> bytes:
    """Downmix interleaved PCM16 to mono."""
    if channels == 1:
        return raw
    if channels == 2:
        return audioop.tomono(raw, SAMPLE_WIDTH, 0.5, 0.5)
    # Uncommon (>2 channel device) - fall back to a numpy average.
    import numpy as np

    samples = np.frombuffer(raw, dtype=np.int16).reshape(-1, channels)
    return samples.mean(axis=1).astype(np.int16).tobytes()


def resample_to_target(mono_pcm16: bytes, in_rate: int, state=None) -> tuple[bytes, object]:
    """Resample mono PCM16 to TARGET_RATE, carrying `state` across calls for continuity."""
    if in_rate == TARGET_RATE:
        return mono_pcm16, state
    return audioop.ratecv(mono_pcm16, SAMPLE_WIDTH, 1, in_rate, TARGET_RATE, state)
