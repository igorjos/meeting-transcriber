# Changelog

Newest entry on top. Each entry documents code changes, grouped as `### Added` / `### Changed` / `### Fixed` / `### Verified`.

Each bullet includes:
- **What**: the concrete change (file(s)/function(s)/flag(s) touched).
- **Why**: the reasoning or root cause.
- **Verification**: what was tested to confirm it works.

---

## 2026-09-21 (Terminating app not working on MacOS)

### Fixed
- **What**: `run_macos.sh` now ends with `exec "$PY" "${TRANSCRIBE_ARGS[@]}"`
  instead of starting the app with `&`, capturing `$!`, and then
  `trap 'kill -INT "$PID"' INT TERM` + `wait "$PID"`. The startup message now
  prints the launcher's own PID (unchanged by `exec`) and mentions that a
  second Ctrl+C force-quits.
  **Why**: A job started with `&` from a non-interactive shell script has SIGINT
  set to "ignored", preventing Python from installing its `KeyboardInterrupt`
  handler. Running the app as the foreground process gives it the terminal's
  SIGINT directly, enabling graceful shutdown.
  **Verification**: Tested with stub `transcribe.py` and launcher, confirming
  Ctrl+C properly triggers shutdown; `.venv/bin/python -m unittest discover -s
  tests -q` → "Ran 46 tests / OK".

---

## 2026-09-16 (Speaker identification threshold tuning)

### Fixed
- **What**: `speaker_id.py` `DEFAULT_SPEAKER_THRESHOLD` raised from `0.75`
  to `0.84`. Added separate `MASKED_MATCH_THRESHOLD = 0.75` for timbre-only
  comparisons when pitch data is unavailable.
  **Why**: At `0.75`, too many different speakers were being matched to existing
  centroids instead of registering as separate people. The higher threshold
  reduces false merging while the lower threshold for quiet/whispered segments
  prevents regression.
  **Verification**: Tested with synthetic voices: two formant-shaped speakers
  scored `0.804` similarity (above old `0.75`, below new `0.84`). Unit tests
  pass: `.venv/bin/python -m unittest discover -s tests -q` → "Ran 46 tests / OK".

### Changed
- **What**: `CHANGELOG.md` - removed process narration from prior entries,
  keeping only code changes per project convention.
  **Why**: Changelog should focus on technical changes only.
  **Verification**: Full file review; technical content preserved; only
  process narration removed.

### Verified
- `git status --short` reviewed - intended files changed as expected.

---

## 2026-09-16 (Graceful shutdown with signal handling)

### Fixed
- **What**: `transcribe.py`'s shutdown path now runs the trailing-segment drain
  on a daemon thread (`shutdown-drain`) with a polling wait. A `signal.signal(signal.SIGINT, _force_exit)`
  handler is armed at shutdown start for force-exit on second Ctrl+C.
  **Why**: whisper.cpp's `transcribe()` is a blocking native call that never checks
  for Python signals. By moving it to a daemon thread, the main thread can respond
  to signals via short-timeout `Thread.join()` calls instead.
  **Verification**: Tested graceful and forced shutdown paths; `.venv/bin/python -m unittest discover -s tests -q` → "Ran 45 tests / OK".

---

## 2026-09-16 (Speaker identification improvements)

### Fixed
- **What**: `speaker_id.py` - corrected pitch weighting and MFCC liftering;
  added masked (timbre-only) cosine matching for segments without reliable pitch.
  `PITCH_WEIGHT` lowered from `3.0` to `1.0`; applied standard sinusoidal
  cepstral liftering to MFCC output.
  **Why**: High pitch weight caused same-speaker prosody variation (pitch rising
  for questions) to drop similarity below threshold. MFCC-mean coefficient 1 alone
  carried ~59% of similarity, causing poor discrimination between different speakers.
  Liftering and lower pitch weight fix these issues.
  **Verification**: Measured same-voice prosody drift (±25% pitch) now scores
  `0.866` (previously capped ~0.82). Different speakers at same pitch score `0.708`
  (below 0.75 threshold). Full suite: "Ran 46 tests / OK".

- **What**: `speaker_id.py` `identify()` now uses masked cosine for segments
  with zero pitch block (no reliable pitch detected).
  **Why**: Full-vector comparison when pitch is all-zero caps similarity at `~0.43`
  regardless of actual timbre match, causing quiet/whispered segments to mint new
  speaker IDs incorrectly.
  **Verification**: Quiet segments now correctly match their own speaker
  (`~0.78-0.79` similarity) instead of minting new IDs.

- **What**: `speaker_id.py`/`vad.py` debug CLIs now check `cap.stopped_by_user`
  and break immediately after `cap.raise_if_failed()`.
  **Why**: These CLIs would otherwise keep polling if capture was stopped via
  system stop-sharing control, unlike `transcribe.py` itself.
  **Verification**: `py_compile` clean; code review confirms both loops mirror
  `transcribe.py`'s existing structure.

- **What**: `transcribe.py` now prints explicit message when capture stopped via
  system stop-sharing control.
  **Why**: Without this, system-initiated stops are indistinguishable from Ctrl+C
  in output, potentially confusing users.
  **Verification**: `py_compile` clean.

### Changed
- **What**: `tests/test_speaker_id.py` - added `_whispered_voice()` and
  `_formant_voice()` fixtures for more realistic testing.
  **Why**: Existing harmonic-reweighting fixture doesn't adequately exercise
  timbre discrimination; formant-based fixture better represents real speech.
  **Verification**: Full test suite: "Ran 45 tests / OK".

### Verified
- All Python files compile cleanly.
- Full test suite: "Ran 45 tests / OK".
- Git status reviewed - only intended files changed.

---

## 2026-09-16 (Output format and error handling improvements)

### Changed
- **What**: `transcribe.py` output format changed from `[{ts}] Person {speaker_id} said: {text}`
  to `[{ts} - Person {speaker_id}] said: {text}`, moving speaker label inside brackets.
  **Why**: Direct request for improved formatting.
  **Verification**: `.venv/bin/python -m unittest discover -s tests -q` → 42/42 pass.

### Fixed
- **What**: `capture_macos.py` `LoopbackCapture._on_stream_error` now distinguishes
  between fatal errors and expected user-initiated stops (code -3817).
  Added `_USER_STOPPED_CODE = -3817` and `stopped_by_user` flag.
  `capture_windows.py` gets matching `stopped_by_user` property (hardcoded False).
  **Why**: User-initiated stream stops (via system stop-sharing control) were being
  treated as fatal errors. Now they set a flag for clean shutdown instead.
  **Verification**: `.venv/bin/python -m py_compile` clean; full test suite: "Ran 42 tests / OK".

### Verified
- All Python files compile cleanly.
- Full test suite: "Ran 42 tests / OK".
- Git status reviewed - only intended files changed.

---

## 2026-09-16 (Speaker identification implementation)

### Added
- **What**: New `speaker_id.py` module - algorithmic per-segment speaker
  identification via MFCC mean/std (13 coefficients each), pitch encoding
  (8-bin Gaussian soft-binned autocorrelation-based F0), and cosine similarity
  matching against growing speaker centroids (unbounded, updated via running mean
  capped at 50 segments).
  **Why**: User required speaker attribution without adding a second ML model,
  scaling to 10+ speakers, correct attribution across interruptions, and
  fast in-memory voice reference vectors.
  **Verification**: All pitch/MFCC/voiceprint/registry features tested with
  synthetic signals; smoke tests covering pitch estimation, 10-speaker registration,
  2-person alternating conversation with interruptions. Full suite: "Ran 42 tests / OK".

- **What**: New `tests/test_speaker_id.py` (22 tests) - covers MFCC, pitch
  estimation, voiceprint extraction, and speaker registry logic using synthetic
  tone signals.
  **Why**: No real voice recordings available; synthetic fixtures allow
  deterministic correctness validation.

### Changed
- **What**: `transcribe.py` - `_process_segment` now takes `speakers: SpeakerRegistry`,
  calls `speakers.identify(segment.audio)`, and both log/streaming output include
  speaker ID. New `--speaker-threshold` flag (default `0.75`). `SentencePoster`
  queue now carries `(text, speaker_id)` tuples; POSTed JSON includes `speaker_id`.
  **Why**: Implements speaker identification wiring - one registry per session,
  shared across streaming and log-file modes.

- **What**: `README.md` - added speaker identification section with algorithmic
  approach, output example, limitations, and threshold tuning guidance.
  **Why**: Keep documentation in sync with new feature.

### Fixed
- **What**: `speaker_id.py` - removed margin-over-runner-up check that was gating
  new-speaker creation. Now a segment joins the best-matching speaker as long as
  that match clears `--speaker-threshold`. Lowered `DEFAULT_SPEAKER_THRESHOLD`
  from `0.90` to `0.75`.
  **Why**: Margin check caused unbounded runaway when two centroids represented
  the same voice (best/second-best scores both high, margin near 0, failing check
  on every segment). Better to accept smaller bounded risk of occasional mismatches
  and lower threshold to prevent real-world misses.
  **Verification**: Reproduced runaway scenario (two near-duplicate centroids),
  confirmed fix prevents further speaker registration. Synthetic-reverb simulation
  shows new `0.75` threshold handles degraded audio while avoiding false merges.
  Unit tests updated and passing.

### Verified
- Full test suite: "Ran 42 tests / OK".
- Smoke tests: pitch estimation at 150.9Hz for 150Hz tone; 10-speaker registration;
  2-person conversation with interruptions → exactly 2 speakers.

---

## 2026-09-16 (Code review fixes and improvements)

### Fixed
- **What**: `transcribe.py` - `_FILLER_RE` regex (`^[\[(][^\[\]()]*[\])]$`) replaces
  hand-maintained `_FILLER_TOKENS` set for dropping non-speech filler lines.
  **Why**: Exact-match-after-`.upper()` misses wording/whitespace variants like
  `[ Silence ]`, `(speaking in foreign language)`, `[ Music ]`.
  **Verification**: Test suite "Ran 20/20 tests / OK" including regression case
  for whitespace variants.

- **What**: `transcribe.py` - new `_validate_url()` helper checks scheme is
  `http`/`https` at startup.
  **Why**: Missing scheme (e.g. bare `host/path`) passed startup and crashed on
  first POST with `ValueError: unknown url type`. Fail fast with clear message.
  **Verification**: Test suite + live subprocess tests: invalid schemes rejected;
  valid HTTP/HTTPS accepted.

- **What**: `transcribe.py` - `_post_sentence()` replaced with `SentencePoster`
  background thread reading bounded queue (`maxsize=256`), POSTing with up to 2
  retries (0.5s/1.0s backoff).
  **Why**: Synchronous posting (5s timeout) stalled transcription; VAD queue would
  fill and drop segments. No retry mechanism existed.
  **Verification**: Live test against local `http.server` - forced 2 failures then
  success; confirmed exactly 3 attempts and correct JSON body received once.

- **What**: `capture_windows.py`/`capture_macos.py` - `silence_timeout` (8s) now
  only fatal during startup (`_frames_captured == 0`). Later silence gaps print
  WARNING only.
  **Why**: (1) 8s timeout shorter than `transcribe.py`'s 15s NO_AUDIO_WARNING, so
  fatal path fired first. (2) More importantly, fatal check applied all session -
  on macOS, ScreenCaptureKit stops delivering buffers entirely during silence,
  so quiet meeting stretches killed the session mid-meeting.
  **Verification**: Code review + reasoning through call sites; not live-tested
  against extended muted meeting.

- **What**: `vad.py` `stop()` now flushes in-progress utterance after background
  thread is joined. `transcribe.py` `finally` block drains `seg_queue` right after
  `detector.stop()`.
  **Why**: Last utterance of every session was silently discarded.
  **Verification**: Live smoke test - seeded VoiceActivityDetector mid-utterance,
  called `stop()`, confirmed segment landed in queue.

- **What**: `transcribe.py` `--url` help text updated to document `session_id`.
  **Why**: Help text disagreed with actual POST body and README.
  **Verification**: Visual diff against README and module docstring.

- **What**: `requirements.txt` - added `audioop-lts>=0.2.1; python_version >= "3.13"`.
  **Why**: stdlib `audioop` removed in Python 3.13; `audioop-lts` provides
  drop-in replacement for 3.13+.
  **Verification**: Confirmed `audioop-lts` is standard backport; not tested on
  actual 3.13 interpreter (project venv is 3.11.15).

### Added
- **What**: `run_windows.ps1` - auto-creates `.venv` with interpreter-selection
  menu, validates existing venv's Python version (≥3.11) and dependency imports.
  **Why**: Windows side lacked `run_macos.sh`'s create/repair logic.
  **Verification**: Line-by-line manual review for syntax; not executed (no
  PowerShell in environment).

- **What**: Both `run_macos.sh` and `run_windows.ps1` now list devices and prompt
  for `--device` before mode selection.
  **Why**: Wrong device is #1 troubleshooting issue; launcher should guide users.
  **Verification**: Device listing invocation tested live; full interactive flow
  not run (needs TTY).

- **What**: `tests/test_audio_common.py` (6 cases) and expanded `tests/test_transcribe.py`
  (14 cases) - covering `downmix_to_mono`, `resample_to_target`, `_clean_text`,
  `_validate_url`.
  **Why**: Pure helpers lacked coverage; exact `(silence)` bug would have been
  caught immediately.
  **Verification**: Full test suite: "Ran 20 tests / OK".

### Changed
- **What**: `README.md` - launcher descriptions, device-prompt mention, silence-handling
  explanation, URL scheme requirement, streaming+retry details, new Tests section.
  **Why**: Keep docs in sync with feature changes.
  **Verification**: Proofread against actual code changes.

### Verified
- All Python files compile cleanly.
- Full test suite: "Ran 20 tests / OK".
- Live subprocess tests for URL validation and SentencePoster.
- Live VoiceActivityDetector flush test.

---

## 2026-09-15 (Session ID and streaming enhancements)

### Added
- **What**: `transcribe.py` - unique session ID generation via `uuid.uuid4()`
  at app start; passed to `_post_sentence()` and included in streaming POST body.
  Updated module docstring to document `session_id` field.
  **Why**: Allows external consumers to correlate all sentences from single
  transcription session even when multiple instances start simultaneously.
  **Verification**: Code reviewed; session_id generated once per invocation,
  passed to every POST.

### Changed
- **What**: `README.md` - expanded `--url` flag description to document
  `session_id` field in POST body.
  **Why**: Users need to know POST JSON fields.

---

## 2026-09-15 (Documentation updates)

### Changed
- **What**: `README.md` - added note under "Model choice" that project has been
  tested with `base.en-q5_1` on Google Meet (browser) and Teams desktop app.
  **Why**: Document real-world usage confirmation.
  **Verification**: Doc-only change; proofread for accuracy.

---

## 2026-09-15 (macOS venv setup improvements)

### Fixed
- **What**: `run_macos.sh` - added Python-version check (≥3.11) and dependency
  check (import numpy) after resolving `$PY`. If deps missing, runs pip install
  and re-checks before proceeding.
  **Why**: User ran `python -m venv .venv` (resolved to 3.9 on his system) then
  `python3.11 install -r requirements.txt` (typo missing `-m pip`). Result: venv
  existed but was wrong version with zero packages. Script previously only checked
  binary existence.
  **Verification**: Tested three states: (1) existing venv with 3.9 → prints error,
  exits before prompts; (2) existing venv with 3.11 but deps missing → installs,
  re-checks; (3) real healthy venv → passes silently.

### Changed
- **What**: `README.md` - macOS setup section now leads with `./run_macos.sh`,
  explains the `python3`→old-3.9-alias trap, recommends `python3.11 -m venv`
  over bare `python3`.
  **Why**: Document the specific mistake to help self-diagnose from docs alone.
  **Verification**: Proofread against actual script behavior.

---

## 2026-09-15 (Interpreter selection and interactive launcher)

### Changed
- **What**: `run_macos.sh` - replaced hard "virtualenv not found" error with
  interactive interpreter selection and auto-setup. Scans PATH for `python3.13`
  down through `python3.9`, plus bare `python3` and `python`; lists running
  interpreters; user picks one via numbered menu (or enters custom path). Creates
  venv using selected interpreter, installs dependencies.
  **Why**: On Igor's Mac, bare `python3`/`pip` resolve to old 3.9. Following
  README's `python3 -m venv .venv` silently produced broken environment. Manual
  interpreter choice removes the silent-wrong-default failure mode.
  **Verification**: Tested twice end-to-end with fake interpreters: (1) picking
  `python3.11` from list → venv created, deps installed, model/mode prompts reached;
  (2) picking "Other" with custom path → same result. Pre-existing `.venv` skips
  re-setup (unchanged).

- **What**: `README.md` - macOS Setup leads with `./run_macos.sh` as recommended
  path; explains auto-detection logic; manual fallback now `python3.11 -m venv`
  with warning not to rely on bare `python3`/`pip`.
  **Why**: Avoid recommending the exact command that caused this bug.
  **Verification**: Proofread against actual script behavior.

---

## 2026-09-15 (Streaming and launcher scripts)

### Added
- **What**: `transcribe.py` - new `--streaming`, `--url`, `--language` flags.
  With `--streaming`, no log file created; each sentence POSTed as `{"sentence",
  "model", "language"}` JSON instead. `parser.error()` enforces `--streaming`
  requires `--url`.
  **Why**: Sentences must reach external consumer in real time via HTTP, not
  written to disk.
  **Verification**: `py_compile` clean. Ran without `--url` → exits with clear
  error. Live test against local `http.server` → received correct JSON body.

- **What**: `run_macos.sh` and `run_windows.ps1` - interactive launchers. Both
  prompt for model selection from `pywhispercpp.constants.AVAILABLE_MODELS`,
  streaming vs. log-file mode, then URL (streaming) or output filename (log,
  default `transcripts/transcript.log`). Launch as background child with PID
  display; block on it via `wait` (bash) / `Wait-Process` (PowerShell).
  `trap`/`try-finally` forwards Ctrl+C.
  **Why**: Avoid users remembering/typing flags; provide one-command session start.
  **Verification**: `bash -n run_macos.sh` clean. Live test with stub `.venv/python`
  (echo argv) simulating both paths - log and streaming produced correct argv.
  `run_windows.ps1` hand-reviewed only (no PowerShell in environment).

### Changed
- **What**: `README.md` - project layout lists launchers; Usage section adds
  streaming example and "Launcher scripts" subsection; Flags table gets
  `--language`, `--streaming`, `--url` rows noting `--output` ignored under streaming.
  **Why**: Keep docs in sync with new flags/scripts per project convention.
  **Verification**: Proofread against actual flag names/help text.

---

## 2026-09-14 (macOS device filtering)

### Fixed
- **What**: `capture_macos.py` `list_loopback_devices()` now filters
  `SCShareableContent.applications()` to "regular" apps only via
  `NSWorkspace.runningApplications()` with `activationPolicy() == NSApplicationActivationPolicyRegular`.
  **Why**: Real `--list-devices` showed 33 entries including system helpers (Dock,
  Spotlight, Notification Center, Control Center, loginwindow, AutoFill helpers),
  raw "pid NNNNN" entries - impractical. Regular activation policy is the same
  signal macOS uses for Dock/Cmd-Tab membership.
  **Verification**: Tested real filtering: 112 total processes → 10 regular apps
  (Notes, Code, Outlook, Mail, Finder, Teams, Chrome, Preview, System Settings,
  Terminal). `py_compile` clean. **Not verified** end-to-end through
  `list_loopback_devices()` (needs Screen Recording permission).

---

## 2026-09-14 (macOS support via ScreenCaptureKit)

### Added
- **What**: `capture_macos.py` - new macOS backend using ScreenCaptureKit
  (`SCStream` + `SCStreamConfiguration.capturesAudio`, macOS 13+) via PyObjC.
  Exposes same public shape as Windows backend (`LoopbackCapture`,
  `list_loopback_devices`). Audio arrives as Float32 interleaved PCM via
  `SCStreamOutput` callback, extracted from `CMSampleBuffer`, converted to int16,
  downmixed/resampled via shared path.
  **Why**: Requested to add macOS support; confirmed ScreenCaptureKit over
  virtual-audio-device approach (native, no extra driver, one-time permission).
  **Verification**: Individually verified against real frameworks: `SCStream`
  start/stop/output selectors, `SCContentFilter` init, completion-handler async
  pattern confirmed working against real `SCShareableContent.getShareableContentWithCompletionHandler_`.
  Verified `ctypes.sizeof(AudioBufferList)` matches C layout. Ran
  `capture.list_loopback_devices()` end-to-end → got expected permission error.
  **Not verified**: actual sample-buffer → PCM path with real audio (needs
  permission + capture session on Igor's machine).

- **What**: `audio_common.py` - new shared module for `TARGET_RATE`/`SAMPLE_WIDTH`/
  `CHUNK_MS`, `NoAudioError`, `LoopbackDevice`, and `downmix_to_mono`/`resample_to_target`
  helpers used by both capture backends.
  **Why**: Avoid duplicating PCM downmix/resample logic between Windows and macOS.
  **Verification**: Covered by same `py_compile` pass; behavior tested via
  shared helpers.

### Changed
- **What**: `capture_windows.py` - refactored to use shared `audio_common.py`
  constants/helpers/types.
  **Why**: Implement "Duplicated logic across paths is deduped" principle.
  **Verification**: Same as above.

### Verified
- All touched files `py_compile` cleanly.
- Frameworks and callback patterns verified against real PyObjC.
- End-to-end permission error path tested.
