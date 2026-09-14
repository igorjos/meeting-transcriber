# Changelog

Convention for this project (required — see `CLAUDE.md` "Before done"):

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

## 2026-09-14 15:31:20+02:00 -> 2026-09-14 15:37:48+02:00 (duration: 6m 28s | tokens: not available)
**Prompt:** "on MacOs I get all of these options: [pasted `--list-devices` output listing 33 entries, e.g. Dock, Spotlight, Notification Center, loginwindow, Control Center, several 'Open and Save Panel Service'/'AutoFill (...)' helper entries, raw 'pid NNNNN' entries, alongside real apps like Microsoft Teams, Google Chrome, Terminal]"

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
**Prompt:** "Update the solution @Transcriber/ to support MacOS as well. Windows is working perfectly"

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
  (`.claude/skills/win-speaker-transcribe/SKILL.md`) had macOS explicitly
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
  `capture_macos.py` — same reasoning as the CLAUDE.md checklist's
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
- **What**: `.claude/skills/win-speaker-transcribe/SKILL.md` — updated to
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
**Prompt:** "Yes Update the @CHANGELOG.md too"

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
**Prompt:** "Update @CLAUDE.md in the log section, specifying that exact timestamp should be used, the time AI needed to finish the job, the tokens spent and the original prompt should all be logged."

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
**Prompt:** "Logs should contain detailed output of what was changed by AI for future analysis, add this to the CLAUDE.md file as well"

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
**Prompt:** "start a convention for logs, which is a must for this project as stated"

### Added
- **What**: `CHANGELOG.md` created — Added/Changed/Fixed convention,
  backfilled with the project's history up to that point. Linked from
  `README.md` under a new "Changelog" section.
  **Why**: `CLAUDE.md`'s "Before done" checklist requires a change log
  per project convention, and none existed yet for this project.
  **Verification**: not verified (documentation-only change).

---

## not available (predates timestamp/duration/token instrumentation) (duration: not available | tokens: not available)
**Prompt:** "yes go point by point now, and follow this in future as well"

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
**Prompt:** "yes update the skill and place it in a proper folder"

### Changed
- **What**: `.claude/skills/win-speaker-transcribe/SKILL.md` — ASR section
  changed from `faster-whisper`/CTranslate2, `small.en`/`medium.en` to
  `pywhispercpp` (whisper.cpp bindings), `tiny.en`/`base.en` quantized
  (e.g. `base.en-q5_1`); added the `silero-vad` → torch/torchaudio +
  VC++ Redistributable dependency note; filled in actual CLI defaults and
  the standalone-debug commands for `capture.py`/`vad.py`. File relocated
  from a loose `call-transcriber\skills\win-speaker-transcribe.skill` zip
  (unexplained origin, flagged to Igor) to the standard
  `.claude/skills/<name>/SKILL.md` layout; old zip and empty `skills\`
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
**Prompt:** "Start by scaffolding the project structure and a requirements.txt, then implement capture.py first and verify loopback audio capture works before moving to VAD and transcription." (one instruction from the original, larger build request specifying the full architecture — capture via PyAudioWPatch/WASAPI loopback, VAD via silero-vad, ASR via pywhispercpp whisper.cpp tiny.en/base.en quantized, capture/inference on separate threads via `queue.Queue`, plain `python -m transcribe` CLI, `--list-devices`/`--device`/`--model`/`--output` flags, flush-to-disk per segment; full text not preserved verbatim, this session's context was summarized before this logging convention existed)

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
- `.claude/skills/win-speaker-transcribe/SKILL.md` documenting the locked
  architecture decisions for this project (initial version — later
  corrected, see entry above).
- `CLAUDE.md` project checklist (added by Igor, not by AI) — self-review
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
