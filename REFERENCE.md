# SessionPrep — Analysis & Processing Reference

This document is the detailed reference for every detector, analysis metric,
and processing stage in SessionPrep.

For the technical rationale, see [TECHNICAL.md](TECHNICAL.md).
For usage and quick start, see [README.md](README.md).

---

## Table of Contents

1. [Diagnostic Categories](#1-diagnostic-categories)
2. [Detectors](#2-detectors)
3. [Analysis Metrics (Stage B)](#3-analysis-metrics-stage-b)
4. [Processing (Stage C)](#4-processing-stage-c)
5. [Fader Restoration (Stage D)](#5-fader-restoration-stage-d)
6. [Normalization Hints](#6-normalization-hints)

---

## 1. Diagnostic Categories

The terminal output and `sessionprep.txt` are organized into four high-level
categories:
  - **PROBLEMS**: typically require fixing before mixing.
  - **ATTENTION**: may be intentional, but worth checking.
  - **INFORMATION**: useful context that generally does not require client fixes and typically does not change processing decisions.
  - **CLEAN**: explicit "No ... detected" lines when checks pass.

### What makes a detector worth having?

A detector earns its place if it meets at least one of these criteria:
  - Catches something you can't easily hear
  - Catches something that wastes significant time if discovered late
  - Catches something the client/label needs to fix
  - Changes your processing decisions

---

## 2. Detectors

### 2.1 File errors

- **What it means:** a file could not be read/analyzed.
- **Why it matters:** missing/failed files break prep and invalidate the session overview.
- **Categorization:**
  - PROBLEMS: any file errors were encountered.

### 2.2 Session format consistency

- **What it means:** whether all analyzed files share the same sample rate / bit depth.
- **Why it matters:** inconsistent session formats create import problems, SRC surprises, and alignment risk.
- **Categorization:**
  - CLEAN: `No inconsistent session formats` (all files match the most common format).
  - PROBLEMS: per-file `Format mismatches` when any file differs from the most common format (the header includes the most common/reference format summary).

### 2.3 File length consistency

- **What it means:** whether all analyzed files have the same timeline length (normalized to the most common sample rate).
- **Why it matters:** mismatched track lengths can cause misalignment, missing tails, or timing drift when importing.
- **Categorization:**
  - CLEAN: `No inconsistent file lengths` (includes the most common length in samples and minutes:seconds).
  - PROBLEMS: per-file `Length mismatches` when any file differs from the most common length (the header includes the most common/reference length summary).

### 2.4 Digital clipping

- **What it means:** consecutive near-full-scale samples were detected (close to +/-1.0). For stereo files this is checked per channel.
- **Why it matters:** clipped prints are often unrecoverable and will distort downstream processing.
- **Controls:** `--clip_consecutive`, `--clip_report_max_ranges` (the report threshold is the run length: how many consecutive near-full-scale samples are required to count as clipping)
- **Categorization:**
  - CLEAN: `No digital clipping detected`.
  - PROBLEMS: `Digital clipping` with per-file details.

### 2.5 DC offset

- **What it means:** the file has a measurable DC component above `--dc_offset_warn_db`.
- **Why it matters:** DC can reduce headroom, bias compressors/saturators, and skew metering.
- **Controls:** `--dc_offset_warn_db`
- **Categorization:**
  - CLEAN: `No DC offset issues detected`.
  - ATTENTION: `DC offset` with per-file details.

### 2.6 Stereo compatibility

- **What it means:** one or more stereo-compatibility warnings were detected:
  - correlation below `--corr_warn` and/or
  - mono fold-down loss above `--mono_loss_warn_db`
- **Why it matters:** these values provide context about stereo content and mono fold-down behavior.
- **Controls:** `--corr_warn`, `--mono_loss_warn_db`
- **Categorization:**
  - INFORMATION: `Stereo compatibility` with per-file details.

### 2.7 Dual-mono (identical L/R)

- **What it means:** a stereo file appears to have the same signal in L and R.
- **Why it matters:** usually a valid delivery choice and often intentional.
- **Controls:** `--dual_mono_eps`
- **Categorization:**
  - INFORMATION: `Dual-mono (identical L/R)` with per-file details.

### 2.8 Silent files

- **What it means:** the file is all zeros (or effectively empty).
- **Why it matters:** may be intentional (placeholder) but is often an export issue.
- **Categorization:**
  - CLEAN: `No silent files detected`.
  - ATTENTION: `Silent files` with per-file details.

### 2.9 One-sided silence

- **What it means:** a stereo file has one channel that is effectively silent while the other has signal.
- **Why it matters:** often an export/cabling/routing mistake; importing it as stereo can cause unexpected balance issues.
- **Controls:** `--one_sided_silence_db`
- **Categorization:**
  - CLEAN: `No one-sided silent stereo files detected`.
  - ATTENTION: `One-sided silence` with per-file details.

### 2.10 Subsonic content

- **What it means:** the file has significant energy below a cutoff frequency
  (default `30 Hz`).
- **Why it matters:** subsonic rumble can eat headroom, trigger
  compressors/limiters, and cause translation issues on smaller speakers.
- **Controls:** `--subsonic_hz`, `--subsonic_warn_ratio_db`,
  `subsonic_windowed`, `subsonic_window_ms`, `subsonic_max_regions`
- **Categorization:**
  - CLEAN: `No significant subsonic content detected`.
  - ATTENTION: `Subsonic content` with per-file details (consider an HPF).

**How detection works:**

The detector computes an FFT-based energy ratio: `power below cutoff / total
power` (excluding DC), expressed in dB. A ratio of −20 dB means 1 % of the
signal's energy is subsonic. The configured threshold (default `−20 dB`)
determines when this becomes an ATTENTION.

**Per-channel analysis (always active for stereo+):**

For multi-channel files, each channel is analyzed independently in addition to
the combined (mono-downmixed) whole-file ratio. This catches subsonic issues
isolated to one channel (bad cable, ground loop on one side) that a mono
downmix would dilute. If only one channel exceeds the threshold, the issue is
reported for that specific channel; if all channels exceed it, a whole-file
issue is reported.

**Windowed analysis (default: on):**

When `subsonic_windowed` is enabled, the detector also splits each channel into
short windows (default 500 ms via `subsonic_window_ms`) and computes the
subsonic ratio per window. Contiguous windows exceeding the threshold are
merged into regions with precise sample ranges, visible as waveform overlays.

This serves two purposes:
  1. **Localization** — shows *where* subsonic content is concentrated (bass
     drops, HVAC bleed in quiet sections) instead of painting the entire file.
  2. **Silent-window gating** — windows with RMS below −80 dBFS are
     automatically skipped, preventing false positives from near-silent gaps
     between notes where floating-point noise can produce misleading ratios.

**Threshold relaxation for windowed analysis:**

Individual 500 ms windows have less frequency resolution than the whole-file
FFT (~22k samples vs ~200k). When the whole-file ratio is borderline (e.g.
−18 dB vs −20 dB threshold), no single window may cross the same threshold
even though the aggregate clearly does. To handle this, the windowed analysis
uses a relaxed threshold (6 dB below the configured threshold). This ensures
that windows where subsonic energy is concentrated still produce visible
regions.

If even the relaxed threshold produces no regions (very diffuse subsonic
content), a whole-file overlay is shown as fallback — an ATTENTION result
always has at least one visible issue.

### 2.11 Tail regions exceeded anchor

- **What it means:** contiguous regions where momentary RMS exceeds the file's anchor by more than `--tail_min_exceed_db`.
- **Why it matters:** highlights "quiet overall but occasionally very loud" tracks that may need section-based rides.
- **Controls:** `--tail_min_exceed_db`, `--tail_hop_ms`, `--tail_max_regions`
- **Categorization:**
  - ATTENTION: `Tail regions exceeded anchor` with per-file details.

### 2.12 Grouping overlaps

- **What it means:** a file matched multiple `--group` specs.
- **Why it matters:** indicates ambiguous patterns; grouping may not apply as intended.
- **Controls:** `--group`, `--group_overlap`
- **Categorization:**
  - ATTENTION: `Grouping overlaps` with overlap details.

---

## 3. Analysis Metrics (Stage B)

Stage B is where SessionPrep extracts level and dynamic information from each
file using windowed measurements. This is meant to be more representative than
a single "whole-file average", especially for tracks with intros, dropouts, and
sparse events.

### 3.1 Peak amplitude (dBFS)

**What it is:**
  - The maximum absolute sample value converted to dBFS (sample peak).

**Why it matters:**
  - A fast reality check for headroom and accidental full-scale content.
  - Useful context for how hot a track is feeding inserts in a DAW.

**Relevance for mix engineers:**
  - Quickly reveals "too hot to mix comfortably" tracks (even if the perceived
    loudness is low).

### 3.2 Momentary RMS windows (short-time energy)

**What it is:**
  - RMS is computed over sliding windows (default `--window 400` ms) across the
    file, creating a distribution of momentary loudness.

**Why it matters:**
  - Windowing avoids being dominated by long silences or by a single loud hit.

**Relevance for mix engineers:**
  - Better matches how you perceive a track's "working level" when you start
    inserting compressors, saturators, and channel strips.

### 3.3 Anchor selection (percentile vs. max)

**What it is:**

The RMS analysis produces many short-time RMS values — one per window (default
400 ms). After relative gating removes near-silent windows (see §3.4), we have
a distribution of momentary RMS levels representing the "active" parts of the
track. The **anchor** is the single representative value chosen from this
distribution, used as the reference level for gain decisions and tail
exceedance reporting.

Two strategies are available:

  - **`percentile`** (default, `--rms_anchor percentile`):
    Takes the Nth percentile of the gated window distribution
    (default P95 via `--rms_percentile 95`). This means 95 % of the active
    windows are at or below the anchor — in practice, the anchor represents
    "what the loud sections of this track typically sound like."

  - **`max`**:
    Takes the single loudest gated window. The anchor is the absolute peak of
    the RMS distribution.

**Why percentile is usually better than max:**

Consider a vocal track with a consistent verse at −20 dBFS RMS, a loud chorus
at −16 dBFS, and one isolated shout that hits −12 dBFS for a single 400 ms
window:

  - **Max anchor** = −12 dBFS. Gain is computed against that one shout, which
    means the verse and chorus are treated as "quieter than the track's level."
    The tail exceedance report is clean (nothing exceeds the max), but the gain
    decision is driven by a moment that may not represent the working level of
    the track at all.

  - **P95 anchor** ≈ −16 dBFS (roughly the chorus level). Gain is computed
    against the chorus — the part that matters most when feeding insert
    processing. The shout shows up as a tail exceedance, flagging it for
    manual clip-gain attention.

In general, **max** is fragile because a single anomalous window (a breath
pop, a drum bleed spike, a momentary feedback ring) can pull the anchor away
from the track's true working level. **Percentile** is robust to these
outliers while still tracking the loud sections faithfully.

**When max is useful:**

  - Very short, punchy files (a single hit, a sound effect) where there is no
    meaningful "distribution" — every window matters equally.
  - When you explicitly want the gain decision anchored to the absolute loudest
    moment in the file.

**How P95 works step by step:**

  1. Compute momentary RMS for each 400 ms window across the file.
  2. Gate: discard windows more than `--gate_relative_db` (default 40 dB) below
     the loudest window. This removes silence and very quiet sections.
  3. Sort the remaining "active" windows by RMS value.
  4. Pick the value at the 95th percentile of that sorted list.
  5. Convert to dBFS → this is `rms_anchor_db`.

The anchor is then used to:
  - Compute sustained-material gain: `gain = target_rms − anchor`
    (capped by `target_peak − peak`).
  - Identify tail exceedances: windows that exceed the anchor by more than
    `--tail_min_exceed_db` are reported as regions needing manual attention.

**Relevance for mix engineers:**
  - Percentile anchoring reflects "the part of the track that matters" — the
    loud sections that will drive your insert processing — rather than a
    momentary spike that may not represent the track's working level.
  - If you find that gain decisions are too conservative (track ends up quieter
    than expected), lower the percentile (e.g., `--rms_percentile 90`).
  - If gain decisions are too aggressive (track ends up too hot), raise the
    percentile or switch to `--rms_anchor max`.

### 3.4 Relative gating for sparse tracks

**What it is:**
  - Before anchor/max/tail statistics are computed, momentary windows that are
    far below the loudest window are ignored.
  - The gate is *relative* to the loudest window:
    `threshold_db = max_window_db - gate_relative_db`

**Why it matters:**
  - Sparse tracks (mostly silence with a few hits/phrases) can otherwise produce a
    misleadingly low percentile anchor dominated by silence.

**Relevance for mix engineers:**
  - Prevents "false alarm" tail exceedances and keeps analysis anchored to the
    actual musical content.

**Parameter intuition:**
  - `--gate_relative_db 40` means "keep windows within 40 dB of the loudest RMS
    window." It is not an absolute dBFS value (so it is not `-40`).

### 3.5 Audio classification (crest factor + envelope decay + density)

**What it is:**
  - A three-metric classifier that combines crest factor (peak-to-RMS ratio),
    envelope decay rate (how fast energy drops after the loudest moment), and
    density (fraction of the track containing active content above the gate).
  - Crest factor alone can misclassify compressed drums (low crest but transient)
    and plucked instruments (high crest but sustained). The decay metric acts as
    a tiebreaker when the two metrics disagree. Density catches sparse percussion
    (toms, crashes, FX hits) that may have ambiguous crest and decay values.

**Classification logic (in priority order):**
  1. Keyword overrides (`--force_transient`, `--force_sustained`)
  2. Sparse + at least one dynamic metric agrees → Transient (toms, crashes, FX)
  3. High crest + fast decay → Transient (drums, percussion)
  4. Low crest + slow decay → Sustained (pads, bass, vocals)
  5. High crest + slow decay → Sustained (plucked/piano — decay overrides)
  6. Low crest + fast decay → Transient (compressed drums — decay overrides)

**Why it matters:**
  - Helps pick a more appropriate normalization strategy.
  - Explains why certain tracks behave differently when you drive dynamics and
    saturation processing.
  - If either crest or decay is close to its threshold, the `Normalization hints`
    section will flag an edge case and suggest `--force_transient` / `--force_sustained`.
  - Sparse tracks (e.g., toms that only play occasionally) are caught by the
    density metric even when crest and decay are ambiguous.

### 3.6 Tail exceedance report (significant regions above anchor)

**What it is:**
  - When using percentile anchoring, the analysis also reports contiguous regions
    where momentary RMS significantly exceeds the anchor.

**Why it matters:**
  - Highlights tracks that have "quiet overall, but occasionally very loud"
    sections, which can affect gain staging and compression decisions.

**Relevance for mix engineers:**
  - Points you to the exact time ranges that may need manual attention (clip gain
    rides, automation, or section-specific treatment).

---

## 4. Processing (Stage C)

In execute mode (`-x/--execute`), based on the analysis, SessionPrep writes a
COPY of your audio to a `processed/` folder.

It applies a specific gain amount to put tracks into a predictable starting
range (default `--target_rms -18` for sustained material or `--target_peak -6`
for transient material). This primarily affects the level feeding the first
insert on each channel.

*Crucially, this is non-destructive.* Your original files are never touched.
The processed files are sample-accurate, gain-shifted copies.
This is mathematically identical to adjusting "Clip Gain" in Pro Tools or other
DAWs, but done with sample-accurate precision across 100+ files very quickly.

---

## 5. Fader Restoration (Stage D)

The script calculates the inverse fader offsets for the gain applied in Stage C.
Applying those offsets happens in the DAW (manually or via automation).

Example:
  - Input: A quiet synth pad (-24 dB).
  - Action: Script adds +6 dB Clip Gain to hit the -18 dB target.
  - Output: Script reports a Fader Offset of -6 dB.

In execute mode (`-x/--execute`), the script generates a text report and a
machine-readable JSON file (`sessionprep.json`) for applying fader offsets.
In dry-run mode (no `-x`), it prints a session overview to the terminal.

This ensures that when you hit play, the mix balance is 100% identical to
the producer's rough mix, your files are error-checked, and your starting levels
are more consistent. Per-insert gain staging is still part of mixing.

> **Planned:** Fader offset application is currently manual or via third-party
> tools (e.g., SoundFlow). A future version will automate this directly via DAW
> scripting APIs such as the
> [Pro Tools Scripting SDK (PTSL)](https://developer.avid.com/).

---

## 6. Normalization Hints

This section (in the output) is not a "health" detector bucket. It is a set of
optional hints related to how the transient/sustained classification may affect
normalization. It is always printed (even without `-x`) so you can review edge
cases without having to re-run in execute mode.

In the `Normalization hints` section, "near transient/sustained threshold" means
the file's crest factor or envelope decay is very close to its respective
threshold (`--crest_threshold` default `12 dB`, `--decay_db_threshold` default
`12 dB`).

Why this matters:
  - The classification uses three metrics (crest factor, envelope decay rate,
    and density) that vote together. When a dynamic metric is borderline, the
    classification could flip with small changes in the audio.
  - Transient tracks are peak-normalized (`--target_peak`).
  - Sustained tracks are RMS-normalized (`--target_rms`) with a peak ceiling.

If either metric is within +/-2 dB of its threshold, the warning is a prompt
to sanity-check the musical intent and optionally override:
  - Use `--force_transient` for drum-like / hit-like material.
  - Use `--force_sustained` for pad-like / sustained material.
