# TODO / Backlog (Prioritized)

## Priority Legend
- **P0**: Critical path — do first
- **P1**: High value — do soon
- **P2**: Medium value — when time permits
- **P3**: Nice to have — backlog

---

## Library Architecture (from DEVELOPMENT.md gaps)

### P1: DAW Scripting Layer

- [x] **`daw_processor.py` — DawProcessor ABC**
  - `config_params()`, `configure()`, `check_connectivity()`
  - `fetch(session)`, `transfer(session)`, `sync(session)`
  - `execute_commands(session, commands)` — ad-hoc commands from GUI tools
  - One processor per DAW (ProTools, DAWProject, etc.)
  - Orchestrated by GUI/CLI directly, not Pipeline

- [x] **`daw_processors/` package — factory**
  - `default_daw_processors()` (empty, ready for concrete processors)

- [x] **DawCommand / DawCommandResult models**
  - Plain data + undo_params; processor executes via internal dispatch

- [x] **Config/Settings/Preferences integration** — four-section config
  (`app`, `colors`, `config_presets`, `group_presets`), Preferences two-tab
  layout (Global + Config Presets), config preset CRUD, group preset CRUD,
  toolbar "Config:" and "Group:" combos, session Config tab with per-session
  overrides, legacy config migration

- [x] **Concrete: ProToolsProcessor** (PTSL) — `ProToolsDawProcessor` in `daw_processors/protools.py`.
  `check_connectivity()`, `fetch()` (folder hierarchy), `transfer()` (audio import + CIE L*a*b*
  perceptual color matching). `sync()` not yet implemented. Configurable command delay.
- [x] **Concrete: DAWProjectProcessor** (.dawproject files) — `DawProjectDawProcessor` in
  `daw_processors/dawproject.py`. Template-based `.dawproject` file generation with track/clip
  creation, fader volumes, group colors. Expression gain (clip gain) automation partially
  implemented (TODO: XML structure issue with dawproject-py library).
- [x] **GUI toolbar dropdown** for active DAW processor selection — combo box + Check/Fetch/Transfer/Sync actions in Session Setup toolbar
- [ ] **GUI DAW Tools panel** (color picker, etc. → execute_commands)
- [ ] **Undo execution** (rollback last transfer/sync batch)

### P1: File-Based Processing Pipeline

- [x] **AudioProcessor enabled toggle** — base `config_params()` returns
  `{id}_enabled` ParamSpec, `configure()` reads `_enabled`, `enabled` property.
  Subclasses chain via `super().config_params() + [...]`. Pipeline configures
  all processors first, then filters to enabled before sorting by priority.

- [x] **Model changes** — `TrackContext` gained `processed_filepath`,
  `applied_processors`, `processor_skip`. `SessionContext` gained
  `prepare_state` (`"none"` / `"ready"` / `"stale"`).

- [x] **`Pipeline.prepare()` method** — wipes output dir, chains enabled
  processors per track (respecting `processor_skip`), writes processed files,
  updates track metadata, sets `prepare_state = "ready"`.

- [x] **`PrepareWorker` QThread** — runs `Pipeline.prepare()` in background
  with progress/finished/error signals.

- [x] **Prepare button** (analysis toolbar) — right-aligned, staleness
  indicators: "Prepare" / "Prepare ✓" / "Prepare (!)". Enabled after analysis.

- [x] **Processing column** (analysis table, col 7) — per-track multiselect
  `QToolButton` + checkable `QMenu`. Labels: "Default" (all active), "None"
  (all skipped), comma-separated (partial). Disabled "None" when no processors
  enabled globally. Editable only in analysis phase.

- [x] **Use Processed toggle** (setup toolbar) — checkable action with stale
  indicator. Controls `session.config["_use_processed"]`.
  `ProToolsDawProcessor.transfer()` uses `processed_filepath` when enabled.

- [x] **Staleness triggers** — gain, classification, RMS anchor, processor
  selection, and re-analysis changes transition `"ready"` → `"stale"`.

- [x] **MonoDownmixProcessor** (stub) — `PRIORITY_POST` (200), `apply()`
  returns audio unchanged. Tests multi-processor UI behaviour.

- [ ] **Batch-edit support for Processing column** (deferred)
- [ ] **Visual feedback in setup table** (processed vs original file badges/tooltips)

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

### P3: PreferencePage Superclass Refactor

- [ ] **Refactor preferences into `PreferencePage` base class with generic Reset to Defaults**
  - Each page subclasses `PreferencePage` with `populate(config)`, `read() -> dict`, `reset_defaults()`
  - Dialog auto-wires Reset button per page
  - Reduces duplication, provides consistent UX across all preference pages
  - Currently Colors and Groups have manual Reset; General/Analysis/Detectors/Processors do not

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

- [x] **Move `rich` from core to optional/CLI dependency** (Status: ✅ Done)
  - `rich` is now in `[project.optional-dependencies].cli`
  - `sessionprep.py` guards imports with a helpful error message
  - GUI builds (Nuitka) explicitly exclude `rich` to save space

### P3: Group Gain as Processor

- [ ] **Extract `GroupGainProcessor` at `PRIORITY_POST` (200)**
  - Currently implemented as `Pipeline._apply_group_levels()` post-step
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

- [x] **Reduce normalization hint noise** (Status: ✅ Done)
  - Removed normalization hints entirely — the three-metric classifier
    (crest + decay + density) makes single-metric "near threshold" warnings
    obsolete.

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

- [x] **Audio classifier upgrade (crest + decay + density)** (Status: ✅ Done)
  - Replaced single crest-factor threshold with a three-metric classifier:
    crest factor, envelope decay rate (10 ms short-window energy envelope),
    and content density (fraction of active RMS windows).
  - Resolves compressed drums (low crest, fast decay → Transient),
    plucked instruments (high crest, slow decay → Sustained), and
    sparse percussion like toms/crashes (sparse + dynamic metric agreement).
  - Sparse tracks require at least one dynamic metric (crest or decay) to
    agree before being classified as Transient, preventing false positives
    on sparse sustained content (e.g., guitar only in the outro).
  - New configurable params: `decay_lookahead_ms` (default 200),
    `decay_db_threshold` (default 12.0), `sparse_density_threshold` (default 0.25).
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
- [x] **Subsonic detection: per-channel analysis** (Status: ✅ Done)
  - Each channel is analyzed independently for stereo/multi-channel files.
  - If only one channel triggers, the issue is reported per-channel with the
    specific channel index. Both channels triggering → whole-file issue.
  - Combined ratio = max of per-channel ratios (no phase-cancellation masking).
  - Per-channel ratios stored in `data["per_channel"]`.
  - `subsonic_stft_analysis()` in `audio.py` (scipy STFT, replaces three old functions).

- [x] **Subsonic detection: windowed analysis option** (Status: ✅ Done)
  - Default on via `subsonic_windowed` config (default: `true`).
  - Splits each channel into windows (`subsonic_window_ms`, default 500 ms).
  - Contiguous exceeding windows merged into regions with precise sample ranges.
  - Regions reported as `IssueLocation` objects (visible on waveform overlays).
  - Capped by `subsonic_max_regions` (default 20).
  - Per-window ratios derived from single STFT pass (vectorised, no Python loop).
  - Whole-file analysis always runs regardless of windowed setting.
  - Four safeguards for accurate windowed results:
    1. Absolute subsonic power gate (window_rms_db + ratio_db < −40 dBFS → skip;
       prevents amp hum/noise in quiet gaps from false positives).
    2. Threshold relaxation (windowed threshold = configured − 6 dB, compensates for reduced frequency resolution in short windows).
    3. Active-signal fallback (if no ratio-based regions found, marks windows
       within 20 dB of the loudest window — matches where signal is active).
    4. Whole-file fallback (if even active-signal finds nothing, a full-file
       overlay is shown so ATTENTION always has a visible issue).

- [x] **Stereo compatibility: merged detector with windowed analysis** (Status: ✅ Done)
  - Merged `StereoCorrelationDetector` + `MonoFolddownDetector` into unified
    `StereoCompatDetector` (id `stereo_compat`).
  - `windowed_stereo_correlation()` in `audio.py`: vectorised numpy with per-window
    DC removal, silence gating, whole-file aggregation from cumulative dot products.
  - Both Pearson correlation and mono folddown loss computed per window from the same
    dot products (L·L, R·R, L·R) at zero extra cost.
  - Contiguous windows exceeding `corr_warn` or `mono_loss_warn_db` merged into regions.
  - Regions reported as `IssueLocation` objects with waveform overlays.
  - Severity upgrades INFO → ATTENTION when localized regions are found.
  - Config: `corr_warn`, `mono_loss_warn_db`, `corr_windowed`, `corr_window_ms`,
    `corr_max_regions`.
  - Files deleted: `stereo_correlation.py`, `mono_folddown.py`.

- [ ] **Reverb/bleed estimation** (Status: ❌) `NEW`
  - Why: A "dry" vocal with 2 seconds of reverb tail affects processing decisions. A "kick" track with hi-hat bleed means I can't gate it cleanly.
  - Approach: Analyze decay characteristics. Signal that doesn't drop > 30 dB within 200ms after transients likely has reverb/bleed.
  - Categorization: ATTENTION (informational)

- [ ] **Start offset misalignment** (Status: ❌) `NEW`
  - Why: Some tracks start with content immediately, others have 10s of silence. Catches "forgot to bounce from session start" errors.
  - Approach: Report time-to-first-content for each file; flag outliers.
  - Note: Related to existing "Track duration mismatches" but different failure mode.
  - Categorization: ATTENTION

- [ ] **Detector performance optimization** (Status: ❌) `NEW`
  - Profiled with `SP_DEBUG=1` on 27-track session (8.17s total analyze, 302.6 ms/track avg).
  - Top targets by per-file cost:
    1. **`audio_classifier`**: 577–1470 ms/file — ~60% of total time, biggest win by far
    2. **`subsonic`**: 460–1940 ms/file — worst on long stereo files (OH/Room ~1.9s)
    3. **`stereo_compat`**: 470–600 ms/file on stereo (0 ms on mono — OK)
  - Minor: `silence` (27–152 ms), `clipping` (24–168 ms), `dc_offset` (25–86 ms)
  - Negligible: `dual_mono`, `one_sided_silence`, `tail_exceedance` (<60 ms)
  - Processors are negligible (<1 ms each, plan phase ~65 ms total for 27 tracks)
  - Ideas: cache shared FFT/STFT data across detectors, downsample before classification, vectorize hot loops

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

- [x] **Document subsonic detection methodology** (Status: ✅ Done)
  - Documented in REFERENCE.md §2.10: STFT-based per-channel analysis via
    `scipy.signal.stft`, max-of-channels combined ratio, vectorised windowed
    analysis, absolute power gate, threshold relaxation, active-signal and
    whole-file fallbacks, frequency-bounded issue overlays.

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

### Vocal Automation (Future Feature)

- [ ] **Vocal automation curve generation** (Status: ❌) `FUTURE`
  - **Scope:** Pre-mix vocal cleanup to make vocals compressor-ready
  - **Goal:** Remove mechanical problems (plosives, sibilance, peaks, level inconsistencies) that waste time and sabotage compressor
  - **NOT in scope:** Macro dynamics (verse/chorus balance), artistic automation, creative mixing decisions
  - **When:** After core features are complete and stable

#### Core Processors

- [ ] **Phrase-level leveler**
  - Purpose: Smooth out loudness variations between phrases (2-4 second windows)
  - Algorithm: Bring outlier phrases toward median ±3-6 dB (genre-dependent)
  - Settings: `window_ms`, `target_range_db`, `smoothing` (aggressive/moderate/gentle)
  - Not normalization: Preserve intentional dynamics, only fix extremes

- [ ] **Plosive tamer**
  - Purpose: Remove low-frequency thumps from P, B consonants
  - Detection: 50-200 Hz bursts, <50ms duration
  - Action: Momentary 6-12 dB reduction (genre-dependent threshold)
  - Settings: `threshold` (0.0-1.0 energy ratio), `reduction_db`, `freq_range`

- [ ] **Sibilance tamer**
  - Purpose: Reduce harsh high-frequency spikes from S, T consonants
  - Detection: 5-10 kHz spikes, <100ms duration
  - Action: Momentary 3-8 dB reduction (frequency-specific or broadband)
  - Settings: `threshold` (ratio vs. mid-freq), `reduction_db`, `freq_range`

- [ ] **Peak limiter**
  - Purpose: Catch random peaks that would overload compressor (mouth clicks, breath pops)
  - Detection: Peaks >6 dB above local RMS (500ms window)
  - Action: Fast reduction over 10-50ms (5ms attack, 30ms release)
  - Settings: `threshold_db`, `target_db`, `attack_ms`, `release_ms`

#### Genre Preset System

- [ ] **Preset configurations**
  - Pop: Aggressive control (±3 dB range, heavy plosive/sibilance reduction)
  - Rock: Moderate control (±6 dB range, preserve energy)
  - Jazz: Minimal control (±10 dB range, preserve natural dynamics)
  - Minimal: Only obvious problems (disable leveler, catch extremes only)
  - Custom: User-configurable thresholds

#### Implementation Details

- [ ] **Automation curve generation**
  - Smoothing: 50ms attack, 200ms release (avoid zipper noise, pumping)
  - Thinning: Reduce to <5000 points for DAW compatibility
  - Global smoothing: Apply ballistics (compressor-style attack/release)
  - Preserve intentional peaks: Don't squash belts/screams (>10 dB above target)

- [ ] **GUI integration**
  - Vocal automation panel (waveform + automation overlay)
  - Preset dropdown (pop/rock/jazz/minimal/custom)
  - Processor checkboxes + threshold sliders
  - Real-time curve regeneration (adjust settings, preview curve)
  - Statistics display (plosives detected, sibilance regions, peaks reduced)
  - Visualization only (no interactive editing—refinement done in DAW)

- [ ] **Export formats**
  - DAWproject: Native volume automation lane
  - Pro Tools PTSL: Native automation (when PTSL integration ready)
  - JSON: For custom workflows / manual import

### Documentation

- [x] **Reorganize docs** (Status: ✅ Done)
  - README.md — overview, installation, quick start, usage examples
  - TECHNICAL.md — audio engineering background, normalization theory, signal chain
  - REFERENCE.md — detector reference, analysis metrics, processing details
  - DEVELOPMENT.md — development setup, building (PyInstaller + Nuitka), library architecture

- [x] **Build System Harmonization** (Status: ✅ Done)
  - Centralized `build_conf.py` for shared metadata.
  - Symmetric `build_pyinstaller.py` and `build_nuitka.py`.
  - Consistent `dist_*/` directory structure.
  - GitHub Actions for automated artifacts.

---

## Summary: Recommended Sprint Order

| Sprint | Focus | Items |
|--------|-------|-------|
| **0** | Testing & architecture | Test factories, unit tests, session detector storage, rendering split |
| **1** | Detection quality | Email summary generator, Over-compression, Noise floor/SNR, "Effectively silent" detection (rare), Reduce hint noise |
| **2** | Group intelligence | Multi-mic phase coherence |
| **3** | Workflow polish | Click/pop detection |
| **4** | Metering depth | LRA, LUFS (P2), True-peak/ISP (P2) |
| ~~**5**~~ | ~~Subsonic improvements~~ | ~~Per-channel analysis, Windowed option, Documentation~~ → ✅ Done (STFT speedup, per-channel, windowed, docs) |
| **6** | Auto-fix capabilities | DC removal, SRC |
| ~~**7**~~ | ~~Classification v2~~ | ~~Crest improvements~~ → ✅ Done (audio classifier with decay metric) |
| ~~**7b**~~ | ~~Simplify CLI grouping~~ | ~~Overlap policies, anonymous IDs~~ → ✅ Done (named groups, first-match-wins, no overlap policy) |
| **8** | DAW scripting | ~~DawProcessor ABC~~, ~~PTSL integration (check/fetch/transfer)~~, ~~PT batch import~~, ~~PT fader levels~~, ~~DAWProject backend~~, sync, DAWProject expression gain fix |
| ~~**9**~~ | ~~File-based processing~~ | ~~AudioProcessor enabled toggle, Pipeline.prepare(), Prepare button, Processing column, Use Processed toggle, staleness, MonoDownmix stub~~ → ✅ Done |
| ~~**10**~~ | ~~Stereo compatibility~~ | ~~Merge StereoCorrelation + MonoFolddown, windowed analysis, waveform overlays~~ → ✅ Done |
| **Ongoing** | Low-hanging fruit | Stereo narrowness, Start offset, Name mismatch, `rich` optional |

---

## Quick Reference: New Items Added This Session

| Item | Priority | Source |
|------|----------|--------|
| Over-compression / brick-wall limiting | P0 | Mix engineer feedback |
| Noise floor / SNR estimation | P0 | Mix engineer feedback |
| Multi-mic phase coherence | P0 | Mix engineer feedback |
| ~~Reduce normalization hint noise~~ | P0 | ✅ Resolved (normalization hints removed — obsolete with three-metric classifier) |
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
| ~~Preferences dialog~~ | — | ✅ Resolved (two-tab layout: Global + Config Presets; config preset CRUD; group preset CRUD; ParamSpec-driven pages; reset-to-default) |
| ~~Config presets + session config~~ | — | ✅ Resolved (four-section config structure, named config presets, toolbar Config: combo, session Config tab, per-session overrides, legacy migration, group preservation on re-analysis) |
| ~~HiDPI scaling~~ | — | ✅ Resolved (QT_SCALE_FACTOR, persisted in app.scale_factor) |
| ~~Detector/processor help text~~ | — | ✅ Resolved (visible subtext + rich tooltips) |
| ~~About dialog~~ | — | ✅ Resolved (version from importlib.metadata) |
| ~~Waveform overlay controls~~ | — | ✅ Resolved (Detector Overlays dropdown with per-detector checkable items, filtered by is_relevant) |
| ~~Peak/RMS marker toggle~~ | — | ✅ Resolved (Peak / RMS Max toggle button, dark violet/teal-blue colors) |
| ~~RMS L/R and RMS AVG split~~ | — | ✅ Resolved (separate toggle buttons replacing single RMS toggle) |
| ~~Show clean detector results pref~~ | — | ✅ Resolved (show_clean_detectors in Detectors section, default off) |
| ~~Default project directory pref~~ | — | ✅ Resolved (directory picker in General prefs, Open Folder starts there) |
| ~~Real progress bar~~ | — | ✅ Resolved (determinate bar from EventBus events, async table row updates) |
| ~~Tail exceedance relevance~~ | — | ✅ Resolved (is_relevant on TrackDetector, suppressed for peak/peak-limited methods) |
| ~~Severity label vs is_relevant mismatch~~ | — | ✅ Resolved (track_analysis_label now accepts detectors list, checks is_relevant; re-evaluated in _on_track_planned) |
| ~~Waveform keyboard shortcuts~~ | — | ✅ Resolved (Ctrl+wheel h-zoom, Ctrl+Shift+wheel v-zoom, Shift+wheel scroll, R/T zoom at guide position) |
| ~~Vectorised waveform downsampling~~ | — | ✅ Resolved (_build_peaks and _build_rms_envelope use NumPy reshape + vectorised min/max/sqrt) |
| ~~AIFF/AIF file support~~ | — | ✅ Resolved (AUDIO_EXTENSIONS constant in audio.py; pipeline, GUI, CLI all scan .wav/.aif/.aiff) |
| ~~Channel count column~~ | — | ✅ Resolved (Ch column in track table, populated from TrackContext.channels) |
| ~~WAV/AIFF chunk I/O~~ | — | ✅ Resolved (chunks.py: read_chunks, write_chunks, remove_chunks, chunk_ids; chunk metadata in file detail report) |
| ~~Spectrogram display mode~~ | — | ✅ Resolved (mel spectrogram via scipy STFT, magma/viridis/grayscale colormaps, frequency scale, configurable FFT/window/dB range) |
| ~~Frequency-bounded detector overlays~~ | — | ✅ Resolved (IssueLocation.freq_min_hz/freq_max_hz, mel-mapped rectangles in spectrogram mode) |
| ~~Spectrogram navigation~~ | — | ✅ Resolved (Ctrl+Shift+wheel freq zoom, Shift+Alt+wheel freq pan, dB floor/ceiling presets) |
| ~~Horizontal time scale~~ | — | ✅ Resolved (time axis in waveform display) |
| ~~Output folder preference~~ | — | ✅ Resolved (directory picker in General prefs) |
| ~~Skip reanalysis on GUI-only changes~~ | — | ✅ Resolved (Preferences dialog detects gui-vs-analysis changes) |
| ~~Subsonic STFT speedup~~ | — | ✅ Resolved (scipy.signal.stft replaces per-window Python FFT loop; scipy promoted to core dep) |
| ~~Scipy as core dependency~~ | — | ✅ Resolved (scipy>=1.12 promoted from gui optional to core dependencies; used by subsonic STFT + spectrogram) |
| ~~Batch RMS anchor / classification override~~ | — | ✅ Resolved (BatchEditTableWidget + BatchComboBox in widgets.py, selectionCommand override preserves multi-selection, Alt+Shift batch apply, async BatchReanalyzeWorker with progress) |
| ~~CLI grouping simplification~~ | — | ✅ Resolved (named groups via `Name:pattern` syntax, first-match-wins, overlap warnings, removed `--group_overlap`/union-find/merge) |
| ~~Group levelling terminology~~ | — | ✅ Resolved ("equalize" → "group level" throughout codebase; `_equalize_group_gains` → `_apply_group_levels`) |
| ~~Stereo compat windowed analysis~~ | — | ✅ Resolved (merged StereoCorrelation + MonoFolddown → StereoCompatDetector; windowed Pearson correlation + mono folddown loss; IssueLocation overlays) |
| ~~Stereo compat false positive fix~~ | — | ✅ Resolved (`_windowed_analysis` fallback only runs when `any_whole_warn` is True; prevents unconditional active-region marking) |
| ~~Stereo compat window default~~ | — | ✅ Resolved (default `corr_window_ms` changed from 500 ms to 250 ms for better localization) |
| ~~Summary report\_as routing fix~~ | — | ✅ Resolved (`_buckets` dict in `rendering.py` was missing `"info"` key; `report_as` config choice `"info"` now routes correctly to info bucket) |
| ~~Prepare error reporting~~ | — | ✅ Resolved (per-track write failures collected in `_prepare_errors`, displayed via `QMessageBox.warning` with file-locking guidance) |
| ~~Mono playback button~~ | — | ✅ Resolved (checkable **M** button in playback controls; `PlaybackController.play(mono=True)` folds stereo to mono via (L+R)/2; orange when active) |
| ~~Analysis column severity counts~~ | — | ✅ Resolved (replaced single worst-severity label with colored per-severity counts: `2P 1A 5I` format; QLabel cell widget with HTML rich text + hidden `_SortableItem` for sorting) |
| ~~Peak/RMS Max markers default off~~ | — | ✅ Resolved (`_show_markers` and toggle default changed to `False`) |
| ~~Auto-Group files by keywords~~ | — | ✅ Resolved (Auto-Group button in analysis toolbar; assigns tracks to groups via `matches_keywords()` pattern matching; confirmation dialog, refreshes tables + report) |
| ~~Pro Tools quicker imports~~ | — | ✅ Resolved (batch import: single `CId_ImportData` call for all files instead of one per track; PTSL batch job wraps entire transfer for modal progress) |
| ~~Pro Tools automatic fader levels~~ | — | ✅ Resolved (`CId_SetTrackVolume` applies fader offsets from bimodal normalization when processed files are used) |
| ~~Fader headroom rebalancing~~ | — | ✅ Resolved (`_compute_fader_offsets` ensures max fader ≤ ceiling − headroom; uniform downshift stored in `fader_rebalance_shift`; per-DAW ceiling via `_fader_ceiling_db`) |
| ~~Detector/processor profiling~~ | — | ✅ Resolved (per-component `time.perf_counter` timing via `dbg()` in pipeline.py; per-detector, per-processor, per-phase totals with averages; gated by `SP_DEBUG` env var) |
| ~~DAWProject processor~~ | — | ✅ Resolved (template-based `.dawproject` generation with tracks, clips, fader volumes, group colors; expression gain TODO) |
| ~~Mix Templates widget~~ | — | ✅ Resolved (session Config tab widget for configuring `.dawproject` template files with name, path, fader ceiling) |
| ~~Fetch error dialog~~ | — | ✅ Resolved (QMessageBox.warning on connectivity failure; status label width constrained; toolbar stays functional) |
| ~~Prepare preserves .dawproject~~ | — | ✅ Resolved (prepare phase removes only audio files from output dir, preserving `.dawproject` and other non-audio artefacts) |
| ~~Config refresh before transfer/prepare~~ | — | ✅ Resolved (`session.config.update(_flat_config())` in both `_do_daw_transfer` and `_on_prepare` ensures widget changes take effect) |