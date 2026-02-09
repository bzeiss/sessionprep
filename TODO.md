# TODO / Backlog (Prioritized)

## Priority Legend
- **P0**: Critical path — do first
- **P1**: High value — do soon
- **P2**: Medium value — when time permits
- **P3**: Nice to have — backlog

---

## Library Architecture (from DEVELOPMENT.md gaps)

### P1: DAW Scripting Layer

- [ ] **`daw_processor.py` — DawProcessor ABC + priority bands**
  - `DAWPRIORITY_IMPORT`, `_ROUTE`, `_APPEARANCE`, `_AUTOMATION`, `_FINALIZE`
  - `plan(session) -> list[DawAction]`

- [ ] **`daw_processors/` package — Concrete DAW processors**
  - `template_router.py` — TemplateRouter (routing rules → folder/bus assignment)
  - `track_colorizer.py` — TrackColorizer (color by classification or rules)
  - `fader_restore.py` — FaderRestore (inverse gain → `set_fader` actions)

- [ ] **`daw_backend.py` — DawBackend ABC**
  - `execute(actions) -> list[DawActionResult]`
  - `supports(action_type) -> bool`

- [ ] **`daw_backends/` package — Concrete backends**
  - `json_export.py` — JsonExportBackend (serialize actions to JSON)
  - `protools.py` — ProToolsBackend (stub, future PTSL integration)

- [ ] **Pipeline phases 3+5: `plan_daw()` and `execute_daw()`**
  - Add `daw_processors` param to Pipeline constructor
  - `plan_daw(session) -> list[DawAction]`
  - `execute_daw(actions, backend) -> list[DawActionResult]`

### P1: Testing Infrastructure

- [ ] **`tests/factories.py` — Test factories**
  - `make_audio()`, `make_track()`, `make_session()`
  - Deterministic, file-I/O-free synthetic test objects

- [ ] **Unit tests for all detectors**
  - One test file per detector in `tests/test_detectors/`

- [ ] **Unit tests for processors**
  - `tests/test_processors/test_bimodal_normalize.py`

- [ ] **Pipeline integration tests**
  - `tests/test_pipeline.py`

- [ ] **Config and queue tests**
  - `tests/test_config.py`, `tests/test_queue.py`

### P2: Session Snapshots

- [ ] **`snapshot.py` — Save/load session analysis state**
  - `save_snapshot(session, path)` — serialize metadata + detector/processor results (no audio)
  - `load_snapshot(path, source_dir) -> SessionContext` — restore state, lazy-load audio
  - JSON format with `schema_version`
  - Enables "analyze once, iterate on settings" workflow

### P2: Rendering Abstraction

- [ ] **Renderer ABC in `rendering.py`**
  - `render_diagnostic_summary(summary) -> Any`
  - `render_track_table(tracks) -> Any`
  - `render_daw_action_preview(actions) -> Any`

- [ ] **DictRenderer** — returns raw dicts for JSON export / GUI binding

- [ ] **Wrap existing functions into PlainTextRenderer class**

### P3: Validation Polish

- [ ] **Component-level `configure()` validation**
  - Each detector/processor should raise `ConfigError` for invalid config slices
  - Currently config is validated globally but not per-component at startup

### P2: Session Detector Result Storage

- [ ] **Move session-level detector results out of `session.config`**
  - Currently stored as `session.config[f"_session_det_{det.id}"]` in `pipeline.py`
  - `SessionContext` should have a dedicated `session_detector_results: dict` field
  - Avoids using `config` as a grab-bag for runtime state

### P2: Separate Aggregation from Rendering

- [ ] **Split `rendering.py` into builder + renderer modules**
  - `build_diagnostic_summary()` (465 lines) contains substantial data aggregation logic
  - When the Renderer ABC is introduced (see above), the builder should move to its own module
  - The rendering module should only contain format-specific output (plain text, Rich, etc.)

### P3: Make `rich` an Optional Dependency

- [ ] **Move `rich` from core to optional/CLI dependency**
  - `rich` is in `[project.dependencies]` because `sessionprep.py` imports it unconditionally
  - The library (`sessionpreplib`) has no dependency on `rich`
  - Anyone using only `sessionpreplib` (GUI, web frontend) pulls in `rich` unnecessarily
  - Fix: move to `[project.optional-dependencies].cli` and guard imports in `sessionprep.py`

### P3: Group Gain as Processor

- [ ] **Extract `GroupGainProcessor` at `PRIORITY_POST` (200)**
  - Currently implemented as `Pipeline._equalize_group_gains()` post-step
  - Moving to a processor makes it disableable/replaceable

---

## P0: Critical Path

### Detection & Diagnostics

- [ ] **Over-compression / brick-wall limiting detection** (Status: ❌) `NEW`
  - Why: A track with crest < 6 dB, peak > -1 dBFS, and RMS > -8 dBFS has been crushed before it reached me. I can't un-limit it. This fundamentally changes how I approach the track.
  - Heuristic:
    ```python
    if crest < 6 and peak > -1 and rms > -8:
        warn("Possible over-limiting: dynamics may be unrecoverable")
    ```
  - Categorization: ATTENTION

- [ ] **Noise floor / SNR estimation** (Status: ❌) `NEW`
  - Why: A quiet vocal comp with audible hiss/hum. Current "silent file" detection misses this—the file has content, but also has a bad noise floor.
  - Approach:
    - Find gaps/silent sections (RMS below threshold for >500ms)
    - Measure RMS of those regions as noise floor
    - Compare to signal RMS: $\text{SNR} = \text{signal\_rms\_db} - \text{noise\_floor\_db}$
  - Threshold: Warn if SNR < 30 dB
  - Categorization: ATTENTION

- [ ] **Multi-mic phase coherence within groups** (Status: ❌) `EXPANDED`
  - Why: `Kick_In` and `Kick_Out` with opposite polarity will cancel when summed. Grouping preserves gain but doesn't catch this.
  - Approach: For each `--group`, compute pairwise correlation between members in low-frequency band (< 500 Hz where phase matters most).
  - Threshold: Warn if correlation < 0
  - Note: Expands existing "Phase coherence between related tracks" item with concrete implementation.
  - Categorization: ATTENTION (within group context)

### UX / Output Quality

- [ ] **Reduce normalization hint noise** (Status: ❌) `NEW`
  - Problem: In example output, 11 of 14 files triggered "near threshold" warnings. That's not actionable—it's noise.
  - Options:
    1. Tighten tolerance from ±2 dB to ±1 dB
    2. Make hints opt-in: `--show_normalization_hints`
    3. Only show top N most borderline cases
  - Recommendation: Option 2 (off by default)

- [ ] **Auto-generate email-ready issue summary** (Status: ❌)
  - Why: Detection without communication is incomplete. This is workflow gold.
  - Goal: Copy/paste a short request for corrected exports.
  - Implementation: `--email-summary` or automatic `sessionprep_issues.txt`
  - Example:
    ```
    Hi [Name],

    Session diagnostics found the following issues:

    PROBLEMS (require corrected exports):
    - Lead Vox_01.wav: digital clipping detected (3 ranges)
    - 808_01.wav: sample rate mismatch (44.1kHz, session is 48kHz)

    ATTENTION (please confirm if intentional):
    - Pad_01.wav: dual-mono stereo file

    Please send corrected files at your earliest convenience.

    Thanks,
    [Name]
    ```

---

## P1: High Value

### Detection & Diagnostics

- [ ] **"Effectively silent" / noise-only file detection** (Status: ❌) `NEW` (rare)
  - Why: Current `is_silent` only triggers on `peak_linear == 0.0` (absolute zero). A file containing only noise floor with no musical content passes as valid.
  - Approach:
    - Compute crest factor and RMS distribution
    - If peak is low (< -40 dBFS) AND crest is very low (< 6 dB) AND content is spectrally flat (noise-like), flag as "effectively silent / noise only"
    - Alternative: If file RMS is entirely below a threshold (e.g., -60 dBFS) with no transients, flag it
  - Relationship: Complements SNR estimation (that's for files WITH content; this is for files that ARE noise)
  - Categorization: ATTENTION

- [ ] **Click/pop detection (non-clipping transients)** (Status: ❌) `NEW`
  - Why: Isolated transients that are anomalously loud—not clipping, but likely editing errors, plugin glitches, or mouth clicks.
  - Approach: Flag when a single sample or very short window (< 5ms) exceeds local RMS by > 20 dB.
  - Categorization: ATTENTION

### Audio Cleanup & Processing

- [ ] **DC offset removal** (Status: ❌)
  - Why: Why detect it if you won't fix it?
  - Implementation: `--fix_dc` flag or automatic in execute mode

---

## P2: Medium Value

### Classification Robustness

- [x] **Audio classifier upgrade (crest + envelope decay)** (Status: ✅ Done)
  - Replaced single crest-factor threshold with a two-metric vote:
    crest factor + envelope decay rate (10 ms short-window energy envelope).
  - Resolves compressed drums (low crest, fast decay → Transient) and
    plucked instruments (high crest, slow decay → Sustained).
  - New configurable params: `decay_lookahead_ms` (default 200),
    `decay_db_threshold` (default 12.0).
  - Detector renamed: `crest_factor` → `audio_classifier`
    (`CrestFactorDetector` → `AudioClassifierDetector`).
  - File renamed: `crest_factor.py` → `audio_classifier.py`.
  - All consumers updated (tail_exceedance, bimodal_normalize, rendering).

- [ ] **Loudness Range (LRA) / dynamics measurement** (Status: ❌)
  - Why: Beyond crest—flag heavily compressed vs genuinely dynamic tracks.
  - Complements over-compression detection.

### Detection & Diagnostics

- [ ] **Spectral gaps / aliasing artifacts** (Status: ❌) `NEW`
  - Why: Declining problem, but still useful if the source was transcoded or poorly sample-rate-converted.
  - Detect:
    - Notch at 16kHz (MP3 encoding artifact)
    - Energy above Nyquist/2 (aliasing from bad SRC)
    - Missing expected low end (e.g., "Bass" track with nothing below 100Hz)
  - Categorization: ATTENTION

- [ ] **Subsonic detection: per-channel analysis** (Status: ❌) `NEW`
  - Why: Current implementation sums to mono before FFT analysis. Subsonic content isolated to one channel (e.g., bad cable, ground loop on one side) could be diluted or missed.
  - Current code:
    ```python
    mono = np.mean(data.astype(np.float64), axis=1)  # averages L/R
    ```
  - Fix: Analyze L and R independently for stereo files; report per-channel if only one side has subsonic issues.
  - Categorization: Improves existing ATTENTION detector

- [ ] **Subsonic detection: windowed analysis option** (Status: ❌) `NEW`
  - Why: Current implementation does a single whole-file FFT (downsampled to ~200k samples). Subsonic rumble isolated to specific sections (bass drops, HVAC bleed in quiet parts) may not trigger the threshold.
  - Options:
    1. Windowed analysis with percentile reporting (like RMS anchor)
    2. Report time ranges where subsonic content exceeds threshold
    3. Keep whole-file as default, add `--subsonic_windowed` for detailed analysis
  - Documentation: At minimum, document current behavior (whole-file average, not sectional)
  - Categorization: Enhancement to existing detector

- [ ] **Reverb/bleed estimation** (Status: ❌) `NEW`
  - Why: A "dry" vocal with 2 seconds of reverb tail affects processing decisions. A "kick" track with hi-hat bleed means I can't gate it cleanly.
  - Approach: Analyze decay characteristics. Signal that doesn't drop > 30 dB within 200ms after transients likely has reverb/bleed.
  - Categorization: ATTENTION (informational)

- [ ] **Start offset misalignment** (Status: ❌) `NEW`
  - Why: Some tracks start with content immediately, others have 10s of silence. Catches "forgot to bounce from session start" errors.
  - Approach: Report time-to-first-content for each file; flag outliers.
  - Note: Related to existing "Track duration mismatches" but different failure mode.
  - Categorization: ATTENTION

### Audio Cleanup & Processing

- [ ] **Optional auto-HPF on processing** (Status: ❌)
  - Idea: `--hpf 30` to clean subsonic garbage.
  - Caution: Should be opt-in only, never default.

- [ ] **Automatic SRC** (Status: ❌)
  - Why: Same issue as DC—flag it and make me open another tool?
  - Implementation: `--target_sr 48000` with high-quality resampling (libsamplerate)
  - Caution: Destructive operation, needs clear user intent.

### Session Integrity Checks

- [ ] **Tempo/BPM metadata consistency** (Status: ❌) `NEW`
  - Why: When tracks come with conflicting or missing tempo info, aligning to click and time-based effects becomes slower.
  - Scope note: Might be out of scope if the source files don't carry reliable tempo metadata.
  - Check:
    - Consistent BPM/tempo metadata across files, if present
    - Warn if mixed/missing
  - Categorization: ATTENTION

- [ ] **Track duration mismatches** (Status: ❌)
  - Why: Common export error (e.g., one track is 3:24 and another is 3:25).
  - Note: Already partially implemented as "Length mismatches" detector. May just need threshold tuning for sub-second differences.

### Metering & Loudness Context

- [ ] **True-peak / ISP warnings** (Status: ❌)
  - Why: Matters more for mastering. At mix stage with a -6 dBFS ceiling, not critical.

- [ ] **LUFS measurement** (Status: ❌)
  - Why: Nice context but usually doesn't change decisions.
  - Display: Add to per-file stats, include integrated + short-term

### Documentation

- [ ] **Document subsonic detection methodology** (Status: ❌) `NEW`
  - Why: Current behavior is non-obvious and could mislead users.
  - Document:
    - Whole-file FFT (not windowed/sectional)
    - Downsampled to ~200k samples for performance
    - Sums to mono before analysis (L/R not independent)
    - Ratio is power in band ≤ cutoff vs. total power (excluding DC)
  - Location: Detector reference docs or inline in README

---

## P3: Nice to Have

### Detection & Diagnostics

- [ ] **Stereo narrowness detection** (Status: ❌) `NEW`
  - What: Stereo file that's effectively mono (not identical, but < 5% width)
  - Why: Could save disk space / simplify processing
  - Categorization: ATTENTION (informational)

- [ ] **Asymmetric panning detection** (Status: ❌) `NEW`
  - What: Stereo file hard-panned to one side
  - Why: Often a bounce error
  - Categorization: ATTENTION

- [ ] **Frequency content vs. name mismatch** (Status: ❌) `NEW`
  - What: "Kick.wav" with no energy below 100Hz, "HiHat.wav" with lots of low end
  - Why: Sanity check against mislabeling
  - Approach: Simple spectral centroid or band energy checks against filename keywords
  - Categorization: ATTENTION (informational)

### Documentation

- [x] **Reorganize docs** (Status: ✅ Done)
  - README.md — overview, installation, quick start, usage examples
  - TECHNICAL.md — audio engineering background, normalization theory, signal chain
  - REFERENCE.md — detector reference, analysis metrics, processing details
  - DEVELOPMENT.md — development setup, building, library architecture

---

## Summary: Recommended Sprint Order

| Sprint | Focus | Items |
|--------|-------|-------|
| **0** | Testing & architecture | Test factories, unit tests, session detector storage, rendering split |
| **1** | Detection quality | Email summary generator, Over-compression, Noise floor/SNR, "Effectively silent" detection (rare), Reduce hint noise |
| **2** | Group intelligence | Multi-mic phase coherence |
| **3** | Workflow polish | Click/pop detection |
| **4** | Metering depth | LRA, LUFS (P2), True-peak/ISP (P2) |
| **5** | Subsonic improvements | Per-channel analysis, Windowed option, Documentation |
| **6** | Auto-fix capabilities | DC removal, SRC |
| ~~**7**~~ | ~~Classification v2~~ | ~~Crest improvements~~ → ✅ Done (audio classifier with decay metric) |
| **8** | DAW scripting | DawProcessor ABC, backends, PTSL integration |
| **Ongoing** | Low-hanging fruit | Stereo narrowness, Start offset, Name mismatch, `rich` optional |

---

## Quick Reference: New Items Added This Session

| Item | Priority | Source |
|------|----------|--------|
| Over-compression / brick-wall limiting | P0 | Mix engineer feedback |
| Noise floor / SNR estimation | P0 | Mix engineer feedback |
| Multi-mic phase coherence | P0 | Mix engineer feedback |
| Reduce normalization hint noise | P0 | Mix engineer feedback |
| Email summary generator | P0 | Workflow requirement |
| "Effectively silent" / noise-only detection (rare) | P1 | User note (close to silence) |
| Click/pop detection | P1 | Mix engineer feedback |
| Session detector result storage | P2 | Architectural review |
| Rendering/aggregation split | P2 | Architectural review |
| Spectral gaps / aliasing artifacts | P2 | Mix engineer feedback |
| Subsonic per-channel analysis | P2 | User note (L/R handling) |
| Subsonic windowed analysis option | P2 | User note (parts vs whole file) |
| Document subsonic methodology | P2 | User note (documentation) |
| Tempo/BPM metadata consistency | P2 | User note (workflow) |
| Reverb/bleed estimation | P2 | Mix engineer feedback |
| Start offset misalignment | P2 | Mix engineer feedback |
| Make `rich` optional dependency | P3 | Architectural review |
| Stereo narrowness detection | P3 | Mix engineer feedback |
| Asymmetric panning detection | P3 | Mix engineer feedback |
| Frequency vs. name mismatch | P3 | Mix engineer feedback |
| ~~Hard-coded processor ID in Pipeline~~ | — | ✅ Resolved |
| ~~Report/JSON generation in CLI~~ | — | ✅ Resolved (moved to `sessionpreplib/reports.py`) |
| ~~Docs reorganization~~ | — | ✅ Resolved (README, TECHNICAL, REFERENCE) |
| ~~Preferences dialog~~ | — | ✅ Resolved (tree nav, ParamSpec-driven pages, reset-to-default) |
| ~~HiDPI scaling~~ | — | ✅ Resolved (QT_SCALE_FACTOR, persisted in gui.scale_factor) |
| ~~Detector/processor help text~~ | — | ✅ Resolved (visible subtext + rich tooltips) |
| ~~About dialog~~ | — | ✅ Resolved (version from importlib.metadata) |