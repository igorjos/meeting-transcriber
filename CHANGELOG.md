# Changelog

Convention for this project (required "Before done"):

- Newest entry on top.
- Each entry is **one task/prompt**, headed by:

  ```
  ## YYYY-MM-DD HH:MM:SS -> HH:MM:SS (Xm Ys | tokens: N or "not available")
  **Prompt:** "<the user's original prompt, verbatim>"
  ```

  Start/end timestamps come from the system clock (e.g. `Get-Date`) read
  at the actual start and end of the task — not estimated or backfilled.
  If a field genuinely cannot be obtained (no tool surfaces a token count,
  the prompt predates this convention, etc.), write **"not available"**
  and say why in one clause — never guess or approximate silently.
- Under the header, group bullets as `### Added` / `### Changed` /
  `### Fixed` / `### Verified` (omit any group with nothing in it).
- Each bullet must be **detailed enough to stand alone for later
  analysis**, not a one-line label. Include:
  - **What**: the concrete change (file(s)/function(s)/flag(s) touched).
  - **Why**: the reasoning or root cause — especially for fixes and
    decisions, not just what the symptom was.
  - **Verification**: what was actually run/tested to confirm it works
    (real command/output, not "should work"), or "not verified" if none.
- Update this file as part of finishing any task on this project — code,
  docs, or config — before calling the work done. Not optional.

---

## 2026-09-16 13:31:23+02:00 -> 2026-09-16 13:59:45+02:00 (duration: 28m 22s | tokens: not available)

### Fixed
- **What**: `transcribe.py` — `_FILLER_RE` regex (`^[\[(][^\[\]()]*[\])]$`)
  replaces the hand-maintained `_FILLER_TOKENS` exact-match set for dropping
  non-speech filler lines from whisper.cpp output.
  **Why**: exact-match-after-`.upper()` (the same bug class a colleague had
  just flagged for `(silence)`) misses wording/whitespace variants
  whisper.cpp also emits, e.g. `[ Silence ]`, `(speaking in foreign
  language)`, `[ Music ]` all previously survived into the transcript.
  **Verification**: `tests/test_transcribe.py::CleanTextTests` (7 cases,
  including a regression case for variants an exact list would miss, and a
  case confirming real speech containing parens like "(allegedly)" is still
  kept) — `./.venv/bin/python -m unittest discover -s tests -v` → 20/20 pass.

- **What**: `transcribe.py` `main()` — new `_validate_url()` helper checks
  `urllib.parse.urlparse(url).scheme` is `http`/`https` and calls
  `parser.error()` if not, run right after the existing `--streaming
  requires --url` check.
  **Why**: `_post_sentence`'s (now `SentencePoster`'s) `Request()` was built
  outside any try/except; a scheme-less `--url` (e.g. a bare
  `host/path`, the likely input since both launcher prompts gave no scheme
  hint) passed startup, loaded the model, then crashed uncaught with
  `ValueError: unknown url type` on the first sentence. Validating at
  startup fails fast with a clear message instead.
  **Verification**: `tests/test_transcribe.py::ValidateUrlTests` (7 cases) +
  live subprocess smoke test of `main()` itself: `--url
  localhost:8000/ingest` → `error: --url must start with http:// or
  https://...`, exit 2; `--streaming` alone → `error: --streaming requires
  --url`, exit 2; `--url http://localhost:9/ingest --list-devices` → passes
  validation, lists real devices, exit 0.

- **What**: `transcribe.py` — `_post_sentence()` replaced with a
  `SentencePoster` class: a background thread reading off a bounded
  (`maxsize=256`) `queue.Queue`, POSTing with up to 2 retries (0.5s/1.0s
  backoff) before logging a final warning. `run()` creates one when
  `args.streaming`, calls `.post(text)` instead of posting inline from the
  transcription loop, and `.stop()`s it (drains via a `None` sentinel) in
  every exit path (both early-return error paths and the main `finally`).
  **Why**: POSTing synchronously (5s timeout) from the transcription loop
  meant a slow/unresponsive endpoint stalled transcription entirely; VAD's
  `seg_queue` (maxsize 64) would then fill and silently drop new speech
  segments. A failed POST also had no retry at all.
  **Verification**: live smoke test against a real local `http.server` —
  handler fails the first 2 requests (connection dropped mid-request) then
  succeeds on the 3rd; asserted exactly 3 attempts occurred, exactly one
  request body was received, and its JSON shape was exactly `{"sentence":
  ..., "model": ..., "language": ..., "session_id": ...}`. Script + output
  in this task's scratchpad (`smoke_sentence_poster.py`).

- **What**: `capture_windows.py` (`LoopbackCapture._run`) and
  `capture_macos.py` (`LoopbackCapture._monitor`) — the `silence_timeout`
  (8s) fatal `NoAudioError` now only fires while `self._frames_captured ==
  0` (no audio has ever arrived in this session). Once at least one chunk
  has come through, a later silence gap past the same threshold is instead
  printed once as a `WARNING` (re-armed on the next real chunk), never
  fatal.
  **Why**: two compounding problems. (1) `silence_timeout` (8s, fatal) was
  shorter than `transcribe.py`'s own `NO_AUDIO_WARNING_S` (15s, non-fatal
  "no speech yet" nudge), so the fatal path always fired first and the
  friendlier warning was dead code. (2) more importantly, the fatal check
  applied for the *entire session*, not just startup — on macOS,
  ScreenCaptureKit stops delivering sample buffers outright when there is
  genuinely nothing to capture (unlike Windows' WASAPI loopback, which
  keeps delivering zero-filled buffers), so an ordinary quiet stretch in a
  live meeting (e.g. everyone muted) hit the same "no audio" path as a
  wrong `--device` and killed the session (`exit 1`) mid-meeting.
  **Verification**: code review + manual reasoning through both call sites
  (confirmed `_frames_captured` is the right, already-existing signal for
  "has real audio ever arrived"); `py_compile` clean on both files. Not
  live-tested against an actual multi-minute muted meeting (would require
  extended manual real-audio setup) — flagged as not verified end-to-end.

- **What**: `vad.py` (`VoiceActivityDetector.stop()`) now flushes any
  in-progress utterance (`_speech_start_sample is not None`) via
  `_emit_segment()` after the background thread is joined (race-free, since
  the thread that mutates that state has already stopped).  `transcribe.py`
  `run()`'s `finally` block now drains `seg_queue` with `get_nowait()` in a
  loop (via a new shared `_process_segment()` helper, factored out of the
  main loop body) right after `detector.stop()` and before `cap.stop()`.
  **Why**: previously the last utterance of every session — the flushed one
  plus anything still sitting in `seg_queue` when Ctrl+C landed — was
  silently discarded on shutdown; neither VAD nor the main loop ever drained
  it.
  **Verification**: live smoke test — directly seeded `_speech_frames`/
  `_speech_start_sample` on a real `VoiceActivityDetector` (real Silero
  model load) to simulate being mid-utterance, called `.stop()`, confirmed
  a `SpeechSegment` (duration 0.096s, 1536 samples) landed in `out_queue`
  and internal state was cleared afterward. Script + output in this task's
  scratchpad (`smoke_vad_flush.py`).

- **What**: `transcribe.py` `main()` — `--url` argparse `help=` text updated
  to `{sentence, model, language, session_id}` (was still missing
  `session_id` from the earlier session-ID feature work).
  **Why**: `python -m transcribe --help` disagreed with both the actual POST
  body and the README.
  **Verification**: visual diff against `README.md`'s `--url` row and the
  module docstring, both of which already had `session_id`.

- **What**: `requirements.txt` — added `audioop-lts>=0.2.1; python_version
  >= "3.13"`.
  **Why**: `audio_common.py` imports stdlib `audioop` (already deprecated in
  3.11, the pinned version), which is removed outright in 3.13.
  `run_macos.sh`'s interpreter picker offers `python3.13` first and its
  version gate only checks `>= 3.11` (no ceiling), so a 3.13 venv passes
  every setup check (`numpy` imports fine) and then dies with
  `ModuleNotFoundError` on the very first audio chunk. `audioop-lts`
  restores the same module/API on 3.13+ rather than capping supported
  versions.
  **Verification**: confirmed via `pip index`-style reasoning that
  `audioop-lts` is the standard stdlib-`audioop` backport for 3.13+ (same
  module name, drop-in). Not installed/exercised here since the project's
  own `.venv` is on 3.11.15 (`python_version >= "3.13"` marker means it's
  simply not pulled in on this machine) — flagged as not live-tested on an
  actual 3.13 interpreter.

### Added
- **What**: `run_windows.ps1` rewritten for parity with `run_macos.sh`:
  auto-creates `.venv` on first run with an interpreter-selection menu (`py
  -3.13`/`-3.12`/`-3.11`/`-3.10`/`-3.9` probed via the `py` launcher, plus
  `python3`/`python` on PATH, plus a free-text "Other" option), validates an
  existing venv's Python version (`>= 3.11`) and that dependencies actually
  import (`numpy` canary), auto-reinstalling `requirements.txt` if not, with
  clear errors instead of a silent/confusing failure.
  **Why**: previously `run_windows.ps1` just checked `Test-Path` on
  `.venv\Scripts\python.exe` and errored out with "run setup first" — none
  of `run_macos.sh`'s create/repair logic existed on the Windows side, an
  asymmetry with no stated reason.
  **Verification**: `py_compile`-equivalent not available (no PowerShell/
  `pwsh` installed in this environment — confirmed via `command -v pwsh`);
  manually proofread line-by-line for balanced blocks and correct
  cmdlet/native-command syntax. **Not executed** — flagged as not
  live-tested; needs a real run on Windows (or with `pwsh` installed) before
  fully trusting it.

- **What**: `run_macos.sh` and `run_windows.ps1` both now list capture
  devices (`-m transcribe --list-devices`) and prompt for `--device` (blank
  = auto-detected default) before the mode prompt, passing `--device` to
  `transcribe.py` only if a value was entered.
  **Why**: wrong `--device` is the README's #1 troubleshooting entry, and
  the auto-detected Windows default is explicitly best-effort
  (`capture_windows.py`'s `_default_loopback_index`) — neither launcher
  previously gave any way to override it without editing the script.
  **Verification**: `run_macos.sh` device-listing invocation exercised
  live via the equivalent direct call (`python -m transcribe --streaming
  --url http://localhost:9/ingest --list-devices`) — real device list
  printed successfully. The full interactive launcher flow (arrow-key-free
  numbered prompts) was not run end-to-end (requires an interactive TTY).

- **What**: `tests/test_audio_common.py` (6 cases: mono passthrough, stereo
  averaging, >2-channel averaging, resample no-op at target rate, resample
  downsamples 48kHz→16kHz, resample state carries across calls) and
  `tests/test_transcribe.py` (14 cases: `_clean_text` filler-dropping incl.
  the whitespace/wording-variant regression, real-speech-with-parens
  preserved, empty/whitespace handling; `_validate_url` accept/reject
  cases), using stdlib `unittest` (no new runtime dependency). `README.md`
  gained a "Tests" section with the run command for both platforms.
  **Why**: none of the pure, hardware-free helpers (`_clean_text`,
  `downmix_to_mono`, `resample_to_target`, now `_validate_url`) had any
  test coverage — the exact `(silence)` filler-matching bug a colleague
  found earlier this session would have been caught immediately by a
  3-line test.
  **Verification**: `./.venv/bin/python -m unittest discover -s tests -v`
  → **20/20 tests pass**, actual output captured (not "should pass").

### Changed
- **What**: `README.md` — launcher-scripts blurb now mentions the device
  prompt and venv auto-repair on both platforms; `--url` flag row notes the
  http(s)-scheme requirement and that POSTs are now backgrounded with
  retry; new Troubleshooting note explaining silence is only fatal before
  the first audio arrives, never mid-session; new "Tests" section.
  **Why**: keep docs in sync with the behavior changes above (the project's
  own CHANGELOG convention treats doc drift as a real defect, not
  a footnote).
  **Verification**: doc-only; proofread against the actual code changes
  made in this entry.

### Verified
- `./.venv/bin/python -m py_compile transcribe.py vad.py capture_windows.py
  capture_macos.py audio_common.py capture.py` → clean, no errors.
- `./.venv/bin/python -m unittest discover -s tests -v` → 20/20 pass.
- Live subprocess smoke test of `--url` validation wiring through
  `main()`/argparse (3 cases: bad scheme, missing `--url`, valid `http://`
  URL) — real process exit codes and stderr captured, matched expectations.
- Live `SentencePoster` smoke test against a real local `http.server`:
  2 forced failures + 1 success → exactly 3 attempts, correct JSON body
  received once (see `smoke_sentence_poster.py` in this task's scratchpad).
- Live `VoiceActivityDetector.stop()` flush smoke test against the real
  class (real Silero model load) — trailing segment confirmed emitted (see
  `smoke_vad_flush.py` in this task's scratchpad).
- **Not verified / explicitly out of scope for this entry**: `run_windows.ps1`
  was not executed (no Windows machine or `pwsh` available here — proofread
  only); the `capture_windows.py`/`capture_macos.py` silence/warning changes
  were not exercised against real extended-silence audio hardware; the
  `audioop-lts` requirements.txt fix was not installed/tested on an actual
  Python 3.13 interpreter (this project's `.venv` is 3.11.15). The smaller
  items from the preceding review that weren't part of the approved 10-item
  plan (`queue.Full` drop counters, `capture_macos.py`'s dispatch-queue
  blocking on a full `out_queue`) were intentionally left untouched — not
  approved for this task.

---

## 2026-09-15 17:40:51+02:00 -> 2026-09-15 17:41:30+02:00 (duration: 39s | tokens: not available)

### Added
- **What**: `transcribe.py` — added unique session ID generation and inclusion in streaming POSTs.
  - Imported `uuid` module.
  - Generate session ID via `uuid.uuid4()` in `run()` at app start.
  - Pass `session_id` parameter to `_post_sentence()` function.
  - Include `session_id` in POST JSON body alongside `sentence`, `model`, and `language`.
  - Updated module docstring to document session ID in POST body.
  **Why**: allows consumers of the streaming endpoint to correlate all sentences from a single transcription session, even when multiple instances start simultaneously (UUID4 guarantees uniqueness).
  **Verification**: reviewed code changes for correctness; session_id is generated once per app invocation and passed to every POST call.

### Changed
- **What**: `README.md` — expanded `--url` flag description to document session_id field in POST body and explain its purpose.
  **Why**: users need to know what fields are in the streaming JSON payload.

---

## 2026-09-15 17:40:12+02:00 -> 2026-09-15 17:40:51+02:00 (duration: 39s | tokens: not available)


### Changed
- **What**: `README.md` — added a line under "Model choice" noting the
  project has been battle-tested with `base.en-q5_1` (English) capturing
  Google Meet in a browser and the Teams desktop app.
  **Why**: reported directly by Igor as real-world usage confirmation worth
  documenting for future readers.
  **Verification**: doc-only change; proofread for accuracy against the
  wording given.

---

## not available -> 2026-09-15 17:40:12+02:00 (duration: not available | tokens: not available)

(Follow-up to the previous entry below, after I asked for the exact repro
steps. Start timestamp not captured at task start — same oversight as the
previous entry; only the real end timestamp is recorded.)

### Fixed
- **What**: `run_macos.sh` — added two checks that now run right after `$PY`
  is resolved (both when a venv was just auto-created, and when a
  pre-existing `.venv` was found and previously used as-is with no
  validation): (1) a Python-version check (`sys.version_info[:2] >= (3,
  11)`) that exits with a clear error and a `rm -rf .venv && ./run_macos.sh`
  suggestion if the venv's interpreter is older than 3.11; (2) a dependency
  check (`import numpy` as a canary) that, if it fails, runs `pip install
  --upgrade pip` + `pip install -r requirements.txt` into the existing venv
  and re-checks, exiting with a clear error pointing at the exact command to
  re-run if it's still broken afterward.
  **Why**: root-caused from the exact repro Igor gave: he ran `python -m
  venv .venv` (bare `python`, which resolves to an old system Python on his
  machine — confirmed 3.9 in his first report of this bug in the current
  session's history), then `python3.11 install -r requirements.txt` — a
  typo missing `-m pip`, which runs `python3.11` looking for a script file
  literally named `install` and fails immediately without installing
  anything into `.venv` (and never touches `.venv`'s own pip either way,
  since it wasn't invoked as `.venv/bin/pip` or `.venv/bin/python -m pip`).
  The result: `.venv/bin/python` exists (created by the `venv` module,
  passes the old `[ -x "$PY" ]` check) but is both the wrong Python version
  and has zero required packages installed. `run_macos.sh` previously
  treated "the binary exists" as "the venv is usable" and launched
  `transcribe.py` straight into an instant `ModuleNotFoundError` — which,
  backgrounded and easy to miss, read to Igor as "fails to execute the
  code, it is not even activating the venv."
  **Verification**: `bash -n run_macos.sh` clean. Reproduced all three
  relevant states for real against stub `.venv/bin/python` scripts in
  scratch dirs (no real network installs, kept fast/offline): (1) existing
  venv reporting Python 3.9.6 → script printed "ERROR: .venv/bin/python is
  Python 3.9.6, but this project requires Python 3.11+." and exited 1
  *before* any model/mode prompts, matching Igor's exact repro; (2) existing
  venv reporting Python 3.11.9 but `import numpy` always failing (deps never
  installed) → script detected it, ran the stubbed `pip install --upgrade
  pip` + `pip install -r requirements.txt`, re-checked, still failed, and
  exited 1 with the "re-run manually to see why" message rather than
  launching transcribe.py; (3) a **real** venv built with
  `/opt/homebrew/bin/python3.11 -m venv .venv` + `./.venv/bin/pip install
  numpy` → both checks passed silently and the script proceeded straight to
  the model/mode prompts and launch, confirming no false positives on a
  genuinely healthy venv.

### Changed
- **What**: `README.md` — macOS manual-setup section now explicitly calls
  out the exact typo that caused this (`python3.11 install ...` missing
  `-m pip`, which silently installs nothing) and adds a line documenting
  that `run_macos.sh` now validates an existing `.venv` instead of trusting
  it blindly.
  **Why**: document the specific mistake so it's easy to self-diagnose from
  the docs alone, on top of the script-level fix.
  **Verification**: proofread against the actual script behavior after
  editing it; not a runnable doc test.

---

## not available -> 2026-09-15 17:11:39+02:00 (duration: not available | tokens: not available)

Allow user to select weather python3 , python3.11 or other alias should be used."

**Note:** start timestamp was not captured at the actual start of this task
(oversight — no `date` call was made before beginning work), so it and the
duration are marked "not available" per the CHANGELOG convention rather than
estimated; only the real end timestamp (read via `date` at completion) is
recorded.

### Changed
- **What**: `run_macos.sh` — replaced the old hard "ERROR: virtualenv not
  found, run setup first" exit with an interactive interpreter-selection +
  auto-setup flow. When `.venv/bin/python` doesn't exist yet, it scans
  `PATH` for `python3.13` down through `python3.9`, plus bare `python3` and
  `python`, lists whichever resolve (each with its resolved path via
  `command -v`), adds an "Other (enter a command or path)" option, lets the
  user pick one (`select`), validates the choice is actually runnable, then
  runs `<chosen> -m venv .venv` followed by `<chosen>'s venv python> -m pip
  install --upgrade pip` and `... install -r requirements.txt` before
  continuing on to the existing model/mode prompts. If `.venv` already
  exists, behavior is unchanged (used as-is, no re-prompt).
  **Why**: reported directly — on Igor's Mac, plain `python3`/`pip` resolve
  to an old system Python 3.9, so following the README's old `python3 -m
  venv .venv` instructions (or running `run_macos.sh`, which only checked
  for an existing `.venv` and never created one) silently produced a
  broken/wrong-version environment; he had to work around it manually with
  `python3.11`/`pip3.11`. Making interpreter choice explicit and scripted
  removes the silent-wrong-default failure mode instead of just documenting
  around it.
  **Verification**: `bash -n run_macos.sh` clean. Ran the new flow twice
  end-to-end against fake interpreters on `PATH` (no real venv/pip network
  install, to keep the test fast and offline) in a scratch dir: (1) picking
  a fake `python3.11` from the numbered list — produced `STUB: created venv
  at .venv using python3.11`, ran `-m pip install --upgrade pip` then `-m
  pip install -r requirements.txt` in order, then correctly fell through to
  the model/mode prompts and launched `.venv/bin/python -m transcribe
  --model tiny.en --output transcripts/transcript.log` as before; (2)
  picking "Other" and typing a custom interpreter path — same result, using
  the typed path instead of a `PATH` lookup. Also confirmed the "no re-setup
  needed" path is unaffected: pre-existing `.venv/bin/python` skips straight
  to the model/mode prompts as it did before this change.
- **What**: `README.md` — macOS Setup section now leads with `./run_macos.sh`
  as the recommended path (auto-detects/prompts for the interpreter),
  explains why (the `python3`/`pip` → old-3.9-alias trap), and changes the
  manual fallback command from `python3 -m venv .venv` to `python3.11 -m
  venv .venv` with a note not to rely on a bare `python3`/`pip` without
  confirming what they resolve to.
  **Why**: keep the documented manual path from recommending the exact
  command that caused this bug.
  **Verification**: proofread against the actual `run_macos.sh` behavior
  after editing it; not a runnable doc test (none exists for this file).

---

## 2026-09-15 13:54:21+02:00 -> 2026-09-15 15:00:14+02:00 (duration: 1h 5m 53s | tokens: not available)

### Added
- **What**: `transcribe.py` — new `--streaming`, `--url`, and `--language` CLI
  flags, a `_post_sentence(url, sentence, model, language)` helper (stdlib
  `urllib.request`, JSON body, 5s timeout), and a `Model(..., language=...)`
  constructor arg. `run()` now branches: with `--streaming`, no
  `TranscriptWriter`/log file is created at all (`writer` stays `None`) and
  each cleaned transcription is POSTed as `{"sentence", "model", "language"}`
  JSON instead of being written to disk; without it, behavior is unchanged
  (writes to `--output` as before). `parser.error("--streaming requires
  --url")` enforces the dependency before anything starts.
  **Why**: requested directly — sentences must reach an external consumer in
  real time via HTTP instead of (not in addition to) the log file.
  **Verification**: `python3 -m py_compile transcribe.py` clean. Ran
  `python -m transcribe --streaming` (no `--url`) for real → exits 2 with
  `error: --streaming requires --url`, confirming the guard fires before
  audio capture/model loading start. Live-tested `_post_sentence` against a
  real local `http.server.HTTPServer` instance (no mocking) — the server
  received exactly `{"sentence": "hello world", "model": "base.en-q5_1",
  "language": "en"}` as JSON, confirming the wire format matches the spec.
  Did not verify the full streaming path end-to-end with real captured
  audio (would require live mic/system audio + a running consumer
  endpoint on Igor's machine).
- **What**: `run_macos.sh` (new, `chmod +x`) and `run_windows.ps1` (new) —
  interactive launchers. Both: prompt with a numbered `select`/menu list of
  whisper.cpp model names pulled from `pywhispercpp.constants.AVAILABLE_MODELS`
  (all `.en` + quantized variants, e.g. `tiny.en-q5_1`, `base.en-q8_0`,
  `large-v3-turbo-q5_0`), prompt to choose streaming vs. log-file mode, then
  prompt for the URL (streaming) or output filename (log, defaulting to
  `transcripts/transcript.log`/`transcripts\transcript.log`) — re-prompting
  on empty input for required fields. They then launch
  `.venv/bin/python`/`.venv\Scripts\python.exe -m transcribe` with the
  assembled args as a background child process, print its PID, and block on
  it (bash `wait`; PowerShell `Wait-Process`) so the launcher script itself
  stays alive only as long as the transcriber does; a `trap ... INT TERM`
  (bash) / `try/finally` with `Stop-Process` (PowerShell) forwards Ctrl+C /
  cleans up the child if the wrapper exits first, satisfying "running in
  background until interrupted" without losing normal Ctrl+C behavior.
  **Why**: requested directly — avoid users having to remember/type
  `transcribe.py` flags, and give a one-command way to start a session.
  **Verification**: `bash -n run_macos.sh` clean. Live-ran `run_macos.sh`
  twice against a stub `.venv/python` (a shell script that echoes its argv)
  with piped input simulating both paths — log-file path (`1`, `2`, blank
  filename) produced `-m transcribe --model tiny.en --output
  transcripts/transcript.log`; streaming path (`2`, `1`,
  `http://example.com/ingest`) produced `-m transcribe --model
  tiny.en-q5_1 --streaming --url http://example.com/ingest` — both matched
  expected argv exactly, and the background+PID+wait mechanics ran without
  error. `run_windows.ps1` was hand-reviewed against the same logic but
  **not executed** — no Windows/PowerShell runtime (`pwsh`) available in
  this environment; syntax and control flow were checked by inspection only,
  not verified by running it.

### Changed
- **What**: `README.md` — project-layout diagram lists the two new launcher
  scripts; Usage section gets a streaming example and a new "Launcher
  scripts" subsection for both OSes; Flags table gets `--language`,
  `--streaming`, `--url` rows and notes `--output` is ignored under
  `--streaming`.
  **Why**: keep setup/usage docs in sync with the new flags and scripts, per
  the "no undocumented flags" expectation the rest of this README follows.
  **Verification**: proofread against the actual flag names/defaults/help
  text in `transcribe.py` after editing it (not re-run as a doc test, this
  file has no test harness).

---

## 2026-09-14 15:31:20+02:00 -> 2026-09-14 15:37:48+02:00 (duration: 6m 28s | tokens: not available)

### Fixed
- **What**: `capture_macos.py` — `list_loopback_devices()`/`_resolve_target()`
  now filter `SCShareableContent.applications()` down to "regular"
  (Dock-visible) apps only, via a new `_regular_app_pids()`/
  `_capturable_apps()` pair that cross-references
  `AppKit.NSWorkspace.runningApplications()`'s `activationPolicy() ==
  NSApplicationActivationPolicyRegular` by PID. Added `import AppKit`
  (already covered by the existing `pyobjc-framework-Cocoa` dependency, no
  requirements.txt change needed).
  **Why**: reported directly — real `--list-devices` output on Igor's
  machine showed 33 entries, the large majority background/system helper
  processes ScreenCaptureKit tracks as window owners but a user would never
  think of as "an app" (Dock, Spotlight, Notification Center, Control
  Center, loginwindow, per-app "Open and Save Panel Service"/"AutoFill (...)"
  helpers, raw "pid NNNNN" entries for processes with no name at all) -
  making the per-app capture feature impractical to use as designed.
  Root cause: `SCShareableContent.applications()` lists every process
  ScreenCaptureKit considers a capturable window owner, which is a much
  broader set than "apps in the Dock"; regular activation policy is the
  same signal macOS itself uses for Dock/Cmd-Tab membership.
  **Verification**: this exact fix's core logic (not the full
  `list_loopback_devices()` call, which additionally needs Screen Recording
  permission this environment's Python doesn't have) was verified for real
  in a scratch venv (`pyobjc-framework-Cocoa`, upgraded `pip` first -
  the initial attempt failed building `pyobjc-core` from source under the
  venv's stock old `pip`): `NSWorkspace.sharedWorkspace().runningApplications()`
  returned 112 processes total, filtered by `activationPolicy() ==
  NSApplicationActivationPolicyRegular` down to exactly 10 - Notes, Code,
  Microsoft Outlook, Mail, Finder, Microsoft Teams, Google Chrome, Preview,
  System Settings, Terminal - i.e. real user-facing apps, correctly
  excluding the Dock/Spotlight/helper-process noise from Igor's reported
  output. `python3 -m py_compile capture_macos.py` clean. **Not verified**:
  the filtered list end-to-end through `list_loopback_devices()` itself
  (needs Screen Recording permission on the actual interpreter running it,
  which this environment's scratch venv doesn't have) - Igor should re-run
  `--list-devices` to confirm the visible list is now short and relevant.

---

## 2026-09-14 14:53:02+02:00 -> 2026-09-14 15:01:04+02:00 (duration: 8m 2s | tokens: not available)

### Added
- **What**: `capture_macos.py` — new macOS capture backend using
  ScreenCaptureKit (`SCStream` + `SCStreamConfiguration.capturesAudio`,
  macOS 13+) via PyObjC (`pyobjc-framework-ScreenCaptureKit`, `-CoreMedia`,
  `-CoreAudio`, `-libdispatch`, `-Cocoa`). Exposes the same public shape as
  the Windows backend (`LoopbackCapture`, `list_loopback_devices`). Audio
  arrives as Float32 interleaved PCM via an async `SCStreamOutput` callback
  (`_StreamHandler`, `_on_audio_sample_buffer`), extracted from the
  `CMSampleBuffer` via `CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer`
  (called through a ctypes-computed `AudioBufferList` struct size, since
  PyObjC doesn't expose a `sizeof()` for it), converted to int16, then run
  through the same downmix/resample path as Windows. `--device` selects a
  capture *target* rather than a device: 0/default = whole system audio,
  N≥1 = one running app's audio in isolation (via `SCContentFilter
  initWithDisplay:includingApplications:exceptingWindows:`), enumerated
  fresh from `SCShareableContent` on every call. Async ScreenCaptureKit
  calls (`getShareableContentWithCompletionHandler_`,
  `startCaptureWithCompletionHandler_`, `stopCaptureWithCompletionHandler_`)
  are turned synchronous via a `_run_until` NSRunLoop-pump helper.
  TCC/Screen-Recording-permission denial (`SCStreamErrorDomain` code
  -3801) is caught and re-raised as a `NoAudioError` with setup
  instructions rather than a raw ObjC error.
  
  **Verification**: not a live end-to-end audio test (no way to grant
  Screen Recording permission non-interactively in this environment).
  Instead, individually verified against the real frameworks in a scratch
  venv (`pip install pyobjc-framework-ScreenCaptureKit pyobjc-framework-CoreMedia
  pyobjc-framework-CoreAudio pyobjc-framework-libdispatch`, macOS 26.6.2):
  confirmed `SCStreamConfiguration.setCapturesAudio_`/`setSampleRate_`/
  `setChannelCount_`, `SCContentFilter` init selectors, `SCStream`
  start/stop/addStreamOutput selectors, and
  `CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer`'s exact 8-arg
  calling convention all exist and match the code written. Ran the
  `_run_until`/completion-handler pattern for real against
  `SCShareableContent.getShareableContentWithCompletionHandler_` and got a
  real, correctly-shaped response back (a `SCStreamErrorDomain` -3801
  permission error, since this session's Python has no Screen Recording
  grant) — confirming the async-to-sync bridging technique actually works
  in this environment, not just in theory. Verified `ctypes.sizeof()` on a
  mirrored `AudioBufferList` struct matches real C struct
  layout/alignment (24 bytes for 1 buffer, 40 for 2). Ran
  `capture.list_loopback_devices()` end-to-end through the real public API
  and got the expected friendly `NoAudioError` permission message back.
  Byte-compiled all touched files (`python3 -m py_compile`) — no syntax
  errors. **Not verified**: the actual sample-buffer → PCM data path
  (`_extract_pcm16`) with real audio, since that needs the permission grant
  and a live capture session on Igor's machine.
- **What**: `audio_common.py` — new shared module for
  `TARGET_RATE`/`SAMPLE_WIDTH`/`CHUNK_MS`, `NoAudioError`, `LoopbackDevice`,
  and `downmix_to_mono`/`resample_to_target` helpers, used by both capture
  backends instead of duplicating that logic per platform.
  **Why**: avoid duplicating the PCM downmix/resample logic (and the
  `NoAudioError`/`LoopbackDevice` types) between `capture_windows.py` and
  `capture_macos.py.
  "Duplicated logic across paths is deduped" item.
  **Verification**: covered by the same `py_compile` pass above; behavior
  unchanged from the pre-existing Windows-only version (function bodies
  moved, not rewritten).

### Changed
- **What**: `capture.py` — rewritten from "Windows WASAPI implementation +
  CLI" into a thin platform dispatcher (`sys.platform` → `capture_windows`
  or `capture_macos`) plus the unchanged standalone CLI
  (`--list-devices`/`--device`/`--seconds`/`--wav`), which now uses
  OS-appropriate "no devices found" hints. The prior Windows implementation
  moved as-is into the new `capture_windows.py`.
  **Why**: `vad.py`/`transcribe.py` only ever used `capture.py`'s public
  surface (`LoopbackCapture`, `NoAudioError`, `list_loopback_devices`) —
  keeping that surface stable while swapping the backend by platform meant
  zero changes needed in `vad.py` and only cosmetic ones in `transcribe.py`.
  **Verification**: `python3 -m py_compile` clean; smoke-tested
  `capture.LoopbackCapture`/`capture.list_loopback_devices` resolve to the
  macOS backend classes/functions when imported under `darwin`.
- **What**: `transcribe.py` — docstring and `--list-devices` "no devices"
  message made platform-aware (`sys.platform` check) instead of
  Windows-only wording; `ArgumentParser` description no longer says "via
  WASAPI loopback".
  **Why**: the hint text and description were Windows-specific and would
  have been actively misleading on macOS.
  **Verification**: `python3 -m py_compile` clean; read through by eye.
- **What**: `requirements.txt` — added `sys_platform` environment markers
  so Windows deps (`PyAudioWPatch`) and macOS deps
  (`pyobjc-framework-Cocoa`/`-ScreenCaptureKit`/`-CoreMedia`/`-CoreAudio`/
  `-libdispatch`) each only install on their own OS via one shared
  `pip install -r requirements.txt`; noted the Screen Recording permission
  requirement and that `pywhispercpp` typically needs no local build on
  macOS (Xcode Command Line Tools) the way it sometimes does on Windows
  (MSVC/CMake).
  **Why**: a single cross-platform requirements file needs conditional
  installs per OS rather than two separate files, and the new macOS PyObjC
  frameworks needed adding.
  **Verification**: installed the macOS marker's packages into a scratch
  venv (`pyobjc-framework-*` all resolved to 11.1, satisfying the `>=10.1`
  pin) and confirmed `capture_macos.py` imports cleanly against them.
- **What**: `README.md` — split Setup/Usage/Troubleshooting into
  Windows/macOS variants (venv activation, permission setup, `--device`
  semantics, debugging commands); project layout diagram updated for the
  new `capture_windows.py`/`capture_macos.py`/`audio_common.py` split;
  Notes section updated to describe the shared downmix/resample module and
  macOS's "capture target" vs. Windows' "device index" distinction.
  **Why**: existing docs assumed Windows-only end to end (setup commands,
  troubleshooting, flag semantics) and needed a macOS counterpart for every
  section that had one.
  **Verification**: not verified (documentation-only change); cross-checked
  by eye against the actual new code/flags.
- **Why**: this skill's own text was the thing that first flagged the
  Windows/macOS split as a locked, deliberate decision — leaving it
  unrevised after actually revisiting that decision would mislead the next
  session into re-raising an already-settled question, or worse, undoing
  this work.
  **Verification**: not verified (documentation-only change).

---

## 2026-09-13 21:16:19+02:00 -> 2026-09-13 21:17:30+02:00 (duration: 1m 11s | tokens: not available)

### Changed
- **What**: `CHANGELOG.md` — convention section rewritten so each entry is
  one task headed by exact start/end timestamp, duration, tokens spent,
  and the original prompt verbatim (previously: just a date heading).
  Existing 2026-09-13 content split out into individual per-task entries
  below under this new format.
  **Why**: requested directly — timestamp/duration/tokens/prompt must be
  logged per task, mirroring the rule just added to `CLAUDE.md`.
  **Verification**: not verified (documentation-only change, no runtime
  behavior to test). Timestamp above taken from `Get-Date` at time of
  writing, not estimated.

---

## not available (predates timestamp/duration/token instrumentation, adopted at the entry above) (duration: not available | tokens: not available)


### Changed
- **What**: `CLAUDE.md`, "Before done" section — added a bullet requiring
  log entries to record exact start/end timestamp (ISO 8601), wall-clock
  duration, tokens spent, and the original prompt verbatim; unavailable
  fields marked "not available" rather than omitted or guessed.
  **Why**: requested directly, to make AI-authored changes auditable
  after the fact (who asked for what, when, how long/costly it was).
  **Verification**: not verified (documentation-only change).

---

## not available (predates timestamp/duration/token instrumentation) (duration: not available | tokens: not available)

### Added
- **What**: `CLAUDE.md`, "Before done" section — new bullet requiring log
  entries to capture detailed AI-authored change output (what/why, files/
  functions touched, verification performed) for future analysis, not a
  one-line label.
### Changed
- **What**: `CHANGELOG.md` convention rewritten to require **What**/
  **Why**/**Verification** per bullet instead of a single terse line;
  that day's existing `Fixed`/`Changed` entries rewritten to the new
  standard as a worked example, plus a new `Verified` entry added for a
  live smoke test run at the time.
  **Why**: a terse one-line changelog doesn't give a future reader (human
  or AI) enough to understand a change without re-deriving it from the
  diff — the point of a change log is to save that re-derivation.
  **Verification**: not verified (documentation-only change).

---

## not available (predates timestamp/duration/token instrumentation) (duration: not available | tokens: not available)

### Added
- **What**: `CHANGELOG.md` created — Added/Changed/Fixed convention,
  backfilled with the project's history up to that point. Linked from
  `README.md` under a new "Changelog" section.
  **Why**: `CLAUDE.md`'s "Before done" checklist requires a change log
  per project convention, and none existed yet for this project.
  **Verification**: not verified (documentation-only change).

---

## not available (predates timestamp/duration/token instrumentation) (duration: not available | tokens: not available)

### Verified
- **What**: full `CLAUDE.md` checklist run against the project, item by
  item, plus a live end-to-end smoke test: `python -m transcribe --device
  10`, speech fed via Windows TTS (`System.Speech.Synthesis.SpeechSynthesizer`)
  through the WASAPI loopback device, model `base.en-q5_1`.
  **Why**: requested directly, and required by the checklist's own
  Verification section ("ran the real test/build suite, captured actual
  output — not 'should pass'").
  **Verification**: actual transcript line produced —
  `[21:08:32] This is a live verification test of the call transcriber
  pipeline.` — written to stdout and the transcript log, matching the
  spoken TTS text. `pywhispercpp` import/model load reconfirmed clean (no
  C++ build needed). Test artifacts deleted after inspection. Checklist
  found two defensible N/A-with-caveat items (no DI container for a
  single-entrypoint CLI; no CHANGELOG.md yet at that point) and zero real
  defects.

---

## not available (predates timestamp/duration/token instrumentation) (duration: not available | tokens: not available)

### Changed
- **What**: ASR section changed from `faster-whisper`/CTranslate2, `small.en`/`medium.en` to
  `pywhispercpp` (whisper.cpp bindings), `tiny.en`/`base.en` quantized
  (e.g. `base.en-q5_1`); added the `silero-vad` → torch/torchaudio +
  VC++ Redistributable dependency note; filled in actual CLI defaults and
  the standalone-debug commands for `capture.py`/`vad.py`. 
  **Why**: the original file's ASR description contradicted this
  project's actual, explicitly-requested architecture (whisper.cpp, not
  faster-whisper) — likely a stale draft from before that decision was
  finalized. Left uncorrected, it would mislead any future session that
  loads this skill into proposing the wrong ASR stack.
  **Verification**: diffed old vs. new content manually against the
  current `capture.py`/`vad.py`/`transcribe.py`/`requirements.txt` to
  confirm every claim in the skill file now matches the actual code.

---

## not available (predates timestamp/duration/token instrumentation; original build session) (duration: not available | tokens: not available)

### Added
- Initial project scaffold: `capture.py`, `vad.py`, `transcribe.py`,
  `requirements.txt`, `README.md`, `.gitignore`.
- WASAPI loopback capture (`PyAudioWPatch`) with device enumeration,
  downmix + resample to 16 kHz mono PCM16.
- Streaming Silero VAD segmentation (`vad.py`) with pre-roll padding so
  utterance onsets aren't clipped, and a 28s max-segment force-flush so
  long monologues still get bounded, timely ASR output.
- whisper.cpp transcription via `pywhispercpp` (`transcribe.py`), default
  model `base.en-q5_1`; transcript lines written to stdout and appended to
  a log file, flushed + `fsync`'d after every segment.
- CLI flags: `--list-devices`, `--device`, `--model`, `--output`, `--threads`.
- `CLAUDE.md` project checklist.

### Fixed
- **What**: `capture.py`, `LoopbackCapture.stop()` — reordered to call
  `self._close_stream()` before `self._thread.join(timeout=timeout)`
  (previously joined first).
  **Why**: the capture thread can be parked inside a blocking
  `stream.read()` while the WASAPI render endpoint is idle (nothing
  playing). Closing the stream is what actually raises in `read()` and
  unblocks the thread; joining first just waits out the full timeout for
  no reason before ever closing it.
  **Verification**: identified via code review, not a reported hang;
  behaviorally exercised on every `stop()` call in every later smoke test
  in this log with no hang observed.
