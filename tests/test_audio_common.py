"""Tests for audio_common.py's pure downmix/resample helpers - no audio
hardware needed."""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import audio_common
from audio_common import TARGET_RATE, DropWarner, downmix_to_mono, resample_to_target


class DownmixToMonoTests(unittest.TestCase):
    def test_mono_input_passes_through_unchanged(self):
        raw = np.array([1, -2, 3, -4], dtype=np.int16).tobytes()
        self.assertEqual(downmix_to_mono(raw, 1), raw)

    def test_stereo_averages_left_and_right(self):
        # Frame 1: L=0, R=100 -> 50. Frame 2: L=-100, R=100 -> 0.
        raw = np.array([0, 100, -100, 100], dtype=np.int16).tobytes()
        result = np.frombuffer(downmix_to_mono(raw, 2), dtype=np.int16)
        np.testing.assert_array_equal(result, [50, 0])

    def test_multi_channel_averages_all_channels(self):
        raw = np.array([0, 30, 60], dtype=np.int16).tobytes()  # one 3-channel frame
        result = np.frombuffer(downmix_to_mono(raw, 3), dtype=np.int16)
        np.testing.assert_array_equal(result, [30])


class ResampleToTargetTests(unittest.TestCase):
    def test_noop_when_input_rate_matches_target(self):
        pcm = np.array([1, 2, 3], dtype=np.int16).tobytes()
        out, state = resample_to_target(pcm, TARGET_RATE)
        self.assertEqual(out, pcm)
        self.assertIsNone(state)

    def test_downsamples_when_rate_differs(self):
        samples = (np.sin(np.linspace(0, 2 * np.pi, 480)) * 1000).astype(np.int16)
        pcm = samples.tobytes()
        out, state = resample_to_target(pcm, 48000)
        out_samples = np.frombuffer(out, dtype=np.int16)
        # 48kHz -> 16kHz is a 3x reduction.
        self.assertLess(len(out_samples), len(samples))
        self.assertIsNotNone(state)

    def test_state_carries_across_calls_without_error(self):
        pcm = (np.sin(np.linspace(0, 2 * np.pi, 480)) * 1000).astype(np.int16).tobytes()
        out1, state1 = resample_to_target(pcm, 48000)
        out2, state2 = resample_to_target(pcm, 48000, state1)
        self.assertIsInstance(out1, bytes)
        self.assertIsInstance(out2, bytes)


class DropWarnerTests(unittest.TestCase):
    def _run(self, times):
        """Record one drop at each fake-clock time in `times`; return (warner, stderr text)."""
        warner = DropWarner("audio chunk(s)", interval_s=5.0)
        err = io.StringIO()
        clock = iter(times)
        with mock.patch.object(audio_common.time, "monotonic", side_effect=lambda: next(clock)), \
                contextlib.redirect_stderr(err):
            for _ in times:
                warner.record()
        return warner, err.getvalue()

    def test_first_drop_warns_immediately(self):
        warner, err = self._run([100.0])
        self.assertEqual(warner.total, 1)
        self.assertIn("dropped 1 audio chunk(s)", err)

    def test_drops_within_the_interval_are_batched_into_one_later_warning(self):
        warner, err = self._run([100.0, 101.0, 102.0, 103.0, 106.0])
        self.assertEqual(warner.total, 5)
        self.assertEqual(err.count("WARNING"), 2)
        self.assertIn("dropped 1 audio chunk(s)", err.splitlines()[0])
        self.assertIn("dropped 4 audio chunk(s)", err.splitlines()[1])
        self.assertIn("5 total", err.splitlines()[1])


if __name__ == "__main__":
    unittest.main()
