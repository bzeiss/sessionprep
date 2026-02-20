# SessionPrep Board

## To Do

### Group Gain as Processor

  - priority: low
    ```md
    Extract GroupGainProcessor at PRIORITY_POST (200).
    Currently implemented as Pipeline._apply_group_levels() post-step.
    Moving to a processor makes it disableable/replaceable.
    ```

### Component-level configure() Validation

  - priority: low
    ```md
    Each detector/processor should raise ConfigError for invalid config slices.
    Currently config is validated globally but not per-component at startup.
    ```

### Asymmetric Panning Detection

  - priority: low
    ```md
    Stereo file hard-panned to one side — often a bounce error.
    Categorization: ATTENTION
    ```

### Frequency Content vs. Name Mismatch

  - priority: low
    ```md
    "Kick.wav" with no energy below 100Hz, "HiHat.wav" with lots of low end.
    Approach: spectral centroid or band energy checks against filename keywords.
    Categorization: ATTENTION (informational)
    ```

### Vocal Automation Curve Generation

  - priority: low
    ```md
    FUTURE: Pre-mix vocal cleanup (plosives, sibilance, peaks, level inconsistencies).
    NOT in scope: Macro dynamics, artistic automation, creative mixing.
    When: After core features are complete and stable.
    Sub-items: phrase-level leveler, plosive tamer, sibilance tamer, peak limiter,
    genre presets (pop/rock/jazz/minimal/custom), automation curve generation,
    GUI panel, DAWproject/PTSL/JSON export.
    ```

### Visual Feedback in Setup Table

  - priority: high
    ```md
    Show processed vs original file status in setup table (badges/tooltips).
    Currently no visual indication of which file will be transferred.
    ```

### Unit Tests — Detectors

  - priority: high
    ```md
    One test file per detector in tests/test_detectors/.
    Covers: silence, clipping, dc_offset, stereo_compat, dual_mono,
    one_sided_silence, subsonic, audio_classifier, tail_exceedance,
    format_consistency, length_consistency.
    ```

### Unit Tests — Processors

  - priority: high
    ```md
    tests/test_processors/test_bimodal_normalize.py
    Test gain decisions for transient, sustained, silent, and edge cases.
    ```

### Pipeline Integration Tests

  - priority: high
    ```md
    tests/test_pipeline.py: analyze → plan → prepare round-trip.
    Covers: topological sort, group levelling, fader offsets, staleness.
    ```

### Test Factories

  - priority: high
    ```md
    tests/factories.py: make_audio(), make_track(), make_session().
    Deterministic, file-I/O-free synthetic test objects for use across all test modules.
    ```

### Undo Execution

  - priority: high
    ```md
    Rollback last transfer/sync batch using DawCommand.undo_params.
    Data model ready; execution not implemented.
    ```

### ProTools sync()

  - priority: high
    ```md
    Incremental delta push — currently raises NotImplementedError.
    Compare current session state against transfer() snapshot; send only changes.
    ```

### GUI DAW Tools Panel

  - priority: high
    ```md
    Color picker, etc. routed through execute_commands().
    Enables ad-hoc DAW operations from the GUI without a full Transfer.
    ```

### Multi-Mic Phase Coherence Within Groups

    ```md
    Kick_In and Kick_Out with opposite polarity cancel when summed.
    Grouping preserves gain but doesn't catch this.
    Approach: for each group, compute pairwise correlation in low-frequency band (< 500 Hz).
    Threshold: warn if correlation < 0.
    Categorization: ATTENTION (within group context)
    ```

### Email-Ready Issue Summary Generator

    ```md
    Detection without communication is incomplete.
    Goal: copy/paste request for corrected exports.
    Implementation: --email-summary flag or automatic sessionprep_issues.txt.
    Format: grouped PROBLEMS / ATTENTION with per-file details and a polite request template.
    ```

### Renderer ABC

  - priority: medium
    ```md
    Add Renderer ABC to rendering.py:
    - render_diagnostic_summary(summary) -> Any
    - render_track_table(tracks) -> Any
    - render_daw_action_preview(actions) -> Any
    Also: DictRenderer (raw dicts for JSON/GUI) and wrap existing functions into PlainTextRenderer.
    ```

### Session Detector Result Storage

  - priority: medium
    ```md
    Move session-level detector results out of session.config.
    Currently stored as session.config[f"_session_det_{det.id}"] in pipeline.py.
    SessionContext should have a dedicated session_detector_results: dict field.
    Avoids using config as a grab-bag for runtime state.
    ```

### Split rendering.py Aggregation from Rendering

  - priority: medium
    ```md
    build_diagnostic_summary() (465 lines) contains substantial data aggregation logic.
    When Renderer ABC is introduced, the builder should move to its own module.
    The rendering module should only contain format-specific output (plain text, Rich, etc.)
    ```

### Loudness Range (LRA) Measurement

  - priority: medium
    ```md
    Beyond crest — flag heavily compressed vs genuinely dynamic tracks.
    Complements over-compression detection.
    ```

### Spectral Gaps / Aliasing Artifacts

  - priority: medium
    ```md
    Detect: Notch at 16kHz (MP3 artifact), energy above Nyquist/2 (bad SRC),
    missing expected low end (e.g., "Bass" track with nothing below 100Hz).
    Categorization: ATTENTION
    ```

### Reverb / Bleed Estimation

  - priority: medium
    ```md
    Signal that doesn't drop > 30 dB within 200ms after transients.
    Affects processing decisions (gating, compression threshold).
    Categorization: ATTENTION (informational)
    ```

### Start Offset Misalignment

  - priority: medium
    ```md
    Report time-to-first-content; flag outliers.
    Catches "forgot to bounce from session start" errors.
    Categorization: ATTENTION
    ```

### Detector Performance Optimization

  - priority: medium
    ```md
    Profiled on 27-track session: audio_classifier 577–1470 ms/file (~60% of total),
    subsonic 460–1940 ms/file, stereo_compat 470–600 ms/file on stereo.
    Ideas: cache shared FFT/STFT data, downsample before classification, vectorize hot loops.
    ```

### Optional Auto-HPF on Processing

  - priority: medium
    ```md
    --hpf 30 flag to clean subsonic garbage on processing.
    Opt-in only, never default.
    ```

### Automatic Sample Rate Conversion

  - priority: medium
    ```md
    --target_sr 48000 with high-quality resampling (libsamplerate).
    Destructive operation — needs clear user intent.
    ```

### Tempo / BPM Metadata Consistency

  - priority: medium
    ```md
    Warn if mixed or missing BPM metadata across files.
    Scope note: only meaningful if source files carry reliable tempo metadata.
    Categorization: ATTENTION
    ```

### Track Duration Sub-second Mismatches

  - priority: medium
    ```md
    "Length mismatches" detector exists; may need threshold tuning for sub-second differences.
    Common export error (e.g., one track is 3:24 and another is 3:25).
    ```

### LUFS Measurement

  - priority: medium
    ```md
    Nice context but usually doesn't change decisions.
    Display: add to per-file stats, include integrated + short-term.
    ```

### Config and Queue Tests

  - priority: high
    ```md
    tests/test_config.py: validate_config, merge_configs, preset load/save.
    tests/test_queue.py: SessionQueue priority ordering, job lifecycle.
    ```

### "Effectively Silent" / Noise-Only Detection

  - priority: high
    ```md
    Current is_silent only triggers on peak == 0.0 (absolute zero).
    Flag when: peak < -40 dBFS AND crest < 6 dB AND content is spectrally flat.
    Complements SNR estimation (that's for files WITH content; this is for noise-only files).
    Categorization: ATTENTION
    ```

### Click / Pop Detection

  - priority: high
    ```md
    Isolated transients that are anomalously loud — editing errors, plugin glitches, mouth clicks.
    Flag when a single sample or very short window (< 5ms) exceeds local RMS by > 20 dB.
    Categorization: ATTENTION
    ```

## P0

### Over-Compression / Brick-Wall Limiting Detection

    ```md
    A track with crest < 6 dB, peak > -1 dBFS, and RMS > -8 dBFS has been crushed.
    Dynamics may be unrecoverable — changes how the mix engineer approaches the track.
    Heuristic: if crest < 6 and peak > -1 and rms > -8: warn(...)
    Categorization: ATTENTION
    ```

### Noise Floor / SNR Estimation

    ```md
    Quiet vocal comp with audible hiss/hum — current silent-file detection misses this.
    Approach: find silent sections (RMS < threshold for > 500ms), measure noise floor RMS,
    compute SNR = signal_rms_db - noise_floor_db. Warn if SNR < 30 dB.
    Categorization: ATTENTION
    ```

### Stereo Narrowness Detection

  - priority: low
    ```md
    Stereo file that's effectively mono (not identical, but < 5% width).
    Could save disk space / simplify processing.
    Categorization: ATTENTION (informational)
    ```

### DC Offset Removal

  - priority: high
    ```md
    Why detect it if we won't fix it?
    --fix_dc flag or automatic in execute mode.
    ```

## In Development

### InnoSetup Installer

  - defaultExpanded: false

## Done

