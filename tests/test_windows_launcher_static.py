"""Static checks for run_windows.ps1 / run_windows.cmd.

No PowerShell is available in this environment (see tests/test_launcher.py's
module docstring - the same is true here), so these can't actually execute
either script. What they do check, on the real files:

- run_windows.ps1 has balanced {}/()/[] (a real, string/comment-aware scan,
  not a substring count - see _brace_depth), which would already catch most
  copy-paste slips that broke the file for every user.
- The whole script body is wrapped in one try/catch, and every path out of
  it (success, `throw`, or an uncaught error) reaches a `Read-Host` (an
  "acknowledge" prompt) before `exit` - this is the actual fix for "the app
  is started and closed immediately": something failing before this change
  would close the window before anyone could read why.
- run_windows.cmd (the double-click entry point) unblocks and bypasses the
  execution-policy restriction that blocks a downloaded, unsigned script,
  and its own `pause` is the last line of defense if run_windows.ps1 can't
  even be loaded (e.g. a policy PowerShell itself refuses to bypass, or
  PowerShell missing) - the one failure mode Read-Host inside the .ps1
  can't cover, because the .ps1 never got to run at all.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PS1 = PROJECT / "run_windows.ps1"
CMD = PROJECT / "run_windows.cmd"


def _brace_depth(text: str, open_c: str, close_c: str) -> "tuple[int, bool]":
    """(final depth, ever negative) for `open_c`/`close_c`, ignoring occurrences inside
    '...'/"..." string literals (PowerShell backtick-escapes and '' doubling handled)
    and #-comments - a plain substring count is fooled by e.g. a quoted `'"'`."""
    depth = 0
    went_negative = False
    in_dquote = in_squote = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if in_dquote:
            if c == "`":
                i += 2
                continue
            if c == '"':
                in_dquote = False
            i += 1
            continue
        if in_squote:
            if c == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                in_squote = False
            i += 1
            continue
        if c == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == '"':
            in_dquote = True
        elif c == "'":
            in_squote = True
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            went_negative = went_negative or depth < 0
        i += 1
    return depth, went_negative


class PowerShellBalanceTests(unittest.TestCase):
    def setUp(self):
        self.text = PS1.read_text(encoding="utf-8")

    def test_braces_parens_brackets_are_balanced(self):
        for open_c, close_c in (("{", "}"), ("(", ")"), ("[", "]")):
            with self.subTest(pair=open_c + close_c):
                depth, went_negative = _brace_depth(self.text, open_c, close_c)
                self.assertFalse(went_negative, f"a closing '{close_c}' appears before its opening '{open_c}'")
                self.assertEqual(depth, 0, f"unbalanced {open_c}{close_c}")

    def test_a_stray_quote_does_not_fool_the_scanner(self):
        # the topic-sanitizing line mixes a single-quoted " and a double-quoted ' -
        # regression check for the scanner itself, not the script.
        self.assertIn("""$topic -replace '"', "'\"""", self.text)
        depth, went_negative = _brace_depth("$topic -replace '\"', \"'\"\n{ok}", "{", "}")
        self.assertFalse(went_negative)
        self.assertEqual(depth, 0)


class WindowNeverClosesSilentlyTests(unittest.TestCase):
    def setUp(self):
        self.text = PS1.read_text(encoding="utf-8")

    def test_whole_body_is_wrapped_in_one_try_catch(self):
        self.assertRegex(self.text, r"\ntry \{")
        self.assertRegex(self.text, r"\n\} catch \{")

    def test_every_throw_and_the_original_write_error_exit_pattern_is_inside_the_try(self):
        try_start = self.text.index("\ntry {")
        catch_start = self.text.index("\n} catch {")
        body = self.text[try_start:catch_start]
        # every original hard-stop must still exist, as a `throw` the try/catch
        # can react to - not a bare `exit`, which would skip the catch/pause below.
        for needle in [
            "is not a runnable command or path.",
            "this project requires Python 3.11+. Fix:",
            "Dependency install into $VenvDir failed.",
        ]:
            self.assertIn(needle, body, msg=f"missing hard-stop message: {needle!r}")
        self.assertNotIn("Write-Error", self.text)
        # No `exit` statement (as opposed to e.g. the embedded `sys.exit(...)` inside
        # the "-c" Python one-liners, which is unrelated) may remain inside the try -
        # a bare mid-script `exit` would terminate the script before reaching the
        # catch/pause below it.
        exit_statements = re.findall(r"^\s*exit\b.*$", body, flags=re.MULTILINE)
        self.assertEqual(exit_statements, [], msg=f"a bare exit inside try/catch skips the pause: {exit_statements}")

    def test_ends_with_a_read_host_acknowledgment_then_exit(self):
        tail = self.text.rstrip().splitlines()[-3:]
        self.assertIn("Read-Host", tail[-2])
        self.assertIn("Press Enter", tail[-2])
        self.assertTrue(tail[-1].strip().startswith("exit"))

    def test_venv_creation_failures_are_asserted(self):
        self.assertEqual(self.text.count("Assert-Success"), 3)  # 1 definition + 2 call sites

    def test_no_hidden_or_persisted_secrets_regressed(self):
        # unrelated regressions this file has already been burned by once - cheap to keep checking.
        self.assertNotIn("AsSecureString", self.text)
        self.assertNotIn("TRANSCRIBER_STORE_URL", self.text.replace('"--store-url"', "").replace("--store-url", ""))


class CmdWrapperTests(unittest.TestCase):
    def setUp(self):
        self.text = CMD.read_text(encoding="utf-8")

    def test_targets_the_real_ps1_file(self):
        self.assertIn("run_windows.ps1", self.text)

    def test_bypasses_execution_policy_for_this_run_only(self):
        self.assertIn("-ExecutionPolicy Bypass", self.text)
        self.assertNotIn("Set-ExecutionPolicy", self.text)  # must not change the system-wide policy

    def test_unblocks_a_downloaded_copy_of_the_script(self):
        self.assertIn("Unblock-File", self.text)

    def test_pauses_as_a_last_resort_if_the_ps1_cannot_even_load(self):
        lines = [l.strip() for l in self.text.splitlines() if l.strip()]
        self.assertEqual(lines[-1], "pause")


if __name__ == "__main__":
    unittest.main()
