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


class NativeArgumentNotEmptyTests(unittest.TestCase):
    """Windows PowerShell 5.1 drops an empty-string argument entirely when building a
    native command's command line - `-c ""` reaches py.exe as bare `-c` with nothing
    after it, which Python rejects ("Argument expected for the -c option") regardless
    of whether the probed version is actually installed, silently emptying the `py`
    launcher candidate list every time. Every one-liner used just to probe an
    interpreter must be a non-empty string."""

    def setUp(self):
        self.text = PS1.read_text(encoding="utf-8")

    def test_python_version_probe_does_not_pass_an_empty_c_argument(self):
        call_lines = [l.strip() for l in self.text.splitlines() if l.strip().startswith("& py ") and " -c " in l]
        self.assertEqual(len(call_lines), 1, f"expected exactly one 'py ... -c' probe call, found: {call_lines}")
        self.assertIn('-c "pass"', call_lines[0])
        self.assertNotIn('-c ""', call_lines[0])


class ErrorActionPreferenceQuirkTests(unittest.TestCase):
    """Regression tests for the actual, most-likely root cause of "the app starts and
    closes immediately": under $ErrorActionPreference = "Stop", redirecting a native
    command's stderr (`2>$null`) still promotes each line to an ErrorRecord *before*
    it's discarded, so "Stop" throws right there - bypassing the very next
    `if ($LASTEXITCODE ...)` check entirely, on the first Python version the `py`
    launcher doesn't have installed. Every `2>$null` call must have EAP toggled off
    immediately around it (see run_windows.ps1's own comments for the full story)."""

    def setUp(self):
        self.text = PS1.read_text(encoding="utf-8")

    def test_every_2_null_native_call_has_eap_toggled_off_immediately_before_it(self):
        lines = self.text.splitlines()
        redirected = [i for i, line in enumerate(lines) if "2>$null" in line and line.strip().startswith("&")]
        self.assertGreaterEqual(len(redirected), 4, "expected at least the 4 known 2>$null call sites")
        for i in redirected:
            with self.subTest(line=lines[i].strip()):
                self.assertIn('$ErrorActionPreference = "SilentlyContinue"', lines[i - 1])

    def test_eap_is_restored_to_stop_immediately_after_each_one(self):
        lines = self.text.splitlines()
        redirected = [i for i, line in enumerate(lines) if "2>$null" in line and line.strip().startswith("&")]
        for i in redirected:
            with self.subTest(line=lines[i].strip()):
                self.assertIn('$ErrorActionPreference = "Stop"', lines[i + 1])


class ListDevicesFailureTests(unittest.TestCase):
    """--list-devices crashing (most commonly torch/c10.dll failing to load) must stop
    the script with a clear, specific hint - not get silently ignored and then hit the
    exact same crash again later when actually starting the session."""

    def setUp(self):
        self.text = PS1.read_text(encoding="utf-8")

    def test_list_devices_failure_is_checked_and_throws(self):
        call_at = self.text.index("--list-devices")
        after = self.text[call_at : call_at + 800]
        self.assertIn("$LASTEXITCODE -ne 0", after)
        self.assertIn("throw", after)
        self.assertIn("VC++ Redistributable", after)


class UntestedPythonVersionAdvisoryTests(unittest.TestCase):
    """A user who installs whatever Python is currently newest (as README.md's old
    wording invited) can land on a release this project's dependencies (torch in
    particular) have no ready-built packages for yet - venv creation succeeds, but
    `pip install -r requirements.txt` then fails partway through, confusingly, deep
    in the middle of what's supposed to be an unattended first-run setup. This can't
    warn about a *specific* too-new version (it depends what's out whenever someone
    runs this), so it warns whenever none of the explicitly-probed 3.9-3.13 `py`
    launcher versions were found at all - the fallback to a bare "python"/"python3"
    is exactly the case that silently picks up whatever (possibly too-new) version
    happens to be on PATH."""

    def setUp(self):
        self.text = PS1.read_text(encoding="utf-8")

    def test_warns_when_no_probed_version_was_found(self):
        self.assertIn("$probedVersionFound", self.text)
        self.assertIn("3.9-3.13", self.text)

    def test_the_warning_appears_before_the_interpreter_choice_menu(self):
        warn_at = self.text.index("no Python 3.9-3.13")
        menu_at = self.text.index("Select the Python interpreter")
        self.assertLess(warn_at, menu_at)


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


class ExtractedFromZipGuardTests(unittest.TestCase):
    """Double-clicking run_windows.cmd straight out of Explorer's zip-folder preview (or
    a not-yet-synced SharePoint/OneDrive copy) copies out only that one file, not its
    siblings - run_windows.ps1 then genuinely doesn't exist next to it, and PowerShell's
    own "-File parameter does not exist" error is the only thing the user ever saw. This
    must be caught before invoking powershell at all, with a message that says what to
    do (extract the zip first), not PowerShell's native error text."""

    def setUp(self):
        self.text = CMD.read_text(encoding="utf-8")

    def test_checks_for_the_ps1_before_invoking_powershell(self):
        check_at = self.text.index('if not exist "%~dp0run_windows.ps1"')
        powershell_at = self.text.index("powershell", check_at + 1)
        self.assertLess(check_at, powershell_at)

    def test_guard_explains_the_fix_and_pauses(self):
        check_at = self.text.index('if not exist "%~dp0run_windows.ps1"')
        close_paren = self.text.index("\n)", check_at)
        block = self.text[check_at:close_paren]
        self.assertIn("Extract All", block)
        self.assertIn("pause", block)
        self.assertIn("exit /b 1", block)


if __name__ == "__main__":
    unittest.main()
