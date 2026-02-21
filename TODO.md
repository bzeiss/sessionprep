# TODO / Backlog (Prioritized)

## Priority Legend
- **P0**: Critical path — do first
- **P1**: High value — do soon
- **P2**: Medium value — when time permits
- **P3**: Nice to have — backlog

---

## Library Architecture (from DEVELOPMENT.md gaps)

### P1: DAW Scripting Layer

- [ ] **GUI DAW Tools panel** (color picker, etc. → execute_commands)
- [ ] **Undo execution** (rollback last transfer/sync batch)
- [ ] **`sync()`** for ProToolsDawProcessor (incremental delta push; currently raises `NotImplementedError`)
- [ ] **DAWproject expression gain fix** (XML structure issue with dawproject-py library)

### P1: File-Based Processing Pipeline

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

---

## P2: Medium Value

### Classification Robustness

- [ ] **Loudness Range (LRA) / dynamics measurement**
  - Why: Beyond crest — flag heavily compressed vs genuinely dynamic tracks.

### Detection & Diagnostics

- [ ] **Spectral gaps / aliasing artifacts**
  - Detect: Notch at 16kHz (MP3 artifact), energy above Nyquist/2 (bad SRC),
    missing expected low end (e.g., "Bass" track with nothing below 100Hz)
  - Categorization: ATTENTION

- [ ] **Reverb/bleed estimation**
  - Approach: Signal that doesn't drop > 30 dB within 200ms after transients.
  - Categorization: ATTENTION (informational)

- [ ] **Start offset misalignment**
  - Approach: Report time-to-first-content; flag outliers.
  - Categorization: ATTENTION

- [ ] **Detector performance optimization**
  - Profiled on 27-track session: `audio_classifier` 577–1470 ms/file (~60% of total),
    `subsonic` 460–1940 ms/file, `stereo_compat` 470–600 ms/file on stereo.
  - Ideas: cache shared FFT/STFT data, downsample before classification, vectorize hot loops.

### Audio Cleanup & Processing

- [ ] **Optional auto-HPF on processing** — `--hpf 30`; opt-in only, never default.

- [ ] **Automatic SRC** — `--target_sr 48000` via libsamplerate; destructive, needs explicit intent.

### Session Integrity Checks

- [ ] **Tempo/BPM metadata consistency**
  - Warn if mixed or missing BPM metadata across files (scope note: only if files carry it).
  - Categorization: ATTENTION

- [ ] **Track duration mismatches**
  - "Length mismatches" detector exists; may need threshold tuning for sub-second differences.

### Metering & Loudness Context

- [ ] **True-peak / ISP warnings** — more relevant at mastering; low priority at mix stage.

- [ ] **LUFS measurement** — nice context, per-file integrated + short-term display.

---

## P3: Nice to Have

### Detection & Diagnostics

- [ ] **Stereo narrowness detection** — effectively mono stereo (< 5% width); ATTENTION (informational).

- [ ] **Asymmetric panning detection** — stereo hard-panned to one side; often a bounce error.

- [ ] **Frequency content vs. name mismatch** — "Kick.wav" with no energy below 100Hz, etc.
  Approach: spectral centroid or band energy checks against filename keywords.

### Vocal Automation (Future Feature)

- [ ] **Vocal automation curve generation** `FUTURE`
  - **Scope:** Pre-mix vocal cleanup (plosives, sibilance, peaks, level inconsistencies).
  - **NOT in scope:** Macro dynamics, artistic automation, creative mixing.
  - **When:** After core features are complete and stable.
  - Sub-items: phrase-level leveler, plosive tamer, sibilance tamer, peak limiter,
    genre presets (pop/rock/jazz/minimal/custom), automation curve generation,
    GUI panel, DAWproject/PTSL/JSON export.

---

## Summary: Recommended Sprint Order

| Sprint | Focus | Items |
|--------|-------|-------|
| **0** | Testing & architecture | Test factories, unit tests, session detector storage, rendering split |
| **1** | Detection quality | Email summary generator, Over-compression, Noise floor/SNR, "Effectively silent" detection (rare) |
| **2** | Group intelligence | Multi-mic phase coherence |
| **3** | Workflow polish | Click/pop detection |
| **4** | Metering depth | LRA, LUFS (P2), True-peak/ISP (P2) |
| **5** | Auto-fix capabilities | DC removal, SRC |
| **6** | DAW scripting | sync (ProTools), DAWproject expression gain fix, DAW Tools panel, Undo |
| **Ongoing** | Low-hanging fruit | Stereo narrowness, Start offset, Name mismatch, Spectral gaps |