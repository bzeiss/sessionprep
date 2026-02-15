# SessionPrep — Technical Background

This document explains the audio engineering concepts behind SessionPrep:
why it exists, what problem it solves, and how its normalization strategy works.
Some of the reasoning below is opinionated and reflects one practical approach
to session preparation — not the only valid one.

For detector and analysis reference, see [REFERENCE.md](REFERENCE.md).
For development setup and architecture, see [DEVELOPMENT.md](DEVELOPMENT.md).

---

## Table of Contents

1. [Physics vs. Flow](#1-physics-vs-flow)
2. [Bimodal Normalization](#2-bimodal-normalization)
3. [Signal Chain & Workflow](#3-signal-chain--workflow)
4. [Why This Is Not Circular Logic](#4-why-this-is-not-circular-logic)
5. [What Happens Next (The Manual Mix Phase)](#5-what-happens-next-the-manual-mix-phase)
6. [Core Concepts & Practical Notes](#6-core-concepts--practical-notes)

---

## 1. Physics vs. Flow

In a professional mixing environment, the engineer is trapped between two
opposing requirements: the (possibly) rigorous physics of software emulation and the
delicate artistic balance of the rough mix.

### Goal A: The Physics (Predictable headroom and drive)

Some analog modelled and drive-sensitive plugins (channels, tape, transformer/tube
saturation, some compressors) can respond differently depending on input level.

The `-18 dBFS` reference is the default (`--target_rms -18`) but can be adjusted
to match your workflow. It is one conservative starting point when you want
predictable headroom and "sane" starting levels at the top of the channel.
The reference level is ultimately a matter of choice — some engineers calibrate
to `-14 dBFS` or higher to deliberately drive plugins harder and make more use
of their saturation and harmonic characteristics.
Many modern plugins (clean EQs, reverbs, delays, modern dynamics, and many recent
analog emulations) are far more forgiving and/or auto-calibrated, so this is not
presented as required for plugins to work correctly.

Even when a plugin does not "need" this, a conservative starting level is usually
low-risk: it tends to preserve headroom, reduce surprise overs, and keep drive/
threshold controls in a familiar range.

Practical limitation:
  - Insert chains have multiple stages, and each stage can have its own "best"
    operating level. SessionPrep only standardizes the level at the top of the
    channel; you still trim between inserts as needed.

1. **NON-LINEAR RESPONSE:**
   Analog gear (and its software clones like SSL channels, 1176s, Tape Machines)
   does not react linearly to gain.
   - Many designs are calibrated around a nominal reference level
     (often discussed as ~0 VU; the exact dBFS-to-VU mapping varies by
     plugin — commonly -18, -20, or -14 dBFS = 0 VU).
   - As you push past -10 dBFS, transformers saturate, tubes compress, and
     op-amps clip.

2. **THE "CALIBRATION GAP":**
   If a producer sends you a modern synth track peaking at -0.1 dBFS, and you
   insert a modeled Neve EQ:
   - Some older or more strictly calibrated models may "hear" a signal much
     hotter than the hardware was designed for.
   - Result: The tone/dynamics may change in a way you did not intend.
   - The Fix: Use the plugin input trim (or a trim plugin) where needed.

3. **THE DYNAMIC BEHAVIOR:**
   Compressor attack and release curves are often program-dependent. Feeding
   a compressor a signal 20dB lower than expected results in sluggish detection
   and weak, transparent compression instead of the "grab" you expect.

### Goal B: The Art (The "Rough Mix" Approval)

The client, producer, and A&R have been listening to the "Rough Mix" for weeks.
They have emotionally bonded to the relative balance: the kick is dominating,
the backing vocals are buried, the shaker is loud.

1. **THE "DEMOITIS" DANGER:**
   If you normalize every track to a technical standard (-18 dBFS), you flatten
   the mix. The kick and shaker are now equal volume. The balance is destroyed.

2. **THE PSYCHOLOGICAL COST:**
   If you start your mix with a flat balance, you spend the first 3 hours just
   trying to recreate the vibe the client already had. You are working backward,
   not forward.

### The Conflict & The Solution

The Conflict:
  - If you preserve the file levels (Art), drive-sensitive plugins may exhibit calibration-dependent behavior.
  - If you normalize the file levels (Physics), your balance is lost.

Practical note:
  - A conservative gain staging approach that keeps analog modelled/drive-sensitive
    plugins in a predictable operating range will still work fine with most modern
    wide-headroom plugins. The targets here are chosen to be compatible with both.

The "Manual" Solution (The Slog):
  Professional mixers solve this by manually adjusting Clip Gain on every single
  region to hit the sweet spot, then counter-adjusting the Fader to match the
  rough mix volume. This takes hours of non-creative clicking.

The Script's Solution:
  It follows an analysis-first workflow:
  - Dry-run (default): analyze the session and print a session health/level overview.
  - Execute (`-x`): optionally write processed tracks and export fader offsets.

  In execute mode, it sets a consistent starting level into the first insert
  (Physics) and calculates the inverse fader offsets to preserve the original
  balance (Art). It also exports
  a machine-readable `sessionprep.json` intended for a follow-up automation tool
  (e.g., SoundFlow) to set faders in the DAW automatically.

---

## 2. Bimodal Normalization

Most normalizers are "dumb" — they treat a Kick drum and a Synth Pad the same.
If you normalize a Kick drum to -18 dB RMS, you can severely compromise it (the peak
may exceed 0 dBFS and clip, because the body is so thin relative to the transient).

### Calibration Note

`-18 dBFS RMS` is a common *conservative* reference point for gain staging into
analog-modeled / drive-sensitive processing, but it is not universal.

Practical reality:
  - Many modern plugins (FabFilter, Valhalla, most stock DAW plugins) are largely
    headroom-agnostic and behave consistently across a wide range of input levels.
  - Some "analog-modeled" plugins auto-calibrate internally (for example certain
    Plugin Alliance / TMT-style designs), so the exact input level is less critical.
  - Other analog-modeled chains (channel strips, tape, transformers/tubes, some
    compressors) can still be noticeably level-dependent.

For that reason, target levels are intentionally adjustable via command line:
  - `--target_rms` for sustained sources
  - `--target_peak` for transient sources / peak ceiling

Defaults:
  - `--target_rms -18`
  - `--target_peak -6`

If your chain is mostly modern, wide-headroom plugins, you can run hotter by
raising `--target_rms` (e.g. -16). If your chain is heavy on saturation/tape/legacy
calibrated plugins, -18 remains a safe default.

Example (modern/hotter chain):
  - `--target_rms -14 --target_peak -3`

Even with the correct target, edge-case outliers (or section-to-section macro
dynamics) may still require manual clip gain adjustments in the DAW.

### The Bimodal Classification Strategy

  Classification uses three metrics: **crest factor** (peak-to-RMS ratio),
  **envelope decay rate** (how fast energy drops after the loudest moment),
  and **density** (fraction of the track containing active content).
  Very sparse tracks with at least one agreeing dynamic metric are classified
  as Transient (catches toms, crashes, FX hits). For non-sparse tracks, crest
  and decay vote together with decay as tiebreaker.

  **TYPE 1: TRANSIENT MATERIAL** (High Crest + Fast Decay)
  (Drums, Percussion, Stabs)
  -> The Script normalizes these to PEAK Level (-6 dBFS).
  -> Goal: Preserve impact and headroom. We don't care about RMS voltage here;
     we care about not clipping the transient.
  -> Also catches compressed drums (low crest but fast decay) that crest factor
     alone would misclassify as sustained.

  **TYPE 2: SUSTAINED MATERIAL** (Low Crest + Slow Decay)
  (Bass, Vocals, Guitars, Synths)
  -> The Script targets RMS Level (-18 dBFS) but is peak-limited by the
     configured peak ceiling (-6 dBFS).
  -> Goal: Ensure consistent "Body" and Voltage. These tracks drive the saturation
     characteristics of your plugins (Tubes, Tape, Transformers).
     If a file hits the peak ceiling before reaching the RMS target, it will end
     up below the RMS target by design.
  -> Also catches plucked instruments and piano (high crest but slow decay) that
     crest factor alone would misclassify as transient.

This hybrid approach ensures that drums remain punchy while sustained instruments
get the "thick" analog sound you want.

---

## 3. Signal Chain & Workflow

This section summarizes the signal flow this tool is designed around in a typical
DAW (Pro Tools, Logic, Cubase, Reaper, Ableton).

This tool is intentionally two-phase:
1) Analysis-first (default): it prints a session overview so you can decide what
   to fix/request before creating the DAW session.
2) Processing second (optional, `-x`): it writes processed tracks and exports
   automation data for restoring the rough balance.

The signal path is not a single volume knob. It is a chain:

```
[SOURCE FILE]
      |
[CLIP GAIN]    <--- STEP 1: OPTIMIZATION (THE SCRIPT)
      |             (Optional, execute mode) We modify the actual file gain
      |             (non-destructively) to hit a chosen target calibration.
      |
[INSERTS]      <--- STEP 2: THE "SWEET SPOT" (THE PHYSICS)
      |             This is where your plugin chain lives.
      |             The first insert receives a predictable input level.
      |             You still gain-stage between inserts as needed.
      |
[FADER]        <--- STEP 3: RESTORATION (THE REPORT)
      |             We counteract the gain change using the Fader.
      |             This restores the Producer's intended balance.
      |             The exported sessionprep.json is designed for automation
      |             tools (e.g., SoundFlow) to apply these fader offsets.
      |
[MIX BUS]
```

---

## 4. Why This Is Not Circular Logic

A common doubt among engineers:
"If you boost the file +4dB (Clip Gain) and cut the fader -4dB, haven't you
just ended up exactly where you started?"

THE ANSWER: NO. You have changed the signal hitting the INSERTS.

Consider a quiet synth pad sitting at -30 dBFS:
1.  **Without this script:** The signal hits your Analog Compressor plugin at
    -30 dB. The threshold knob is at noon, but the compressor may not engage
    meaningfully. The tubes don't saturate. It may sound thin. You have to
    crank the "Input" knob 12dB just to wake it up.
2.  **With this tool (execute mode):** The signal hits the Compressor at a more
    predictable operating level. That can make thresholds/drive controls behave
    more consistently for calibrated or drive-sensitive plugins.
3.  **The Fader:** The fader (applied manually or via automation) restores the
    intended rough balance.

You can end up with the same perceived mix balance, but with a different internal
gain structure feeding the inserts.

---

## 5. What Happens Next (The Manual Mix Phase)

This script does not replace the Mix Engineer. It simply sets the table.
Once you have created a fresh session and imported the prepared tracks (and applied
the fader offsets manually or via automation), the real work begins.

This is where manual clip gain riding still matters: if a verse is 6 dB quieter
than the chorus (or an intro is sparse but the outro is dense), you will still
shape those section-to-section macro dynamics by hand.

You are now in a superior starting position:

1.  **High-Resolution Fader Throws:**
    This tool optimizes the level hitting inserts; the required fader offsets may
    be small or large depending on the source material and the chosen targets.
    If your resulting fader positions end up near Unity (0 dB), you benefit from
    higher-resolution moves around 0 dB. If the offsets push faders far from unity,
    you still gain the primary benefit: predictable insert drive.

2.  **Dynamic Clip Gain (The Human Touch):**
    The script sets a "Global Anchor" (e.g., maximizing the loudest Chorus).
    Now, YOU do the musical work:
    - Grab the quiet Verse regions and clip-gain them up manually.
    - Spot a harsh "S" or pop and clip-gain it down.
    - The script handled the macro-physics; you handle the micro-dynamics.

3.  **Creative Automation:**
    The calculated fader positions are just a static starting point to match
    the rough mix. As you mix, you will (and should) automate faders, push
    into compressors, and change balances. You are simply starting from a
    grid of calibrated, healthy signals rather than a chaotic mess of files.

---

## 6. Core Concepts & Practical Notes

### RMS vs. LUFS

LUFS is an excellent standard for broadcast delivery and perceived loudness, but
its K-weighting and gating are designed to match hearing, not electrical energy.
For gain-staging analog modelled and drive-sensitive processing, a momentary RMS
measure is a simple and predictable proxy for average signal energy. It does not
perfectly model analog circuit behavior, but its simplicity makes it practical
for setting consistent starting levels.

### Internal gain structure (why clip gain and fader offsets both exist)

Changing clip gain and applying the inverse fader offset can preserve the rough
balance while still changing the level feeding inserts. This can matter for
calibrated plugins and non-linear stages where drive changes tone/dynamics.
It can also improve fader control resolution in cases where the resulting fader
positions end up closer to unity, but that is not guaranteed.

### Classification is heuristic (transient vs sustained)

The transient/sustained split is intentionally a heuristic based on three metrics:
crest factor (peak-to-RMS ratio), envelope decay rate (energy drop after the
loudest moment), and density (fraction of active content). Classification priority:
  1. Sparse + at least one dynamic metric agrees → Transient (toms, crashes, FX)
  2. High crest + slow decay → Sustained (plucked instruments, piano)
  3. Low crest + fast decay → Transient (compressed drums, loops)

It will still be wrong for some sources. The script supports explicit overrides:
  - `--force_transient ...`
  - `--force_sustained ...`

Thresholds are adjustable:
  - `--crest_threshold 12` (default) — crest factor above this suggests transient
  - `--decay_db_threshold 12` (default) — energy drop above this suggests transient
  - `--decay_lookahead_ms 200` (default) — time window for measuring decay
  - `--sparse_density_threshold 0.25` (default) — tracks with less active content are sparse

If you see systematic misclassification, adjust thresholds for the session,
and use the force flags to lock edge cases.

### Multi-mic sources (phase vs non-linearity)

Constant gain does not change time alignment, so phase relationships are
preserved. The practical risk is tonal divergence if each mic is driven into
non-linear per-channel processing differently. If you care about matched behavior,
prefer bus processing, or keep heavy saturation/compression on the group bus.

Track grouping (multi-mic and bundles):
If a set of tracks should behave as a single instrument (e.g. kick in/out/sub,
snare top/bottom, BV stacks), use `--group Name:pattern1,pattern2` so those
files get identical applied gain. This preserves internal balance and avoids
changing how the bundle hits a bus compressor.

Example:
  ```
  --group Kick:kick,kick_sub --group OH:overhead,oh --group Toms:tom
  ```

Patterns support substring, glob (`*`/`?`), or exact match (suffix `$`).
First match wins — if a file matches multiple groups, it is assigned to the
first matching group and a warning is printed.

### Windowing and anchor strategy

Sustained-material gain is computed against a single representative RMS value
called the **anchor**. Finding this anchor is a multi-step process:

1. **Window**: Slice the track into overlapping short-time RMS windows (default
   400 ms). Each window produces one momentary RMS value. Together they form a
   distribution of momentary loudness across the file.

2. **Gate**: Discard windows that are far below the loudest window
   (`--gate_relative_db`, default 40 dB). This removes silence and very quiet
   passages, leaving only "active" content. Critical for sparse tracks (FX
   hits, vocal doubles, breakdown elements) where most windows are near-silent.

3. **Select anchor**:
   - **`percentile`** (default): Take the Nth percentile of the gated
     distribution (default P95). This represents "what the loud sections
     typically sound like" while ignoring rare spikes — a single anomalous
     window (breath pop, drum bleed, feedback ring) cannot pull the anchor
     away from the track's true working level.
   - **`max`**: Take the single loudest gated window. Useful for very short
     files (single hits, sound effects) but fragile for longer material.

4. **Use**: The anchor drives the sustained gain calculation
   (`gain = target_rms − anchor`, capped by `target_peak − peak`) and defines
   the baseline for tail exceedance reporting.

Why P95 is the default: most real-world tracks have occasional moments louder
than their "working level" (a vocalist leaning in, a dynamic fill, an
arrangement accent). Max anchoring treats those moments as the reference,
under-gaining the rest of the file. Percentile anchoring tracks the chorus-
level loudness that will actually drive your insert processing, and flags the
louder moments as tail exceedances for manual clip-gain attention.

See [REFERENCE.md §3.3](REFERENCE.md) for a detailed walkthrough with examples.

### Tail (significant exceedances)

The tail report is meant to be actionable for a mix engineer, not an exhaustive
dump of every window above the anchor. By default it reports only significant
exceedances above the anchor:
  - `--tail_min_exceed_db` defaults to 3.0 dB
  - `--tail_hop_ms` defaults to 10 ms to reduce noise and region spam
  - `--tail_max_regions` caps the number of regions per file

### True peaks / inter-sample peaks (ISP) and headroom

This tool is designed for mixing headroom, not mastering. By enforcing a conservative
sample-peak ceiling (default `--target_peak -6`), the signal retains enough margin
that typical ISP overshoots are not a practical problem in the session context.

### Pre-session workflow and automation

The default mode is analysis-first: it prints a session overview so you can fix
format issues, clipping, DC offset, correlation concerns, and other problems
before creating a DAW session. Execute mode (`-x`) is optional: it writes processed
tracks and exports `sessionprep.json`, intended for a follow-up automation tool
(e.g., SoundFlow) to apply fader offsets in Pro Tools.

### Heuristic limitation

The momentary RMS anchor (percentile or max) sets a single global reference per
file. It does not replace musical judgment or section-based leveling. Expect to
ride clip gain (or use automation) to manage macro dynamics between song sections.
