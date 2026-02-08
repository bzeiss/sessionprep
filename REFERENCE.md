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

- **What it means:** the file has significant energy below a cutoff frequency (default `30 Hz`).
- **Why it matters:** subsonic rumble can eat headroom, trigger compressors/limiters, and cause translation issues.
- **Controls:** `--subsonic_hz`, `--subsonic_warn_ratio_db`
- **Categorization:**
  - CLEAN: `No significant subsonic content detected`.
  - ATTENTION: `Subsonic content` with per-file details (consider an HPF).

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
  - The analysis chooses one representative RMS value ("anchor") from the window
    distribution.
  - Default is a percentile anchor (P95 via `--rms_percentile 95`), which tends
    to represent loud sections while ignoring rare spikes.
  - `--rms_anchor max` anchors to the single loudest window.

**Why it matters:**
  - This anchor becomes the reference level used in downstream reporting (and in
    execute mode, gain decisions).

**Relevance for mix engineers:**
  - Percentile anchoring usually reflects "the part of the track that matters"
    instead of a momentary transient.

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

### 3.5 Crest factor + classification

**What it is:**
  - A simple crest factor estimate: `peak - anchor_rms`.
  - Used to classify tracks as "transient" vs "sustained".

**Why it matters:**
  - Helps pick a more appropriate normalization strategy.
  - Explains why certain tracks behave differently when you drive dynamics and
    saturation processing.
  - If the crest factor is close to `--crest_threshold`, the `Normalization hints`
    section will flag an edge case and suggest `--force_transient` / `--force_sustained`.

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
the file is very close to the crest threshold (`--crest_threshold`, default
`12 dB`).

Why this matters:
  - Above the threshold, the file is treated as transient and normalized by peak
    (`--target_peak`).
  - Below the threshold, the file is treated as sustained and normalized by RMS
    (`--target_rms`) with a peak ceiling.

If the crest factor is within +/-2 dB of the threshold, small differences in the
measurement (windowing, gating, fades, edits) can flip the classification. The
warning is a prompt to sanity-check the musical intent and optionally override:
  - Use `--force_transient` for drum-like / hit-like material.
  - Use `--force_sustained` for pad-like / sustained material.
