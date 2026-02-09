# SessionPrep — Architecture

This document describes the implemented architecture of the SessionPrep library.
It serves as the authoritative reference for the codebase.

The goal is to extract all detection, analysis, and processing logic from the
monolithic `sessionprep.py` into a reusable library (`sessionpreplib`) that can
be consumed by a CLI app, a GUI app, or any other frontend without code
duplication.

> **Note:** The `ptsl/` folder is not part of this architecture and should be
> disregarded.

---

## Table of Contents

1. [Development Setup](#1-development-setup)
2. [Building & Distribution](#2-building--distribution)
3. [Package Layout](#3-package-layout)
4. [Core Models](#4-core-models)
5. [Audio Utilities](#5-audio-utilities)
6. [Detectors](#6-detectors)
7. [Audio Processors](#7-audio-processors)
8. [Pipeline](#8-pipeline)
9. [Session Queue](#9-session-queue)
10. [Configuration & Presets](#10-configuration--presets)
11. [Event System](#11-event-system)
12. [Rendering](#12-rendering)
13. [Schema Versioning](#13-schema-versioning)
14. [Error Isolation Strategy](#14-error-isolation-strategy)
15. [Validation Layer](#15-validation-layer)
16. [CLI App (`sessionprep.py`)](#16-cli-app-sessionpreppy)
17. [GUI App (`sessionprep-gui.py` / `sessionprepgui/`)](#17-gui-app-sessionprep-guipy--sessionprepgui)
18. [Migration Notes](#18-migration-notes)

---

## 1. Development Setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management,
virtual environments, and Python version management. All configuration lives in
`pyproject.toml` (PEP 621).

### 1.1 Prerequisites

- **Python >= 3.12** (uv can install this for you)
- **uv** — install via:
  ```
  # Windows (PowerShell)
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

### 1.2 Setting Up the Environment

```bash
# Clone the repository
git clone <repo-url>
cd sessionprep

# Create virtual environment and install all dependencies (core + dev)
uv sync
```

`uv sync` reads `pyproject.toml`, creates `.venv/`, installs all dependencies
(including dev group: pytest, pytest-cov, pyinstaller), and generates `uv.lock`
for reproducible installs.

### 1.3 Running the CLI

```bash
# Via uv (recommended during development)
uv run python sessionprep.py <directory> [options]

# Or activate the venv and run directly
# Windows:
.venv\Scripts\activate
python sessionprep.py <directory> [options]

# macOS/Linux:
source .venv/bin/activate
python sessionprep.py <directory> [options]
```

### 1.4 Running Tests

```bash
uv run pytest
uv run pytest --cov=sessionpreplib    # with coverage
```

### 1.5 Adding Dependencies

```bash
# Add a runtime dependency
uv add <package>

# Add a dev-only dependency
uv add --group dev <package>
```

This updates `pyproject.toml` and `uv.lock` automatically.

### 1.6 Upgrading Dependencies

```bash
# Re-resolve all dependencies to their latest compatible versions
uv lock --upgrade

# Install the updated versions into .venv/
uv sync
```

### 1.7 Updating uv Itself

```bash
uv self update
```

### 1.8 Managing Python Versions

```bash
# Install a specific Python version
uv python install 3.12

# Pin the project to a version
uv python pin 3.12
```

---

## 2. Building & Distribution

### 2.1 Standalone Executable (PyInstaller)

The `build_script.py` automates PyInstaller builds. It bundles the CLI and/or
GUI, the library, and all dependencies into standalone executables that require
no Python installation.

```bash
# Build both CLI and GUI (onedir, default)
uv run python build_script.py

# Build both as single executables
uv run python build_script.py --onefile

# Build CLI only
uv run python build_script.py --target cli

# Build GUI only
uv run python build_script.py --target gui

# Clean previous build artifacts first
uv run python build_script.py --clean --onefile

# Clean only (no build)
uv run python build_script.py --clean-only
```

The `--target` flag accepts `cli`, `gui`, or `all` (default). Each executable
name includes a platform and architecture suffix:

| Platform        | CLI onefile output                             | GUI onefile output                                 |
|-----------------|------------------------------------------------|----------------------------------------------------|
| Windows x64     | `dist/sessionprep-win-x64.exe`                 | `dist/sessionprep-gui-win-x64.exe`                 |
| macOS ARM       | `dist/sessionprep-macos-arm64`                 | `dist/sessionprep-gui-macos-arm64`                 |
| macOS Intel     | `dist/sessionprep-macos-x64`                   | `dist/sessionprep-gui-macos-x64`                   |
| Linux x64       | `dist/sessionprep-linux-x64`                   | `dist/sessionprep-gui-linux-x64`                   |

Output goes to `dist/`:
- `--onefile` → single executable (~24 MB per target)
- default (onedir) → `dist/<name>/` folder

The onefile build is simpler to distribute but has slower startup (~2-3s)
because it unpacks to a temp directory. The onedir build starts instantly but
requires distributing the entire folder.

**Note:** On macOS, GUI builds always use onedir mode (producing a `.app`
bundle that is automatically zipped) because `--onefile` + `--windowed` is
deprecated in PyInstaller.

**Prerequisites for GUI builds:** The GUI optional dependencies must be
installed before building:
```bash
uv sync --extra gui
```
The build script checks for required packages and will abort with a helpful
message if any are missing. On macOS, `Pillow` (included in dev dependencies)
is used by PyInstaller to convert the `.png` icon to `.icns` format
automatically.

### 2.2 Python Package (pip-installable)

The project is also installable as a standard Python package:

```bash
# Install from local source
pip install .

# Install in editable mode (for development)
pip install -e .

# Build a wheel
uv build
```

After installation, the CLI is available as:
```bash
sessionprep <directory> [options]
```

### 2.3 Version Management

The version number lives in a single file:

```
sessionpreplib/_version.py   →   __version__ = "0.1.0"
```

Everything else reads from this one source:

| Consumer | How it reads the version |
|----------|------------------------|
| `pyproject.toml` | `dynamic = ["version"]` + `[tool.hatch.version] path` |
| `sessionpreplib` | `from ._version import __version__` (re-exported in `__init__.py`) |
| CLI (`sessionprep.py`) | `from sessionpreplib import __version__` (powers `--version` flag) |
| GUI About dialog | `from sessionpreplib import __version__` |
| PyInstaller builds | Bundled automatically via `--collect-all sessionpreplib` |

To bump the version, edit only `sessionpreplib/_version.py`.

### 2.4 Project Structure for Packaging

| File | Purpose |
|------|--------|
| `pyproject.toml` | Package metadata, dependencies, build config, entry points |
| `uv.lock` | Lockfile for reproducible dependency resolution |
| `build_script.py` | PyInstaller automation (standalone exe builds for CLI + GUI) |
| `sessionprep.py` | Thin CLI entry point |
| `sessionprep-gui.py` | Thin GUI entry point (delegates to `sessionprepgui` package) |
| `.gitignore` | Excludes `.venv/`, `build/`, `dist/`, `*.spec`, `__pycache__/` |

### 2.5 Dependencies

| Package | Type | Used by |
|---------|------|--------|
| `numpy` | Runtime | `sessionpreplib` (DSP, array ops) |
| `soundfile` | Runtime | `sessionpreplib/audio.py` (WAV I/O, bundles libsndfile) |
| `rich` | Runtime | `sessionprep.py` (CLI rendering: tables, panels, progress) |
| `PySide6` | Optional (gui) | `sessionprepgui` (Qt widgets, main window, waveform) |
| `sounddevice` | Optional (gui) | `sessionprepgui/playback.py` (audio playback via PortAudio) |
| `pytest` | Dev | Test runner |
| `pytest-cov` | Dev | Coverage reporting |
| `pyinstaller` | Dev | Standalone executable builds |
| `Pillow` | Dev | Icon format conversion for PyInstaller (macOS .png → .icns) |

GUI dependencies are declared as optional in `pyproject.toml` under
`[project.optional-dependencies].gui`. Install with `pip install .[gui]` or
`uv sync` (which installs all groups by default).

---

## 3. Package Layout

```
sessionpreplib/
    __init__.py                  # Public API surface (re-exports __version__)
    _version.py                  # Single source of truth for version number
    models.py                    # All dataclasses + enums (incl. IssueLocation)
    config.py                    # Preset load/save/merge + config validation
    audio.py                     # Audio I/O + cached DSP utilities
    utils.py                     # Keyword matching, group assignment, sort key
    events.py                    # EventBus
    detector.py                  # TrackDetector / SessionDetector ABCs
    processor.py                 # AudioProcessor ABC + priority bands
    pipeline.py                  # 3-phase orchestrator + validation
    queue.py                     # SessionQueue / SessionJob
    rendering.py                 # Diagnostic summary builder + PlainText renderer
    detectors/
        __init__.py              # Exports all detectors; provides default_detectors()
        silence.py               # SilenceDetector
        clipping.py              # ClippingDetector
        dc_offset.py             # DCOffsetDetector
        stereo_correlation.py    # StereoCorrelationDetector
        dual_mono.py             # DualMonoDetector
        mono_folddown.py         # MonoFolddownDetector
        one_sided_silence.py     # OneSidedSilenceDetector
        subsonic.py              # SubsonicDetector
        audio_classifier.py      # AudioClassifierDetector
        tail_exceedance.py       # TailExceedanceDetector
        format_consistency.py    # FormatConsistencyDetector (session-level)
        length_consistency.py    # LengthConsistencyDetector (session-level)
    processors/
        __init__.py              # Exports all processors; provides default_processors()
        bimodal_normalize.py     # BimodalNormalizeProcessor

sessionprepgui/                  # GUI package (PySide6)
    __init__.py                  # Exports main()
    res/                         # Application icons (SVG, PNG, ICO)
    settings.py                  # Persistent config (load/save/validate, OS paths)
    theme.py                     # Colors, FILE_COLOR_* constants, dark theme
    helpers.py                   # esc(), track_analysis_label(), fmt_time(), severity maps
    worker.py                    # AnalyzeWorker (QThread)
    report.py                    # HTML report rendering (summary, fader table, track detail)
    waveform.py                  # WaveformWidget (per-channel waveform, dB scale, markers, mouse guide)
    playback.py                  # PlaybackController (sounddevice lifecycle + signals)
    preferences.py               # PreferencesDialog (tree nav + per-param pages, reset-to-default)
    mainwindow.py                # SessionPrepWindow (QMainWindow) + main()

sessionprep.py                   # Thin CLI: argparse + Rich rendering + glue
sessionprep-gui.py               # Thin GUI entry point (delegates to sessionprepgui)
sessionprep_original.py          # Backup of the monolithic original
```

---

## 4. Core Models

All data models live in `sessionpreplib/models.py`.

### 4.1 Severity

```python
class Severity(Enum):
    CLEAN     = "clean"
    INFO      = "info"
    ATTENTION = "attention"
    PROBLEM   = "problem"
```

Maps 1:1 to the diagnostic categories (CLEAN / INFORMATION / ATTENTION /
PROBLEMS).

### 4.2 IssueLocation

Represents a detected issue at a specific position or region in the waveform.
Used by the GUI to render issue overlays and tooltips on the waveform widget.

```python
@dataclass
class IssueLocation:
    sample_start: int            # Start sample position (inclusive)
    sample_end: int | None       # End sample position (inclusive), None for point issues
    channel: int | None          # Channel index (0=L, 1=R, …), None = all channels
    severity: Severity
    label: str                   # Machine-readable tag, e.g. "clipping"
    description: str             # Human-readable text for tooltips / overlays
```

### 4.3 DetectorResult

Universal output of every detector. Serves three audiences:

- **Machine readers** (processors, JSON export) read `data`.
- **Human readers** (CLI, GUI, text report) read `summary`, `detail_lines`,
  `hint`.
- **Categorization logic** (diagnostic summary) reads `severity`.
- **GUI waveform** reads `issues` for visual overlays.

```python
@dataclass
class DetectorResult:
    detector_id: str
    severity: Severity
    summary: str
    data: dict[str, Any]
    detail_lines: list[str] = field(default_factory=list)
    hint: str | None = None
    error: str | None = None
    issues: list[IssueLocation] = field(default_factory=list)
```

Each detector documents its `data` keys. Processors that depend on a detector
reference these keys explicitly. See [Section 6](#6-detectors) for per-detector
`data` schemas.

Detectors populate `issues` with `IssueLocation` objects to mark specific
positions or regions in the waveform. Per-channel issues (e.g., clipping on
channel 0) set `channel` to the channel index; whole-file issues (e.g., DC
offset) set `channel` to `None`.

### 4.4 ParamSpec

Declarative specification for a configuration parameter.  Lives in
`config.py`.  Used by detectors, processors, and the shared analysis /
session sections to describe their parameters.

```python
@dataclass(frozen=True)
class ParamSpec:
    key: str                         # config key name
    type: type | tuple               # expected Python type(s)
    default: Any
    label: str                       # short UI label
    description: str = ""            # tooltip / help text
    min: float | int | None = None   # inclusive lower bound
    max: float | int | None = None   # inclusive upper bound
    min_exclusive: bool = False
    max_exclusive: bool = False
    choices: list | None = None      # allowed string values
    item_type: type | None = None    # element type for list fields
    nullable: bool = False           # True if None is valid
```

Every detector and processor exposes its parameters via a
`config_params()` classmethod that returns `list[ParamSpec]`.  Shared
analysis parameters and global processing defaults are in
`ANALYSIS_PARAMS` (in `config.py`).

### 4.5 ProcessorResult

```python
@dataclass
class ProcessorResult:
    processor_id: str
    gain_db: float
    classification: str
    method: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
```

### 4.6 DawAction / DawActionResult

Defined but not yet consumed. Placeholder for future DAW scripting.

```python
@dataclass
class DawAction:
    action_type: str
    target: str
    params: dict[str, Any]
    source: str
    priority: int = 0

@dataclass
class DawActionResult:
    action: DawAction
    success: bool
    error: str | None = None
```

### 4.7 TrackContext

Per-file state object. Created once per track, passed through all phases.

```python
@dataclass
class TrackContext:
    filename: str
    filepath: str
    audio_data: np.ndarray | None
    samplerate: int
    channels: int
    total_samples: int
    bitdepth: str
    subtype: str
    duration_sec: float
    status: str = "OK"
    detector_results: dict[str, DetectorResult] = field(default_factory=dict)
    processor_results: dict[str, ProcessorResult] = field(default_factory=dict)
    group: str | None = None
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)
```

### 4.8 SessionContext

```python
@dataclass
class SessionContext:
    tracks: list[TrackContext]
    config: dict[str, Any]
    groups: dict[str, str] = field(default_factory=dict)
    group_overlaps: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
```

### 4.9 SessionResult / SessionJob / JobStatus

Used by the queue layer.

```python
@dataclass
class SessionResult:
    session: SessionContext
    daw_actions: list[DawAction] = field(default_factory=list)
    diagnostic_summary: dict[str, Any] = field(default_factory=dict)

class JobStatus(Enum):
    PENDING / RUNNING / COMPLETED / FAILED / CANCELLED

@dataclass
class SessionJob:
    job_id: str
    source_dir: str
    config: dict[str, Any]
    status: JobStatus = JobStatus.PENDING
    priority: int = 0
    result: SessionResult | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
```

---

## 5. Audio Utilities

### 5.1 `sessionpreplib/audio.py`

Contains audio I/O, cached DSP helpers, and stateless DSP functions.

**Cached helpers** (store results on `TrackContext._cache`):

- `get_peak(track) -> float` — peak linear amplitude
- `get_peak_db(track) -> float`
- `is_silent(track) -> bool` — peak == 0
- `get_rms_window_means(track, window_ms, stereo_mode) -> ndarray`
- `get_window_samples(track, window_ms) -> int`
- `get_gated_rms_data(track, window_ms, stereo_mode, gate_relative_db) -> dict`
  Returns `{"active_means", "active_mask", "all_means", "max_mean", "rms_max_db"}`
- `get_stereo_channels_subsampled(track) -> tuple[ndarray, ndarray] | None`
- `get_stereo_channels_dc_removed(track) -> tuple[ndarray, ndarray] | None`
- `get_stereo_rms(track) -> dict | None`
  Returns `{"l_rms_lin", "r_rms_lin", "l_rms_db", "r_rms_db"}`

**Stateless DSP functions:**

- `detect_clipping_ranges(data, threshold_count, max_ranges) -> (int, list[tuple[int, int, int|None]])` — ranges are `(start, end, channel)`
- `subsonic_ratio_db(data, samplerate, cutoff_hz, max_samples) -> float`
- `linear_to_db(linear) -> float`
- `db_to_linear(db) -> float`
- `format_duration(samples, samplerate) -> str`

**File I/O:**

- `load_track(filepath) -> TrackContext` — reads WAV via soundfile, returns populated TrackContext
- `write_track(track, output_path) -> None` — writes audio_data preserving original subtype

### 5.2 `sessionpreplib/utils.py`

- `protools_sort_key(filename) -> str` — sort key matching Pro Tools behavior
- `matches_keywords(filename, keywords) -> bool` — substring, glob, exact (`$` suffix) matching
- `parse_group_specs(group_args) -> list[dict]`
- `assign_groups_to_files_with_policy(filenames, group_specs, overlap_policy) -> (dict, list)`
  Supports `"warn"`, `"error"`, `"merge"` overlap policies with union-find for merge.

---

## 6. Detectors

### 6.1 Abstract Base Classes

Defined in `sessionpreplib/detector.py`.

```python
class TrackDetector(ABC):
    id: str
    name: str
    depends_on: list[str] = []

    def configure(self, config: dict[str, Any]) -> None: ...
    @abstractmethod
    def analyze(self, track: TrackContext) -> DetectorResult: ...
    def clean_message(self) -> str | None: ...

class SessionDetector(ABC):
    id: str
    name: str

    def configure(self, config: dict[str, Any]) -> None: ...
    @abstractmethod
    def analyze(self, session: SessionContext) -> list[DetectorResult]: ...
    def clean_message(self) -> str | None: ...
```

### 6.2 Dependency Declaration

Detectors declare `depends_on` — a list of detector IDs that must run first.
The pipeline topologically sorts track detectors by this field (Kahn's
algorithm). Validates at startup that all dependencies exist and there are no
cycles.

### 6.3 Detector Catalog

#### 6.3.1 SilenceDetector (`silence.py`)

- **ID:** `silence` | **Depends on:** (none)
- **Data:** `{"is_silent": bool}`
- **Issues:** Whole-file `IssueLocation` (all channels) when track is silent
- **Severity:** `ATTENTION` if silent, `CLEAN` otherwise
- **Clean message:** `"No silent files detected"`

#### 6.3.2 ClippingDetector (`clipping.py`)

- **ID:** `clipping` | **Depends on:** `["silence"]`
- **Config:** `clip_consecutive`, `clip_report_max_ranges`
- **Data:** `{"is_clipped": bool, "runs": int, "ranges": list[tuple[int, int, int|None]]}` — each range is `(start, end, channel)`
- **Issues:** Per-channel `IssueLocation` for each clipping range
- **Severity:** `PROBLEM` if clipped, `CLEAN` otherwise
- **Hint:** `"request reprint / check limiting"`

#### 6.3.3 DCOffsetDetector (`dc_offset.py`)

- **ID:** `dc_offset` | **Depends on:** `["silence"]`
- **Config:** `dc_offset_warn_db`
- **Data:** `{"dc_db": float, "dc_warn": bool}`
- **Issues:** Whole-file `IssueLocation` (all channels) when DC offset detected
- **Severity:** `ATTENTION` if exceeds threshold, `CLEAN` otherwise
- **Hint:** `"consider DC removal"`

#### 6.3.4 StereoCorrelationDetector (`stereo_correlation.py`)

- **ID:** `stereo_correlation` | **Depends on:** `["silence"]`
- **Config:** `corr_warn`
- **Data:** `{"lr_corr": float | None, "corr_warn": bool}`
- **Severity:** `INFO` if below threshold, `CLEAN` otherwise

#### 6.3.5 DualMonoDetector (`dual_mono.py`)

- **ID:** `dual_mono` | **Depends on:** `["silence"]`
- **Config:** `dual_mono_eps`
- **Data:** `{"dual_mono": bool}`
- **Severity:** `INFO` if dual-mono, `CLEAN` otherwise

#### 6.3.6 MonoFolddownDetector (`mono_folddown.py`)

- **ID:** `mono_folddown` | **Depends on:** `["silence"]`
- **Config:** `mono_loss_warn_db`
- **Data:** `{"mono_loss_db": float | None, "mono_warn": bool}`
- **Severity:** `INFO` if exceeds threshold, `CLEAN` otherwise

#### 6.3.7 OneSidedSilenceDetector (`one_sided_silence.py`)

- **ID:** `one_sided_silence` | **Depends on:** `["silence"]`
- **Config:** `one_sided_silence_db`
- **Data:** `{"one_sided_silence": bool, "one_sided_silence_side": str | None, "l_rms_db": float, "r_rms_db": float}`
- **Issues:** Per-channel `IssueLocation` spanning the entire file for the silent channel
- **Severity:** `ATTENTION` if detected, `CLEAN` otherwise
- **Hint:** `"check stereo export / channel routing"`

#### 6.3.8 SubsonicDetector (`subsonic.py`)

- **ID:** `subsonic` | **Depends on:** `["silence"]`
- **Config:** `subsonic_hz`, `subsonic_warn_ratio_db`
- **Data:** `{"subsonic_ratio_db": float, "subsonic_warn": bool}`
- **Issues:** Whole-file `IssueLocation` (all channels) when subsonic content detected
- **Severity:** `ATTENTION` if exceeds threshold, `CLEAN` otherwise
- **Hint:** `"consider HPF ~{cutoff_hz} Hz"`

#### 6.3.9 AudioClassifierDetector (`audio_classifier.py`)

- **ID:** `audio_classifier` | **Depends on:** `["silence"]`
- **Config:** `window`, `stereo_mode`, `rms_anchor`, `rms_percentile`, `gate_relative_db`, `crest_threshold`, `decay_lookahead_ms`, `decay_db_threshold`, `sparse_density_threshold`, `force_transient`, `force_sustained`
- **Data:** `{"peak_db", "rms_max_db", "rms_anchor_db", "rms_anchor_mean", "crest", "decay_db", "density", "classification", "is_transient", "near_threshold"}`
- **Classification logic:** Three-metric system — crest factor (peak-to-RMS ratio), envelope decay rate (energy drop after loudest moment), and density (fraction of active RMS windows). Sparse tracks with at least one agreeing dynamic metric are classified as Transient (catches toms, crashes, FX hits). For non-sparse tracks, crest and decay vote together with decay as tiebreaker: high crest + slow decay → Sustained (plucked/piano); low crest + fast decay → Transient (compressed drums).
- **Severity:** Always `INFO`

#### 6.3.10 TailExceedanceDetector (`tail_exceedance.py`)

- **ID:** `tail_exceedance` | **Depends on:** `["silence", "audio_classifier"]`
- **Config:** `window`, `stereo_mode`, `rms_anchor`, `rms_percentile`, `gate_relative_db`, `tail_min_exceed_db`, `tail_max_regions`, `tail_hop_ms`
- **Data:** `{"tail_regions": list[dict], "tail_summary": {"regions", "total_duration_sec", "max_exceed_db", "anchor_db"}}`
- **Issues:** Per-region `IssueLocation` (all channels) for each exceedance region
- **Severity:** `ATTENTION` if regions found, `CLEAN` otherwise

#### 6.3.11 FormatConsistencyDetector (`format_consistency.py`)

- **Type:** SessionDetector | **ID:** `format_consistency`
- **Data (per mismatch):** `{"filename", "expected_sr", "expected_bd", "actual_sr", "actual_bd", "mismatch_reasons"}`
- Stores `_most_common_sr` and `_most_common_bd` on `session.config`.
- **Severity:** `PROBLEM` per mismatched file

#### 6.3.12 LengthConsistencyDetector (`length_consistency.py`)

- **Type:** SessionDetector | **ID:** `length_consistency`
- **Data (per mismatch):** `{"filename", "expected_samples", "expected_duration_fmt", "actual_samples", "actual_duration_fmt"}`
- Stores `_most_common_len` and `_most_common_len_fmt` on `session.config`.
- **Severity:** `PROBLEM` per mismatched file

### 6.4 Registration

```python
def default_detectors() -> list[TrackDetector | SessionDetector]:
    return [
        SilenceDetector(), ClippingDetector(), DCOffsetDetector(),
        StereoCorrelationDetector(), DualMonoDetector(), MonoFolddownDetector(),
        OneSidedSilenceDetector(), SubsonicDetector(), AudioClassifierDetector(),
        TailExceedanceDetector(), FormatConsistencyDetector(), LengthConsistencyDetector(),
    ]
```

---

## 7. Audio Processors

### 7.1 Abstract Base Class

Defined in `sessionpreplib/processor.py`.

```python
PRIORITY_CLEANUP   = 0
PRIORITY_NORMALIZE = 100
PRIORITY_POST      = 200
PRIORITY_FINALIZE  = 900

class AudioProcessor(ABC):
    id: str
    name: str
    priority: int

    def configure(self, config: dict[str, Any]) -> None: ...
    @abstractmethod
    def process(self, track: TrackContext) -> ProcessorResult: ...
    @abstractmethod
    def apply(self, track: TrackContext, result: ProcessorResult) -> np.ndarray: ...
```

### 7.2 process() vs apply() Split

- **`process()`** — pure analysis, decides gain/classification/method. Runs in
  both dry-run and execute mode. GUI can call this to preview without modifying
  audio.
- **`apply()`** — performs the audio transformation. Only called in execute mode.

### 7.3 BimodalNormalizeProcessor (`bimodal_normalize.py`)

- **ID:** `bimodal_normalize` | **Priority:** `PRIORITY_NORMALIZE` (100)
- **Reads:** `silence.data["is_silent"]`, `audio_classifier.data["peak_db", "rms_anchor_db", "classification", "is_transient"]`
- **Config:** `target_rms`, `target_peak`
- **Logic:**
  - Silent -> gain 0, classification "Silent"
  - Transient -> gain = target_peak - peak
  - Sustained -> gain = min(target_rms - rms, target_peak - peak)

### 7.4 Group Gain Equalization

Implemented as a post-processing step in `Pipeline._equalize_group_gains()`.
After all processors run, grouped tracks receive the minimum gain of the group.

### 7.5 Fader Offsets

Implemented in `Pipeline._compute_fader_offsets()`. Calculates inverse of gain,
applies anchor adjustment (`--anchor` or `--normalize_faders`). Stored in
`ProcessorResult.data["fader_offset"]`.

### 7.6 Registration

```python
def default_processors() -> list[AudioProcessor]:
    return [BimodalNormalizeProcessor()]
```

---

## 8. Pipeline

### 8.1 Overview

Defined in `sessionpreplib/pipeline.py`. Three implemented phases:

```
analyze()   -> Run all detectors (track-level + session-level)
plan()      -> Run audio processors + group equalization + fader offsets
execute()   -> Apply gains, backup originals, write processed files
```

### 8.2 Phase Usage by Mode

| Mode | Phases executed |
|------|----------------|
| Dry-run (default) | `analyze` -> `plan` |
| Execute (audio only) | `analyze` -> `plan` -> `execute` |

### 8.3 Pipeline Class

```python
class Pipeline:
    def __init__(self, detectors, audio_processors, config, event_bus): ...
    def analyze(self, session: SessionContext) -> SessionContext: ...
    def plan(self, session: SessionContext) -> SessionContext: ...
    def execute(self, session, output_dir, backup_dir, is_overwriting) -> SessionContext: ...
```

`plan()` internally calls:
- `_equalize_group_gains()` — grouped tracks get minimum gain
- `_compute_fader_offsets()` — inverse gain + anchor adjustment

### 8.4 `load_session()` Helper

```python
def load_session(source_dir, config, event_bus=None) -> SessionContext
```

Loads all WAVs from `source_dir`, assigns groups, generates overlap warnings.

### 8.5 Topological Sort

`_topo_sort_detectors()` uses Kahn's algorithm. Raises `ConfigError` on
cycles or missing dependencies.

---

## 9. Session Queue

Defined in `sessionpreplib/queue.py`. Manages multiple `SessionJob` instances,
processes them sequentially in priority order.

```python
class SessionQueue:
    def add(self, source_dir, config, priority, label) -> SessionJob
    def remove(self, job_id) -> bool
    def reorder(self, job_id, new_priority) -> None
    def cancel(self, job_id) -> None
    def pending() -> list[SessionJob]
    def completed() -> list[SessionJob]
    def all_jobs() -> list[SessionJob]
    def run_next(self, pipeline_factory, event_bus) -> SessionJob | None
    def run_all(self, pipeline_factory, event_bus, on_complete) -> list[SessionJob]
```

`run_next()` creates a fresh pipeline per job via `pipeline_factory(config)`,
loads the session, runs analyze -> plan -> (optionally execute), stores
`SessionResult`. Emits `job.start` and `job.complete` events.

---

## 10. Configuration & Presets

Defined in `sessionpreplib/config.py`.

### 10.1 Merge Order

```
built-in defaults  ->  preset file  ->  CLI args / GUI overrides
```

### 10.2 Functions

- `default_config() -> dict` — all built-in defaults
- `merge_configs(*configs) -> dict` — left-to-right merge; list keys (`force_transient`, `force_sustained`, `group`) are concatenated
- `validate_config(config) -> None` — raises `ConfigError` on invalid values
- `load_preset(path) -> dict` — loads a JSON preset, strips `schema_version` and `_description` metadata
- `save_preset(config, path, *, description=None) -> None` — saves non-default, non-internal keys with `schema_version`

Internal/CLI-only keys (`execute`, `overwrite`, `output_folder`, `backup`,
`report`, `json`) are excluded from presets automatically.

### 10.3 Preset File Format

JSON with `schema_version`. Only non-default values are saved.

Example `metal_session.json`:

```json
{
    "schema_version": "1.0",
    "_description": "Metal session preset",
    "target_rms": -16.0,
    "target_peak": -3.0,
    "crest_threshold": 15.0,
    "force_transient": ["kick", "snare", "tom"]
}
```

### 10.4 Default Config Keys

```python
{
    "target_rms": -18.0,           "target_peak": -6.0,
    "crest_threshold": 12.0,       "clip_consecutive": 3,
    "clip_report_max_ranges": 10,  "dc_offset_warn_db": -40.0,
    "corr_warn": -0.3,            "dual_mono_eps": 1e-5,
    "mono_loss_warn_db": 6.0,     "one_sided_silence_db": -80.0,
    "subsonic_hz": 30.0,          "subsonic_warn_ratio_db": -20.0,
    "window": 400,                "stereo_mode": "avg",
    "rms_anchor": "percentile",   "rms_percentile": 95.0,
    "gate_relative_db": 40.0,     "tail_max_regions": 20,
    "tail_min_exceed_db": 3.0,    "tail_hop_ms": 10,
    "force_transient": [],         "force_sustained": [],
    "group": [],                   "group_overlap": "warn",
    "anchor": None,                "normalize_faders": False,
    "execute": False,              "overwrite": False,
    "output_folder": "processed",  "backup": "_originals",
    "report": "sessionprep.txt",   "json": "sessionprep.json",
}
```

---

## 11. Event System

Defined in `sessionpreplib/events.py`. Lightweight publish/subscribe bus.

```python
class EventBus:
    def subscribe(self, event_type, handler) -> None
    def unsubscribe(self, event_type, handler) -> None
    def emit(self, event_type, **data) -> None
```

### Implemented Event Types

| Event | Emitted by | Data |
|-------|-----------|------|
| `track.load` | `load_session` | `filename`, `index`, `total` |
| `track.analyze_start` | Pipeline | `filename`, `index`, `total` |
| `track.analyze_complete` | Pipeline | `filename`, `index`, `total` |
| `detector.start` | Pipeline | `detector_id`, `filename` |
| `detector.complete` | Pipeline | `detector_id`, `filename`, `severity` |
| `session_detector.start` | Pipeline | `detector_id` |
| `session_detector.complete` | Pipeline | `detector_id` |
| `processor.start` | Pipeline | `processor_id`, `filename` |
| `processor.complete` | Pipeline | `processor_id`, `filename` |
| `track.write_start` | Pipeline | `filename`, `index`, `total` |
| `track.write_complete` | Pipeline | `filename`, `index`, `total` |
| `job.start` | Queue | `job_id` |
| `job.complete` | Queue | `job_id`, `status` |

No EventBus = no overhead. All emissions are guarded with `if self.event_bus`.

---

## 12. Rendering

Defined in `sessionpreplib/rendering.py`. Two standalone functions (not wrapped
in an ABC — see TODO.md for future Renderer abstraction).

### 12.1 `build_diagnostic_summary(session, track_detectors, session_detectors) -> dict`

Aggregates detector results into the four-category summary structure:

```python
{
    "problems": [{"title", "hint", "items", "standalone"}, ...],
    "attention": [...],
    "information": [...],
    "clean": [...],
    "normalization_hints": [...],
    "clean_count": int,
    "total_ok": int,
    "overview": {...},
}
```

Handles: file errors, format/length mismatches, clipping, DC offset, stereo
compatibility (correlation + mono folddown combined), dual-mono, silence,
one-sided silence, subsonic, tail exceedance, grouping overlaps, clean
summaries, normalization hints (near-threshold crest), and overview statistics.

### 12.2 `render_diagnostic_summary_text(summary) -> str`

Renders the summary dict as plain text with emoji category headers. Used for
both console output (via `sessionprep.py`) and the `sessionprep.txt` report.

### 12.3 CLI Rendering

Rich-based rendering (progress bars, tables, panels) lives in `sessionprep.py`,
not the library. `rich` is a CLI-only dependency.

---

## 13. Schema Versioning

All serialized outputs include a version field:

```json
{
    "schema_version": "1.0",
    ...
}
```

Currently applies to:
- `sessionprep.json` (the analysis/automation export)
- Preset files (via `save_preset()`)

---

## 14. Error Isolation Strategy

### 14.1 Principles

- **Partial results are always better than a crash.**
- Errors are isolated per-track, per-detector, and per-processor.
- Configuration errors abort early (before any work starts).

### 14.2 Error Handling Table

| Failure | Policy |
|---------|--------|
| One detector throws on one track | Store `DetectorResult` with `Severity.PROBLEM`, `error` field set; **continue** |
| One track file cannot be read | Mark `TrackContext.status = "Error: ..."`, skip all detectors/processors; **continue** |
| An audio processor throws on one track | Store `ProcessorResult` with `error` field set; skip audio write; **continue** |
| Config validation fails at startup | **Abort** with descriptive error |
| Cyclic detector dependency | **Abort** at pipeline construction |

### 14.3 Implementation

Every `analyze()`, `process()`, and `apply()` call is wrapped in try/except
by the pipeline. Components do not need to handle unexpected exceptions.

---

## 15. Validation Layer

### 15.1 Pipeline Validation

Called at pipeline construction:

1. All `depends_on` references point to existing detector IDs
2. No circular dependencies among detectors
3. No duplicate IDs across detectors and processors

### 15.2 Config Validation

Validation is **ParamSpec-driven**: each detector and processor declares
its own parameters via `config_params() -> list[ParamSpec]`.  Shared
analysis parameters live in `ANALYSIS_PARAMS`, session-level parameters
in `SESSION_PARAMS` (both in `config.py`).  See [Section 4.4](#44-paramspec)
for the `ParamSpec` dataclass.

**Validation entry points:**

| Function | Input | Output | Use case |
|----------|-------|--------|----------|
| `validate_param_values(params, values)` | `list[ParamSpec]`, flat dict | `list[ConfigFieldError]` | Validate any subset of params |
| `validate_config_fields(config)` | flat dict | `list[ConfigFieldError]` | Validate all known params (auto-collects from components) |
| `validate_structured_config(structured)` | structured dict | `list[ConfigFieldError]` | Validate the GUI config file section by section |
| `validate_config(config)` | flat dict | raises `ConfigError` | Backward-compatible wrapper (CLI) |

**Checks performed per field (in order):**

1. **Null check** — rejects `None` unless `nullable` is set
2. **Type check** — validates Python type (with `bool ⊄ int` enforcement)
3. **Enum check** — validates value against `choices` list
4. **Range check** — validates numeric bounds (inclusive/exclusive)
5. **List item check** — validates element types in list fields

Errors from `validate_structured_config` prefix the key with the section
path (e.g. `detectors.clipping.clip_consecutive`) for UI disambiguation.

---

## 16. CLI App (`sessionprep.py`)

A thin shell (~770 lines) that imports everything from `sessionpreplib`:

1. **argparse** — defines all CLI arguments (same flags as original script)
2. **Config conversion** — `vars(args)` merged with `default_config()`
3. **Pipeline construction** — `default_detectors()` + `default_processors()`
4. **Rich rendering** — progress bars, panels, diagnostic summary, fader table
5. **Report/JSON writing** — `generate_report()` and `save_json()` for file outputs
6. **Backup handling** — backs up originals when overwriting

The CLI contains **zero** analysis, detection, processing, or DSP logic.

---

## 17. GUI App (`sessionprep-gui.py` / `sessionprepgui/`)

The GUI is a PySide6 application split across the `sessionprepgui/` package.
`sessionprep-gui.py` is a thin entry point that delegates to the package.

### 17.1 Running the GUI

```bash
uv run python sessionprep-gui.py
```

Requires PySide6 and sounddevice (installed via the `gui` optional dependency
group).

### 17.2 Package Architecture

| Module | Responsibility |
|--------|---------------|
| `__init__.py` | Exports `main()` |
| `settings.py` | `load_config()`, `save_config()`, `config_path()` — persistent GUI preferences |
| `theme.py` | `COLORS` dict, `FILE_COLOR_*` constants, dark palette + stylesheet |
| `helpers.py` | `esc()`, `track_analysis_label()`, `fmt_time()`, severity maps |
| `worker.py` | `AnalyzeWorker` (QThread) — runs pipeline in background thread |
| `report.py` | HTML rendering: `render_summary_html()`, `render_fader_table_html()`, `render_track_detail_html()` |
| `waveform.py` | `WaveformWidget` — per-channel waveform painting, dB measurement scale, peak/RMS markers, mouse guide, issue overlays, playback cursor, tooltips |
| `playback.py` | `PlaybackController` — sounddevice OutputStream lifecycle, QTimer cursor updates, signal-based API |
| `preferences.py` | `PreferencesDialog` — tree-navigated settings dialog, ParamSpec-driven widgets, reset-to-default, HiDPI scaling |
| `mainwindow.py` | `SessionPrepWindow` (QMainWindow) — orchestrator, UI layout, slot handlers |

### 17.3 Dependency Direction

```
settings (leaf) <--  mainwindow
theme (leaf)  <--  helpers  <--  report  <--  mainwindow
                                                 |
              waveform     <--------------------+
              playback     <--------------------+
              worker       <--------------------+
              preferences  <--------------------+
```

No circular imports. `settings`, `theme`, and `helpers` are pure leaves.
`preferences` reads `ParamSpec` metadata from detectors and processors.
`mainwindow` composes all other modules.

### 17.4 Key Design Decisions

- **PlaybackController** encapsulates all `sounddevice` state with a
  signal-based API (`cursor_updated`, `playback_finished`, `error`).
  `mainwindow.py` has zero direct `sd` usage.
- **WaveformWidget** renders issue overlays from `IssueLocation` objects.
  Per-channel regions are drawn in the corresponding channel lane; whole-file
  issues span all channels. Tooltips use a 5-pixel hit tolerance for narrow
  markers. Additional features:
  - **dB measurement scale** — left/right margins (30 px) with dBFS tick
    labels (0, −3, −6, −12, −18, −24, −36, −48, −60), tick marks, and
    faint connector lines spanning the waveform area. Adaptive spacing
    (min 18 px between ticks, lane-edge padding to prevent cross-channel
    overlap). Scale adjusts dynamically with vertical resize and `_vscale`.
  - **Peak marker ("P")** — magenta solid vertical line at the sample with
    the highest absolute amplitude across all channels. A small horizontal
    crosshair is drawn at the peak amplitude on the owning channel only.
    Hovering shows "Peak: X.X dBFS" tooltip.
  - **Max RMS marker ("R")** — cyan solid vertical line at the centre of
    the loudest momentary RMS window (combined across channels). Horizontal
    crosshair on the positive side of each channel lane. Hovering shows
    "Max RMS: X.X dBFS" tooltip.
  - **Mouse guide** — a thin grey dashed horizontal line follows the mouse
    cursor across the full widget width. The corresponding dBFS value is
    shown at the top of the current channel's scale margins (left and right).
    The guide disappears when the mouse leaves the widget.
- **report.py** contains pure HTML-building functions (no widget references),
  making them independently testable.
- **PreferencesDialog** dynamically generates settings pages from each
  component's `config_params()` metadata. Widget type, range, step size,
  and decimal precision are all derived from the `ParamSpec`. Each parameter
  gets a visible description subtext, a rich tooltip (with key name, default,
  range), and a reset-to-default button using Qt's `SP_BrowserReload` icon.
- **HiDPI scaling** is applied via `QT_SCALE_FACTOR` environment variable,
  read directly from the JSON config file before `QApplication` is created
  (bypassing the validate-and-merge path to avoid side effects).
- The GUI contains **zero** analysis, detection, processing, or DSP logic —
  all analysis runs through `sessionpreplib` via `AnalyzeWorker`.

### 17.5 Persistent Configuration (`sessionprep.config.json`)

The GUI stores all detector and processor default values in a JSON config
file in the OS-specific user preferences directory:

| OS      | Path |
|---------|------|
| Windows | `%APPDATA%\sessionprep\sessionprep.config.json` |
| macOS   | `~/Library/Application Support/sessionprep/sessionprep.config.json` |
| Linux   | `$XDG_CONFIG_HOME/sessionprep/sessionprep.config.json` (defaults to `~/.config/`) |

**Structured format** — organised by component, not a flat key list:

```json
{
    "gui": {
        "scale_factor": 1.0
    },
    "analysis": {
        "window": 400,
        "stereo_mode": "avg",
        "rms_anchor": "percentile",
        "rms_percentile": 95.0,
        "gate_relative_db": 40.0,
        "group_overlap": "warn",
        "normalize_faders": false
    },
    "detectors": {
        "clipping": { "clip_consecutive": 3, "clip_report_max_ranges": 10 },
        "dc_offset": { "dc_offset_warn_db": -40.0 },
        "...": "..."
    },
    "processors": {
        "bimodal_normalize": { "target_rms": -18.0, "target_peak": -6.0 }
    }
}
```

- **`gui`** — GUI-specific settings (HiDPI scale factor); not consumed by the
  analysis pipeline
- **`analysis`** — shared RMS / loudness parameters and global processing
  defaults (from `ANALYSIS_PARAMS`)
- **`detectors.<id>`** — per-detector parameters (from each detector's `config_params()`)
- **`processors.<id>`** — per-processor parameters (from each processor's `config_params()`)

Session-specific values (`force_transient`, `force_sustained`, `group`,
`anchor`) are **not** stored in the config file — they are per-session
and will be saved in session project files in the future.

**Lifecycle:**

1. On first launch, `build_structured_defaults()` creates the file with
   all built-in defaults from every component's `config_params()`.
   GUI-specific defaults (`gui.scale_factor`) are injected by `settings.py`.
2. On subsequent launches, the file is deep-merged with current defaults
   (forward-compatible for new keys/detectors) and validated via
   `validate_structured_config()`. The `gui` section is preserved through
   the merge even if analysis/detector/processor validation fails.
3. If the file is corrupt or fails validation, analysis/detector/processor
   sections are reset to defaults; the `gui` section is preserved. The
   corrupt file is backed up as `*.bak`.
4. `flatten_structured_config()` converts the structured dict into a flat
   key-value dict that the pipeline and `AnalyzeWorker` consume (the `gui`
   section is excluded from flattening — it is consumed only by the GUI).
5. The **Preferences dialog** (`preferences.py`) provides a tree-navigated
   UI for editing all parameters. On save, changes are written to the config
   file and a re-analysis is triggered if a session is loaded.

The CLI is **not** affected by this file — it continues to use its own
`default_config()` + command-line arguments.

---

## 18. Migration Notes

### 18.1 Mapping from Original Code to Library

| Original (`sessionprep.py`) | Library location |
|------|-------------|
| `parse_arguments()` | `sessionprep.py` (CLI only) |
| `analyze_audio()` | `audio.py` (DSP) + individual detectors |
| `check_clipping()` / `detect_clipping_ranges()` | `audio.py` + `detectors/clipping.py` |
| `subsonic_ratio_db()` | `audio.py` + `detectors/subsonic.py` |
| `calculate_gain()` | `processors/bimodal_normalize.py` |
| `matches_keywords()` | `utils.py` |
| `parse_group_specs()` / `assign_groups_to_files*()` | `utils.py` |
| `build_session_overview()` | Session-level detectors + `rendering.py` |
| `build_diagnostic_summary()` | `rendering.py` |
| `render_diagnostic_summary_text()` | `rendering.py` |
| `print_diagnostic_summary()` | `sessionprep.py` (Rich) |
| `generate_report()` / `save_json()` | `sessionprep.py` |
| `process_files()` | `pipeline.py` + `sessionprep.py` |
| `protools_sort_key()` | `utils.py` |
| `db_to_linear()` / `linear_to_db()` / `format_duration()` | `audio.py` |

### 18.2 Dependencies

| Package | Used by |
|---------|---------|
| `numpy` | `sessionpreplib` (core dependency) |
| `soundfile` | `sessionpreplib/audio.py` (audio I/O) |
| `rich` | `sessionprep.py` only — **not** a library dependency |
| `PySide6` | `sessionprepgui` only — optional GUI dependency |
| `sounddevice` | `sessionprepgui/playback.py` only — optional GUI dependency |

### 18.3 Layer Cake

```
SessionQueue                          manages N jobs, priority-ordered
  +-- SessionJob                      one session dir + config + status
       +-- Pipeline                   configured per-job via factory
            |-- analyze()             -> TrackDetectors (topo-sorted)
            |                         -> SessionDetectors
            |-- plan()                -> AudioProcessors (priority-sorted)
            |                         -> Group equalization + fader offsets
            +-- execute()             -> AudioProcessors.apply() + file write
```
