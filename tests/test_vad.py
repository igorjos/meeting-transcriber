"""Tests for vad.py's segmentation logic, with a scripted stand-in for the
Silero iterator so no model/torch inference runs."""
from __future__ import annotations

import contextlib
import io
import queue
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import vad


class ScriptedIterator:
    """Stands in for silero's VADIterator: returns the event scripted for the
    Nth window ({"start": ...} / {"end": ...}) and counts reset_states()."""

    def __init__(self, script: dict):
        self.script = script
        self.calls = 0
        self.resets = 0

    def __call__(self, audio, return_seconds=False):
        event = self.script.get(self.calls)
        self.calls += 1
        return event

    def reset_states(self):
        self.resets += 1


def _window(i: int) -> bytes:
    """One 512-sample window whose every sample is (i+1)*100, so a window's
    audio is identifiable inside a concatenated segment."""
    return np.full(vad.WINDOW_SAMPLES, (i + 1) * 100, dtype=np.int16).tobytes()


def _values(audio: np.ndarray) -> set:
    return set(np.unique(np.round(audio * 32768.0)).astype(int).tolist())


class VadSegmentationTests(unittest.TestCase):
    def _detector(self, script: dict, out_queue=None, **kwargs):
        iterator = ScriptedIterator(script)
        out_queue = out_queue if out_queue is not None else queue.Queue()
        with mock.patch.object(vad, "load_silero_vad", return_value=object()), mock.patch.object(
            vad, "VADIterator", return_value=iterator
        ):
            detector = vad.VoiceActivityDetector(queue.Queue(), out_queue, **kwargs)
        return detector, iterator, out_queue

    def test_speech_between_start_and_end_becomes_one_segment_with_pre_roll(self):
        detector, _it, out = self._detector({1: {"start": 0}, 4: {"end": 0}})
        for i in range(6):
            detector._process_window(_window(i))

        segment = out.get_nowait()
        # window 0 (pre-roll, before the start event) through window 4 (end event)
        self.assertEqual(_values(segment.audio), {100, 200, 300, 400, 500})
        self.assertEqual(len(segment.audio), 5 * vad.WINDOW_SAMPLES)
        self.assertTrue(out.empty())

    def test_stop_flushes_an_utterance_still_in_progress(self):
        detector, _it, out = self._detector({0: {"start": 0}})
        for i in range(3):
            detector._process_window(_window(i))
        self.assertTrue(out.empty())

        detector.stop()
        segment = out.get_nowait()
        self.assertEqual(_values(segment.audio), {100, 200, 300})

    def test_forced_split_does_not_repeat_audio_from_the_previous_segment(self):
        # max_segment_s=0.1 forces a flush after 4 windows (2048 samples).
        detector, iterator, out = self._detector({0: {"start": 0}, 5: {"start": 0}, 6: {"end": 0}}, max_segment_s=0.1)
        for i in range(8):
            detector._process_window(_window(i))

        first, second = out.get_nowait(), out.get_nowait()
        self.assertEqual(_values(first.audio), {100, 200, 300, 400})
        self.assertEqual(iterator.resets, 1)
        # The pre-roll used to still hold windows 0-3 here, so the next
        # segment began with ~200ms that had already been emitted.
        self.assertTrue(_values(second.audio).isdisjoint({100, 200, 300, 400}), _values(second.audio))
        self.assertEqual(_values(second.audio), {500, 600, 700})  # w4 = silence-side pre-roll

    def test_dropped_segments_are_counted_and_reported(self):
        detector, _it, out = self._detector(
            {0: {"start": 0}, 1: {"end": 0}, 3: {"start": 0}, 4: {"end": 0}}, out_queue=queue.Queue(maxsize=1)
        )
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for i in range(5):
                detector._process_window(_window(i))

        self.assertEqual(out.qsize(), 1)
        self.assertEqual(detector._drops.total, 1)
        self.assertIn("dropped 1 speech segment(s)", err.getvalue())


if __name__ == "__main__":
    unittest.main()
