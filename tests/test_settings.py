"""Tests for settings.py - the .env store and the validators shared with transcribe.py."""
from __future__ import annotations

import contextlib
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import settings


class ValidateUrlTests(unittest.TestCase):
    def test_none_is_valid(self):
        self.assertIsNone(settings.validate_url(None))

    def test_empty_string_is_valid(self):
        self.assertIsNone(settings.validate_url(""))

    def test_accepts_http(self):
        self.assertIsNone(settings.validate_url("http://localhost:8000/ingest"))

    def test_accepts_https(self):
        self.assertIsNone(settings.validate_url("https://example.com/ingest"))

    def test_rejects_bare_host_port_path(self):
        self.assertIsNotNone(settings.validate_url("localhost:8000/ingest"))

    def test_rejects_bare_hostname(self):
        self.assertIsNotNone(settings.validate_url("my-server.local/ingest"))

    def test_rejects_unsupported_scheme(self):
        self.assertIsNotNone(settings.validate_url("ftp://example.com/ingest"))

    def test_error_message_names_the_given_flag(self):
        # --store-url reuses this helper (see UploadLogFileTests below) and
        # needs its own flag name in the error, not a hardcoded "--url".
        error = settings.validate_url("localhost:9/ingest", "--store-url")
        self.assertIn("--store-url", error)
        self.assertNotIn("--url ", error)


class ParseAuthHeaderTests(unittest.TestCase):
    def test_empty_means_no_header(self):
        self.assertEqual(settings.parse_auth_header(None), {})
        self.assertEqual(settings.parse_auth_header(""), {})

    def test_parses_name_and_value(self):
        self.assertEqual(
            settings.parse_auth_header("Authorization: Bearer abc.def"),
            {"Authorization": "Bearer abc.def"},
        )

    def test_value_may_contain_colons(self):
        self.assertEqual(settings.parse_auth_header("X-Key: a:b:c"), {"X-Key": "a:b:c"})

    def test_rejects_malformed_headers(self):
        for bad in ["NoColonHere", ": value", "Name:", "Name:   ", "Bad Name: v", "Name: a\r\nInjected: 1"]:
            with self.assertRaises(ValueError, msg=bad):
                settings.parse_auth_header(bad)


class ValidateEmailTests(unittest.TestCase):
    def test_empty_is_valid(self):
        self.assertIsNone(settings.validate_email(None))
        self.assertIsNone(settings.validate_email(""))

    def test_accepts_ordinary_addresses(self):
        for good in ["a@b.co", "first.last+tag@sub.example.com", "igor_j@example.io"]:
            self.assertIsNone(settings.validate_email(good), msg=good)

    def test_rejects_malformed_addresses(self):
        for bad in ["plain", "a@b", "a b@c.com", "@x.com", "a@", "a@@b.com", "a@b.com|x", "a@b.com\nX: 1"]:
            self.assertIsNotNone(settings.validate_email(bad), msg=repr(bad))

    def test_error_names_the_given_flag(self):
        self.assertIn("--email", settings.validate_email("nope"))
        self.assertIn("Email", settings.validate_email("nope", "Email"))


class LoadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / ".env"

    def test_missing_file_is_empty(self):
        self.assertEqual(settings.load(self.path), {})

    def test_parses_plain_quoted_export_and_comment_lines(self):
        self.path.write_text(
            "# comment\n\nA=1\nexport B = two words \nC=\"quoted value\"\nD='single'\nnot a setting\n=novalue\n",
            encoding="utf-8",
        )
        self.assertEqual(settings.load(self.path), {"A": "1", "B": "two words", "C": "quoted value", "D": "single", })

    def test_value_keeps_hash_and_equals_signs(self):
        self.path.write_text("URL=https://x.example/p?a=b#frag\nH=Authorization: Bearer abc==\n", encoding="utf-8")
        self.assertEqual(
            settings.load(self.path),
            {"URL": "https://x.example/p?a=b#frag", "H": "Authorization: Bearer abc=="},
        )

    def test_last_duplicate_wins(self):
        self.path.write_text("A=1\nA=2\n", encoding="utf-8")
        self.assertEqual(settings.load(self.path), {"A": "2"})

    def test_handles_crlf_and_bom(self):
        self.path.write_bytes(b"\xef\xbb\xbfA=1\r\nB=2\r\n")
        self.assertEqual(settings.load(self.path), {"A": "1", "B": "2"})


class SaveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / ".env"

    def test_creates_the_file_owner_only(self):
        settings.save(settings.EMAIL_KEY, "me@example.com", self.path)
        self.assertEqual(settings.load(self.path), {settings.EMAIL_KEY: "me@example.com"})
        if sys.platform != "win32":
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions")
    def test_tightens_an_existing_world_readable_file(self):
        self.path.write_text("A=1\n", encoding="utf-8")
        self.path.chmod(0o644)
        settings.save(settings.MODEL_KEY, "tiny.en", self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions")
    def test_a_stale_loose_temp_file_never_holds_the_secret(self):
        stale = self.path.with_name(".env.tmp")
        stale.write_text("stale", encoding="utf-8")
        stale.chmod(0o666)
        settings.save(settings.AUTH_HEADER_KEY, "Authorization: Bearer abc", self.path)
        self.assertFalse(stale.exists())
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_keeps_other_lines_and_updates_in_place(self):
        self.path.write_text("# notes\nOTHER=1\nTRANSCRIBER_MODEL=old\nTAIL=z\n", encoding="utf-8")
        settings.save(settings.MODEL_KEY, "tiny.en", self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "# notes\nOTHER=1\nTRANSCRIBER_MODEL=tiny.en\nTAIL=z\n")

    def test_appends_a_new_key(self):
        self.path.write_text("OTHER=1\n", encoding="utf-8")
        settings.save(settings.MODEL_KEY, "tiny.en", self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "OTHER=1\nTRANSCRIBER_MODEL=tiny.en\n")

    def test_updates_the_effective_duplicate(self):
        self.path.write_text("TRANSCRIBER_MODEL=a\nTRANSCRIBER_MODEL=b\n", encoding="utf-8")
        settings.save(settings.MODEL_KEY, "c", self.path)
        self.assertEqual(settings.load(self.path)[settings.MODEL_KEY], "c")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "TRANSCRIBER_MODEL=a\nTRANSCRIBER_MODEL=c\n")

    def test_round_trips_header_values_with_equals_and_spaces(self):
        value = "Authorization: Bearer abc==def"
        settings.save(settings.AUTH_HEADER_KEY, value, self.path)
        self.assertEqual(settings.load(self.path)[settings.AUTH_HEADER_KEY], value)

    def test_empty_api_key_is_saved_as_an_explicit_none(self):
        settings.save(settings.AUTH_HEADER_KEY, "", self.path)
        self.assertEqual(settings.load(self.path), {settings.AUTH_HEADER_KEY: ""})

    def test_surrounding_quotes_and_whitespace_are_normalised(self):
        settings.save(settings.EMAIL_KEY, '  "me@example.com" ', self.path)
        self.assertEqual(settings.load(self.path)[settings.EMAIL_KEY], "me@example.com")

    def test_rejected_values_leave_the_file_untouched(self):
        self.path.write_text("OTHER=1\n", encoding="utf-8")
        cases = [
            (settings.EMAIL_KEY, "not-an-email"),
            (settings.EMAIL_KEY, ""),
            (settings.MODEL_KEY, ""),
            (settings.URL_KEY, "ftp://x"),
            (settings.AUTH_HEADER_KEY, "no-colon"),
            (settings.MODEL_KEY, "two\nlines"),
            (settings.MODEL_KEY, "MODEL_KEY=injected\nTRANSCRIBER_URL=http://evil"),
            (settings.MODEL_KEY, "caf\u00e9"),
            (settings.MODEL_KEY, "tab\there"),
            (settings.MODEL_KEY, '""wrapped""'),
            ("SOMETHING_ELSE", "x"),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    settings.save(key, value, self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "OTHER=1\n")
        self.assertFalse(self.path.with_name(".env.tmp").exists())


class StoreUrlIsNeverASettingTests(unittest.TestCase):
    """The store (upload) URL is asked fresh every session - see run_macos.sh/run_windows.ps1 -
    and must never be persisted, so settings.py doesn't know it as a setting at all."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / ".env"

    def test_store_url_key_is_not_registered(self):
        self.assertNotIn("TRANSCRIBER_STORE_URL", settings.SETTINGS)

    def test_saving_it_is_rejected(self):
        with self.assertRaises(ValueError):
            settings.save("TRANSCRIBER_STORE_URL", "https://store.example.com/up", self.path)
        self.assertFalse(self.path.exists())

    def test_a_hand_edited_leftover_value_is_never_read_back(self):
        self.path.write_text("TRANSCRIBER_STORE_URL=https://store.example.com/up\nTRANSCRIBER_MODEL=tiny.en\n", encoding="utf-8")
        self.assertNotIn("TRANSCRIBER_STORE_URL", settings.describe(settings.load(self.path))[0])
        self.assertEqual(len(settings.describe(settings.load(self.path))), 1)


class MaskTests(unittest.TestCase):
    def test_plain_settings_are_shown_verbatim(self):
        self.assertEqual(settings.mask(settings.EMAIL_KEY, "me@example.com"), "me@example.com")
        self.assertEqual(settings.mask(settings.URL_KEY, "https://x/y"), "https://x/y")

    def test_long_secret_shows_only_the_last_four_characters(self):
        masked = settings.mask(settings.AUTH_HEADER_KEY, "Authorization: Bearer supersecrettoken1234")
        self.assertEqual(masked, "Authorization: ****1234")

    def test_short_secret_shows_no_characters_at_all(self):
        self.assertEqual(settings.mask(settings.AUTH_HEADER_KEY, "X-Key: abc123"), "X-Key: ****")

    def test_empty_and_malformed_secrets(self):
        self.assertEqual(settings.mask(settings.AUTH_HEADER_KEY, ""), "(none)")
        self.assertEqual(settings.mask(settings.AUTH_HEADER_KEY, "raw-secret-without-colon"), "****")


class CommandLineTests(unittest.TestCase):
    """settings.main() is what the launchers call; ENV_PATH is redirected to a scratch file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / ".env"
        patcher = mock.patch.object(settings, "ENV_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv: str, value: "str | None" = None) -> "tuple[int, str, str]":
        out, err = io.StringIO(), io.StringIO()
        env = {settings.VALUE_ENV: value} if value is not None else {}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = settings.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_show_exits_1_when_nothing_is_saved(self):
        self.assertEqual(self.run_cli("show"), (1, "", ""))
        self.path.write_text("UNRELATED=1\n", encoding="utf-8")
        self.assertEqual(self.run_cli("show")[0], 1)

    def test_show_lists_saved_values_and_masks_the_key(self):
        self.path.write_text(
            "TRANSCRIBER_EMAIL=me@example.com\nTRANSCRIBER_AUTH_HEADER=Authorization: Bearer supersecrettoken1234\n",
            encoding="utf-8",
        )
        code, out, _ = self.run_cli("show")
        self.assertEqual(code, 0)
        self.assertIn("me@example.com", out)
        self.assertIn("Authorization: ****1234", out)
        self.assertNotIn("supersecret", out)
        self.assertNotIn("Model", out)

    def test_get_distinguishes_missing_saved_and_unusable(self):
        self.assertEqual(self.run_cli("get", settings.EMAIL_KEY)[0], 1)
        self.path.write_text("TRANSCRIBER_EMAIL=me@example.com\nTRANSCRIBER_MODEL=\n", encoding="utf-8")
        self.assertEqual(self.run_cli("get", settings.EMAIL_KEY), (0, "me@example.com\n", ""))
        code, out, err = self.run_cli("get", settings.MODEL_KEY)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("not usable", err)

    def test_get_of_an_explicitly_empty_api_key_succeeds_with_empty_output(self):
        self.path.write_text("TRANSCRIBER_AUTH_HEADER=\n", encoding="utf-8")
        self.assertEqual(self.run_cli("get", settings.AUTH_HEADER_KEY), (0, "\n", ""))

    def test_get_of_an_unknown_key_is_an_error(self):
        self.assertEqual(self.run_cli("get", "PATH")[0], 2)

    def test_set_reads_the_value_from_the_environment(self):
        code, out, err = self.run_cli("set", settings.EMAIL_KEY, value="me@example.com")
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(settings.load(self.path), {settings.EMAIL_KEY: "me@example.com"})

    def test_set_with_an_invalid_value_exits_2_with_a_message_and_saves_nothing(self):
        code, _, err = self.run_cli("set", settings.EMAIL_KEY, value="nope")
        self.assertEqual(code, 2)
        self.assertIn("Email must look like", err)
        self.assertFalse(self.path.exists())

    def test_set_without_a_value_is_an_empty_value(self):
        self.assertEqual(self.run_cli("set", settings.EMAIL_KEY)[0], 2)
        self.assertEqual(self.run_cli("set", settings.AUTH_HEADER_KEY)[0], 0)

    def test_bad_usage_exits_2(self):
        for argv in [(), ("frobnicate",), ("get",), ("set",), ("show", "extra")]:
            with self.subTest(argv=argv):
                self.assertEqual(self.run_cli(*argv)[0], 2)


if __name__ == "__main__":
    unittest.main()
