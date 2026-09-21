"""Algorithmic speaker identification (no ML model).

Consumes one SpeechSegment at a time (as produced by vad.py) and assigns it
a speaker id by extracting a compact "voiceprint" - MFCC (timbre) and pitch
statistics computed with plain signal-processing math, no trained embedding
network - and matching it by cosine similarity against a small in-memory
registry of previously seen voiceprints, growing the registry on demand so
it scales to however many people actually speak instead of a fixed count.

Deliberately not a model: silero-vad's torch/torchaudio dependency is
already the heaviest thing this project installs (see requirements.txt) for
just a small VAD model, so a second, likely-larger neural speaker-embedding
model wasn't worth adding here. Feature extraction below is pure numpy - no
scipy/librosa either (neither is installed in this project).

Runs synchronously on the caller's thread (called from transcribe.py's
_process_segment) - a handful of small FFTs per segment is trivial next to
a whisper.cpp inference call, so there's no need for the dedicated
producer/consumer thread pattern capture.py/vad.py use for the (much
heavier, continuous) audio-capture and VAD stages.
"""
from __future__ import annotations

import argparse
import functools
import math
import queue
import sys
import time
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS, HOP_MS = 25.0, 10.0
FRAME_LEN = int(SAMPLE_RATE * FRAME_MS / 1000)  # 400
HOP_LEN = int(SAMPLE_RATE * HOP_MS / 1000)  # 160
_HAMMING_WINDOW = np.hamming(FRAME_LEN).astype(np.float32)

NUM_MEL_FILTERS = 26
NUM_MFCC = 13

# Sinusoidal cepstral liftering (standard MFCC post-processing; same formula
# used by HTK/Kaldi/python_speech_features): coefficient 1 (broad spectral
# tilt) naturally carries far more raw energy than the higher coefficients
# (finer spectral detail, closer to individual formant structure) - measured
# on this file's own output, coefficient 1 alone was ~59% of |mfcc_mean|^2.
# Since L2-normalizing a block only rescales it (doesn't reshape it), that
# imbalance survives normalization and dominates cosine similarity, making
# two voices with the same broad spectral tilt but different formants (i.e.
# genuinely different speakers) look nearly identical by angle. Liftering
# rebalances the coefficients before normalization. Verified against a
# two-formant synthetic-voice fixture (see tests/test_speaker_id.py): mean-
# block cosine similarity between two different synthetic "speakers" at the
# same pitch dropped from 0.84 to 0.13, while same-speaker self-similarity
# stayed at 1.0.
_LIFTER_L = 22
_MFCC_LIFTER = (1.0 + (_LIFTER_L / 2.0) * np.sin(np.pi * np.arange(1, NUM_MFCC + 1) / _LIFTER_L)).astype(np.float32)

PITCH_MIN_HZ, PITCH_MAX_HZ = 70.0, 400.0  # human-voice fundamental range
VOICED_THRESHOLD = 0.30  # min normalized-autocorrelation peak to call a frame voiced
MIN_VOICED_FRAMES = 3  # fewer voiced frames in a segment -> skip pitch stats
# Pitch mean is encoded as a small soft-binned "bump" (a Gaussian centered
# on the estimated mean, sampled at PITCH_NUM_BINS reference frequencies)
# rather than as a raw [mean, std] pair. A raw 2-D [mean, std] vector was
# tried first and doesn't work: cosine similarity is angle-only, and with
# std near 0 (a steady voice), [mean, std] points in nearly the same
# direction for *any* positive mean - so two very differently-pitched
# voices ended up looking nearly identical once compared by angle, no
# matter how much that block was weighted. Soft-binning turns "where is the
# pitch" into "which direction does the bump point," which cosine
# similarity can actually discriminate.
PITCH_NUM_BINS = 8
PITCH_BIN_SIGMA_HZ = 35.0  # bump width - roughly typical within-speaker pitch variation
_PITCH_BINS = np.linspace(PITCH_MIN_HZ, PITCH_MAX_HZ, PITCH_NUM_BINS)
# MFCC blocks are always unit-norm; the pitch bump's raw norm is usually
# well under 1 (a Gaussian sampled at 8 points), so it needs a boost to
# meaningfully compete for weight in the concatenated, globally
# renormalized voiceprint - empirically checked against synthetic voices
# (see tests/test_speaker_id.py) rather than derived analytically.
#
# Was 3.0 (pitch ~82% of the voiceprint's squared norm) until a real bug
# report: one continuous speaker's ordinary prosody (pitch rising ~25-40%
# for a question vs. flatter when explaining) was enough on its own to drop
# same-speaker cosine similarity below the match threshold, since pitch so
# thoroughly dominated the vector. 1.0 means pitch/mfcc-mean/mfcc-std each
# contribute roughly a third of the squared norm - pitch remains a real,
# useful signal (genuinely different speakers often differ in fundamental
# frequency) without being able to single-handedly out-vote timbre. See
# CHANGELOG.md for the measured before/after cosine similarities.
PITCH_WEIGHT = 1.0

VOICEPRINT_DIM = 2 * NUM_MFCC + PITCH_NUM_BINS  # MFCC mean + MFCC std + pitch bump
_TIMBRE_DIM = 2 * NUM_MFCC  # mean+std block length; used to slice out pitch for masked comparisons

# Calibrated against synthetic speech with simulated room reverb (a few
# delayed/attenuated copies summed in) plus utterance-to-utterance
# pitch/timbre jitter standing in for natural prosody and phonetic-content
# variation - clean repeated tones (no reverb, no content variation) score
# 0.99+ self-similarity, which is unrealistically optimistic; under
# simulated reverb, genuine same-speaker similarity ranged as low as ~0.6-0.8
# while a clearly different voice through the same degraded pipeline still
# scored ~0.15 - see tests/test_speaker_id.py and CHANGELOG.md for the
# reproduction. Raised from 0.75 to 0.84 after real-world use: at 0.75,
# distinct speakers were too often matching an existing centroid instead of
# being recognized as their own "Person N" - see CHANGELOG.md. Still just a
# starting point - tune with the CLI below.
DEFAULT_SPEAKER_THRESHOLD = 0.84

# Separate, lower bar for the timbre-only ("masked") cosine comparison used
# when a segment has no reliable pitch (see identify() below) - that
# comparison has no pitch dims to help confirm a match, so even a correct
# same-speaker match scores meaningfully lower than a full-vector one:
# measured ~0.78-0.82 for genuinely pitch-less (quiet/whispered/very short)
# segments of the same synthetic speaker, comfortably under
# DEFAULT_SPEAKER_THRESHOLD but still well clear of a different speaker.
# Kept at the main threshold's previous, separately-calibrated value.
MASKED_MATCH_THRESHOLD = 0.75
MAX_CENTROID_WEIGHT = 50  # caps how "frozen" a centroid can get


def _frame_signal(audio: np.ndarray, frame_len: int = FRAME_LEN, hop_len: int = HOP_LEN) -> np.ndarray:
    """(num_frames, frame_len); zero-pads short input so it still returns at
    least one frame instead of an empty array."""
    audio = np.asarray(audio, dtype=np.float32)
    if len(audio) < frame_len:
        audio = np.pad(audio, (0, frame_len - len(audio)))
    n_frames = 1 + (len(audio) - frame_len) // hop_len
    idx = np.arange(frame_len)[None, :] + hop_len * np.arange(n_frames)[:, None]
    return audio[idx]


def _hz_to_mel(hz: np.ndarray) -> np.ndarray:
    return 2595.0 * np.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel: np.ndarray) -> np.ndarray:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


@functools.lru_cache(maxsize=None)
def _mel_filterbank(num_filters: int, n_fft: int, sample_rate: int, fmin: float = 80.0) -> np.ndarray:
    """(num_filters, n_fft//2+1) triangular mel filterbank. Cached - it's a
    pure function of fixed constants, recomputing per segment is wasted work."""
    fmax = sample_rate / 2.0
    mel_points = np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), num_filters + 2)
    hz_points = _mel_to_hz(mel_points)
    bin_freqs = np.linspace(0.0, fmax, n_fft // 2 + 1)

    filterbank = np.zeros((num_filters, len(bin_freqs)), dtype=np.float32)
    for i in range(num_filters):
        left, center, right = hz_points[i], hz_points[i + 1], hz_points[i + 2]
        rising = (bin_freqs - left) / (center - left)
        falling = (right - bin_freqs) / (right - center)
        filterbank[i] = np.clip(np.minimum(rising, falling), 0, None)
    return filterbank


@functools.lru_cache(maxsize=None)
def _dct_basis(num_coeffs: int, num_filters: int) -> np.ndarray:
    """(num_coeffs, num_filters) DCT-II basis, coefficients k=1..num_coeffs
    (skips k=0 / overall log-energy - a volume proxy, not speaker identity)."""
    n = np.arange(num_filters)
    k = np.arange(1, num_coeffs + 1)[:, None]
    basis = np.cos(np.pi / num_filters * (n + 0.5) * k)
    return (basis * math.sqrt(2.0 / num_filters)).astype(np.float32)


def _mfcc(audio: np.ndarray) -> np.ndarray:
    """(num_frames, NUM_MFCC), sinusoidally liftered (see _MFCC_LIFTER)."""
    frames = _frame_signal(audio)
    spectrum = np.fft.rfft(frames * _HAMMING_WINDOW, n=FRAME_LEN, axis=1)
    power = (np.abs(spectrum) ** 2) / FRAME_LEN

    fbank = _mel_filterbank(NUM_MEL_FILTERS, FRAME_LEN, SAMPLE_RATE)
    energies = power @ fbank.T
    log_energies = np.log(np.maximum(energies, 1e-10))

    dct_basis = _dct_basis(NUM_MFCC, NUM_MEL_FILTERS)
    return (log_energies @ dct_basis.T) * _MFCC_LIFTER


def _autocorrelate_frames(frames: np.ndarray) -> np.ndarray:
    """Full (non-circular) autocorrelation of every frame at once, via a
    zero-padded batched FFT (Wiener-Khinchin) - no per-frame Python loop."""
    n_fft = 2 * frames.shape[1]
    spectrum = np.fft.rfft(frames, n=n_fft, axis=1)
    autocorr = np.fft.irfft(spectrum * np.conj(spectrum), n=n_fft, axis=1)
    return autocorr[:, : frames.shape[1]].real


def _estimate_pitch(audio: np.ndarray) -> "tuple[np.ndarray, np.ndarray]":
    """(f0_hz, voiced_mask), one entry per frame."""
    frames = _frame_signal(audio)
    autocorr = _autocorrelate_frames(frames)
    zero_lag = np.maximum(autocorr[:, 0], 1e-10)

    min_lag = int(SAMPLE_RATE / PITCH_MAX_HZ)
    max_lag = min(int(SAMPLE_RATE / PITCH_MIN_HZ), autocorr.shape[1] - 1)
    search = autocorr[:, min_lag : max_lag + 1]

    best_lag = np.argmax(search, axis=1) + min_lag
    best_val = np.max(search, axis=1)
    normalized = best_val / zero_lag

    voiced = normalized >= VOICED_THRESHOLD
    f0 = np.where(voiced, SAMPLE_RATE / np.maximum(best_lag, 1), 0.0)
    return f0.astype(np.float32), voiced


def _l2_normalize(vec: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    norm = np.linalg.norm(vec)
    return vec if norm <= eps else vec / norm


def _pitch_rbf(mean_hz: float) -> np.ndarray:
    """(PITCH_NUM_BINS,) Gaussian "bump" centered at mean_hz, sampled at
    fixed reference frequencies - see the PITCH_NUM_BINS comment above for
    why this replaces a raw [mean, std] pair."""
    return np.exp(-0.5 * ((_PITCH_BINS - mean_hz) / PITCH_BIN_SIGMA_HZ) ** 2).astype(np.float32)


def extract_voiceprint(audio: np.ndarray) -> Optional[np.ndarray]:
    """Fixed-length (VOICEPRINT_DIM,) unit-norm feature vector summarizing
    timbre (MFCC mean/std) and pitch (a soft-binned mean) for one segment of
    audio, or None if the audio is genuinely too quiet to say anything about
    (true silence). Deliberately has no minimum-duration gate beyond that -
    a short interruption ("Wait!") is exactly the case worth attributing
    correctly, and a duration floor would just push short segments into the
    same-as-last-speaker fallback in SpeakerRegistry, silently defeating
    that."""
    audio = np.asarray(audio, dtype=np.float32)
    if len(audio) == 0 or np.max(np.abs(audio)) < 1e-6:
        return None

    mfcc = _mfcc(audio)
    mean_block = _l2_normalize(mfcc.mean(axis=0))
    std_block = _l2_normalize(mfcc.std(axis=0))

    f0, voiced = _estimate_pitch(audio)
    voiced_f0 = f0[voiced]
    if len(voiced_f0) >= MIN_VOICED_FRAMES:
        pitch_block = PITCH_WEIGHT * _l2_normalize(_pitch_rbf(float(voiced_f0.mean())))
    else:
        pitch_block = np.zeros(PITCH_NUM_BINS, dtype=np.float32)

    # The two MFCC blocks are L2-normalized independently before
    # concatenation: MFCC and pitch live on very different raw scales, so
    # normalizing only once at the end would let whichever block has the
    # larger raw magnitude dominate cosine similarity. L2-normalizing each
    # 13-D MFCC block preserves its *shape* (relative pattern across
    # coefficients) while discarding overall level - appropriate there,
    # since overall level is a volume proxy, not speaker identity.
    vec = np.concatenate([mean_block, std_block, pitch_block]).astype(np.float32)
    if np.linalg.norm(vec) < 1e-9:
        return None
    return _l2_normalize(vec)


@dataclass
class SpeakerMatch:
    """Diagnostics from the most recent identify() call - not needed by the
    real call site (which just uses the returned int), only by the debug
    CLI below for threshold tuning."""

    speaker_id: int
    score: float  # cosine similarity to the assigned centroid; NaN if unavailable
    second_score: float  # runner-up's similarity; NaN if not applicable
    is_new: bool


class SpeakerRegistry:
    """Grows a list of speaker voiceprint centroids on demand and assigns
    each incoming segment to the best matching one (or registers a new
    speaker) by cosine similarity. No fixed speaker count, no persistence
    across runs - "Person N" numbering is scoped to one session."""

    def __init__(
        self,
        threshold: float = DEFAULT_SPEAKER_THRESHOLD,
        max_centroid_weight: int = MAX_CENTROID_WEIGHT,
    ):
        self.threshold = threshold
        self.max_centroid_weight = max_centroid_weight

        self._centroids: List[np.ndarray] = []
        self._counts: List[int] = []
        self._last_speaker_id: Optional[int] = None
        self.last_match: Optional[SpeakerMatch] = None

    @property
    def num_speakers(self) -> int:
        return len(self._centroids)

    def identify(self, audio: np.ndarray) -> int:
        vec = extract_voiceprint(audio)
        if vec is None:
            # Too quiet to say anything about - reuse whoever spoke last
            # rather than mint a new speaker from noise, or default to 1 if
            # this is (unusually) the very first segment of the session.
            is_new = self._last_speaker_id is None
            speaker_id = self._last_speaker_id if not is_new else 1
            self._last_speaker_id = speaker_id
            self.last_match = SpeakerMatch(speaker_id, float("nan"), float("nan"), is_new)
            return speaker_id

        best_score = second_score = float("nan")
        if self._centroids:
            centroids = np.stack(self._centroids)
            # A pitch block is either a weighted Gaussian bump (never exactly
            # zero) or exactly all-zero (see extract_voiceprint) - so this
            # exact-zero check reliably detects "no reliable pitch for this
            # segment" (too short/unvoiced/quiet).
            if np.any(vec[_TIMBRE_DIM:] != 0.0):
                sims = centroids @ vec  # unit vectors -> dot product == cosine sim
                match_threshold = self.threshold
            else:
                # Comparing the full vector here would compare this
                # segment's all-zero pitch block against centroids that do
                # have real pitch content, capping the best possible cosine
                # similarity at ~0.43 (2 unit blocks vs. 3) regardless of
                # how well the timbre actually matches - in practice this
                # meant a run of quiet/whispered/very short segments could
                # never match their own speaker and instead minted a new
                # "Person N" on every single one. Falling back to a
                # timbre-only (masked) cosine similarity - dropping the
                # pitch dims from both sides - avoids that ceiling. No
                # renormalization is needed: cosine similarity is invariant
                # to scaling either side by a positive constant, so slicing
                # both vectors to the same dims and taking their raw dot
                # product over their real norms is already a correct cosine
                # over just those dims.
                timbre = vec[:_TIMBRE_DIM]
                timbre_norm = np.linalg.norm(timbre)
                centroid_timbre = centroids[:, :_TIMBRE_DIM]
                centroid_norms = np.linalg.norm(centroid_timbre, axis=1)
                denom = np.maximum(timbre_norm * centroid_norms, 1e-9)
                sims = (centroid_timbre @ timbre) / denom
                match_threshold = MASKED_MATCH_THRESHOLD
            best_idx = int(np.argmax(sims))
            best_score = float(sims[best_idx])
            if len(sims) > 1:
                second_score = float(np.partition(sims, -2)[-2])  # diagnostic only, see below

            # Assign to the best match whenever it clears the threshold -
            # regardless of how close the runner-up scored. An earlier
            # version also required best_score to beat the runner-up by a
            # margin, which is a real bug, not just extra caution: once two
            # registered centroids both happen to represent the same real
            # person (e.g. from one earlier borderline miss - a cough, a
            # burst of room echo, whatever), every future segment from that
            # person is "ambiguous" between those two near-duplicates *by
            # construction*, fails a margin check forever, and spawns a
            # brand new speaker on every single segment - an unbounded
            # runaway (reported as "the count never stopped" for one
            # continuous speaker in a reverberant room), not an occasional
            # error. Being unsure *which* known-good match is right is a
            # softer, self-correcting failure - the centroid update below
            # keeps nudging the closer one back into shape - than spawning a
            # new identity, which only compounds.
            if best_score >= match_threshold:
                self._update_centroid(best_idx, vec)
                speaker_id = best_idx + 1
                self._last_speaker_id = speaker_id
                self.last_match = SpeakerMatch(speaker_id, best_score, second_score, False)
                return speaker_id

        self._centroids.append(vec)
        self._counts.append(1)
        speaker_id = len(self._centroids)
        self._last_speaker_id = speaker_id
        self.last_match = SpeakerMatch(speaker_id, best_score, second_score, True)
        return speaker_id

    def _update_centroid(self, idx: int, vec: np.ndarray) -> None:
        # Running mean, weight capped so a chatty speaker's centroid never
        # fully "freezes" - a bad early segment can still be diluted out.
        n = min(self._counts[idx], self.max_centroid_weight)
        self._centroids[idx] = _l2_normalize((self._centroids[idx] * n + vec) / (n + 1))
        self._counts[idx] += 1


# --------------------------------------------------------------------------
# Standalone CLI: run capture + VAD + speaker-id together and print the
# assigned speaker and similarity score for each segment, to tune
# --speaker-threshold against real audio before wiring up transcribe.py.
# --------------------------------------------------------------------------


def _cmd_test(device_index: Optional[int], seconds: float, threshold: float) -> None:
    import capture as capture_mod
    from vad import SpeechSegment, VoiceActivityDetector

    cap_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=512)
    seg_queue: "queue.Queue[SpeechSegment]" = queue.Queue()

    cap = capture_mod.LoopbackCapture(cap_queue, device_index=device_index)
    vad = VoiceActivityDetector(cap_queue, seg_queue)
    registry = SpeakerRegistry(threshold=threshold)

    cap.start()
    vad.start()

    info = cap.device_info
    print(f"Listening on: {info['name']} for {seconds:.1f}s (try a couple of different voices)...")

    segments_found = 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            seg = seg_queue.get(timeout=0.5)
        except queue.Empty:
            cap.raise_if_failed()
            if cap.stopped_by_user:
                break
            continue
        segments_found += 1
        speaker_id = registry.identify(seg.audio)
        match = registry.last_match
        score = "n/a (too quiet)" if math.isnan(match.score) else f"{match.score:.3f}"
        tag = " [NEW]" if match.is_new else ""
        ambiguous = ""
        if not math.isnan(match.second_score) and (match.score - match.second_score) < 0.05:
            ambiguous = f" (close call vs. runner-up {match.second_score:.3f} - registry may need consolidating)"
        print(
            f"  segment {segments_found}: {seg.start_s:.2f}s -> {seg.end_s:.2f}s "
            f"({seg.duration_s:.2f}s) -> Person {speaker_id}{tag} (similarity={score}){ambiguous}"
        )

    vad.stop()
    cap.stop()
    cap.raise_if_failed()

    if segments_found == 0:
        print(
            "\nNo speech segments detected. Either nothing was said/played, or --device is "
            "wrong. Run 'python capture.py --list-devices' to check the device index."
        )
        sys.exit(1)

    print(f"\n{registry.num_speakers} distinct speaker(s) detected across {segments_found} segment(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Algorithmic speaker-id clustering (test/debug utility).")
    parser.add_argument("--list-devices", action="store_true", help="List loopback devices and exit.")
    parser.add_argument("--device", type=int, default=None, help="Loopback device index to use.")
    parser.add_argument("--seconds", type=float, default=30.0, help="Seconds to listen in test mode.")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_SPEAKER_THRESHOLD,
        help=f"Cosine-similarity match threshold, 0-1 (default: {DEFAULT_SPEAKER_THRESHOLD}).",
    )
    args = parser.parse_args()

    if args.list_devices:
        import capture as capture_mod

        for dev in capture_mod.list_loopback_devices():
            print(f"  {dev}")
        return

    try:
        _cmd_test(args.device, args.seconds, args.threshold)
    except Exception as exc:  # NoAudioError, ValueError, etc.
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
