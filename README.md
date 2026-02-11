# SessionPrep

Batch audio analyzer and normalizer for mix session preparation.

SessionPrep audits and prepares raw audio tracks for professional mix sessions.
It performs the mechanical, non-creative labor of checking files for problems and
normalizing levels, so you can focus on mixing.

This is a **preflight heuristic** and a **starting-point generator**, not a fully
automatic mix prep tool. Manual clip gain riding is still expected for
section-to-section dynamics.

<table>
  <tr>
    <td width="33%">
      <a href="https://github.com/user-attachments/assets/50897f27-1b22-45ef-a017-586a893f8313">
        <img src="https://github.com/user-attachments/assets/50897f27-1b22-45ef-a017-586a893f8313" alt="image 1" width="100%" />
      </a>
    </td>
    <td width="33%">
      <a href="https://github.com/user-attachments/assets/95b92d4f-4720-49bf-a8e5-8affe1685281">
        <img src="https://github.com/user-attachments/assets/95b92d4f-4720-49bf-a8e5-8affe1685281" alt="image 2" width="100%" />
      </a>
    </td>
    <td width="33%">
      <a href="https://github.com/user-attachments/assets/eaec24fe-da54-41c7-ad22-1caf349bb740">
        <img src="https://github.com/user-attachments/assets/eaec24fe-da54-41c7-ad22-1caf349bb740" alt="image 3" width="100%" />
      </a>
    </td>
  </tr>
</table>


---

## Quick Start

**1. Download** `sessionprep-gui` (GUI) or `sessionprep` (CLI) from the
releases page — no Python required.

**2. Run the GUI** — open a session directory and review the analysis:

```
sessionprep-gui
```

The GUI provides an interactive file table, per-track detail view, waveform
and spectrogram display with issue overlays, and audio playback. On first launch it creates a
`sessionprep.config.json` in your user preferences directory with all default
thresholds.

Use **File → Preferences** to customize every detector and processor parameter
through a tree-based settings dialog. Each parameter shows a description,
expected type, valid range, and default value. A reset button next to each
widget restores the default. Changes are saved to `sessionprep.config.json`
and trigger an immediate re-analysis if a session is loaded.

The Preferences dialog also exposes a **HiDPI scale factor** (under General)
that scales the entire UI — useful for high-DPI displays. Changing it requires
an application restart.

**3. Or use the CLI** for scripting and batch workflows:

```bash
sessionprep /path/to/tracks          # analyze (safe, read-only)
sessionprep /path/to/tracks -x       # analyze + process (writes to processed/)
```

**4. Import** processed tracks into your DAW, apply fader offsets from `sessionprep.txt`.

---

## Installation

### Standalone executables (recommended, no Python required)

Download from the releases page:

| Executable | Description |
|------------|-------------|
| `sessionprep-gui` | GUI application (interactive analysis + waveform + playback) |
| `sessionprep` | Command-line tool (scripting, batch workflows, CI) |

### From source

If you prefer to run the Python scripts directly:

```bash
git clone <repo-url>
cd sessionprep
uv sync --all-extras                        # installs core + CLI + GUI dependencies
uv run python sessionprep-gui.py             # run the GUI
uv run python sessionprep.py /path/to/tracks # run the CLI
```

Or manually with pip (if you don't use [uv](https://docs.astral.sh/uv/)):

```bash
pip install .[cli,gui]                      # install with optional dependencies
python sessionprep-gui.py                   # GUI
python sessionprep.py /path/to/tracks       # CLI
```

> **Note:** `uv sync` is the recommended setup — it handles the virtual
> environment, Python version, and all dependencies automatically.
> Both CLI and GUI require `numpy`, `soundfile`, and `scipy`. The GUI
> additionally requires `PySide6` and `sounddevice`; the CLI additionally
> requires `rich`.

See [DEVELOPMENT.md](DEVELOPMENT.md) for full development setup, building, and
distribution instructions.

---

## How It Works

SessionPrep operates in four stages:

| Stage | Name | What happens | When |
|-------|------|-------------|------|
| **A** | Diagnostics | Format checks, clipping, DC offset, stereo sanity, silence, subsonic | Always |
| **B** | Analysis | Peak, RMS windows, crest factor, classification, tail exceedance | Always |
| **C** | Processing | Bimodal normalization (clip gain adjustment) | Execute mode (`-x`) |
| **D** | Restoration | Fader offsets to restore the rough mix balance | In DAW (manual or automation) |

**Dry-run mode** (default, no `-x`): runs Stages A+B and prints a session
overview. No files are written.

**Execute mode** (`-x`): runs all stages, writes processed files to `processed/`,
and generates `sessionprep.txt` + `sessionprep.json`.

### Diagnostic categories

The output is organized into four categories:

- **PROBLEMS** — typically require fixing before mixing (clipping, format mismatches)
- **ATTENTION** — may be intentional, worth checking (DC offset, subsonic, silence)
- **INFORMATION** — useful context (stereo compatibility, dual-mono)
- **CLEAN** — explicit "No ... detected" lines when checks pass

For details on every detector, see [REFERENCE.md](REFERENCE.md).

### Processing: Bimodal normalization (CLI only)

Most normalizers treat a kick drum and a synth pad the same. SessionPrep
classifies tracks using three metrics — crest factor, envelope decay rate,
and content density — then normalizes them differently:

- **Transient** (high crest + fast decay, or sparse percussion): peak-normalized to `--target_peak` (-6 dBFS)
- **Sustained** (low crest + slow decay): RMS-normalized to `--target_rms` (-18 dBFS), peak-limited

The three-metric approach resolves common misclassifications: compressed drums
(low crest but fast decay → correctly Transient), plucked instruments (high
crest but slow decay → correctly Sustained), and sparse percussion like toms
and crashes (caught by density even with ambiguous crest/decay values).

For the engineering rationale, see [TECHNICAL.md](TECHNICAL.md).

---

## Usage Examples (CLI)

### Basic analysis (dry-run)
```bash
python sessionprep.py .
```

### Execute processing
```bash
python sessionprep.py . -x
```

### Force classification overrides
```bash
python sessionprep.py . -x --force_transient 808 snare --force_sustained pad
```

### Hotter calibration (modern plugins)
```bash
python sessionprep.py . -x --target_rms -16
```

### Safety-first RMS anchor (strict max window)
```bash
python sessionprep.py . -x --rms_anchor max
```

### Tune the relative gate (sparse tracks)
```bash
python sessionprep.py . -x --gate_relative_db 30
```

### Anchor track (keep one fader at 0 dB)
```bash
python sessionprep.py . -x --anchor "Kick"
```

### Custom report filenames
```bash
python sessionprep.py . -x --report my_report.txt --json my_report.json
```

### Group related tracks (identical gain)
```bash
python sessionprep.py . -x --group "Kick In,Kick Out,Kick Sub" --group "BV*"
```

### Control grouping overlaps
```bash
# Warn on overlap (default)
python sessionprep.py . -x --group "Kick In,Kick Out" --group "Kick*" --group_overlap warn

# Abort on overlap
python sessionprep.py . -x --group "Kick In,Kick Out" --group "Kick*" --group_overlap error

# Merge overlapping groups
python sessionprep.py . -x --group "Kick In,Kick Out" --group "Kick*" --group_overlap merge
```

---

## Documentation

| Document | Contents |
|----------|----------|
| [README.md](README.md) | This file — overview, installation, quick start, usage |
| [REFERENCE.md](REFERENCE.md) | Detector reference, analysis metrics, processing details |
| [TECHNICAL.md](TECHNICAL.md) | Audio engineering background, normalization theory, signal chain |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Development setup, building, library architecture |
| [TODO.md](TODO.md) | Backlog and planned features |

---

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
