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
      <a href="https://github.com/user-attachments/assets/da7499f5-d830-4094-87fd-f8c7fc4cdd59">
        <img width="1602" height="983" alt="image" src="https://github.com/user-attachments/assets/da7499f5-d830-4094-87fd-f8c7fc4cdd59" />
      </a>
    </td>
    <td width="33%">
      <a href="https://github.com/user-attachments/assets/dcbbc93b-5a1c-4cef-a0c9-33423f1e5ba7">
        <img width="1593" height="975" alt="image" src="https://github.com/user-attachments/assets/dcbbc93b-5a1c-4cef-a0c9-33423f1e5ba7" />
      </a>
    </td>
    <td width="33%">
      <a href="https://github.com/user-attachments/assets/d33707db-bd73-4bfc-ad06-eb2375d22eb1">
        <img width="1603" height="988" alt="image" src="https://github.com/user-attachments/assets/d33707db-bd73-4bfc-ad06-eb2375d22eb1" />
      </a>
    </td>
  </tr>
  <tr>
    <td width="33%">
      <a href="https://github.com/user-attachments/assets/be442d0c-3cf9-4c21-bee6-5677bc28ef0d">
        <img width="1604" height="981" alt="image" src="https://github.com/user-attachments/assets/be442d0c-3cf9-4c21-bee6-5677bc28ef0d" />
      </a>
    </td>
    <td width="33%">
      <a href="https://github.com/user-attachments/assets/59b3e26d-cba4-40ce-8214-2f1f47f3fd6e">
        <img width="1602" height="981" alt="image" src="https://github.com/user-attachments/assets/59b3e26d-cba4-40ce-8214-2f1f47f3fd6e" />
      </a>
    </td>
    <td width="33%">
      <a href="https://github.com/user-attachments/assets/43c67cf7-d344-4694-8049-897b3b28e0d4">
        <img width="1327" height="838" alt="image" src="https://github.com/user-attachments/assets/43c67cf7-d344-4694-8049-897b3b28e0d4" />
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
and spectrogram display with issue overlays, and audio playback.  Select
multiple tracks (Shift-click or Ctrl-click) and hold **Alt+Shift** while
clicking a dropdown (RMS Anchor, Classification) to apply the change to all
selected tracks at once — mirroring the Pro Tools convention for batch control
changes.  Re-analysis runs asynchronously with a progress bar.

On first launch it creates a `sessionprep.config.json` in your user
preferences directory with all default thresholds.

Use **File → Preferences** to customize settings through a two-tab dialog:

- **Global** — General (HiDPI scale, default project directory), Colors
  (palette editor), and Groups (named group presets with color, gain-linking,
  and DAW target columns).
- **Config Presets** — named presets for Analysis, Detector, Processor, and
  DAW Processor parameters. Add, duplicate, rename, or delete presets. Each
  parameter shows a description, expected type, valid range, and default
  value. A reset button next to each widget restores the default.

Changes are saved to `sessionprep.config.json` and trigger an immediate
re-analysis if a session is loaded.

The analysis toolbar includes **Group:** and **Config:** dropdowns for quick
preset switching. Changing the config preset with an active session warns you
before re-analyzing (group assignments are preserved). Each session also has
a **Config** tab where you can override any parameter for that session only,
without affecting the global preset.

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
uv run python sessionprep.py .
```

### Execute processing
```bash
uv run python sessionprep.py . -x
```

### Force classification overrides
```bash
uv run python sessionprep.py . -x --force_transient 808 snare --force_sustained pad
```

### Hotter calibration (modern plugins)
```bash
uv run python sessionprep.py . -x --target_rms -16
```

### Safety-first RMS anchor (strict max window)
```bash
uv run python sessionprep.py . -x --rms_anchor max
```

### Tune the relative gate (sparse tracks)
```bash
uv run python sessionprep.py . -x --gate_relative_db 30
```

### Anchor track (keep one fader at 0 dB)
```bash
uv run python sessionprep.py . -x --anchor "Kick"
```

### Custom report filenames
```bash
uv run python sessionprep.py . -x --report my_report.txt --json my_report.json
```

### Group related tracks (identical gain)
```bash
uv run python sessionprep.py . -x --group Kick:kick,kick_sub --group OH:overhead,oh --group Toms:tom
```

Groups use `Name:pattern1,pattern2` syntax. Patterns support substring, glob
(`*`/`?`), or exact match (suffix `$`). First match wins; if a file matches
multiple groups a warning is printed.

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
