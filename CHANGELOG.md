# Changelog

Newest entry on top. Each entry documents code changes, grouped as `### Added` / `### Changed` / `### Fixed` / `### Verified`.

Each bullet includes:
- **What**: the concrete change (file(s)/function(s)/flag(s) touched).
- **Why**: the reasoning or root cause.
- **Verification**: what was tested to confirm it works.

---

## 2026-09-28 10:30:33+02:00 -> 2026-09-28 10:49:31+02:00 (duration: 18m 58s | tokens: not available)
### Added
- **What**: new `run_windows.cmd` - a double-click entry point for Windows
  users who don't know PowerShell. It calls `Unblock-File` on
  `run_windows.ps1` (clears the "Mark of the Web" a browser/zip download
  adds, which otherwise makes PowerShell refuse to run it with "is not
  digitally signed"), runs it with `-ExecutionPolicy Bypass` (scoped to that
  one invocation only - nothing system-wide is changed), and ends with an
  unconditional `pause`, so the window stays open even in the one failure
  mode `run_windows.ps1`'s own fix below can't cover: the script failing to
  load at all (execution policy, a parse error, PowerShell missing).
  **Why**: `.ps1` files aren't run by double-click on Windows (they open in
  a text editor) - non-technical users had no way to start the app without
  first learning PowerShell's execution-policy and Mark-of-the-Web quirks,
  both classic causes of "it opens and closes instantly" for a freshly
  downloaded script.
  **Verification**: new `tests/test_windows_launcher_static.py`
  (`CmdWrapperTests`) - the wrapper targets the real `.ps1` file, uses a
  scoped `-ExecutionPolicy Bypass` (and never a persistent
  `Set-ExecutionPolicy`), calls `Unblock-File`, and its last line is
  `pause`. Mutation checks: removing the trailing `pause`, and swapping the
  scoped bypass for a persistent `Set-ExecutionPolicy`, each fail their
  test. Not run for real (no PowerShell/cmd.exe in this environment).
- **What**: `README.md` - new "Windows quick start (never used PowerShell?
  start here)" numbered walkthrough under Setup > Windows, written for
  someone who has never run a script before: install Python (with the
  "Add python.exe to PATH" checkbox called out, a common miss), install the
  VC++ Redistributable, unzip the project outside `Downloads`, double-click
  `run_windows.cmd`, what the Windows SmartScreen "Windows protected your PC"
  prompt is and that it's expected, how to stop the app (Ctrl+C) and close
  the window (Enter), and where to look if it closes early. New
  Troubleshooting entry "The window opens and closes immediately (Windows)"
  explaining the two most common underlying causes in plain language
  (execution policy / Mark of the Web) with the fix for each, for the case
  where a user bypasses `run_windows.cmd` and runs `run_windows.ps1`
  directly. Module table and Tests section updated for `run_windows.cmd`.
  **Why**: requested - step-by-step Windows instructions at a non-technical
  level.

### Fixed
- **What**: `run_windows.ps1` - the entire script body now runs inside one
  `try { ... } catch { ... }`. The three internal `Write-Error "..."; exit 1`
  hard-stops became `throw "..."` (a bare `exit` inside the try would have
  skipped the catch below); two `Assert-Success` calls were added right
  after the two virtualenv-creation commands (which could previously fail
  silently - a non-zero exit code from a native command doesn't stop a
  PowerShell script on its own, unlike a PowerShell-native error) so a
  broken interpreter/venv install now fails with an immediate, specific
  message instead of running further and failing more confusingly later.
  Every path out of the script - success, a `throw`, or any other uncaught
  error - now falls through to one place: print why (in red for an error,
  yellow for `transcribe.py` exiting non-zero, plain for a normal end),
  then `Read-Host "Press Enter to close this window"`, then `exit` with the
  matching code.
  **Why**: requested - the app must show why it's closing and wait for
  acknowledgment before the window closes, for every way it can end, not
  just the ones that happened to already print something. Previously, a
  setup failure early in the script (bad interpreter, failed venv/pip
  install, `Write-Error` under `$ErrorActionPreference = "Stop"` throwing
  before its `exit 1` line even ran) would end the script with no pause at
  all, which - combined with `run_windows.cmd`'s new double-click flow, or
  any host that closes the console on process exit - is exactly what
  "started and closed immediately" looks like from the user's side.
  **Verification**: new `tests/test_windows_launcher_static.py`
  (`WindowNeverClosesSilentlyTests`, `PowerShellBalanceTests`) - a real
  string/comment-aware brace/paren/bracket balance scan (not a naive
  substring count, which a stray quoted `'"'` elsewhere in the file already
  fooled once during this change - regression-tested directly); the whole
  body is wrapped in one try/catch; all three original hard-stop messages
  still exist as `throw`s inside it with no bare `exit` inside the try; the
  script ends with `Read-Host "Press Enter..."` then `exit`; exactly the
  expected number of `Assert-Success` calls; no reintroduced
  `-AsSecureString`/`TRANSCRIBER_STORE_URL` (regressions this file has
  already had once each). Mutation checks: an unbalanced brace, and
  reverting the catch block to a bare `Write-Host` + `exit 1` with no
  pause, each fail their respective test. Not run for real (no PowerShell
  in this environment) - the balance scan is this session's best available
  substitute for an actual parse check.

### Verified
- `.venv/bin/python -m unittest discover -s tests` -> "Ran 159 tests / OK"
  (148 before this entry's test work).
- **Not verified**: neither `run_windows.cmd` nor `run_windows.ps1` was run
  on real Windows/PowerShell - this environment has neither. The static
  checks above catch a real class of regression (imbalance, a bare `exit`
  bypassing the pause, a missing `pause`/`Unblock-File`/scoped bypass) but
  cannot confirm the scripts parse and run correctly end-to-end.

---

## 2026-09-28 09:38:59+02:00 -> 2026-09-28 10:29:48+02:00 (duration: 50m 49s | tokens: not available)
### Changed
- **What**: `settings.py` - removed `STORE_URL_KEY`/`TRANSCRIBER_STORE_URL`
  from the `SETTINGS` registry entirely (`save`/`get`/`set` now reject it as
  an unknown key, and `show`/`describe` never list it, even if an old `.env`
  still has the line from before this change). `run_macos.sh` and
  `run_windows.ps1` no longer route the store (upload) URL through
  `resolve_setting`/`Resolve-Setting`; it's asked with a plain non-empty
  prompt every session, same as before `.env` support existed. Module/README
  wording updated to describe only email, model, streaming URL and the API
  key header as persisted.
  **Why**: requested - the store URL (and the client name/topic, already
  never persisted) must always be asked fresh, never remembered.
  **Verification**: new `StoreUrlIsNeverASettingTests` in
  `tests/test_settings.py` (key not registered, saving it raises, a
  hand-edited leftover value is never read back); `tests/test_launcher.py`
  updated so a second run with everything else reused still prompts for the
  store URL, and asserts it's absent from `.env` afterwards. Mutation check:
  routing the prompt back through `resolve_setting` fails 10 of the 18
  affected tests.
- **What**: `run_macos.sh`'s `resolve_api_key` now reads the API key with
  plain `read -r -p` (was `read -r -s -p`, which suppresses echo);
  `run_windows.ps1`'s `Resolve-ApiKey` now uses plain `Read-Host` (was
  `Read-Host -AsSecureString` decoded via `NetworkCredential`).
  **Why**: requested - the API key prompt should be visible/typed like any
  other answer, not a hidden password-style field.
  **Verification**: new `test_api_key_input_is_visible_not_hidden` (asserts
  neither launcher's source still hides the prompt - `read -s`/
  `AsSecureString` - since a subprocess-piped stdin can't itself demonstrate
  terminal echo either way); the existing key-never-on-the-command-line
  test still passes unchanged. Mutation check: reinstating `read -s` fails
  the new test.
- **What**: `_upload_log_file()` in `transcribe.py` now sends the log as
  multipart field `log` (was `file`); the filename was already the log's own
  name (`path.name`) and is unchanged. `--store-url`'s help text and the
  module docstring and README flags table updated to match.
  **Why**: requested field name for the uploaded binary.
  **Verification**: `UploadLogFileTests.test_uploads_real_file_content_as_multipart_field`
  updated to assert `name="log"`. Mutation check: reverting the field name
  to `"file"` fails that test.

### Verified
- `.venv/bin/python -m unittest discover -s tests` -> "Ran 148 tests / OK"
  (144 before this entry's test work). `py_compile` on every changed module,
  `bash -n run_macos.sh` clean, and `run_windows.ps1` re-checked for balanced
  braces/parens/brackets (still no PowerShell available to actually run it).

---

## 2026-09-25 11:25:32+02:00 -> 2026-09-25 14:40:08+02:00 (duration: 3h 14m 36s | tokens: not available)
### Added
- **What**: new `settings.py` module: a stdlib-only `.env` store (`load`,
  `save`, `check_value`) plus the value validators (`validate_url`,
  `validate_email`, `parse_auth_header`) shared with `transcribe.py`. Keys:
  `TRANSCRIBER_EMAIL`, `TRANSCRIBER_MODEL`, `TRANSCRIBER_URL`,
  `TRANSCRIBER_STORE_URL`, `TRANSCRIBER_AUTH_HEADER`. `save()` validates
  before writing, keeps every other line of the file untouched, and writes
  through a temp file created with `O_EXCL` at mode 0600 before an atomic
  `os.replace` - the temp file is removed first if a stale one is left over
  from a crash, so a secret is never written into a file with looser
  permissions than the final one. `mask()` shows a saved API key's header
  name plus at most its last 4 characters. Exposes a small CLI
  (`show` / `get KEY` / `set KEY`, value passed via the `SETTINGS_VALUE`
  environment variable, never argv) that the launchers call as a subprocess.
  **Why**: requested - remember email, model, endpoint URLs and the API key
  across runs so only missing/changed values are asked for; client
  name/topic explicitly must never be stored.
  **Verification**: new `tests/test_settings.py` (44 tests - parsing
  comments/quotes/CRLF/BOM, duplicate-key handling, permission on create and
  on an existing loosely-permissioned file, a stale-temp-file never holding
  the written secret, rejected values leaving the file untouched, the CLI's
  three commands and exit codes). Mutation checks: skipping the
  `check_value()` call before writing, and creating the file at 0644 instead
  of 0600, each fail multiple tests.
- **What**: `transcribe.py` gains `--topic` and `--email` flags. New
  `_header_value()` (flattens control characters/line breaks to spaces and
  `|` to `/`) and `_session_header()` build the first log line:
  `# Transcript started <ISO time> | model=... | language=... | topic=... |
  email=...` (topic/email fields omitted when blank), replacing the old
  `| model=...`-only line; used in both log-file and streaming mode (the
  streaming console line keeps its `| streaming to <url>` suffix). `--email`
  is validated at startup (`settings.validate_email`); module docstring
  updated with an example.
  **Why**: requested - the log's first line should also record language,
  client/topic and the user's email, and a hostile topic string must not be
  able to forge a second header/transcript line.
  **Verification**: new `HeaderValueTests`, `SessionHeaderTests`,
  `MainArgumentTests` in `tests/test_transcribe.py`, and an end-to-end
  assertion in `tests/test_shutdown.py` checking the real first line of both
  the log file and the uploaded body against a regex. A version that only
  stripped bare `\n` (not other control characters/`|`) fails the
  hostile-topic test.
- **What**: `run_macos.sh` (and the equivalent for `run_windows.ps1`) now
  read/write `.env` via `settings.py` at startup: lists what's saved
  (secrets masked) and asks once "Change any of the saved settings?"; `n`
  reuses every saved value as-is and only asks for what's genuinely missing
  (plus the mode-specific URL) and the topic; `y` re-prompts each needed
  setting with the saved value as the default (Enter keeps it, `none` clears
  the API key). The model menu, stream/store URL prompts and the API-key
  prompt (`read -s`, PowerShell `-AsSecureString`) all route through this;
  a newly entered value is saved immediately via `settings.py set` and
  invalid answers are re-asked (the error from `settings.py` is shown). The
  client/topic prompt is always asked and is never passed to `settings.py`.
  The resolved API key is exported as `TRANSCRIBER_AUTH_HEADER` in the
  child's environment rather than appended to `transcribeArgs`, so it never
  appears in the printed "Starting: ..." line or the process list.
  **Why**: requested - ask on startup, store the repeatable answers, ask
  again only for what's missing or changed, and never store the topic;
  reusing the existing `TRANSCRIBER_AUTH_HEADER` environment-variable path
  (rather than `--auth-header`) keeps the key off the command line, matching
  how the app already prefers that variable over the flag.
  **Verification**: new `tests/test_launcher.py` (14 tests) runs the real
  `run_macos.sh` against a copied `settings.py` and a stand-in `transcribe.py`
  that reports its argv/environment, feeding answers over stdin: first run
  saves everything but the topic; a second run reuses saved settings and
  asks only for the topic; "change" re-prompts with saved defaults;
  streaming vs. log-file/upload save into separate URL keys; upload stays
  opt-in even with a saved store URL; a removed key is not re-asked for; the
  `.env` file is mode 0600 and the raw key never appears in the launcher's
  stdout/stderr or the child's argv; invalid answers (bad email, bad model
  number, bad store URL, malformed key) are rejected and re-asked; other
  lines already in `.env` survive a save. Mutation checks: ignoring the
  "change saved settings?" answer, and putting the key on the argv instead
  of the environment, each fail multiple tests. `run_windows.ps1` was
  proofread and hand-verified for brace/paren/bracket balance only - **not
  run** (no PowerShell here); it mirrors the bash flow line-for-line
  including the environment-variable handoff for the key.
- **What**: `README.md` - `settings.py` added to the module table; new
  "Saved settings (`.env`)" section (keys, file format, permissions, that
  the key never reaches argv, that `transcribe.py` itself never reads
  `.env`); new "First line of the log" section with an example line; new
  `--topic`/`--email` flag rows; Tests section mentions the `.env` store,
  the log header and the real `run_macos.sh` prompt-flow test.

### Verified
- `.venv/bin/python -m unittest discover -s tests` -> "Ran 144 tests / OK"
  (84 before this entry's test work). `py_compile` on every changed module,
  `bash -n run_macos.sh` clean.
- Real CLI: `python -m transcribe --email nope` rejected with
  `--email must look like name@example.com`; `--help` lists `--topic` and
  `--email`.
- **Not verified**: `run_windows.ps1` (no PowerShell available); a live
  session against a real store/streaming endpoint with real topic/email
  values; Windows file permissions on `.env` (the code falls back to the
  folder's inherited permissions there, as documented in the README).

---

## not available (not captured at turn start) -> 2026-09-24 15:30:06+02:00 (duration: not available | tokens: not available)
### Fixed
- **What**: `transcribe.py` - the `--store-url` upload now sends only the
  current session. `TranscriptWriter` records `start_offset` (the file size
  before this session's header is written) and `_upload_log_file()` sends
  `data[start_offset:]` (the whole file if it shrank below the offset).
  **Why**: the log is append-only and can already hold earlier sessions (one
  existing log held 7); every upload re-sent all of them.
  **Verification**: `SessionUploadTests`, `TranscriptWriterTests`, and an
  end-to-end test pre-populating the log with an old session and asserting it
  is absent from the uploaded body. With the offset forced to 0 the end-to-end
  test fails.
- **What**: `transcribe.py` - upload at most once and time-boxed. New
  `LogUploader` (atomic once-only claim, POST on a daemon thread, caller
  waits at most `max_wait`); the normal path uses 15s (2 attempts), the
  second-Ctrl+C handler one 3s attempt with a 4s limit. A third Ctrl+C during
  the upload finds it claimed and exits immediately.
  **Why**: the force-exit handler stayed armed while the normal-shutdown
  upload ran, so repeated Ctrl+C started additional uploads, and a forced
  exit could take 10s+ (two 5s attempts; DNS lookups aren't covered by the
  socket timeout).
  **Verification**: `LogUploaderTests` (once-only, second call during an
  in-flight upload returns immediately, gives up after `max_wait` against a
  hanging server) and `tests/test_shutdown.py` (hung store: forced exit
  finishes in well under 12s; third Ctrl+C exits in under 3s; exactly one
  request received in each). Removing the once-only claim fails three of
  them.
- **What**: `run_windows.ps1` now runs the app in the foreground
  (`& $py @transcribeArgs; exit $LASTEXITCODE`) instead of `Start-Process` +
  `Wait-Process` with a `Stop-Process -Force` in `finally`; header comment
  and README layout line updated.
  **Why**: Ctrl+C ran the `finally`, which force-killed the app mid-shutdown,
  losing the last segment and the `--store-url` upload (same class of bug as
  the earlier macOS launcher fix); `-ArgumentList` also doesn't quote
  arguments containing spaces (URLs, log paths).
  **Verification**: proofread only - **not run** (no PowerShell here).
- **What**: `speaker_id.py` - `SpeakerRegistry._update_centroid`: a
  pitch-less (quiet) segment that matches a pitched centroid no longer
  modifies it; a pitch-less centroid is replaced by the first pitched
  segment that matches it; otherwise the running mean is unchanged.
  **Why**: quiet matches were averaged in with a zero pitch block - five of
  them shrank the pitch part ~5x and the speaker's next normal segment
  scored 0.68 and became a new person. Averaging only their timbre was also
  tried and rejected: it drifted the centroid toward noisy quiet-speech
  timbre (0.837 after 5 quiet segments, 0.79 after 60).
  **Verification**: new `test_quiet_segments_do_not_dilute_a_speakers_pitch`
  (centroid bit-identical after 60 quiet updates; next normal segment still
  matches).
- **What**: `speaker_id.py` - similarity is now chosen per centroid
  (`_similarities`): full-vector only when both the segment and the centroid
  have pitch, timbre-only otherwise, ranked by margin over each
  comparison's own bar.
  **Why**: a speaker whose first segment was quiet was registered without
  pitch, so any normal segment scored at most ~0.82 against them - below the
  0.84 default - and always became a second person.
  **Verification**: new
  `test_speaker_first_seen_quietly_is_matched_by_their_normal_speech`.
- **What**: `speaker_id.py` - the timbre-only bar is now
  `threshold - MASKED_THRESHOLD_OFFSET` (0.09, giving the previous 0.75 at
  the default) via `SpeakerRegistry.masked_threshold`, replacing the fixed
  `MASKED_MATCH_THRESHOLD`.
  **Why**: with `--speaker-threshold` below 0.75, quiet segments faced a
  stricter bar than normal ones.
  **Verification**: `test_masked_threshold_follows_the_main_threshold`.
- **What**: `vad.py` - the pre-roll buffer is cleared when a segment is
  force-split at `max_segment_s`.
  **Why**: it still held the tail of the segment just emitted, so ~200ms
  around every 28s split was transcribed twice.
  **Verification**: `tests/test_vad.py` forced-split test; reverting the fix
  fails it.
- **What**: `capture_windows.py` - a read failure while `stop()` is closing
  the stream is no longer stored as a capture error.
  **Why**: `stop()` closes the stream precisely to unblock the reader, so the
  debug CLIs' `cap.stop(); cap.raise_if_failed()` reported a bogus error.
  **Verification**: `tests/test_capture_windows.py` (fake `pyaudiowpatch`)
  covers both the stop case and a genuine failure still being reported;
  reverting the fix fails the stop test. Not run against real WASAPI.
- **What**: `transcribe.py` - `SentencePoster`: the overflow docstring now
  says the new sentence is dropped (it claimed the oldest was); `stop()` no
  longer blocks forever on a full queue (`put(None, timeout)`) and warns with
  the approximate number of undelivered sentences if the worker is still
  busy after the timeout.
  **Verification**: `SentencePosterTests` against a hanging local server.

### Added
- **What**: per-segment error handling in `run()`: a failing
  `_process_segment` (whisper error, full disk...) prints an error and the
  session continues; `MAX_CONSECUTIVE_SEGMENT_ERRORS` (5) failures in a row
  end it with exit code 1. The shutdown drain also guards each segment.
  **Why**: one bad segment used to end the whole meeting's transcription.
  **Verification**: `test_a_failing_segment_does_not_end_the_session`
  (2 failures then 6 successes, all in the log) and
  `test_repeated_segment_failures_stop_the_session_with_an_error`.
- **What**: `audio_common.DropWarner`, used by `vad.py` (segments) and both
  capture backends (chunks): a full downstream queue is now counted and
  reported (first drop immediately, then at most one warning per 5s).
  **Why**: overload used to lose audio/speech silently.
  **Verification**: `DropWarnerTests` (fake clock) and a VAD drop test.
- **What**: `transcribe.py` - `--auth-header 'Name: value'` (or the
  `TRANSCRIBER_AUTH_HEADER` environment variable) added to every POST
  (streaming and upload); validated at startup (header-name token, no line
  breaks). Plain `http://` to a non-loopback host prints a warning.
  Multipart `filename`/`name` values are escaped (`"`, CR, LF) so a crafted
  log name can't break out of the Content-Disposition header.
  **Why**: hardening - transcripts were sent unauthenticated and the upload
  could not carry credentials.
  **Verification**: `ParseAuthHeaderTests`, `InsecureTransportWarningTests`,
  multipart escaping test, and header-delivery tests on both POST paths.
- **What**: tests - `tests/test_shutdown.py` + `tests/shutdown_harness.py`
  (real `transcribe.run()` in a subprocess with capture/VAD/whisper faked:
  first/second/third Ctrl+C, upload on exit, hung store, failing segments),
  `tests/test_vad.py`, `tests/test_capture_windows.py`, plus additions to
  `test_transcribe.py`, `test_audio_common.py`, `test_speaker_id.py`.
  **Why**: the shutdown/upload/force-exit paths and `vad.py` had no
  automated coverage; earlier verification of them was throwaway scripts.

### Changed
- **What**: `requirements.txt` - upper version bounds one major above what
  was developed against (numpy 2.4, silero-vad 6.2, pywhispercpp 1.5, pyobjc
  12.2, PyAudioWPatch 0.2, audioop-lts 0.2).
  **Why**: a future breaking release can no longer silently break a fresh
  install.
  **Verification**: `pip install --dry-run -r requirements.txt` resolves
  against the existing environment.
- **What**: `README.md` - flags table (`--store-url` now session-only with
  its limits, new `--auth-header`), speaker-id notes (timbre bar follows the
  threshold; quiet matches don't alter voiceprints), launcher line, Tests
  section.

### Verified
- `.venv/bin/python -m unittest discover -s tests` -> "Ran 84 tests / OK"
  (50 before this entry's test work), run twice in a row (~20s each) to check
  the subprocess tests aren't flaky. `py_compile` on all modules and
  `bash -n run_macos.sh` clean.
- Measured before changing anything: whisper.cpp inference does **not**
  block other Python threads (a background ticker kept running during a
  real 24.5s transcription, which took 0.37s) and real log timestamps stay
  within ~2s of the file's last write, so neither was changed.
- **Not verified**: `run_windows.ps1` and the Windows capture change on real
  Windows; a real receiving service for the upload; live capture + whisper.

---

## not available (not captured at turn start) -> 2026-09-24 11:04:08+02:00 (duration: not available | tokens: not available)
### Added
- **What**: `transcribe.py` - new `--store-url URL` flag. When set (log-file
  mode only), the finished local log file (`--output`) is POSTed to the URL
  as `multipart/form-data` (file field `file`, filename = the log's name,
  plus an `X-Session-Id` header) at shutdown. New helpers
  `_build_multipart_body()` (stdlib multipart encoder) and
  `_upload_log_file()` (reads the file from disk, 5s timeout, one retry,
  failures print a warning and never raise). Called from two places in
  `run()`: after `writer.close()` in the normal shutdown path, and inside
  the second-Ctrl+C `_force_exit` handler just before `sys.exit(1)`.
  `_validate_url()` gained an optional `flag` argument so the http(s) check
  reports `--store-url` instead of a hardcoded `--url`. `main()` rejects
  `--store-url` combined with `--streaming` (streaming mode writes no local
  file). Module docstring updated.
  **Why**: requested - keep a local log file as today, and additionally
  ship it to a remote store when the session ends, including on a forced
  exit. The forced-exit upload reads the file straight from disk instead of
  going through the `TranscriptWriter`: the shutdown drain thread may still
  be writing through it, and `TranscriptWriter` fsyncs after every line, so
  the on-disk file is always complete up to the last written line. The
  upload is deliberately not attempted on startup-failure exits (capture or
  model failed to start) since nothing was transcribed. Multipart was
  chosen over a raw body as the wire format on request.
  **Verification**: `.venv/bin/python -m unittest discover -s tests -q` ->
  "Ran 50 tests / OK" (46 existing + 4 new in `tests/test_transcribe.py`:
  multipart body shape, a live upload of a real temp file to a local
  `http.server` checking content type / filename / content / session
  header, a missing file warning instead of raising, and the flag name in
  the URL-validation error). End-to-end (scratch, not in repo): ran the
  real `transcribe.main()` -> `run()` with capture, VAD and whisper faked
  and a local HTTP server as store; (1) one SIGINT: exit code 0, log
  uploaded with the header line and the final trailing segment; (2) two
  SIGINTs while the trailing transcription was stuck: "Force-stopping"
  printed, exit code 1 after ~3s, log uploaded (without the stuck segment,
  as expected). **Not verified**: a real receiving service, and the
  Windows launcher change (no PowerShell available).

- **What**: `run_macos.sh` and `run_windows.ps1` - in log-file mode, before
  the filename prompt, ask "Upload the log file to a remote location when
  the session ends? [y/N]"; on yes, prompt (re-prompting on empty) for the
  store URL and pass `--store-url`. Streaming mode is unchanged. `README.md`
  gained a `--store-url` row in the flags table and a mention in the
  launcher blurb.
  **Why**: the requested select-upload / enter-URL / then choose local file
  sequence, kept in both launchers for parity.
  **Verification**: `bash -n run_macos.sh` clean; ran the real script with
  piped answers against a stub app for three cases (yes + URL, default no,
  empty URL then a valid one) and checked the resulting `Starting:` command
  line each time. `run_windows.ps1` was proofread only, not executed.

---

## not available (not captured at turn start) -> 2026-09-21 10:04:34+02:00 (duration: not available | tokens: not available)
### Fixed
- **What**: `run_macos.sh` now ends with `exec "$PY" "${TRANSCRIBE_ARGS[@]}"`
  instead of starting the app with `&`, capturing `$!`, and then
  `trap 'kill -INT "$PID"' INT TERM` + `wait "$PID"`. The startup message now
  prints the launcher's own PID (unchanged by `exec`) and mentions that a
  second Ctrl+C force-quits. Header comment and `README.md` (layout listing,
  "Launcher scripts" blurb) no longer describe the launcher as running the
  app "in the background".
  **Why**: root cause was not the queue. A job started with `&` from a
  non-interactive shell script (no job control) has SIGINT set to "ignored"
  before `exec`; CPython honors that and never installs its
  `KeyboardInterrupt` handler (measured: the `&` child reports
  `signal.getsignal(SIGINT) == 1` i.e. `SIG_IGN`, a foreground child reports
  `default_int_handler`). So neither the terminal's Ctrl+C nor the trap's
  forwarded `kill -INT` ever reached `transcribe.py`'s shutdown path, while
  the launcher's `wait` returned as soon as its own trap fired (it was the
  last line of the script) - the prompt came back and the orphaned app kept
  capturing and transcribing new audio. Running the app as the foreground
  process gives it the terminal's SIGINT directly, so the existing graceful
  shutdown (and the second-Ctrl+C force-exit) work as intended.
  **Verification**: copied the real `run_macos.sh` next to a stub
  `transcribe.py` (same `KeyboardInterrupt` structure) and a symlinked
  `.venv/bin/python`, fed the prompts on stdin, and simulated a terminal
  Ctrl+C with `os.killpg(pgid, SIGINT)`. Original script: launcher exited
  (code 130), app process still alive, never printed "Stopping...". Fixed
  script: app printed "Stopping...", exited, launcher exit code 0.
  `bash -n run_macos.sh` clean; `.venv/bin/python -m unittest discover -s
  tests -q` -> "Ran 46 tests / OK" (no test covers the launcher).
  **Not verified**: a real run with live ScreenCaptureKit capture and
  whisper.cpp; `run_windows.ps1` also starts the app as a separate process
  but was not changed or run here (no PowerShell, and no reported problem
  on Windows).

---

## not available (not captured at turn start) -> 2026-09-16 20:45:19+02:00 (duration: not available | tokens: not available)
### Fixed
- **What**: `speaker_id.py` `DEFAULT_SPEAKER_THRESHOLD` raised from `0.75`
  to `0.84`. Added a new, separate `MASKED_MATCH_THRESHOLD = 0.75`
  constant; `SpeakerRegistry.identify()` now compares `best_score` against
  whichever threshold matches the comparison actually used -
  `DEFAULT_SPEAKER_THRESHOLD` for a normal full-vector (pitch + timbre)
  match, `MASKED_MATCH_THRESHOLD` for the timbre-only ("masked") match used
  when a segment has no reliable pitch. `README.md`'s flags table,
  "Speaker identification" section, and both `--threshold` example commands
  updated to match.
  **Why**: reported directly - at `0.75`, too many genuinely different
  speakers were being matched to an existing centroid instead of
  registering as their own "Person N", i.e. not being recognized as
  separate people. Raising the threshold requires closer similarity before
  assigning a match, reducing that false-merge rate. A single global value
  would have regressed a previous fix, though: the timbre-only masked
  comparison (used for quiet/whispered/very-short segments with no
  reliable pitch) has no pitch dimension to help confirm a match, so even a
  correct same-speaker match on that path measured only ~0.78-0.82
  similarity in testing - below `0.84`, which would have made quiet
  segments mint a new speaker far more often. A separate, lower threshold
  for that specific comparison path (kept at the previous, already-
  calibrated `0.75`) avoids that regression while still applying the
  stricter `0.84` bar to normal speech.
  **Verification**: `.venv/bin/python -m py_compile speaker_id.py` clean.
  Measured directly against the patched module: two formant-shaped
  synthetic speakers at the same pitch with moderately different timbre
  scored `0.804` full-vector similarity - above the old `0.75` default
  (would have merged) and below the new `0.84` (correctly separates) - now
  a permanent regression test,
  `SpeakerRegistryTests.test_moderately_similar_voices_are_kept_separate_at_the_raised_threshold`.
  Confirmed the existing `test_quiet_unvoiced_segment_matches_speaker_by_timbre`
  (masked-cosine path, ~0.78-0.79 similarity) fails without the dual-
  threshold split (matches a new speaker instead of the correct one) and
  passes with it. Full suite: `.venv/bin/python -m unittest discover -s
  tests -q` -> "Ran 46 tests in 0.104s / OK" (45 pre-existing + 1 new).
  **Not verified**: no real multi-speaker audio in this environment -
  `0.84` is used as given (a real-world-calibrated value), not
  independently re-derived here; what's newly verified is the masked-path
  split that keeps it from regressing quiet-segment matching, not a claim
  that `0.84` itself is optimal for every mic/room.

### Changed
- **What**: `CHANGELOG.md` - removed process narration from prior entries
  (references to which internal review pass or planning step produced a
  finding, and phrases like "this session"/"this turn") per the updated
  `project checklist` "Before done" rule that changelog entries should cover code
  changes only. All technical content (What/Why/Verification) was left
  intact; only the framing naming a review/planning pass as the source was
  removed. Verification-environment caveats (e.g. why a live hardware test
  wasn't possible) and the changelog's own required "why a field is 'not
  available'" disclosures were kept as-is, since both are required
  elsewhere in `project checklist`'s Verification/Before-done sections rather than
  being process narration.
  **Why**: direct request, following the `project checklist` update adding
  "Changelog shouldn't include details about model, skills just the code
  changes."
  **Verification**: read the full file end to end; grepped it afterward
  for the specific phrases removed (tool names, "this/prior/current turn",
  "the review turn") to confirm none remained outside the verification-
  caveat/data-provenance exceptions noted above.

### Verified
- `git status --short` reviewed - `CHANGELOG.md`, `README.md`,
  `speaker_id.py`, `tests/test_speaker_id.py` changed as expected (plus
  `project checklist`, edited directly by hand, not part of this change); nothing
  committed (not requested).

---

## not available (not captured at turn start) -> 2026-09-16 19:46:41+02:00 (duration: not available | tokens: not available)
### Fixed
- **What**: `transcribe.py`'s `run()` shutdown path (`finally` block). The
  trailing-segment drain (transcribing whatever final utterance
  `detector.stop()` flushed onto `seg_queue`) now runs on a daemon thread
  (`shutdown-drain`) that the main thread waits on via a `while
  drain_thread.is_alive(): drain_thread.join(timeout=0.2)` poll, instead of
  calling `_process_segment`/`model.transcribe()` directly on the main
  thread. A `signal.signal(signal.SIGINT, _force_exit)` handler is armed at
  the top of the `finally` block (right after the first `"\nStopping..."`
  print), replacing the default handler for the remainder of shutdown; it
  prints a message and calls `sys.exit(1)`.
  **Why**: root-caused the report - the first Ctrl+C was already being
  handled correctly (it's what produces the `"Stopping..."` line the user
  saw), but whisper.cpp's `transcribe()` is a blocking native call that
  never checks for pending Python signals. CPython only runs a signal
  handler (default or custom) when the *main* thread reaches a bytecode
  checkpoint; while the main thread was itself inside `model.transcribe()`
  for the flushed final segment, no amount of further Ctrl+C presses could
  do anything until that call returned on its own - hence "Ctrl+C doesn't
  stop it, have to force-quit". The `ggml_metal_free: deallocating` line the
  user saw only after force-stopping is whisper.cpp's own native cleanup
  output, consistent with the process having been stuck mid-`transcribe()`
  the whole time. Moving that call onto a daemon thread means the main
  thread is only ever blocked on a short-timeout `Thread.join()`, which
  *does* return to the interpreter's checkpoint frequently - so a second
  Ctrl+C can now always get through and force an immediate, clean exit
  (daemon threads don't block process exit, so the stuck native call is
  simply abandoned when the process exits). The first Ctrl+C's behavior
  (attempt a graceful stop, flush and transcribe the final segment, close
  files) is unchanged - this only adds a working escape hatch for when that
  graceful path is slow or stuck, plus makes the existing bounded waits
  later in the same `finally` block (`cap.stop()`, `poster.stop()`)
  reachable by the same second-press handler instead of only being escapable
  via an external force-kill.
  **Verification**: `.venv/bin/python -m py_compile transcribe.py` clean.
  Full suite: `.venv/bin/python -m unittest discover -s tests -q` -> "Ran 45
  tests in 0.087s / OK" (no existing test exercises this signal-driven
  shutdown path, so this alone doesn't cover the fix). Isolated mechanism
  repro (scratchpad, not part of the repo, since driving a real
  ScreenCaptureKit+whisper.cpp hang isn't practical in this environment):
  spawned a subprocess running the same
  daemon-thread-plus-join-polling-plus-re-armed-SIGINT-handler shape against
  a 10-second stand-in blocking call, sent one SIGINT once the child
  confirmed it was blocked, and measured the child exiting in 0.00s (exit
  code 1, printed its "force exit" message) instead of waiting out the 10s -
  confirming the escape hatch actually preempts a long blocking call instead
  of only working when nothing is stuck. **Not verified**: an actual live
  run against real ScreenCaptureKit capture + whisper.cpp inference (no
  audio hardware/permission loop available in this environment) - the user
  should confirm a single Ctrl+C now cleanly stops a real session, and that
  a second Ctrl+C escapes promptly if the first ever seems stuck again.

---

## not available (not captured at turn start) -> 2026-09-16 17:32:26+02:00 (duration: not available | tokens: not available)
### Fixed
- **What**: `speaker_id.py` `PITCH_WEIGHT` lowered from `3.0` to `1.0`.
  Pitch was ~82% of the voiceprint's squared norm at `3.0` (pitch/mfcc-mean/
  mfcc-std now split roughly evenly at `1.0`, each ~33%).
  **Why**: this is a live variant of the
  already-fixed "Person N never stops incrementing" bug: a single
  speaker's ordinary prosody (pitch rising for a question, flatter when
  explaining) could, on its own, drop same-speaker cosine similarity below
  the match threshold, independent of the earlier margin-check bug. Before
  re-validating against the module's real code, an earlier repro used
  synthetic voices reweighting only the first 5 harmonics of one spectrum
  for "different speaker" comparisons; re-checking that fixture (see
  `_mfcc` liftering bullet below) showed most of its apparent difference
  sat in a near-identical FFT-leakage floor above ~600Hz shared by both
  signals, not a fair proxy for real timbre difference - so a more
  realistic two-formant synthetic fixture (`_formant_voice`, new in
  `tests/test_speaker_id.py`) was built to re-verify both directions
  (same-speaker prosody drift vs. different-speaker-same-pitch) before
  picking a final value.
  **Verification**: measured directly against the patched module (not a
  standalone reimplementation) via a scratch script: same real voice at
  120Hz vs. 150Hz (+25%, a realistic question/statement prosody swing) now
  scores `0.866` (was capped near `0.82` at the old weight for a smaller
  +25% shift, and lower for a real reverberant/noisy segment per the prior
  entry's repro); a different synthetic speaker (different formants) at
  the *same* 120Hz pitch now scores `0.708`, cleanly under the `0.75`
  default. Full suite re-run after the change (see Verified below).
- **What**: `speaker_id.py` `_mfcc()` now applies standard sinusoidal
  cepstral liftering (`lifter(k) = 1 + (L/2)*sin(pi*k/L)`, `L=22`, matches
  HTK/Kaldi/python_speech_features) to its output, via a new
  `_MFCC_LIFTER` constant.
  **Why**: the MFCC-mean block barely
  discriminates between different speakers: coefficient 1 (broad spectral
  tilt) alone carried ~59% of `|mfcc_mean|^2`, and since L2-normalizing a
  block only rescales it (doesn't reshape its direction), that dominance
  survives normalization and controls the cosine angle almost by itself.
  Root-caused with the more realistic `_formant_voice` fixture described
  above (two different vocal-tract-like resonance shapes at the same
  120Hz pitch): raw MFCC-mean-only cosine was `0.836` (would merge into
  one speaker); liftering alone dropped it to `0.126`, while same-speaker
  self-similarity stayed at `1.000` - confirming this is a real
  discriminability fix, not just adding noise (a plain, non-formant
  harmonic-reweighting fixture was tried first and barely moved either
  way, `0.994` -> `0.982`, which is what led to building the formant-based
  fixture instead of concluding liftering doesn't help).
  **Verification**: as above, plus new regression test
  `SpeakerRegistryTests.test_different_speaker_at_same_pitch_gets_new_id`
  in `tests/test_speaker_id.py`.
- **What**: `speaker_id.py` `SpeakerRegistry.identify()` - when the
  incoming segment's voiceprint has no reliable pitch (its pitch block is
  exactly all-zero, i.e. fewer than `MIN_VOICED_FRAMES` voiced frames),
  similarity against every centroid is now computed as a timbre-only
  ("masked") cosine over just the MFCC mean/std dims (`vec[:_TIMBRE_DIM]`,
  new constant `_TIMBRE_DIM = 2 * NUM_MFCC`), instead of the full vector.
  **Why**: comparing a segment's all-zero
  pitch block against a centroid's real pitch content caps the best
  achievable full-vector cosine similarity at `2/sqrt(2*(2+PITCH_WEIGHT^2))`
  (~`0.43` at the old weight) *regardless of how well the timbre actually
  matches* - confirmed live: a synthetic whisper/noise segment scored
  `0.020` against its own speaker's centroid, and 6 such segments in a row
  each registered a brand-new speaker instead of matching. Cosine
  similarity is invariant to scaling either side by a positive constant,
  so slicing both the incoming vector and each centroid to the same
  `_TIMBRE_DIM` dims and comparing (with each side's real norm, not
  assumed to be 1) is a correct cosine over just those dims - no
  renormalization of the sliced sub-vectors needed.
  **Verification**: scratch script - registered two distinct synthetic
  speakers, then fed a heavily-noised ("whispered", 0/28 voiced frames)
  0.3s slice of each one's own voice: pre-fix this would score ~`0.02-0.43`
  regardless of speaker and mint a new id every time; post-fix each
  whispered slice correctly matched its own speaker (`0.781`, `0.789`) and
  not the other one. New regression test
  `SpeakerRegistryTests.test_quiet_unvoiced_segment_matches_speaker_by_timbre`.
- **What**: `speaker_id.py`'s and `vad.py`'s standalone debug CLIs
  (`_cmd_test`'s polling loop in each) now check `cap.stopped_by_user` and
  `break` immediately after the existing `cap.raise_if_failed()` call,
  matching the check already present in `transcribe.py`'s main loop.
  **Why**: these two CLIs would otherwise keep
  polling (and only exit once `--seconds` elapses) if capture was stopped
  via macOS's system stop-sharing control mid-run, unlike `transcribe.py`
  itself.
  **Verification**: `py_compile` clean; read-through confirms both loops
  now mirror `transcribe.py`'s existing `except queue.Empty:` branch
  structure. **Not verified**: no live SCStream hardware run in this
  environment (same caveat as the `-3817` fix below).
- **What**: `transcribe.py`'s main loop now prints `"\nCapture stopped
  (via the system's stop-sharing control)."` immediately before the
  `stopped_by_user` `break`, ahead of the shared `finally:` block's
  existing `"\nStopping..."` message.
  **Why**: this shutdown path was otherwise
  textually indistinguishable from an ordinary Ctrl+C in the printed
  output, which could confuse a user who didn't use the system control
  themselves (e.g. someone else on the call ended screen sharing).
  **Verification**: `py_compile` clean; read-through of the changed branch.

### Changed
- **What**: `tests/test_speaker_id.py` - added `_whispered_voice()` (a
  `_synthetic_voice` variant with enough added noise to zero out the pitch
  block while staying multi-frame) and `_formant_voice()` (pulse train
  shaped by resonance peaks, a more realistic same-pitch/different-timbre
  fixture - see its docstring for why the existing harmonic-reweighting
  fixture doesn't adequately exercise timbre discrimination). Loosened
  `ExtractVoiceprintTests.test_distinct_voices_are_much_less_similar_than_same_voice`'s
  threshold from `0.5` to `0.6` (measured ~`0.51` after the `PITCH_WEIGHT`
  change - that fixture leans on pitch-driven separation, which is now
  intentionally less dominant; still far below same-voice's `0.95+`).
  **Why**: cover the three fixes above with real regression
  tests, using fixtures realistic enough to
  actually exercise them (the reasoning for needing
  `_formant_voice` is captured in that fixture's docstring, not repeated
  here).
  **Verification**: see Verified below.

### Verified
- `.venv/bin/python -m py_compile speaker_id.py transcribe.py vad.py
  capture_macos.py capture_windows.py tests/test_speaker_id.py` - clean.
- `.venv/bin/python -m unittest discover -s tests -v` - **45/45 pass**
  (42 pre-existing + 3 new: `test_natural_pitch_drift_does_not_fragment_same_speaker`,
  `test_different_speaker_at_same_pitch_gets_new_id`,
  `test_quiet_unvoiced_segment_matches_speaker_by_timbre`). Confirmed
  `test_registry_grows_to_ten_distinct_speakers` and
  `test_near_duplicate_centroids_do_not_runaway` (both from the prior
  turn) still pass unmodified under the new `PITCH_WEIGHT`/liftering.
- `git status --short` reviewed - only `speaker_id.py`, `tests/test_speaker_id.py`,
  `transcribe.py`, `vad.py`, plus this `CHANGELOG.md` entry, changed;
  nothing committed (not requested).
- **Not verified**: no real multi-speaker audio in this environment, same
  standing caveat as the two entries below covering this module -
  everything here is validated against synthetic fixtures (including the
  newly-added formant-based one) and their honest limitations, not real
  speech. A fourth, previously-flagged ("minor") concern - that a true-silence
  very first segment could collide with a later real speaker's "Person 1"
  label - was re-checked against the actual `identify()` control flow
  and found not to reproduce: the silence
  fallback path sets `_last_speaker_id` but never appends to `_centroids`,
  so the next (real) segment still finds an empty registry and correctly
  registers as speaker 1 itself, with no collision. Left unchanged; no
  fix applied for it.

---

## not available (not captured at turn start) -> 2026-09-16 16:43:35+02:00 (duration: not available | tokens: not available)
### Changed
- **What**: `transcribe.py`'s `_process_segment` output format — both the
  `--streaming` `print(...)` call and the log-file `writer.write(...)` call
  changed from `[{ts}] Person {speaker_id} said: {text}` to
  `[{ts} - Person {speaker_id}] said: {text}`, moving the speaker label
  inside the timestamp brackets, separated by `" - "`. `README.md`'s
  "Speaker identification" example output block updated to match the new
  format.
  **Why**: direct user request: "put the 'Person N' text within the time
  brackets split by - ."
  **Verification**: `.venv/bin/python -m py_compile transcribe.py` clean;
  `.venv/bin/python -m unittest discover -s tests -q` — 42/42 pass (grepped
  `tests/*.py` for `said:` first to confirm no test asserted the old bracket
  format).

### Fixed
- **What**: `capture_macos.py`'s `LoopbackCapture._on_stream_error` (the
  `stream_didStopWithError_` SCStream delegate callback) treated *every*
  stream stop as a fatal capture failure — printing `ERROR: ScreenCaptureKit
  stream stopped unexpectedly: ...` and exiting with code 1 — including when
  SCStreamErrorDomain reports code `-3817` ("The user stopped the stream"),
  i.e. precisely when the user stops capture via the system's own
  stop-sharing control, or when our own deliberate `.stop()` is already in
  flight (`_stop_event` already set). Added `_USER_STOPPED_CODE = -3817`
  (mirrors the existing `_TCC_DENIED_CODE = -3801` pattern). `_on_stream_error`
  now checks `self._stop_event.is_set()` or `error.code() ==
  _USER_STOPPED_CODE` first; either way it sets a new `self._stopped_by_user`
  flag (exposed via a new `stopped_by_user` property) instead of the fatal
  `NoAudioError`. `capture_windows.py` gets a matching `stopped_by_user`
  property that's hardcoded `False` (WASAPI loopback has no equivalent
  system control), preserving `capture.py`'s documented "both backends
  expose the same shape" contract. `transcribe.py`'s `run()` main loop (in
  the `except queue.Empty:` branch, right after the existing
  `cap.raise_if_failed()` check) now checks `cap.stopped_by_user` and breaks
  the loop cleanly — falling into the existing `finally` shutdown path, same
  as a Ctrl+C stop, exit code 0 — instead of that state ever reaching
  `raise_if_failed()` as a fatal error.
  **Why**: user reported hitting this exact error text when trying to
  terminate the macOS process. Root cause traced by reading
  `capture_macos.py`: `stream_didStopWithError_` fires asynchronously
  whenever the stream stops for *any* reason, including this specific
  "the user stopped it" code, which macOS sends when capture is stopped via
  the system's own stop-sharing/recording-indicator control — a channel
  entirely outside this app's own `cap.stop()` call, so it wasn't guarded by
  `_stop_event` at all. Since `_on_stream_error` had no way to distinguish
  "stream died unexpectedly" from "this stop was intentional/expected," a
  normal, successful termination was being surfaced as a scary ERROR with a
  failing exit code instead of a clean shutdown.
  **Verification**: `.venv/bin/python -m py_compile capture_macos.py
  capture_windows.py transcribe.py` clean; full suite `.venv/bin/python -m
  unittest discover -s tests -q` — 42/42 pass. **Not verified**: no live
  SCStream hardware test in this environment confirming the delegate
  callback actually fires with code -3817 when the system stop-sharing
  control is used (this environment has no display-capture session to
  trigger it) — the fix is grounded in the exact error text/code the user
  pasted and the existing `_TCC_DENIED_CODE` precedent for reading
  `error.code()` from this same delegate callback, not an end-to-end
  reproduction.

### Verified
- `.venv/bin/python -m py_compile transcribe.py capture_macos.py
  capture_windows.py` — clean.
- `.venv/bin/python -m unittest discover -s tests -q` — 42/42 pass.
- `git status --short` reviewed — only the intended files changed
  (`CHANGELOG.md`, `README.md`, `capture_macos.py`, `capture_windows.py`,
  `transcribe.py`); nothing committed (not requested).

---

## 2026-09-16 14:54:58+02:00 -> 2026-09-16 16:30:04+02:00 (duration: 1h 35m 6s | tokens: not available)
### Added
- **What**: New `speaker_id.py` module — algorithmic (no ML model) per-segment
  speaker identification. `extract_voiceprint(audio)` computes a fixed-length
  28+6=34-dim unit-norm feature vector per `SpeechSegment`: 13 MFCC
  coefficients' mean and std (pure-numpy Hamming-windowed framing -> `rfft`
  power spectrum -> manually-built 26-filter triangular mel filterbank ->
  log -> manual DCT-II via a cached cosine-basis matrix multiply, coefficient
  0/log-energy skipped as a volume proxy, not identity), each L2-normalized
  independently; plus an 8-bin Gaussian soft-binned ("RBF") encoding of
  autocorrelation-based pitch (F0, 70-400Hz search range,
  Wiener-Khinchin/batched-FFT autocorrelation, no per-frame Python loop),
  weighted and L2-normalized. `SpeakerRegistry` holds a growing (unbounded,
  starts empty) list of per-speaker centroid vectors; `identify(audio)`
  returns a 1-based speaker id by cosine-similarity match against every
  known centroid (best match must clear `--speaker-threshold`; see the
  Fixed section below for a margin-based refinement that was tried here
  first and had to be removed), updating that centroid via a running mean
  capped at 50 segments' weight, or registers a new speaker if no match
  clears the bar. A standalone debug CLI
  (`python speaker_id.py --device N --seconds 30 --threshold T`, mirroring
  `vad.py`'s own debug-CLI pattern) prints each segment's assigned speaker
  and live similarity score, since the threshold cannot be meaningfully
  pre-tuned without a real multi-speaker recording.
  **Why**: user explicitly required speaker attribution without adding a
  second ML model alongside Silero VAD/whisper.cpp (silero-vad's
  torch/torchaudio is already this project's heaviest dependency), scaling
  to 10+ unknown-in-advance speakers, correct attribution across
  interruptions, and a fast in-memory "voice sample" reference rather than
  re-comparing raw audio. Two feature-encoding designs were tried and
  rejected before this one, caught via synthetic-signal smoke testing (not
  just unit tests) before they reached the test suite: (1) a raw
  `[pitch_mean, pitch_std]` 2-D block, L2-normalized like the MFCC blocks —
  rejected because cosine similarity is angle-only, and for any two voices
  with a small std (a steady voice), `[mean, std]` points in nearly the same
  direction *regardless of the actual mean value*, so very differently
  pitched synthetic voices (e.g. 95Hz vs 300Hz) were scoring ~0.99 similar;
  (2) the same block left un-normalized but scaled up via a raw weight —
  made the collapse *worse* as weight increased (the block's contribution to
  overall cosine similarity approaches its own direction similarity as it
  dominates the vector norm, and that direction was still ~constant across
  different means). The shipped 8-bin Gaussian soft-binning turns "where is
  the pitch" into "which direction does the bump point," which cosine
  similarity can actually discriminate — confirmed this fixed it (see
  Verification).
- **What**: New `tests/test_speaker_id.py` (22 tests) — `MfccTests`/
  `EstimatePitchTests`/`ExtractVoiceprintTests` on synthetic tone signals
  (shape/finiteness, a known-frequency sine wave's estimated pitch landing
  within 5Hz of the true value, silence handling, self-similarity of two
  slices of the "same" synthetic voice vs. much lower similarity between
  distinct ones); `SpeakerRegistryTests` covering first-segment bootstrap,
  same-voice segments staying on one id (see the margin-bug regression note
  below), a distinct voice getting a new id, a short (0.3s) interruption
  from a known voice still matching correctly, the too-quiet/short-audio
  fallback path (including as the very first segment), registry growth to
  exactly 10 distinct synthetic speakers with an explicit stricter
  threshold (plus a repeat pass confirming it doesn't keep growing on
  re-identification of the same 10), and a dedicated regression test for
  the runaway-fragmentation bug described in Fixed below.
  **Why**: no real voice recordings are available in this environment, so
  correctness is validated against deterministic synthetic periodic signals
  (fundamental + weighted harmonics + noise) rather than real-speech
  accuracy claims.

### Changed
- **What**: `transcribe.py` — `_process_segment` now takes a `speakers:
  SpeakerRegistry` param (both the main-loop and shutdown-drain call sites
  updated, preserving the existing dedup between live/drain processing),
  calls `speakers.identify(segment.audio)` after the existing empty-text
  early return (so filler-only segments cost no feature extraction and
  never pollute the registry), and both log-file and streaming output lines
  changed from `f"[{ts}] {text}"` to `f"[{ts}] Person {speaker_id} said:
  {text}"`. `SentencePoster`'s internal queue changed from bare sentence
  strings to `(text, speaker_id)` tuples (`None` is still an unambiguous
  shutdown sentinel); `post()` takes a `speaker_id` param; the POSTed JSON
  body gains a `"speaker_id"` key alongside the existing `sentence, model,
  language, session_id`. New `--speaker-threshold` float flag (default
  `0.75`, see Fixed below for how that number was chosen) on
  `SpeakerRegistry`'s cosine-similarity match threshold, help
  text pointing at the `speaker_id.py` debug CLI for tuning. Module
  docstring and `--url` help text updated to document the new
  `speaker_id` JSON field.
  **Why**: implements the plan's core wiring — one `SpeakerRegistry` per
  session, shared across streaming and log-file modes (not gated behind
  `--streaming`), speaker lookup synchronous on the same thread as
  transcription (a few small FFTs is trivial next to a whisper.cpp
  inference call — no new thread/queue needed, unlike capture/VAD's
  producer/consumer pattern).
  **Design decisions surfaced to the user before implementation (via the
  approved plan) rather than decided silently**: output stays one line per
  VAD segment rather than merging consecutive same-speaker segments into a
  combined "turn" block, specifically to preserve the pre-existing
  guarantee that the transcript is flushed line-by-line and a killed
  process never loses a completed segment, and so interruptions render as
  separate consecutive lines rather than requiring cross-segment buffering;
  "Person N" numbering is session-scoped only, nothing persisted to disk
  between runs (the user's "fast reference" ask was read as within-session
  speed via a compact vector, not cross-session identity, which would also
  raise unasked-for privacy questions about persisting biometric-ish
  voiceprints).
- **What**: `README.md` — module table gains `speaker_id.py`; new "Speaker
  identification" section (between "Model choice" and "Troubleshooting")
  documenting the algorithmic approach, an output example, and explicit
  limitations (session-scoped only; interruptions separated by a VAD
  silence gap are attributed correctly, true simultaneous overlapping
  speech is not split since capture is single-channel; less discriminative
  than a neural embedding model by design; default threshold needs
  real-mic tuning); Flags table gains `--speaker-threshold`, `--url`'s row
  updated for the new JSON field; Troubleshooting's standalone-debug-CLI
  block extended with `speaker_id.py`; Tests section note extended.
  **Why**: keep docs in sync with the behavior change per this project's
  own documentation standards.

### Fixed
- **What**: `speaker_id.py` `SpeakerRegistry.identify()` — removed the
  margin-over-runner-up check that originally gated new-speaker creation
  alongside the `--speaker-threshold` check (i.e. the best-matching known
  speaker previously also had to beat the second-best by `MATCH_MARGIN`
  (0.05), or a new speaker was registered instead of assigning to the best
  match). Now a segment joins whichever known speaker it best matches as
  long as that single best match clears `--speaker-threshold`, full stop —
  `MATCH_MARGIN` and the `margin` constructor param are removed
  (`second_score` is still computed and exposed via `SpeakerMatch` for the
  debug CLI's diagnostics, just no longer used to gate the decision). Also
  lowered `DEFAULT_SPEAKER_THRESHOLD` from `0.90` to `0.75`.
  **Why**: user-reported real-world bug - one continuous speaker in a
  conference room with noticeable room echo/reverb, with only the pitch
  variation natural to normal speech (higher when asking a question, lower
  when explaining), kept incrementing to a new "Person N" on every single
  detected chunk, without ever stopping. Root-caused and reproduced (see
  Verification) before fixing: (1) the shipped `DEFAULT_SPEAKER_THRESHOLD`
  of `0.90` was calibrated only against clean, repeated synthetic tones
  (0.99+ self-similarity) with no reverb and no utterance-to-utterance
  content variation - unrealistically optimistic versus real speech, where
  simulated reverb plus natural phonetic-content and prosodic pitch
  variation pushed genuine same-speaker similarity as low as ~0.6-0.8 in
  testing, well under 0.90; that alone explains occasional misses. (2) The
  margin check turned occasional misses into an *unbounded runaway*: once
  any two registered centroids ever ended up representing the same real
  voice (e.g. from one earlier miss caused by (1)), every later segment
  from that voice scored a near-identical, very high similarity to *both*
  near-duplicate centroids (since they're both really the same voice) -
  so the margin between best and second-best was ~0, permanently failing
  the `>= MATCH_MARGIN` check and forcing a brand new speaker registration
  on every subsequent segment, forever. This is a materially worse failure
  mode (unbounded, compounding) than the thing the margin check was meant
  to prevent (occasionally, boundedly assigning a segment to the wrong one
  of two similar-sounding *different* people when ambiguous) - so the
  fix's philosophy is to accept the smaller, bounded risk over the
  unbounded one, and lower the threshold enough that real (reverb-and-
  prosody-affected) same-speaker audio reliably clears it in the first
  place.
  **Verification**: reproduced against the real `SpeakerRegistry`/
  `extract_voiceprint` code (not a description of the bug, an actual
  failing run) by directly seeding a registry with two centroids built
  from two slightly-different synthetic samples of one underlying voice
  (simulating "one earlier miss already happened") and feeding it further
  samples of that same voice - pre-fix, every single one of 7 further
  samples registered a brand new speaker (best/second-best scores 0.998+
  each, margin ~0.000-0.013, all under the 0.05 requirement), reproducing
  "never stopped" exactly; post-fix, all further samples correctly
  re-attached to the existing (closer) centroid and `num_speakers` stayed
  at 2 - now also a permanent regression test,
  `SpeakerRegistryTests.test_near_duplicate_centroids_do_not_runaway`.
  Threshold recalibration verified via a separate synthetic-reverb
  simulation (delayed/attenuated-copy summation approximating early
  reflections/decay, plus per-utterance pitch and harmonic-weight jitter
  approximating natural prosody/phonetic-content variation): a 40-chunk run
  of one continuous heavily-degraded synthetic speaker stayed at exactly 1
  registered speaker throughout at the new `0.75` default (vs. fragmenting
  under the old `0.90`), while a two-person alternating-conversation
  scratch test (see the pre-existing Verified bullet below) re-run at the
  new default still correctly resolved to exactly 2 stable, distinct
  speakers - confirming the lower threshold didn't reopen the opposite
  failure mode (merging different people) for that scenario. The
  `test_registry_grows_to_ten_distinct_speakers` unit test was updated to
  pass an explicit stricter `threshold=0.90` for that specific test, since
  clean non-reverberant synthetic tones (that test's whole point is
  proving the registry has no hardcoded speaker cap, not modeling real
  acoustics) are, if anything, more alike to each other after MFCC
  smoothing than real distinct voices are - at the new realistic-for-real-
  audio default of `0.75` that fixture only separated into 6 of the 10
  intended distinct speakers.

### Verified
- `python -m unittest discover -s tests -v` -> 42/42 pass (20 pre-existing +
  22 new in `test_speaker_id.py`, including the runaway-fragmentation
  regression test added in Fixed above).
- `python -m py_compile` clean on `speaker_id.py` and `transcribe.py`.
- `python -m transcribe --help` and `python speaker_id.py --help` both show
  the new flags correctly; `python speaker_id.py --list-devices` runs
  successfully against this machine's real macOS ScreenCaptureKit targets.
- Scratchpad smoke tests (real `speaker_id.py` code, not mocked) beyond the
  unit suite: pitch estimation against a real 150Hz synthetic tone landed at
  150.9Hz; a 10-synthetic-speaker set (varied pitch 75-395Hz and harmonic
  content) correctly registered as exactly 10 distinct ids with a second
  independently-seeded pass of the same 10 voices mapping back to the same
  ids (no registry growth); a simulated 12-turn alternating two-person
  conversation with pitch vibrato, amplitude noise, and short (0.3-0.4s)
  interruptions resolved to exactly 2 stable, correctly-attributed speakers
  throughout.
- **Not verified**: no live hardware test against a real multi-speaker audio
  recording/call was performed in this environment (no such recording
  available) — `--speaker-threshold`'s default (`0.75`) is calibrated
  against synthetic voices with *simulated* reverb and prosody/content
  jitter, still not real speech or a real room, and is explicitly
  documented as needing per-mic/room tuning via the debug CLI before
  relying on it in an actual meeting. The reported bug was root-caused and
  fixed from the user's description plus this simulation, not from a
  capture of the actual reverberant room in question.

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
  documentation convention treats doc drift as a real defect, not
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
  **Why**: requested directly — "Windows is working perfectly", asked to
  add macOS support to this same project. This project's own skill file
  out of scope in favor of a separate `audiotrans` project; flagged that
  conflict and asked first (via clarifying questions) whether to revisit
  it — confirmed yes, and confirmed ScreenCaptureKit over a
  BlackHole/virtual-audio-device approach (native, no extra driver install,
  at the cost of a one-time Screen Recording permission grant and needing
  macOS 13+).
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
  `capture_macos.py` — same reasoning as the project checklist's
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
  cover both platforms: description and locked-architecture section now
  document the macOS ScreenCaptureKit decision (dated, with the
  BlackHole-vs-ScreenCaptureKit tradeoff recorded and the reasoning for
  picking ScreenCaptureKit), project layout/working-practices sections
  updated for the new file split and macOS-specific debugging notes, and an
  explicit callout that `_extract_pcm16` hasn't been live-tested. Skill
  `name:` left unchanged for continuity (referenced by session history/
  changelog) despite no longer being Windows-only.
  **Why**: this skill's own text was the thing that first flagged the
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
  logged per task, mirroring the rule just added to `project checklist`.
  **Verification**: not verified (documentation-only change, no runtime
  behavior to test). Timestamp above taken from `Get-Date` at time of
  writing, not estimated.

---

## not available (predates timestamp/duration/token instrumentation, adopted at the entry above) (duration: not available | tokens: not available)
### Changed
- **What**: `project checklist`, "Before done" section — added a bullet requiring
  log entries to record exact start/end timestamp (ISO 8601), wall-clock
  duration, tokens spent, and the original prompt verbatim; unavailable
  fields marked "not available" rather than omitted or guessed.
  **Why**: requested directly, to make AI-authored changes auditable
  after the fact (who asked for what, when, how long/costly it was).
  **Verification**: not verified (documentation-only change).

---

## not available (predates timestamp/duration/token instrumentation) (duration: not available | tokens: not available)
### Added
- **What**: `project checklist`, "Before done" section — new bullet requiring log
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
  **Why**: `project checklist`'s "Before done" checklist requires a change log
  per project convention, and none existed yet for this project.
  **Verification**: not verified (documentation-only change).

---

## not available (predates timestamp/duration/token instrumentation) (duration: not available | tokens: not available)
### Verified
- **What**: full `project checklist` checklist run against the project, item by
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
  changed from `faster-whisper`/CTranslate2, `small.en`/`medium.en` to
  `pywhispercpp` (whisper.cpp bindings), `tiny.en`/`base.en` quantized
  (e.g. `base.en-q5_1`); added the `silero-vad` → torch/torchaudio +
  VC++ Redistributable dependency note; filled in actual CLI defaults and
  the standalone-debug commands for `capture.py`/`vad.py`. File relocated
  from a loose `call-transcriber\skills\win-speaker-transcribe.skill` zip
  (unexplained origin, flagged to Igor) to the standard
  `standard skill layout; old zip and empty `skills\`
  folder deleted.
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
  architecture decisions for this project (initial version — later
  corrected, see entry above).
- `project checklist` project checklist — self-review
  checklist to run before calling any change in this project done.

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
