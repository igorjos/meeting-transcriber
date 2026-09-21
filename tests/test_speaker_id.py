"""Tests for speaker_id.py's pure signal-processing helpers and clustering -
no audio hardware or real voice recordings needed."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import speaker_id

SR = speaker_id.SAMPLE_RATE


def _synthetic_voice(f0: float, harmonics: list, duration_s: float = 1.0, seed: int = 0) -> np.ndarray:
    """Deterministic periodic 'voice-like' signal (fundamental + weighted
    harmonics, plus a little noise) - not real speech, just a repeatable
    stand-in with a distinct pitch/timbre per (f0, harmonics) pair."""
    rng = np.random.default_rng(seed)
    n = int(SR * duration_s)
    t = np.arange(n) / SR
    signal = np.zeros(n, dtype=np.float32)
    for i, weight in enumerate(harmonics, start=1):
        signal += weight * np.sin(2 * np.pi * f0 * i * t)
    signal += 0.01 * rng.standard_normal(n).astype(np.float32)
    return (signal / np.max(np.abs(signal)) * 0.8).astype(np.float32)


def _whispered_voice(f0: float, harmonics: list, duration_s: float = 0.3, seed: int = 0) -> np.ndarray:
    """Like _synthetic_voice, but with enough noise mixed in to break
    autocorrelation-based voicing (extract_voiceprint's pitch block comes
    back all-zero) while keeping enough frames for a real multi-frame MFCC
    average - a stand-in for a quiet/breathy/whispered utterance, not a
    sub-one-frame clip."""
    rng = np.random.default_rng(seed)
    n = int(SR * duration_s)
    t = np.arange(n) / SR
    signal = np.zeros(n, dtype=np.float32)
    for i, weight in enumerate(harmonics, start=1):
        signal += weight * np.sin(2 * np.pi * f0 * i * t)
    signal += 1.5 * rng.standard_normal(n).astype(np.float32)
    return (signal / np.max(np.abs(signal)) * 0.8).astype(np.float32)


def _formant_voice(f0: float, formants: list, duration_s: float = 1.0, seed: int = 0) -> np.ndarray:
    """Pulse train at f0 shaped by a couple of resonance ('formant') peaks -
    a much closer proxy for vocal-tract-driven timbre difference than
    _synthetic_voice's plain per-harmonic reweighting. Needed because two
    _synthetic_voice signals that only reweight the first few harmonics of
    one spectrum end up with almost all of their difference concentrated
    below ~600 Hz, while ~20 of the 26 mel filters (covering the rest of
    the 80 Hz-8 kHz range) sit at a near-identical FFT-leakage floor in
    both signals - that shared floor dominates the DCT/cosine comparison
    and makes two clearly-different-sounding synthetic voices score ~0.99
    similar in MFCC space regardless of how the harmonics were reweighted,
    which isn't a realistic test of speaker discrimination. Spreading
    formant peaks across the spectrum (mimicking different vocal tract
    resonances) gives each voice its own broadband shape instead."""
    rng = np.random.default_rng(seed)
    n = int(SR * duration_s)
    t = np.arange(n) / SR
    n_harmonics = int(SR / 2 / f0) - 1
    signal = np.zeros(n, dtype=np.float32)
    for k in range(1, n_harmonics + 1):
        freq = f0 * k
        gain = 1.0
        for center_hz, bandwidth_hz, amplitude in formants:
            gain += amplitude * math.exp(-0.5 * ((freq - center_hz) / bandwidth_hz) ** 2)
        signal += (gain / k) * np.sin(2 * np.pi * freq * t + rng.uniform(0, 2 * np.pi))
    signal += 0.02 * rng.standard_normal(n).astype(np.float32)
    return (signal / np.max(np.abs(signal)) * 0.8).astype(np.float32)


class MfccTests(unittest.TestCase):
    def test_output_shape_is_fixed_width(self):
        mfcc = speaker_id._mfcc(_synthetic_voice(150.0, [1.0, 0.5], duration_s=1.0))
        self.assertEqual(mfcc.shape[1], speaker_id.NUM_MFCC)
        self.assertGreater(mfcc.shape[0], 0)

    def test_output_is_finite_for_a_tone(self):
        mfcc = speaker_id._mfcc(_synthetic_voice(150.0, [1.0, 0.5], duration_s=0.5))
        self.assertTrue(np.all(np.isfinite(mfcc)))

    def test_silence_produces_finite_output_not_nan(self):
        mfcc = speaker_id._mfcc(np.zeros(SR, dtype=np.float32))
        self.assertTrue(np.all(np.isfinite(mfcc)))

    def test_short_audio_still_returns_at_least_one_frame(self):
        audio = np.full(50, 0.1, dtype=np.float32)  # well under one frame (400 samples)
        mfcc = speaker_id._mfcc(audio)
        self.assertEqual(mfcc.shape, (1, speaker_id.NUM_MFCC))


class EstimatePitchTests(unittest.TestCase):
    def test_detects_known_frequency_sine_wave(self):
        f0, voiced = speaker_id._estimate_pitch(_synthetic_voice(150.0, [1.0], duration_s=1.0))
        self.assertGreater(voiced.sum(), 0)
        self.assertLess(abs(f0[voiced].mean() - 150.0), 5.0)

    def test_silence_is_all_unvoiced(self):
        _f0, voiced = speaker_id._estimate_pitch(np.zeros(SR, dtype=np.float32))
        self.assertFalse(voiced.any())

    def test_output_arrays_same_length(self):
        f0, voiced = speaker_id._estimate_pitch(_synthetic_voice(150.0, [1.0], duration_s=0.7))
        self.assertEqual(len(f0), len(voiced))

    def test_white_noise_is_finite_no_crash(self):
        rng = np.random.default_rng(0)
        audio = (rng.standard_normal(SR) * 0.3).astype(np.float32)
        f0, _voiced = speaker_id._estimate_pitch(audio)
        self.assertTrue(np.all(np.isfinite(f0)))


class ExtractVoiceprintTests(unittest.TestCase):
    def test_returns_fixed_length_unit_vector(self):
        vp = speaker_id.extract_voiceprint(_synthetic_voice(150.0, [1.0, 0.5]))
        self.assertEqual(vp.shape, (speaker_id.VOICEPRINT_DIM,))
        self.assertTrue(np.all(np.isfinite(vp)))
        self.assertAlmostEqual(float(np.linalg.norm(vp)), 1.0, places=5)

    def test_returns_none_for_silence(self):
        self.assertIsNone(speaker_id.extract_voiceprint(np.zeros(SR, dtype=np.float32)))

    def test_returns_none_for_empty_audio(self):
        self.assertIsNone(speaker_id.extract_voiceprint(np.zeros(0, dtype=np.float32)))

    def test_same_voice_slices_are_highly_self_similar(self):
        vp_a = speaker_id.extract_voiceprint(_synthetic_voice(120.0, [1.0, 0.5, 0.25], seed=1))
        vp_b = speaker_id.extract_voiceprint(_synthetic_voice(120.0, [1.0, 0.5, 0.25], seed=2))
        self.assertGreater(float(vp_a @ vp_b), 0.95)

    def test_distinct_voices_are_much_less_similar_than_same_voice(self):
        vp_a = speaker_id.extract_voiceprint(_synthetic_voice(110.0, [1.0, 0.95, 0.85, 0.7, 0.5], seed=1))
        vp_b = speaker_id.extract_voiceprint(_synthetic_voice(295.0, [1.0, 0.15], seed=2))
        # Threshold loosened from 0.5 to 0.6 (measured ~0.51) after
        # PITCH_WEIGHT dropped 3.0 -> 1.0 (see SpeakerRegistryTests'
        # prosody/timbre tests below) - pitch was previously doing most of
        # the separating work for this pitch-heavy fixture (110Hz vs
        # 295Hz), so a smaller pitch weight legitimately narrows the gap
        # here while still leaving it far below same-voice's 0.95+.
        self.assertLess(float(vp_a @ vp_b), 0.6)


class SpeakerRegistryTests(unittest.TestCase):
    VOICE_A = (120.0, [1.0, 0.5, 0.25])
    VOICE_B = (295.0, [1.0, 0.15])

    def test_first_segment_becomes_speaker_one(self):
        registry = speaker_id.SpeakerRegistry()
        speaker = registry.identify(_synthetic_voice(*self.VOICE_A, seed=1))
        self.assertEqual(speaker, 1)
        self.assertEqual(registry.num_speakers, 1)

    def test_repeated_slices_of_same_voice_get_same_id(self):
        # Regression test for a margin-check bug caught during planning: with
        # exactly one known speaker there's no runner-up to compare against,
        # and an earlier draft computed second_score as NaN in that case -
        # `best_score >= threshold and (best_score - nan) >= margin` is
        # always False, so a second utterance from the *only* known speaker
        # could never re-match, permanently fragmenting a simple 1:1
        # conversation into a new "speaker" every other line.
        registry = speaker_id.SpeakerRegistry()
        first = registry.identify(_synthetic_voice(*self.VOICE_A, duration_s=1.0, seed=1))
        second = registry.identify(_synthetic_voice(*self.VOICE_A, duration_s=0.8, seed=2))
        third = registry.identify(_synthetic_voice(*self.VOICE_A, duration_s=1.2, seed=3))
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        self.assertEqual(registry.num_speakers, 1)

    def test_distinctly_different_voice_gets_new_id(self):
        registry = speaker_id.SpeakerRegistry()
        id_a = registry.identify(_synthetic_voice(*self.VOICE_A, seed=1))
        id_b = registry.identify(_synthetic_voice(*self.VOICE_B, seed=2))
        self.assertNotEqual(id_a, id_b)
        self.assertEqual(registry.num_speakers, 2)

    def test_short_interruption_from_known_voice_still_matches(self):
        registry = speaker_id.SpeakerRegistry()
        registry.identify(_synthetic_voice(*self.VOICE_A, duration_s=1.0, seed=1))
        id_b = registry.identify(_synthetic_voice(*self.VOICE_B, duration_s=1.0, seed=2))
        interruption = registry.identify(_synthetic_voice(*self.VOICE_B, duration_s=0.3, seed=3))
        self.assertEqual(interruption, id_b)
        self.assertEqual(registry.num_speakers, 2)

    def test_short_audio_as_very_first_segment_still_returns_an_id(self):
        registry = speaker_id.SpeakerRegistry()
        speaker = registry.identify(np.zeros(10, dtype=np.float32))
        self.assertEqual(speaker, 1)

    def test_short_audio_falls_back_to_last_speaker(self):
        registry = speaker_id.SpeakerRegistry()
        first = registry.identify(_synthetic_voice(*self.VOICE_A, seed=1))
        fallback = registry.identify(np.zeros(10, dtype=np.float32))
        self.assertEqual(fallback, first)
        self.assertEqual(registry.num_speakers, 1)

    def test_last_match_reports_similarity_score(self):
        registry = speaker_id.SpeakerRegistry()
        registry.identify(_synthetic_voice(*self.VOICE_A, seed=1))
        registry.identify(_synthetic_voice(*self.VOICE_A, seed=2))
        self.assertFalse(registry.last_match.is_new)
        self.assertGreaterEqual(registry.last_match.score, registry.threshold)

    def test_registry_grows_to_ten_distinct_speakers(self):
        # Pitch AND harmonic shape both vary per voice (not a smooth
        # adjacent-speakers-are-nearly-identical interpolation), the way 10
        # different real people's voices would actually differ. Uses an
        # explicit, stricter threshold rather than the module default: this
        # test's job is to prove the registry has no hardcoded speaker cap
        # and correctly separates sufficiently-distinct voices, which is a
        # different concern from DEFAULT_SPEAKER_THRESHOLD's calibration
        # (that default is tuned low for robustness against real reverb/
        # content variation fragmenting one real speaker - see
        # SpeakerRegistryTests.test_near_duplicate_centroids_do_not_runaway
        # - and clean synthetic tones are, if anything, *more* alike after
        # MFCC smoothing than real voices with genuine formant structure,
        # so a threshold this low doesn't cleanly separate all 10 of them).
        voices = [
            (75.0, [1.0, 0.05]),
            (110.0, [1.0, 0.95, 0.85, 0.7, 0.5]),
            (150.0, [1.0, 0.1, 0.03]),
            (185.0, [1.0, 0.9, 0.4]),
            (220.0, [1.0, 0.05, 0.02, 0.01]),
            (260.0, [1.0, 0.9, 0.8, 0.6, 0.4, 0.2]),
            (295.0, [1.0, 0.15]),
            (330.0, [1.0, 0.6, 0.9, 0.3]),
            (365.0, [1.0, 0.08, 0.04]),
            (395.0, [1.0, 0.7, 0.5, 0.9, 0.2]),
        ]
        registry = speaker_id.SpeakerRegistry(threshold=0.90)
        ids = [registry.identify(_synthetic_voice(f0, h, seed=i)) for i, (f0, h) in enumerate(voices)]
        self.assertEqual(registry.num_speakers, 10)
        self.assertEqual(len(set(ids)), 10)

        # A second, independently-seeded sample of each voice should map
        # back to its original id, not grow the registry further.
        ids_repeat = [registry.identify(_synthetic_voice(f0, h, seed=100 + i)) for i, (f0, h) in enumerate(voices)]
        self.assertEqual(ids_repeat, ids)
        self.assertEqual(registry.num_speakers, 10)

    def test_near_duplicate_centroids_do_not_runaway(self):
        # Regression test for a real bug report: one continuous speaker in
        # a reverberant ("hall effect") room kept incrementing to a new
        # "Person N" on every single chunk, without ever stopping. Root
        # cause: an earlier version required the best match to beat the
        # runner-up by a margin, not just clear the threshold. Once *two*
        # registered centroids happened to represent the same real voice
        # (e.g. from one earlier borderline miss), every later segment from
        # that voice scored nearly identical similarity to both - so the
        # margin between best and second-best was ~0, forever failing that
        # check and spawning a brand new speaker on every call. This
        # directly reproduces that starting state (two centroids seeded
        # from the same underlying voice, as could happen after one earlier
        # miss) and asserts the registry stays bounded instead of growing
        # on every subsequent segment from that same voice.
        registry = speaker_id.SpeakerRegistry()
        base_f0, base_harmonics = 130.0, [1.0, 0.6, 0.4, 0.25, 0.15]
        vp1 = speaker_id.extract_voiceprint(_synthetic_voice(base_f0, base_harmonics, seed=1))
        vp2 = speaker_id.extract_voiceprint(
            _synthetic_voice(base_f0 * 1.05, [w * 1.1 for w in base_harmonics], seed=2)
        )
        registry._centroids = [vp1, vp2]
        registry._counts = [1, 1]
        registry._last_speaker_id = 2

        for i in range(3, 20):
            audio = _synthetic_voice(
                base_f0 * (1.0 + 0.02 * (i % 3 - 1)), [w * (1.0 + 0.05 * (i % 2)) for w in base_harmonics], seed=i
            )
            registry.identify(audio)

        self.assertEqual(registry.num_speakers, 2, "registry grew unboundedly instead of staying at 2")

    def test_natural_pitch_drift_does_not_fragment_same_speaker(self):
        # Regression test for the actual reported bug: one continuous
        # speaker's ordinary prosody (a rising pitch when asking a
        # question vs. flatter when explaining) was, on its own, enough to
        # drop same-speaker cosine similarity below the match threshold
        # when PITCH_WEIGHT was 3.0 (pitch ~82% of the voiceprint) - e.g. a
        # 120Hz -> 150Hz (+25%) shift alone scored ~0.82 pre-fix, under the
        # then-0.90 default. PITCH_WEIGHT is now 1.0 (~33% of the
        # voiceprint); the same +25% shift now measures ~0.87 (comfortably
        # above the 0.75 default).
        registry = speaker_id.SpeakerRegistry()
        low_pitch = registry.identify(_synthetic_voice(120.0, [1.0, 0.5, 0.25], seed=1))
        higher_pitch = registry.identify(_synthetic_voice(150.0, [1.0, 0.5, 0.25], seed=2))
        self.assertEqual(low_pitch, higher_pitch)
        self.assertEqual(registry.num_speakers, 1)

    def test_different_speaker_at_same_pitch_gets_new_id(self):
        # Regression test for a real discriminability bug found alongside
        # the one above: because L2-normalizing a block only rescales it
        # (doesn't reshape it), the MFCC-mean block's naturally dominant
        # low-order coefficient made two *different* synthetic voices at
        # the same pitch score ~0.99 similar pre-fix (see _formant_voice's
        # docstring for why a formant-based fixture is what actually
        # exercises this, unlike plain harmonic-reweighting). Sinusoidal
        # cepstral liftering (applied inside _mfcc) rebalances the
        # coefficients so this case now separates (~0.71, under the 0.75
        # default) instead of merging into one "Person N".
        registry = speaker_id.SpeakerRegistry()
        speaker_a = registry.identify(_formant_voice(120.0, [(700, 80, 3), (1220, 90, 2)], seed=1))
        speaker_b = registry.identify(_formant_voice(120.0, [(400, 60, 3), (2200, 150, 2)], seed=2))
        self.assertNotEqual(speaker_a, speaker_b)
        self.assertEqual(registry.num_speakers, 2)

        # And a second slice of speaker A's own voice (same pitch, same
        # formants) should still resolve back to A, not fragment further -
        # the fix separates genuinely different voices without making the
        # matcher trigger-happy about registering new ones.
        speaker_a_again = registry.identify(_formant_voice(120.0, [(700, 80, 3), (1220, 90, 2)], seed=3))
        self.assertEqual(speaker_a_again, speaker_a)
        self.assertEqual(registry.num_speakers, 2)

    def test_quiet_unvoiced_segment_matches_speaker_by_timbre(self):
        # Regression test for a related bug found while investigating the
        # two above: extract_voiceprint zero-pads a segment's pitch block
        # when there aren't enough voiced frames to estimate pitch (too
        # short/quiet/whispered). Comparing that zero pitch block against a
        # centroid that *does* have real pitch content caps the best
        # possible full-vector cosine similarity at ~0.43 - below any
        # workable threshold - regardless of how well the timbre actually
        # matches, so a run of quiet/whispered segments could never
        # re-match their own speaker and instead minted a new "Person N"
        # every time. SpeakerRegistry.identify now falls back to a
        # timbre-only (masked) cosine comparison when the incoming
        # voiceprint has no pitch content, which isn't subject to that
        # ceiling.
        registry = speaker_id.SpeakerRegistry()
        speaker_a = registry.identify(_synthetic_voice(120.0, [1.0, 0.5, 0.25], seed=1))
        speaker_b = registry.identify(_synthetic_voice(295.0, [1.0, 0.15], seed=2))

        whispered = _whispered_voice(120.0, [1.0, 0.5, 0.25], seed=20)
        vp = speaker_id.extract_voiceprint(whispered)
        self.assertTrue(np.all(vp[speaker_id._TIMBRE_DIM :] == 0.0), "fixture must produce a zero pitch block")

        matched = registry.identify(whispered)
        self.assertEqual(matched, speaker_a)
        self.assertNotEqual(matched, speaker_b)
        self.assertFalse(registry.last_match.is_new)
        self.assertEqual(registry.num_speakers, 2)

    def test_moderately_similar_voices_are_kept_separate_at_the_raised_threshold(self):
        # Regression test for a real-world report: at the old 0.75 default,
        # too many genuinely different speakers were being merged into one
        # "Person N" instead of being recognized as their own. These two
        # formant-shaped synthetic voices (same 120Hz pitch, moderately -
        # not wildly - different timbre) score ~0.80: above the old 0.75
        # default (would have merged), below the raised 0.84 default
        # (correctly kept separate).
        base = [(700, 130, 3.0), (2200, 200, 2.0)]
        similar = [(950, 130, 1.8), (2700, 200, 1.3)]

        lenient_registry = speaker_id.SpeakerRegistry(threshold=0.75)
        old_a = lenient_registry.identify(_formant_voice(120.0, base, seed=1))
        old_b = lenient_registry.identify(_formant_voice(120.0, similar, seed=2))
        self.assertEqual(old_a, old_b, "sanity check: this pair must score above the old 0.75 default")

        registry = speaker_id.SpeakerRegistry()  # default threshold, i.e. 0.84
        speaker_a = registry.identify(_formant_voice(120.0, base, seed=1))
        speaker_b = registry.identify(_formant_voice(120.0, similar, seed=2))
        self.assertNotEqual(speaker_a, speaker_b)
        self.assertEqual(registry.num_speakers, 2)


if __name__ == "__main__":
    unittest.main()
