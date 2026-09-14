"""ScreenCaptureKit-based system-audio capture (macOS backend).

macOS has no WASAPI-style loopback API. Instead this uses ScreenCaptureKit's
audio capture (SCStream with SCStreamConfiguration.capturesAudio, macOS 13+):
no virtual audio driver or Multi-Output Device setup required, but it does
need the running process (your terminal, or the `python3.x` binary if run
directly) to be granted Screen Recording permission in System Settings ->
Privacy & Security -> Screen Recording, even though only audio is captured -
that's an OS-level requirement, not something this code can bypass.

Audio arrives asynchronously as Float32 interleaved PCM at a rate/channel
count we fix via SCStreamConfiguration (48 kHz stereo), delivered on a
dedicated libdispatch queue - so unlike the Windows backend there's no
blocking `stream.read()` loop; `_on_audio_sample_buffer` is invoked directly
by ScreenCaptureKit per sample buffer. It's converted to int16 here and then
run through the same downmix/resample path as the Windows backend.

"Devices" don't exist the way WASAPI loopback devices do; instead
`device_index` selects a capture *target*: 0 (default) is the whole system
mix, N>=1 is a single running application's audio in isolation (handy for
capturing just Teams/Zoom/etc, e.g. to skip notification dings) - see
`list_loopback_devices`. Since ScreenCaptureKit enumerates running apps
fresh on every call, indices can shift between one `--list-devices` and the
next `start()` if apps launch/quit in between - re-run `--list-devices`
right before picking one, same as the Windows loopback indices are already
documented not to be stable across reboots.
"""
from __future__ import annotations

import ctypes
import queue
import threading
import time
from typing import Optional

import numpy as np

import AppKit
import CoreAudio as CA
import CoreMedia as CM
import dispatch
import Foundation
import objc
import ScreenCaptureKit as SCK

from audio_common import CHUNK_MS, LoopbackDevice, NoAudioError, downmix_to_mono, resample_to_target

AUDIO_SAMPLE_RATE = 48000  # what we ask SCStreamConfiguration for
AUDIO_CHANNELS = 2

_TCC_DENIED_CODE = -3801  # SCStreamErrorDomain: user/OS declined capture permission
_MAX_AUDIO_BUFFERS = 2  # stereo is delivered as 1 interleaved buffer; allow up to 2 defensively


class _CAudioBuffer(ctypes.Structure):
    _fields_ = [
        ("mNumberChannels", ctypes.c_uint32),
        ("mDataByteSize", ctypes.c_uint32),
        ("mData", ctypes.c_void_p),
    ]


class _CAudioBufferList(ctypes.Structure):
    _fields_ = [
        ("mNumberBuffers", ctypes.c_uint32),
        ("mBuffers", _CAudioBuffer * _MAX_AUDIO_BUFFERS),
    ]


# Byte size CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer needs for
# the AudioBufferList we hand it - computed via a ctypes mirror of the real C
# struct (which PyObjC doesn't expose a sizeof() for) purely to get correct
# struct-layout/alignment math, not used for the actual data marshaling.
_AUDIO_BUFFER_LIST_SIZE = ctypes.sizeof(_CAudioBufferList)


def _run_until(predicate, timeout: float) -> bool:
    """Pump the calling thread's NSRunLoop until predicate() is true or timeout elapses.

    Used to turn ScreenCaptureKit's completion-handler-style async calls
    (getShareableContentWithCompletionHandler_, startCaptureWithCompletionHandler_,
    ...) into synchronous calls.
    """
    run_loop = Foundation.NSRunLoop.currentRunLoop()
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        run_loop.runUntilDate_(Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.05))
    return predicate()


def _friendly_sck_error(error) -> str:
    code = None
    try:
        code = error.code()
    except Exception:
        pass
    if code == _TCC_DENIED_CODE:
        return (
            "macOS denied screen/audio capture permission (this is required for system-audio "
            "capture even though no video is recorded). Grant it in System Settings -> Privacy "
            "& Security -> Screen Recording for your terminal app (or the python3.x binary, if "
            "run directly), then quit and reopen that app and retry."
        )
    try:
        return str(error.localizedDescription())
    except Exception:
        return str(error)


def _get_shareable_content(timeout: float = 5.0):
    result: dict = {}

    def handler(content, error):
        result["content"] = content
        result["error"] = error

    SCK.SCShareableContent.getShareableContentWithCompletionHandler_(handler)
    if not _run_until(lambda: "content" in result, timeout):
        raise NoAudioError("Timed out waiting for macOS to list capturable screens/apps.")

    error = result["error"]
    if error is not None:
        raise NoAudioError(_friendly_sck_error(error))
    return result["content"]


def _regular_app_pids() -> set:
    """PIDs of user-facing (Dock-visible, "regular") running apps.

    ScreenCaptureKit's own `SCShareableContent.applications()` lists every
    process that owns any window/layer - Dock, Spotlight, Notification
    Center, per-app "Open and Save Panel Service"/"AutoFill" helpers, etc. -
    which is far too noisy for "pick an app to isolate its audio". Regular
    activation policy (as opposed to accessory/prohibited) is the same
    signal macOS itself uses to decide what shows up in the Dock/Cmd-Tab,
    and matches what a user actually means by "an app".
    """
    workspace = AppKit.NSWorkspace.sharedWorkspace()
    return {
        app.processIdentifier()
        for app in workspace.runningApplications()
        if app.activationPolicy() == AppKit.NSApplicationActivationPolicyRegular
    }


def _capturable_apps(content) -> list:
    """SCShareableContent's running apps, filtered down to regular (Dock-visible) ones."""
    regular_pids = _regular_app_pids()
    return [app for app in content.applications() if app.processID() in regular_pids]


def _resolve_target(content, device_index: Optional[int]):
    """Return (SCRunningApplication or None, friendly-name) for device_index.

    None target means "no app filter" (whole-system audio).
    """
    if device_index is None or device_index == 0:
        return None, "System Audio (All Apps)"

    apps = _capturable_apps(content)
    app_idx = device_index - 1
    if app_idx < 0 or app_idx >= len(apps):
        raise ValueError(
            f"Device {device_index} is not a valid capture target. "
            "Run with --list-devices to see valid choices."
        )
    app = apps[app_idx]
    return app, f"App: {app.applicationName()}"


def list_loopback_devices() -> list[LoopbackDevice]:
    """Enumerate capture targets: whole-system audio, plus one entry per regular running app."""
    content = _get_shareable_content()
    devices = [
        LoopbackDevice(
            index=0,
            name="System Audio (All Apps)",
            sample_rate=AUDIO_SAMPLE_RATE,
            channels=AUDIO_CHANNELS,
            is_default=True,
        )
    ]
    for i, app in enumerate(_capturable_apps(content), start=1):
        name = app.applicationName() or app.bundleIdentifier() or f"pid {app.processID()}"
        devices.append(
            LoopbackDevice(
                index=i,
                name=f"App: {name}",
                sample_rate=AUDIO_SAMPLE_RATE,
                channels=AUDIO_CHANNELS,
            )
        )
    return devices


def _extract_pcm16(sample_buffer) -> tuple[bytes, int]:
    """Pull interleaved int16 PCM out of a ScreenCaptureKit audio CMSampleBuffer.

    SCStream delivers Float32 interleaved PCM (per the SCStreamConfiguration
    we set in LoopbackCapture.start()); this converts it to the int16 PCM the
    rest of the pipeline (audio_common's downmix/resample, VAD, whisper.cpp)
    expects.
    """
    buffer_list = CA.AudioBufferList(_MAX_AUDIO_BUFFERS)
    status, _size_needed, _block_buffer = CM.CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
        sample_buffer, None, buffer_list, _AUDIO_BUFFER_LIST_SIZE, None, None, 0, None
    )
    if status != 0 or len(buffer_list) == 0:
        return b"", 0

    audio_buffer = buffer_list[0]
    channels = int(audio_buffer.mNumberChannels) or AUDIO_CHANNELS
    raw_f32 = bytes(audio_buffer.mData)  # memoryview -> bytes copy, Float32 interleaved

    samples_f32 = np.frombuffer(raw_f32, dtype=np.float32)
    samples_i16 = np.clip(samples_f32 * 32768.0, -32768, 32767).astype(np.int16)
    return samples_i16.tobytes(), channels


class _StreamHandler(Foundation.NSObject):
    """SCStreamOutput + SCStreamDelegate sink; forwards callbacks to a LoopbackCapture."""

    def initWithCapture_(self, capture: "LoopbackCapture"):
        self = objc.super(_StreamHandler, self).init()
        if self is None:
            return None
        self._capture = capture
        return self

    def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, of_type):
        if of_type != SCK.SCStreamOutputTypeAudio:
            return
        self._capture._on_audio_sample_buffer(sample_buffer)

    def stream_didStopWithError_(self, stream, error):
        self._capture._on_stream_error(error)


class LoopbackCapture:
    """Captures system audio via ScreenCaptureKit.

    Produces 16 kHz mono PCM16 bytes onto `out_queue`, same as the Windows
    backend. `chunk_ms` is accepted for API parity but is informational only
    here - ScreenCaptureKit/CoreAudio choose its own audio buffer sizing.
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

        self._stream = None
        self._handler: Optional[_StreamHandler] = None
        self._device_info: Optional[dict] = None
        self._error: Optional[BaseException] = None
        self._frames_captured = 0
        self._bytes_captured = 0
        self._resample_state = None

        self._lock = threading.Lock()
        self._last_data_time: Optional[float] = None
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None

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
        content = _get_shareable_content()
        target, name = _resolve_target(content, self.device_index)

        displays = list(content.displays())
        if not displays:
            raise NoAudioError(
                "No capturable displays reported by ScreenCaptureKit (is Screen Recording "
                "permission granted? See --list-devices for details)."
            )
        display = displays[0]

        if target is None:
            content_filter = SCK.SCContentFilter.alloc().initWithDisplay_excludingApplications_exceptingWindows_(
                display, [], []
            )
        else:
            content_filter = SCK.SCContentFilter.alloc().initWithDisplay_includingApplications_exceptingWindows_(
                display, [target], []
            )

        config = SCK.SCStreamConfiguration.alloc().init()
        config.setCapturesAudio_(True)
        config.setSampleRate_(AUDIO_SAMPLE_RATE)
        config.setChannelCount_(AUDIO_CHANNELS)
        config.setExcludesCurrentProcessAudio_(False)
        # We only want audio, but SCStream always carries a video side too -
        # keep it minimal (tiny frame, ~1 fps) so it doesn't cost real CPU/GPU.
        config.setWidth_(2)
        config.setHeight_(2)
        config.setMinimumFrameInterval_(CM.CMTimeMake(1, 1))
        config.setShowsCursor_(False)

        self._handler = _StreamHandler.alloc().initWithCapture_(self)
        self._stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(
            content_filter, config, self._handler
        )

        audio_queue = dispatch.dispatch_queue_create(b"transcriber.macos-capture", None)
        ok, add_error = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self._handler, SCK.SCStreamOutputTypeAudio, audio_queue, None
        )
        if not ok:
            raise NoAudioError(f"Could not attach ScreenCaptureKit audio output: {add_error}")

        start_result: dict = {}

        def on_start(error):
            start_result["error"] = error

        self._stream.startCaptureWithCompletionHandler_(on_start)
        if not _run_until(lambda: "error" in start_result, 10.0):
            raise NoAudioError("Timed out starting ScreenCaptureKit audio capture.")
        if start_result["error"] is not None:
            raise NoAudioError(_friendly_sck_error(start_result["error"]))

        self._device_info = {
            "index": self.device_index if self.device_index is not None else 0,
            "name": name,
            "defaultSampleRate": float(AUDIO_SAMPLE_RATE),
            "maxInputChannels": AUDIO_CHANNELS,
        }
        self._error = None
        self._last_data_time = time.monotonic()
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor, name="macos-capture-monitor", daemon=True)
        self._monitor_thread.start()

    def _monitor(self) -> None:
        """Watches for silence_timeout with no audio delivered, same contract as Windows'
        in-loop check - data itself arrives via `_on_audio_sample_buffer`, not here."""
        while not self._stop_event.is_set():
            time.sleep(0.5)
            with self._lock:
                last = self._last_data_time
            if last is not None and time.monotonic() - last > self.silence_timeout:
                self._error = NoAudioError(
                    f"No audio received via ScreenCaptureKit for {self.silence_timeout:.0f}s. "
                    "Is anything actually playing? If this is the first run, check System "
                    "Settings -> Privacy & Security -> Screen Recording and confirm this "
                    "app/terminal has access, then restart it and retry."
                )
                break

    def _on_audio_sample_buffer(self, sample_buffer) -> None:
        try:
            pcm16, channels = _extract_pcm16(sample_buffer)
        except Exception:
            return
        if not pcm16:
            return

        with self._lock:
            self._last_data_time = time.monotonic()
        self._frames_captured += 1
        self._bytes_captured += len(pcm16)

        mono = downmix_to_mono(pcm16, channels)
        resampled, self._resample_state = resample_to_target(mono, AUDIO_SAMPLE_RATE, self._resample_state)

        try:
            self.out_queue.put(resampled, timeout=1.0)
        except queue.Full:
            # Consumer is stalled; drop this chunk rather than block capture.
            pass

    def _on_stream_error(self, error) -> None:
        self._error = NoAudioError(f"ScreenCaptureKit stream stopped unexpectedly: {error}")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._stream is not None:
            stop_result: dict = {}

            def on_stop(error):
                stop_result["error"] = error

            try:
                self._stream.stopCaptureWithCompletionHandler_(on_stop)
                _run_until(lambda: "error" in stop_result, timeout)
            except Exception:
                pass
            self._stream = None

        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=timeout)
            self._monitor_thread = None
        self._handler = None

    def raise_if_failed(self) -> None:
        if self._error is not None:
            err, self._error = self._error, None
            raise err
