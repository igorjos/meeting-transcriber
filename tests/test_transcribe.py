"""Tests for the pure helpers in transcribe.py - no audio hardware needed."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import transcribe


class FakeSegment:
    def __init__(self, text: str):
        self.text = text


class CleanTextTests(unittest.TestCase):
    def test_keeps_real_speech(self):
        segments = [FakeSegment(" Hello there. ")]
        self.assertEqual(transcribe._clean_text(segments), "Hello there.")

    def test_joins_multiple_segments_with_spaces(self):
        segments = [FakeSegment("Hello"), FakeSegment("world.")]
        self.assertEqual(transcribe._clean_text(segments), "Hello world.")

    def test_drops_known_filler_tokens(self):
        for token in ["[BLANK_AUDIO]", "[SILENCE]", "(silence)", "[NOISE]", "[MUSIC]", "[APPLAUSE]"]:
            self.assertEqual(transcribe._clean_text([FakeSegment(token)]), "", msg=token)

    def test_drops_filler_variants_exact_token_list_would_miss(self):
        # Regression coverage: an exact-match token set (even case-insensitive)
        # misses whitespace/wording variants that whisper.cpp also emits.
        for token in ["[ Silence ]", "(speaking in foreign language)", "[ Music ]", "[laughs]", "[SILENCE]"]:
            self.assertEqual(transcribe._clean_text([FakeSegment(token)]), "", msg=token)

    def test_drops_empty_and_whitespace_only_segments(self):
        self.assertEqual(transcribe._clean_text([FakeSegment("   ")]), "")
        self.assertEqual(transcribe._clean_text([FakeSegment("")]), "")

    def test_keeps_real_speech_containing_parens(self):
        segments = [FakeSegment("The weather (allegedly) is nice.")]
        self.assertEqual(transcribe._clean_text(segments), "The weather (allegedly) is nice.")

    def test_keeps_speech_that_merely_starts_with_a_bracket(self):
        segments = [FakeSegment("[Music] then someone started talking")]
        self.assertEqual(transcribe._clean_text(segments), "[Music] then someone started talking")


class ValidateUrlTests(unittest.TestCase):
    def test_none_is_valid(self):
        self.assertIsNone(transcribe._validate_url(None))

    def test_empty_string_is_valid(self):
        self.assertIsNone(transcribe._validate_url(""))

    def test_accepts_http(self):
        self.assertIsNone(transcribe._validate_url("http://localhost:8000/ingest"))

    def test_accepts_https(self):
        self.assertIsNone(transcribe._validate_url("https://example.com/ingest"))

    def test_rejects_bare_host_port_path(self):
        self.assertIsNotNone(transcribe._validate_url("localhost:8000/ingest"))

    def test_rejects_bare_hostname(self):
        self.assertIsNotNone(transcribe._validate_url("my-server.local/ingest"))

    def test_rejects_unsupported_scheme(self):
        self.assertIsNotNone(transcribe._validate_url("ftp://example.com/ingest"))


if __name__ == "__main__":
    unittest.main()
