"""End-to-end tests for run_macos.sh's saved-settings (.env) flow.

The real launcher and the real settings.py run in a scratch copy of the project
next to a stand-in `transcribe.py` that just reports the argv and environment it
was started with, so no audio, model or network is involved. Answers are fed
through stdin the way a person would type them.

run_windows.ps1 mirrors this flow but is not covered here (no PowerShell in CI).
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

FAKE_TRANSCRIBE = '''\
import json, os, sys
if "--list-devices" in sys.argv:
    print("  [1] Fake device")
    sys.exit(0)
print("ARGS=" + json.dumps(sys.argv[1:]))
print("AUTH=" + os.environ.get("TRANSCRIBER_AUTH_HEADER", "<unset>"))
'''

# The store (upload) URL and the client name/topic are never persisted (see
# StoreUrlIsNeverASettingTests in test_settings.py for the topic - it's simply
# never routed through settings.py at all) - both are asked fresh every run.

EMAIL = "me@example.com"
STORE_URL = "https://store.example.com/up"
KEY = "Authorization: Bearer secrettoken1234"


@unittest.skipIf(sys.platform == "win32", "exercises the bash launcher")
@unittest.skipUnless(shutil.which("bash"), "bash not available")
class LauncherSettingsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        shutil.copy(PROJECT / "run_macos.sh", self.dir / "run_macos.sh")
        shutil.copy(PROJECT / "settings.py", self.dir / "settings.py")
        (self.dir / "transcribe.py").write_text(FAKE_TRANSCRIBE, encoding="utf-8")
        py = self.dir / ".venv" / "bin" / "python"
        py.parent.mkdir(parents=True)
        py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        py.chmod(py.stat().st_mode | stat.S_IXUSR)
        self.env_file = self.dir / ".env"

    def launch(self, answers: list[str]) -> "tuple[dict, str, subprocess.CompletedProcess]":
        env = {k: v for k, v in os.environ.items() if k != "TRANSCRIBER_AUTH_HEADER"}
        proc = subprocess.run(
            ["bash", str(self.dir / "run_macos.sh")],
            input="".join(a + "\n" for a in answers),
            capture_output=True,
            text=True,
            timeout=60,
            cwd=self.dir,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, msg=f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        report: dict = {}
        launcher_out = proc.stdout.split("ARGS=")[0]  # the stand-in app echoes the key back; only the launcher's own output matters
        for line in proc.stdout.splitlines():
            if line.startswith("ARGS="):
                report["args"] = json.loads(line[len("ARGS="):])
            elif line.startswith("AUTH="):
                report["auth"] = line[len("AUTH="):]
        self.assertIn("args", report, msg=f"app never started:\n{proc.stdout}\n{proc.stderr}")
        return report, launcher_out, proc

    def saved(self) -> dict:
        import settings

        return settings.load(self.env_file)

    # answers, in the order the launcher asks: email, model number, topic, device, mode, upload?, store URL, key, filename
    FIRST_RUN = [EMAIL, "5", "Acme kickoff", "", "2", "y", STORE_URL, KEY, ""]

    def test_first_run_asks_for_everything_and_saves_all_but_the_topic_and_store_url(self):
        report, out, _ = self.launch(self.FIRST_RUN)

        self.assertEqual(
            report["args"],
            ["--model", "base.en-q5_1", f"--email={EMAIL}", "--topic=Acme kickoff",
             "--store-url", STORE_URL, "--output", "transcripts/transcript.log"],
        )
        self.assertEqual(report["auth"], KEY)
        self.assertEqual(
            self.saved(),
            {
                "TRANSCRIBER_EMAIL": EMAIL,
                "TRANSCRIBER_MODEL": "base.en-q5_1",
                "TRANSCRIBER_AUTH_HEADER": KEY,
            },
        )
        self.assertNotIn("Acme", self.env_file.read_text(encoding="utf-8"))
        self.assertNotIn(STORE_URL, self.env_file.read_text(encoding="utf-8"))

    def test_env_file_is_owner_only(self):
        self.launch(self.FIRST_RUN)
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)

    def test_api_key_input_is_visible_not_hidden(self):
        # Piped stdin never echoes keystrokes either way, so this checks the
        # launcher itself doesn't hide the prompt (no `read -s` / SecureString)
        # rather than observing terminal echo, which a subprocess can't see.
        macos_src = (PROJECT / "run_macos.sh").read_text(encoding="utf-8")
        api_key_lines = [line for line in macos_src.splitlines() if "API key header" in line and "read" in line]
        self.assertTrue(api_key_lines, "no API key prompt line found")
        for line in api_key_lines:
            self.assertNotIn(" -s ", line)
            self.assertNotRegex(line, r"read\s+.*-s\b")
        windows_src = (PROJECT / "run_windows.ps1").read_text(encoding="utf-8")
        self.assertNotIn("AsSecureString", windows_src)

    def test_api_key_is_never_on_the_command_line_or_printed_back_by_the_launcher(self):
        report, out, proc = self.launch(self.FIRST_RUN)
        self.assertNotIn("secrettoken1234", " ".join(report["args"]))
        self.assertNotIn("secrettoken1234", out)
        self.assertNotIn("secrettoken1234", proc.stderr)

    def test_second_run_reuses_saved_settings_and_only_asks_for_topic_and_store_url(self):
        self.launch(self.FIRST_RUN)
        before = self.env_file.read_bytes()

        # "n" to "change any?", then topic, device, mode, upload?, store URL (always
        # asked - never saved), filename
        report, out, _ = self.launch(["n", "Beta review", "", "2", "y", STORE_URL, ""])

        self.assertIn("--topic=Beta review", report["args"])
        self.assertIn(f"--email={EMAIL}", report["args"])
        self.assertIn("base.en-q5_1", report["args"])
        self.assertIn(STORE_URL, report["args"])
        self.assertEqual(report["auth"], KEY)
        self.assertEqual(self.env_file.read_bytes(), before)
        self.assertIn("Saved settings", out)
        self.assertNotIn("secrettoken1234", out)
        self.assertIn("Authorization: ****1234", out)

    def test_topic_is_asked_every_session_and_never_persisted(self):
        self.launch(self.FIRST_RUN)
        report, _, _ = self.launch(["n", "Second topic", "", "2", "n", ""])
        self.assertIn("--topic=Second topic", report["args"])
        self.assertNotIn("Second topic", self.env_file.read_text(encoding="utf-8"))
        self.assertNotIn("Acme", self.env_file.read_text(encoding="utf-8"))

    def test_upload_stays_opt_in(self):
        self.launch(self.FIRST_RUN)
        report, _, _ = self.launch(["n", "Topic", "", "2", "n", ""])
        self.assertNotIn("--store-url", report["args"])
        self.assertEqual(report["auth"], "<unset>")

    def test_changing_saved_settings_prompts_with_the_saved_value_as_default(self):
        self.launch(self.FIRST_RUN)

        # y = change; email: keep; model: keep; topic; device; mode; upload; new store URL; remove the key; filename
        report, _, _ = self.launch(["y", "", "", "Topic", "", "2", "y", "https://other.example.com/x", "none", ""])

        self.assertIn("https://other.example.com/x", report["args"])
        self.assertIn("base.en-q5_1", report["args"])
        self.assertEqual(report["auth"], "<unset>")
        saved = self.saved()
        self.assertNotIn("TRANSCRIBER_STORE_URL", saved)
        self.assertNotIn("other.example.com", self.env_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["TRANSCRIBER_AUTH_HEADER"], "")
        self.assertEqual(saved["TRANSCRIBER_EMAIL"], EMAIL)

    def test_a_removed_api_key_is_not_asked_for_again(self):
        self.launch(self.FIRST_RUN)
        self.launch(["y", "", "", "Topic", "", "2", "y", STORE_URL, "none", ""])
        # nothing left to answer for the key: topic, device, mode, upload?, store url, filename only
        report, out, _ = self.launch(["n", "Topic", "", "2", "y", STORE_URL, ""])
        self.assertEqual(report["auth"], "<unset>")
        self.assertIn("No API key saved", out)

    def test_asks_only_for_what_is_missing(self):
        self.env_file.write_text(f"TRANSCRIBER_EMAIL={EMAIL}\nTRANSCRIBER_MODEL=tiny.en\n", encoding="utf-8")

        # "n" to change; topic, device, mode=log, upload y, store URL (missing), key (missing), filename
        report, _, _ = self.launch(["n", "Topic", "", "2", "y", STORE_URL, "", ""])

        self.assertIn("tiny.en", report["args"])
        self.assertIn(STORE_URL, report["args"])
        saved = self.saved()
        self.assertNotIn("TRANSCRIBER_STORE_URL", saved)
        self.assertEqual(saved["TRANSCRIBER_AUTH_HEADER"], "")  # blank answer recorded as "needs no key"

    def test_streaming_mode_saves_the_stream_url_separately(self):
        report, _, _ = self.launch([EMAIL, "1", "Topic", "", "1", "http://localhost:9000/ingest", ""])
        self.assertEqual(report["args"][-3:], ["--streaming", "--url", "http://localhost:9000/ingest"])
        saved = self.saved()
        self.assertEqual(saved["TRANSCRIBER_URL"], "http://localhost:9000/ingest")
        self.assertNotIn("TRANSCRIBER_STORE_URL", saved)
        self.assertEqual(saved["TRANSCRIBER_MODEL"], "tiny.en")

    def test_invalid_answers_are_rejected_and_reasked(self):
        # bad email, then good; bad model number, then good; store URL (asked once,
        # not format-checked by the launcher - transcribe.py itself validates it at
        # startup); bad key, then good
        answers = ["not-an-email", EMAIL, "99", "5", "Topic", "", "2", "y", STORE_URL,
                   "no-colon-here", KEY, ""]
        report, out, proc = self.launch(answers)
        self.assertIn(f"--email={EMAIL}", report["args"])
        self.assertIn(STORE_URL, report["args"])
        self.assertIn("Invalid choice", out)
        self.assertIn("Email must look like", proc.stderr)
        self.assertIn("API key header must look like", proc.stderr)
        self.assertEqual(report["auth"], KEY)

    def test_empty_topic_is_reasked(self):
        report, out, _ = self.launch([EMAIL, "5", "   ", "Real topic", "", "2", "n", ""])
        self.assertIn("--topic=Real topic", report["args"])
        self.assertIn("cannot be empty", out)

    def test_hand_edited_invalid_saved_value_is_asked_again(self):
        self.env_file.write_text("TRANSCRIBER_EMAIL=oops\nTRANSCRIBER_MODEL=tiny.en\n", encoding="utf-8")
        report, _, proc = self.launch(["n", EMAIL, "Topic", "", "2", "n", ""])
        self.assertIn(f"--email={EMAIL}", report["args"])
        self.assertIn("not usable", proc.stderr)
        self.assertEqual(self.saved()["TRANSCRIBER_EMAIL"], EMAIL)

    def test_other_lines_in_the_env_file_survive(self):
        self.env_file.write_text("# my notes\nOTHER=1\n", encoding="utf-8")
        self.launch(self.FIRST_RUN[:1] + ["5", "Topic", "", "2", "n", ""])
        text = self.env_file.read_text(encoding="utf-8")
        self.assertIn("# my notes\nOTHER=1\n", text)
        self.assertIn(f"TRANSCRIBER_EMAIL={EMAIL}", text)


if __name__ == "__main__":
    unittest.main()
