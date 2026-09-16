"""Tests for audio_common.py's pure downmix/resample helpers - no audio
hardware needed."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from audio_common import TARGET_RATE, downmix_to_mono, resample_to_target


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


if __name__ == "__main__":
    unittest.main()
