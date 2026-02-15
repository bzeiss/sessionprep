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
8. [DAW Processors](#8-daw-processors)
9. [Pipeline](#9-pipeline)
10. [Session Queue](#10-session-queue)
11. [Configuration & Presets](#11-configuration--presets)
12. [Event System](#12-event-system)
13. [Rendering](#13-rendering)
14. [Schema Versioning](#14-schema-versioning)
15. [Error Isolation Strategy](#15-error-isolation-strategy)
16. [Validation Layer](#16-validation-layer)
17. [CLI App (`sessionprep.py`)](#17-cli-app-sessionpreppy)
18. [GUI App (`sessionprep-gui.py` / `sessionprepgui/`)](#18-gui-app-sessionprep-guipy--sessionprepgui)
19. [Migration Notes](#19-migration-notes)

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

SessionPrep supports two build engines for creating standalone executables:
**PyInstaller** (fast, standard) and **Nuitka** (high-performance, compiled).
Both engines share a centralized configuration in `build_conf.py`.

### 2.1 Centralized Metadata (`build_conf.py`)

All build metadata—target entry points, asset paths, platform-specific names,
and library exclusion rules—is defined in `build_conf.py`. This ensures that
both build engines produce consistent results and maintain strict dependency
hygiene (e.g., ensuring `rich` is never bundled with the GUI).

### 2.2 PyInstaller Build (Standard)

The `build_pyinstaller.py` script automates PyInstaller builds. It produces
executables that are relatively quick to build but have slightly slower
startup (~2-3s) because they unpack to a temporary directory.

```bash
# Build both CLI and GUI (onedir, default)
uv run python build_pyinstaller.py

# Build both as single executables
uv run python build_pyinstaller.py --onefile

# Build CLI only
uv run python build_pyinstaller.py cli

# Build GUI only
uv run python build_pyinstaller.py gui

# Clean previous build artifacts first
uv run python build_pyinstaller.py --clean gui
```

Output goes to `dist_pyinstaller/`.

### 2.3 Nuitka Build (High-Performance)

The `build_nuitka.py` script uses Nuitka to transpile the Python code into C
and compile it to a native machine-code binary. This results in faster startup
times and better performance, at the cost of significantly longer compilation
times.

```bash
# Build both CLI and GUI
uv run python build_nuitka.py all

# Build CLI only
uv run python build_nuitka.py cli

# Build GUI only
uv run python build_nuitka.py gui

# Clean cache before building
uv run python build_nuitka.py --clean all
```

Output goes to `dist_nuitka/`.

### 2.4 Platform Suffixes

Each executable name includes a platform and architecture suffix generated
automatically by `build_conf.py`:

| Platform        | CLI output filename                             | GUI output filename                                 |
|-----------------|-------------------------------------------------|-----------------------------------------------------|
| Windows x64     | `sessionprep-win-x64.exe`                       | `sessionprep-gui-win-x64.exe`                       |
| macOS ARM       | `sessionprep-macos-arm64`                       | `sessionprep-gui-macos-arm64`                       |
| macOS Intel     | `sessionprep-macos-x64`                         | `sessionprep-gui-macos-x64`                         |
| Linux x64       | `sessionprep-linux-x64`                         | `sessionprep-gui-linux-x64`                         |
| Linux ARM64     | `sessionprep-linux-arm64`                       | `sessionprep-gui-linux-arm64`                       |

**Note on macOS:** GUI builds always use `onedir` mode (producing a `.app`
bundle) because `--onefile` + `--windowed` is deprecated in both engines for
macOS GUI apps. The scripts automatically zip the `.app` bundle for
distribution.

**Prerequisites for GUI builds:** GUI dependencies must be installed:
`uv sync --extra gui`.

### 2.5 Python Package (pip-installable)

The project remains installable as a standard Python package:

```bash
# Install from local source
pip install .

# Build a wheel
uv build
```

After installation, the CLI is available as `sessionprep`.

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

### 2.6 Linux Build Requirements

To build on Linux (especially with Nuitka), you need a few system libraries
installed:

```bash
# Debian / Ubuntu
sudo apt install gcc patchelf ccache libatomic1

# Fedora
sudo dnf install gcc patchelf ccache libatomic-static
```

- **`gcc`**: The C compiler.
- **`patchelf`**: Required by Nuitka to modify RPATHs in standalone binaries.
- **`ccache`**: (Optional) Speeds up recompilation significantly.
- **`libatomic`**: Required for linking NumPy extensions statically on some architectures/compilers.

### 2.7 Project Structure for Packaging

| File | Purpose |
|------|--------|
| `pyproject.toml` | Package metadata, dependencies, build config, entry points |
| `uv.lock` | Lockfile for reproducible dependency resolution |
| `build_conf.py` | Shared build metadata and isolation rules (Source of Truth) |
| `build_pyinstaller.py`| PyInstaller automation (standard builds) |
| `build_nuitka.py` | Nuitka automation (optimized builds) |
| `sessionprep.py` | Thin CLI entry point |
| `sessionprep-gui.py` | Thin GUI entry point |
### 2.5 Dependencies

| Package | Type | Used by |
|---------|------|--------|
| `numpy` | Runtime | `sessionpreplib` (DSP, array ops) |
| `soundfile` | Runtime | `sessionpreplib/audio.py` (WAV I/O, bundles libsndfile) |
| `scipy` | Runtime | `sessionpreplib/audio.py` (subsonic STFT analysis), `sessionprepgui/waveform.py` (mel spectrogram) |
| `rich` | Runtime | `sessionprep.py` (CLI rendering: tables, panels, progress) |
| `PySide6` | Optional (gui) | `sessionprepgui` (Qt widgets, main window, waveform) |
| `sounddevice` | Optional (gui) | `sessionprepgui/playback.py` (audio playback via PortAudio) |
| `py-ptsl` | Optional (gui) | `sessionpreplib/daw_processors/protools.py` (Pro Tools Scripting SDK gRPC client) |
| `pytest` | Dev | Test runner |
| `pytest-cov` | Dev | Coverage reporting |
| `pyinstaller` | Dev | Standalone executable builds |
| `Pillow` | Dev | Icon format conversion for PyInstaller (macOS .png → .icns) |

Core runtime dependencies (`numpy`, `soundfile`, `scipy`) are declared in
`[project].dependencies`. GUI-only dependencies (`PySide6`, `sounddevice`)
are declared as optional under `[project.optional-dependencies].gui`.
Install with `pip install .[gui]` or `uv sync` (which installs all groups
by default).

---

## 3. Package Layout

```
sessionpreplib/
    __init__.py                  # Public API surface (re-exports __version__)
    _version.py                  # Single source of truth for version number
    models.py                    # All dataclasses + enums (incl. IssueLocation)
    config.py                    # Preset load/save/merge + config validation
    audio.py                     # Audio I/O + cached DSP utilities
    chunks.py                    # WAV/AIFF chunk I/O (read, write, remove, identify)
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
        mono_downmix.py          # MonoDownmixProcessor (stub)
    daw_processors/
        __init__.py              # Exports all DAW processors; provides default_daw_processors()
        protools.py              # ProToolsDawProcessor — Pro Tools integration via py-ptsl gRPC SDK

sessionprepgui/                  # GUI package (PySide6)
    __init__.py                  # Exports main()
    res/                         # Application icons (SVG, PNG, ICO)
    settings.py                  # Persistent config (load/save/validate, OS paths)
    theme.py                     # Colors, FILE_COLOR_* constants, dark theme
    helpers.py                   # esc(), track_analysis_label(), fmt_time(), severity maps
    worker.py                    # QThread workers: AnalyzeWorker, BatchReanalyzeWorker, DawCheckWorker, DawFetchWorker, DawTransferWorker
    report.py                    # HTML report rendering (summary, fader table, track detail)
    waveform.py                  # WaveformWidget (waveform + spectrogram display, dB/freq scales, markers, overlays, keyboard/mouse nav)
    playback.py                  # PlaybackController (sounddevice lifecycle + signals)
    param_widgets.py             # Reusable ParamSpec widget builders + GroupsTableWidget
    preferences.py               # PreferencesDialog (two-tab layout: Global + Config Presets)
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
    freq_min_hz: float | None = None  # Optional frequency lower bound (Hz)
    freq_max_hz: float | None = None  # Optional frequency upper bound (Hz)
```

When `freq_min_hz` and `freq_max_hz` are set, the GUI renders frequency-bounded
rectangles in spectrogram mode (mapped via mel scale).  Without frequency bounds
the overlay spans the full frequency range.

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

### 4.6 DawCommand / DawCommandResult

Plain data objects representing DAW operations and their outcomes.
`DawCommand` is created by a `DawProcessor`, executed by the same processor
via internal dispatch (Option B — dumb data, smart processor).

```python
@dataclass
class DawCommand:
    """A single operation to perform against a DAW.

    Plain data object — the DawProcessor that created it is responsible
    for execution.  undo_params captures the state needed to reverse
    the operation (e.g. the previous fader value).
    """
    command_type: str                            # e.g. "set_clip_gain", "set_fader", "set_color"
    target: str                                  # e.g. track name, folder path
    params: dict[str, Any] = field(default_factory=dict)
    source: str = ""                             # processor id that produced this
    undo_params: dict[str, Any] | None = None    # for future rollback

@dataclass
class DawCommandResult:
    """Outcome of executing a single DawCommand."""
    command: DawCommand
    success: bool
    error: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
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
    classification_override: str | None = None
    rms_anchor_override: str | None = None
    chunk_ids: list[str] = field(default_factory=list)
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)
    # File-based processing pipeline
    processed_filepath: str | None = None
    applied_processors: list[str] = field(default_factory=list)
    processor_skip: set[str] = field(default_factory=set)
```

- `processed_filepath` — absolute path to the processed output file (set by
  `Pipeline.prepare()`), or `None` if not yet prepared.
- `applied_processors` — list of processor IDs that were applied during the
  last `prepare()` run.
- `processor_skip` — set of processor IDs to skip for this track (per-track
  override; empty = use all enabled processors, i.e. "Default").

### 4.8 SessionContext

```python
@dataclass
class SessionContext:
    tracks: list[TrackContext]
    config: dict[str, Any]
    groups: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    detectors: list = field(default_factory=list)
    processors: list = field(default_factory=list)
    daw_state: dict[str, Any] = field(default_factory=dict)
    daw_command_log: list[DawCommandResult] = field(default_factory=list)
    prepare_state: str = "none"
```

- `daw_state` — namespaced per DAW processor id. Each processor stores fetched
  data and last-transfer snapshots here (e.g.
  `session.daw_state["protools"]["folders"]`).
- `daw_command_log` — flat, append-only list of all executed DAW commands across
  all transfer/sync/execute_commands calls.
- `prepare_state` — tracks the state of the file-based Prepare step. Values:
  `"none"` (never prepared), `"ready"` (prepared and up-to-date), `"stale"`
  (prepared but invalidated by changes to gain, classification, RMS anchor,
  processor selection, or re-analysis).

### 4.9 SessionResult / SessionJob / JobStatus

Used by the queue layer.

```python
@dataclass
class SessionResult:
    session: SessionContext
    daw_commands: list[DawCommand] = field(default_factory=list)
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
- `subsonic_stft_analysis(signal, samplerate, cutoff_hz, *, window_ms, hop_ms, abs_gate_db, silence_rms) -> (float, list[tuple[int, int, float]])` — single-pass STFT subsonic analysis on a 1-D signal; returns `(whole_file_ratio_db, per_window_ratios)`. Uses `scipy.signal.stft` with Hann windowing, vectorised band/total power computation, and silence + absolute power gates.
- `linear_to_db(linear) -> float`
- `db_to_linear(db) -> float`
- `format_duration(samples, samplerate) -> str`

**File I/O:**

- `load_track(filepath) -> TrackContext` — reads WAV via soundfile, returns populated TrackContext
- `write_track(track, output_path) -> None` — writes audio_data preserving original subtype

### 5.2 `sessionpreplib/utils.py`

- `protools_sort_key(filename) -> str` — sort key matching Pro Tools behavior
- `matches_keywords(filename, keywords) -> bool` — substring, glob, exact (`$` suffix) matching
- `parse_group_specs(group_args) -> list[dict]` — parses `Name:pattern1,pattern2` syntax (mandatory `Name:` prefix; raises `ValueError` on invalid input)
- `assign_groups(filenames, group_specs) -> (dict, list[str])` — first-match-wins assignment; returns `(filename→group_name, warnings)`

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
- **Config:** `subsonic_hz`, `subsonic_warn_ratio_db`, `subsonic_windowed` (default `True`), `subsonic_window_ms`, `subsonic_max_regions`
- **Data:** `{"subsonic_ratio_db": float, "subsonic_warn": bool, "per_channel": {ch: {"ratio_db", "warn"}}, "windowed_regions"?: list[dict]}`
- **Per-channel analysis** (always active for stereo+): Each channel is analyzed independently via `subsonic_stft_analysis` (one `scipy.signal.stft` call per channel). The combined ratio is the **maximum** (worst) of all per-channel ratios — more conservative than the previous mono-downmix approach, which could mask subsonic content through phase cancellation. If only one channel triggers the warning, the issue is reported per-channel with `channel` set to the offending channel index; if all channels trigger, a whole-file issue is reported.
- **Windowed analysis** (default on via `subsonic_windowed`): Per-window ratios are derived from the same STFT pass (vectorised, no Python loop). Contiguous exceeding windows are merged into regions reported as `IssueLocation` objects with precise `sample_start`/`sample_end` and frequency bounds (`freq_min_hz=0`, `freq_max_hz=cutoff`). Capped by `subsonic_max_regions`. Safeguards:
  1. **Absolute subsonic power gate:** Each window's absolute subsonic energy level is checked (`window_rms_db + ratio_db`). Windows where this is below −40 dBFS are suppressed — their subsonic content is too quiet to matter regardless of the ratio. Prevents amp hum/noise in quiet gaps from producing false positives.
  2. **Threshold relaxation:** The windowed threshold is relaxed by 6 dB below the configured threshold. Short windows have less frequency resolution, so borderline subsonic content that triggers the whole-file check may not reach the same threshold per-window.
  3. **Active-signal fallback:** If no windows exceed the relaxed threshold, the detector falls back to marking windows that have significant signal (RMS within 20 dB of the loudest window, vectorised via NumPy reshape + `np.diff` contiguous merging). Since the whole-file analysis already confirmed subsonic content, the active-signal windows are where it lives.
  4. **Whole-file fallback:** If even the active-signal approach produces no regions, a whole-file `IssueLocation` is emitted so ATTENTION always has at least one visible overlay.
- **Issues:** Per-region `IssueLocation` (with frequency bounds) in windowed mode; falls back to active-signal regions or whole-file span
- **Severity:** `ATTENTION` if exceeds threshold (whole-file or any channel), `CLEAN` otherwise
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

    def config_params(cls) -> list[ParamSpec]: ...   # base: {id}_enabled toggle
    def configure(self, config: dict[str, Any]) -> None: ...
    @property
    def enabled(self) -> bool: ...
    @abstractmethod
    def process(self, track: TrackContext) -> ProcessorResult: ...
    @abstractmethod
    def apply(self, track: TrackContext, result: ProcessorResult) -> np.ndarray: ...
```

The base `config_params()` returns a single `{id}_enabled` `ParamSpec` (bool,
default `True`). Subclasses extend via `super().config_params() + [...]`.
The base `configure()` reads `self._enabled` from the config. The Pipeline
configures all processors first, then filters to only enabled ones before
sorting by priority.

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

### 7.4 MonoDownmixProcessor (`mono_downmix.py`)

- **ID:** `mono_downmix` | **Priority:** `PRIORITY_POST` (200)
- **Status:** Stub — `apply()` returns audio unchanged. A real implementation
  would sum/average channels and return a mono array.
- **Logic:**
  - Mono files → classification "Mono", method "pass-through (already mono)"
  - Multi-channel files → classification "Stereo", method "downmix Nch → 1ch (stub)"
  - Gain is always 0.0 dB (no-op)

### 7.5 Group Levelling

Implemented as a post-processing step in `Pipeline._apply_group_levels()`.
After all processors run, grouped tracks receive the minimum gain of the group.

### 7.6 Fader Offsets

Implemented in `Pipeline._compute_fader_offsets()`. Calculates inverse of gain,
applies anchor adjustment (`--anchor` or `--normalize_faders`). Stored in
`ProcessorResult.data["fader_offset"]`.

### 7.7 Registration

```python
def default_processors() -> list[AudioProcessor]:
    return [BimodalNormalizeProcessor(), MonoDownmixProcessor()]
```

---

## 8. DAW Processors

### 8.1 Overview

Defined in `sessionpreplib/daw_processor.py`. DAW processors handle integration
with external DAWs (Digital Audio Workstations). Each concrete subclass handles
one DAW — e.g. `ProToolsProcessor` (PTSL), `DAWProjectProcessor` (.dawproject
files).

**Key design decisions:**

- **One processor per DAW** — not split per-function. A single
  `ProToolsProcessor` handles faders, routing, colors, etc. internally.
- **One active at a time** — selected via GUI toolbar dropdown.
- **Outside the Pipeline** — GUI/CLI orchestrates DAW operations directly.
  The Pipeline stays `analyze → plan → execute` for audio processing only.
- **Command model (Option B)** — `DawCommand` is a plain dataclass (just data).
  The concrete `DawProcessor` owns all execution logic — it builds commands,
  dispatches them internally, and returns `DawCommandResult` objects.

### 8.2 Abstract Base Class

```python
class DawProcessor(ABC):
    id: str
    name: str

    def config_params(cls) -> list[ParamSpec]: ...   # base: enabled toggle
    def configure(self, config) -> None: ...
    @property
    def enabled(self) -> bool: ...

    # Lifecycle
    def check_connectivity(self) -> tuple[bool, str]: ...
    def fetch(self, session) -> SessionContext: ...
    def transfer(self, session) -> list[DawCommandResult]: ...
    def sync(self, session) -> list[DawCommandResult]: ...

    # Ad-hoc commands (GUI tools)
    def execute_commands(self, session, commands) -> list[DawCommandResult]: ...
```

### 8.3 Lifecycle

Called by the GUI/CLI, **not** by the Pipeline:

1. **`configure(config)`** — read `ParamSpec` values (including `enabled`)
2. **`check_connectivity()`** — verify the DAW is reachable. Returns
   `(ok, message)`. Socket-based for Pro Tools PTSL, path validation for
   file-based processors.
3. **`fetch(session)`** — pull external state into
   `session.daw_state[self.id]` (routing folders, track list, colors, etc.)
4. **`transfer(session)`** — initial full push to the DAW. Internally builds
   `DawCommand` objects, executes each via processor-private dispatch, appends
   results to `session.daw_command_log`, and snapshots the transferred state
   for future `sync()` diffs.
5. **`sync(session)`** — incremental delta push. Compares current session state
   against the snapshot stored by `transfer()` and sends only the changes.

### 8.4 Ad-hoc Commands

`execute_commands(session, commands)` accepts externally-built `DawCommand`
objects — e.g. from a GUI color picker or rename tool — and routes them through
the same internal dispatch as `transfer()`/`sync()`. Results are appended to
`session.daw_command_log`.

### 8.5 Execution Model (Option B)

Commands are **plain data**; the processor is the **executor**.

```
transfer(session):
    commands = self._build_commands(session)    # list[DawCommand]
    results = []
    for cmd in commands:
        result = self._execute_command(cmd)     # processor-internal dispatch
        results.append(result)
        session.daw_command_log.append(result)
    return results

_execute_command(cmd):
    match cmd.command_type:
        case "set_clip_gain":  ...  # e.g. PTSL call
        case "set_fader":      ...
        case "set_color":      ...
    → DawCommandResult(cmd, success, error, timestamp)
```

`_build_commands` and `_execute_command` are processor-private — the ABC does
not define them. Each concrete processor implements its own dispatch.

### 8.6 Undo Infrastructure

Each `DawCommand` carries an `undo_params` field (e.g. `{"previous_value": -3.2}`)
capturing the state needed to reverse the operation. Undo = processor replays the
command log in reverse using `undo_params`. Implementation deferred; data model
ready from day one.

### 8.7 Configuration

Base class provides an `{id}_enabled` `ParamSpec` (bool, default `True`).
Subclasses extend via `super().config_params() + [...]`. The `daw_processors`
section in the structured config follows the same pattern as detectors and
processors:

```json
{
    "daw_processors": {
        "protools": { "protools_enabled": true, ... },
        "dawproject": { "dawproject_enabled": true, ... }
    }
}
```

### 8.8 Registration

```python
def default_daw_processors() -> list[DawProcessor]:
    return [ProToolsDawProcessor()]
```

### 8.9 ProToolsDawProcessor (`protools.py`)

Concrete `DawProcessor` for Avid Pro Tools, communicating via the
[Pro Tools Scripting SDK (PTSL)](https://developer.avid.com/) gRPC interface
through the `py-ptsl` Python client.

- **ID:** `protools`
- **Config:** `protools_enabled` (bool, default `True`),
  `protools_command_delay` (float, default `1.0` s — delay between Pro Tools
  commands to allow the DAW to settle)

**Lifecycle implementation:**

| Method | Behaviour |
|--------|-----------|
| `check_connectivity()` | Opens a `ptsl.Engine`, calls `ptsl.open()`, returns success/failure + Pro Tools session name |
| `fetch(session)` | Retrieves the folder track hierarchy and stores it in `session.daw_state["protools"]["folders"]`. Populates the GUI folder tree for drag-and-drop track assignment. |
| `transfer(session)` | Imports audio files into their assigned Pro Tools folders, sets track colors based on group → CIE L\*a\*b\* perceptual matching against the Pro Tools color palette. Accepts a `progress_callback(step, total, message)` for GUI progress reporting. Results are appended to `session.daw_command_log`. |
| `sync(session)` | Not yet implemented (raises `NotImplementedError`). |

**Color matching:**

The `transfer()` method assigns colors to newly imported tracks based on their
group. The matching pipeline is:

1. Fetch the Pro Tools color palette via `CId_GetColorPalette`.
2. For each session group, resolve the group's configured color name to an
   ARGB hex string (from `session.config["gui"]`).
3. Convert both the group ARGB and each palette entry to CIE L\*a\*b\* color
   space (via linearised sRGB → XYZ D65 → L\*a\*b\*).
4. Find the palette index with the smallest Euclidean distance in L\*a\*b\*
   (perceptual matching).
5. Apply `CId_SetTrackColor` with the matched palette index.

Tracks with no group assignment skip colorization.

---

## 9. Pipeline

### 9.1 Overview

Defined in `sessionpreplib/pipeline.py`. Four implemented phases:

```
analyze()   -> Run all detectors (track-level + session-level)
plan()      -> Run audio processors + group equalization + fader offsets
prepare()   -> Apply processors per track, write processed files to output dir
execute()   -> Apply gains, backup originals, write processed files (CLI legacy)
```

### 9.2 Phase Usage by Mode

| Mode | Phases executed |
|------|----------------|
| Dry-run (default) | `analyze` → `plan` |
| GUI with Prepare | `analyze` → `plan` → `prepare` |
| Execute (CLI legacy) | `analyze` → `plan` → `execute` |

### 9.3 Pipeline Class

```python
class Pipeline:
    def __init__(self, detectors, audio_processors, config, event_bus,
                 max_workers=None): ...
    def analyze(self, session: SessionContext) -> SessionContext: ...
    def plan(self, session: SessionContext) -> SessionContext: ...
    def prepare(self, session, output_dir, progress_cb=None) -> SessionContext: ...
    def execute(self, session, output_dir, backup_dir, is_overwriting) -> SessionContext: ...
```

### 9.4 Prepare Phase

`prepare()` generates processed audio files into a dedicated output folder,
enabling the GUI to offer processed files for DAW transfer without overwriting
originals.

**Behaviour:**

1. Wipe `output_dir` (clean slate on every run).
2. For each OK track, determine applicable processors: all enabled processors
   whose ID is **not** in `track.processor_skip`.
3. Filter to processors with a valid (non-error) `ProcessorResult`.
4. Deep-copy `audio_data`, chain `apply()` calls in priority order.
5. Write the processed audio to `output_dir/filename` (preserving original
   subtype via `write_track()`).
6. Update `track.processed_filepath` and `track.applied_processors`.
7. Set `session.prepare_state = "ready"`.

Tracks with no applicable processors are skipped (no file written,
`processed_filepath` set to `None`).

**Staleness:** The GUI transitions `prepare_state` from `"ready"` to `"stale"`
whenever gain, classification, RMS anchor, per-track processor selection, or
re-analysis changes occur. The Prepare button and Use Processed toggle reflect
the current state.

### 9.5 Parallel Execution

All three parallelizable stages use `concurrent.futures.ThreadPoolExecutor`:

1. **File loading** (`load_session`): WAV files are read from disk in parallel.
   I/O-bound — threading gives significant speedup.
2. **Track analysis** (`analyze`): Per-track detector chains run in parallel
   across files.  Within a single track, detectors still run sequentially
   (topological order from `depends_on`).  Session-level detectors run after
   all tracks complete (barrier).
3. **Track planning** (`plan`): Per-track processor chains run in parallel.
   Group equalization and fader offsets run after all tracks complete.

**Why threads, not processes:** Detector/processor compute is dominated by
numpy FFT/RMS operations which release the GIL, giving real parallelism
without the serialization overhead of multiprocessing (audio data arrays are
large).

**Thread safety:**
- `EventBus` is protected by `threading.Lock` (safe to emit from pool threads).
- Detector/processor instances are shared across threads but are read-only
  after `configure()` — `analyze()`/`process()` methods only read `self` and
  write to the per-track `TrackContext` (unique per thread).
- Progress counter in `AnalyzeWorker` uses `threading.Lock` for atomic
  increment + emit.

**Worker count:** `max_workers` defaults to `min(os.cpu_count(), 8)`, capped
at the number of tracks.

`plan()` internally calls:
- `_apply_group_levels()` — grouped tracks get minimum gain
- `_compute_fader_offsets()` — inverse gain + anchor adjustment

### 9.6 `load_session()` Helper

```python
def load_session(source_dir, config, event_bus=None) -> SessionContext
```

Loads all WAVs from `source_dir` in parallel, assigns groups (named,
first-match-wins), appends overlap warnings to `session.warnings`.

### 9.7 Topological Sort

`_topo_sort_detectors()` uses Kahn's algorithm. Raises `ConfigError` on
cycles or missing dependencies.

---

## 10. Session Queue

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

## 11. Configuration & Presets

Defined in `sessionpreplib/config.py`.

### 11.1 Merge Order

```
built-in defaults  ->  preset file  ->  CLI args / GUI overrides
```

### 11.2 Functions

- `default_config() -> dict` — all built-in defaults
- `merge_configs(*configs) -> dict` — left-to-right merge; list keys (`force_transient`, `force_sustained`, `group`) are concatenated
- `validate_config(config) -> None` — raises `ConfigError` on invalid values
- `load_preset(path) -> dict` — loads a JSON preset, strips `schema_version` and `_description` metadata
- `save_preset(config, path, *, description=None) -> None` — saves non-default, non-internal keys with `schema_version`

Internal/CLI-only keys (`execute`, `overwrite`, `output_folder`, `backup`,
`report`, `json`) are excluded from presets automatically.

### 11.3 Preset File Format

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

### 11.4 Default Config Keys

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
    "group": [],
    "anchor": None,                "normalize_faders": False,
    "execute": False,              "overwrite": False,
    "output_folder": "processed",  "backup": "_originals",
    "report": "sessionprep.txt",   "json": "sessionprep.json",
}
```

---

## 12. Event System

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
| `track.plan_start` | Pipeline | `filename`, `index`, `total` |
| `processor.start` | Pipeline | `processor_id`, `filename` |
| `processor.complete` | Pipeline | `processor_id`, `filename` |
| `track.plan_complete` | Pipeline | `filename`, `index`, `total` |
| `track.write_start` | Pipeline | `filename`, `index`, `total` |
| `track.write_complete` | Pipeline | `filename`, `index`, `total` |
| `prepare.start` | Pipeline | `filename` |
| `prepare.complete` | Pipeline | `filename` |
| `prepare.error` | Pipeline | `filename`, `error` |
| `job.start` | Queue | `job_id` |
| `job.complete` | Queue | `job_id`, `status` |

No EventBus = no overhead. All emissions are guarded with `if self.event_bus`.

---

## 13. Rendering

Defined in `sessionpreplib/rendering.py`. Two standalone functions (not wrapped
in an ABC — see TODO.md for future Renderer abstraction).

### 13.1 `build_diagnostic_summary(session, track_detectors, session_detectors) -> dict`

Aggregates detector results into the four-category summary structure:

```python
{
    "problems": [{"title", "hint", "items", "standalone"}, ...],
    "attention": [...],
    "information": [...],
    "clean": [...],
    "clean_count": int,
    "total_ok": int,
    "overview": {...},
}
```

Handles: file errors, format/length mismatches, clipping, DC offset, stereo
compatibility (correlation + mono folddown combined), dual-mono, silence,
one-sided silence, subsonic, tail exceedance, grouping overlaps, clean
summaries, and overview statistics.

### 13.2 `render_diagnostic_summary_text(summary) -> str`

Renders the summary dict as plain text with emoji category headers. Used for
both console output (via `sessionprep.py`) and the `sessionprep.txt` report.

### 13.3 CLI Rendering

Rich-based rendering (progress bars, tables, panels) lives in `sessionprep.py`,
not the library. `rich` is a CLI-only dependency.

---

## 14. Schema Versioning

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

## 15. Error Isolation Strategy

### 15.1 Principles

- **Partial results are always better than a crash.**
- Errors are isolated per-track, per-detector, and per-processor.
- Configuration errors abort early (before any work starts).

### 15.2 Error Handling Table

| Failure | Policy |
|---------|--------|
| One detector throws on one track | Store `DetectorResult` with `Severity.PROBLEM`, `error` field set; **continue** |
| One track file cannot be read | Mark `TrackContext.status = "Error: ..."`, skip all detectors/processors; **continue** |
| An audio processor throws on one track | Store `ProcessorResult` with `error` field set; skip audio write; **continue** |
| Config validation fails at startup | **Abort** with descriptive error |
| Cyclic detector dependency | **Abort** at pipeline construction |

### 15.3 Implementation

Every `analyze()`, `process()`, and `apply()` call is wrapped in try/except
by the pipeline. Components do not need to handle unexpected exceptions.

---

## 16. Validation Layer

### 16.1 Pipeline Validation

Called at pipeline construction:

1. All `depends_on` references point to existing detector IDs
2. No circular dependencies among detectors
3. No duplicate IDs across detectors and processors

### 16.2 Config Validation

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

## 17. CLI App (`sessionprep.py`)

A thin shell (~770 lines) that imports everything from `sessionpreplib`:

1. **argparse** — defines all CLI arguments (same flags as original script)
2. **Config conversion** — `vars(args)` merged with `default_config()`
3. **Pipeline construction** — `default_detectors()` + `default_processors()`
4. **Rich rendering** — progress bars, panels, diagnostic summary, fader table
5. **Report/JSON writing** — `generate_report()` and `save_json()` for file outputs
6. **Backup handling** — backs up originals when overwriting

The CLI contains **zero** analysis, detection, processing, or DSP logic.

---

## 18. GUI App (`sessionprep-gui.py` / `sessionprepgui/`)

The GUI is a PySide6 application split across the `sessionprepgui/` package.
`sessionprep-gui.py` is a thin entry point that delegates to the package.

### 18.1 Running the GUI

```bash
uv run python sessionprep-gui.py
```

Requires PySide6 and sounddevice (installed via the `gui` optional dependency
group).

### 18.2 Package Architecture

| Module | Responsibility |
|--------|---------------|
| `__init__.py` | Exports `main()` |
| `settings.py` | `load_config()`, `save_config()`, `config_path()` — persistent GUI preferences |
| `theme.py` | `COLORS` dict, `FILE_COLOR_*` constants, dark palette + stylesheet |
| `helpers.py` | `esc()`, `track_analysis_label(track, detectors=None)` (filters via `is_relevant()`), `fmt_time()`, severity maps |
| `widgets.py` | `BatchEditTableWidget`, `BatchComboBox` — reusable batch-edit base classes preserving multi-row selection across cell-widget clicks (zero app imports) |
| `worker.py` | QThread workers: `AnalyzeWorker` (pipeline in background, thread-safe progress, per-track signals), `BatchReanalyzeWorker` (subset re-analysis after batch overrides), `PrepareWorker` (runs `Pipeline.prepare()` in background with progress), `DawCheckWorker` (connectivity check), `DawFetchWorker` (folder fetch), `DawTransferWorker` (transfer with progress + progress_value signals) |
| `report.py` | HTML rendering: `render_summary_html()`, `render_fader_table_html()`, `render_track_detail_html()` |
| `waveform.py` | `WaveformWidget` — two display modes (waveform + spectrogram), vectorised NumPy peak/RMS downsampling, mel spectrogram (256 mel bins via `scipy.signal.stft`, configurable FFT/window/dB range/colormap), dB and frequency scales, peak/RMS markers, crosshair mouse guide (dBFS in waveform, Hz in spectrogram), mouse-wheel zoom/pan (Ctrl+wheel h-zoom, Ctrl+Shift+wheel v-zoom, Shift+Alt+wheel freq pan, Shift+wheel scroll), keyboard shortcuts (R/T zoom), detector issue overlays with optional frequency bounds, RMS L/R and RMS AVG envelopes, playback cursor, tooltips |
| `playback.py` | `PlaybackController` — sounddevice OutputStream lifecycle, QTimer cursor updates, signal-based API |
| `param_widgets.py` | Reusable ParamSpec-driven widget builders (`_build_param_page`, `_read_widget`, `_set_widget_value`, `_build_tooltip`) + `GroupsTableWidget` (drag-reorderable group editor with color/gain-linked/DAW-target columns) |
| `preferences.py` | `PreferencesDialog` — two-tab layout (Global + Config Presets), config preset CRUD, group preset CRUD, ParamSpec-driven widgets, reset-to-default, HiDPI scaling |
| `mainwindow.py` | `SessionPrepWindow` (QMainWindow) — orchestrator, UI layout, slot handlers, toolbar config/group preset combos, session Config tab |

### 18.3 Dependency Direction

```
settings (leaf) <--  mainwindow
theme (leaf)  <--  helpers  <--  report  <--  mainwindow
widgets (leaf) <---------------------------------+
                                                 |
              waveform     <--------------------+
              playback     <--------------------+
              worker       <--------------------+
              preferences  <--------------------+
```

No circular imports. `settings`, `theme`, `helpers`, and `widgets` are pure
leaves. `preferences` reads `ParamSpec` metadata from detectors and processors.
`mainwindow` composes all other modules.

### 18.4 Key Design Decisions

- **BatchEditTableWidget** (`widgets.py`) is a generic `QTableWidget` subclass
  that preserves multi-row selection when a persistent-editor cell widget (e.g.
  a `QComboBox`) receives focus.  The core mechanism is a `selectionCommand()`
  override that returns `NoUpdate` when Qt's internal
  `checkPersistentEditorFocus()` would otherwise `ClearAndSelect`.  This
  replicates the behaviour found in Pro Tools, where Shift-selecting multiple
  tracks and Alt-clicking a control (send, insert, etc.) applies the change to
  all selected tracks.  The modifier key combination is **Alt+Shift**.
  `BatchComboBox` detects this modifier on `mousePressEvent` and sets a
  `batch_mode` flag that the changed-slot inspects.  Combo signals should use
  `textActivated` (not `currentTextChanged`) so that re-selecting the same
  value still triggers the batch path.  `batch_selected_keys(key_column)` and
  `restore_selection(keys, key_column)` accept a configurable identifier
  column (default 0) so the pattern works regardless of table layout.
  `BatchReanalyzeWorker` runs the re-analysis asynchronously with progress
  signals; its custom signal is named `batch_finished` to avoid collision with
  `QThread.finished`.  App-level integration uses a single generic method
  `_batch_apply_combo(combo, column, value, prepare_fn, run_detectors)` that
  any dropdown batch-change can delegate to.
- **PlaybackController** encapsulates all `sounddevice` state with a
  signal-based API (`cursor_updated`, `playback_finished`, `error`).
  `mainwindow.py` has zero direct `sd` usage.
- **WaveformWidget** supports two display modes: **waveform** (default) and
  **spectrogram**. Issue overlays from `IssueLocation` objects are rendered in
  both modes. Per-channel regions are drawn in the corresponding channel lane;
  whole-file issues span all channels. Issues with `freq_min_hz`/`freq_max_hz`
  render as frequency-bounded rectangles in spectrogram mode (mapped via mel
  scale); without frequency bounds the overlay spans the full frequency range.
  Tooltips use a 5-pixel hit tolerance for narrow markers. Overlays and tooltips
  are filtered by `_enabled_overlays` — only detectors checked in the Detector
  Overlays dropdown are painted/hoverable.
  Additional features:
  - **Waveform toolbar** — layout:
    `[▾ Waveform/Spectrogram] [▾ Display] [▾ Detector Overlays] [Peak / RMS Max] [RMS L/R] [RMS AVG]  ... [Fit] [+] [−] [↑] [↓]`
    The Display Mode dropdown (leftmost) uses a `QActionGroup` for mutual
    exclusivity between waveform and spectrogram. The Display dropdown
    contains spectrogram-specific settings (FFT Size, Window, Color Theme,
    dB Floor with presets −120..−20, dB Ceiling with presets −30..0) and is
    only visible in spectrogram mode. Waveform-only controls (Peak/RMS Max,
    RMS L/R, RMS AVG) are hidden in spectrogram mode. The Detector Overlays
    dropdown is a `QToolButton` with a `QMenu` of checkable actions, one per
    detector that produced issues for the current track (visible in both
    modes). All unchecked by default. Detectors suppressed by `is_relevant()`
    are excluded. The button label shows a count when items are checked
    (e.g. "Detector Overlays (2)"). Menu is rebuilt on track selection and
    on classification override changes.
  - **Spectrogram mode** — mel spectrogram (256 mel bins) computed via
    `scipy.signal.stft` in a background thread (`WaveformLoadWorker`).
    Configurable FFT size, window type, dB floor/ceiling, and colormap.
    Colormap registry (`SPECTROGRAM_COLORMAPS`): magma, viridis, grayscale
    (256-entry RGBA LUTs). Frequency axis with mel-spaced ticks at 50, 100,
    200, 500, 1k, 2k, 5k, 10k, 20k Hz. Cached `QImage` keyed by view
    params for efficient repainting.
  - **Horizontal time scale** — time axis at the bottom of the waveform area.
  - **dB measurement scale** (waveform mode) — left/right margins (30 px)
    with dBFS tick labels (0, −3, −6, −12, −18, −24, −36, −48, −60), tick
    marks, and faint connector lines spanning the waveform area. Adaptive
    spacing (min 18 px between ticks, lane-edge padding to prevent
    cross-channel overlap). Scale adjusts dynamically with vertical resize
    and `_vscale`.
  - **Peak marker ("P")** — dark violet solid vertical line at the sample
    with the highest absolute amplitude across all channels. A small
    horizontal crosshair is drawn at the peak amplitude on the owning
    channel only. Hovering shows "Peak: X.X dBFS" tooltip. Controlled
    by the "Peak / RMS Max" toggle (default: on).
  - **Max RMS marker ("R")** — dark teal-blue solid vertical line at the
    centre of the loudest momentary RMS window (combined across channels).
    Horizontal crosshair on the positive side of each channel lane.
    Hovering shows "Max RMS: X.X dBFS" tooltip. Controlled by the same
    "Peak / RMS Max" toggle.
  - **RMS L/R overlay** — per-channel RMS envelope curve (yellow), toggled
    via the "RMS L/R" toolbar button.
  - **RMS AVG overlay** — combined (average) RMS envelope curve (orange),
    toggled via the "RMS AVG" toolbar button.
  - **Mouse guide** — a thin grey dashed crosshair follows the mouse cursor.
    In waveform mode, the corresponding dBFS value is shown at the scale
    margins. In spectrogram mode, the corresponding frequency (Hz) is shown.
    The guide disappears when the mouse leaves the widget.
  - **Mouse navigation** — Ctrl+wheel for horizontal zoom, Ctrl+Shift+wheel
    for vertical zoom (amplitude in waveform, frequency range in spectrogram),
    Shift+Alt+wheel for frequency panning (spectrogram only), Shift+wheel
    for horizontal scroll.
- **report.py** contains pure HTML-building functions (no widget references),
  making them independently testable.
- **PreferencesDialog** uses a two-tab layout:
  - **Global** tab — General (HiDPI scale, project dir, etc.), Colors
    (palette editor), Groups (group preset CRUD + `GroupsTableWidget`).
    Each has its own tree + stacked widget.
  - **Config Presets** tab — toolbar with combo + Add/Duplicate/Rename/Delete
    buttons. Below it: Analysis, Detectors (with presentation params),
    Processors, DAW Processors pages. Each has its own tree + stacked widget.
    Switching presets saves current widget values to the old preset and loads
    the new one.
  Widget type, range, step size, and decimal precision are all derived from
  `ParamSpec` (via `param_widgets.py`). Each parameter gets a visible
  description subtext, a rich tooltip (with key name, default, range), and a
  reset-to-default button using Qt's `SP_BrowserReload` icon.
- **Toolbar config preset chooser** — the analysis toolbar includes a
  "Config:" combo that shows available config presets. Switching presets
  with an active session warns the user, then resets session config and
  triggers re-analysis while preserving group assignments.
- **Session Config tab** — a per-session config override editor (tree +
  stacked widget) that mirrors the Config Presets layout. Initialised from
  the active global config preset on first analysis. Edits take effect
  immediately via `_flat_config()` without needing to save. A "Reset to
  Preset Defaults" button restores the global preset values.
- **HiDPI scaling** is applied via `QT_SCALE_FACTOR` environment variable,
  read directly from the JSON config file before `QApplication` is created
  (bypassing the validate-and-merge path to avoid side effects).
- **File-based processing pipeline** — an opt-in, non-destructive workflow
  for generating processed audio files. The pipeline is controlled by three
  UI elements:
  - **Prepare button** (analysis toolbar, right-aligned) — triggers
    `PrepareWorker` → `Pipeline.prepare()`. Text reflects staleness state:
    "Prepare" (never run), "Prepare ✓" (ready), "Prepare (!)" (stale).
    Enabled after analysis completes.
  - **Processing column** (analysis table, column 7) — per-track multiselect
    `QToolButton` with a checkable `QMenu` listing all enabled
    `AudioProcessor` instances. Label shows "Default" (all processors
    active, i.e. `processor_skip` is empty), "None" (all skipped), or
    comma-separated names (partial selection). When no processors are
    enabled globally, the button is disabled and shows "None". Toggling
    a processor adds/removes its ID from `track.processor_skip` and marks
    the Prepare state as stale. Editable only in the analysis phase.
  - **Use Processed toggle** (setup toolbar) — checkable `QAction` that
    sets `session.config["_use_processed"]`. Label shows "Use Processed:
    On/Off" with "(!) " appended when `prepare_state == "stale"`. Enabled
    only when `prepare_state` is `"ready"` or `"stale"`. When on,
    `ProToolsDawProcessor.transfer()` uses `track.processed_filepath`
    instead of `track.filepath` for each track that has a processed file.
  - **Staleness triggers** — changing gain, classification, RMS anchor,
    per-track processor selection, or re-analyzing transitions
    `prepare_state` from `"ready"` to `"stale"`, updating both the Prepare
    button and Use Processed toggle labels.
  - **Output directory** — resolved from `config["app"]["output_folder"]`
    (default: `"processed"`), relative to the session source directory.
- **DAW integration** — the Session Setup tab provides a toolbar with
  Connect/Check, Fetch, Transfer, and Sync actions. A combo box selects the
  active DAW processor. The folder tree (right side of a splitter) supports
  drag-and-drop assignment of tracks to folders. Transfer runs asynchronously
  via `DawTransferWorker`, with a progress panel (label + `QProgressBar`)
  below the tree that auto-hides 2 seconds after completion.
- **Waveform worker cancellation** — `WaveformLoadWorker` and
  `SpectrogramRecomputeWorker` carry a `threading.Event` cancellation flag.
  When the user switches tracks, the old worker is cancelled (flag set)
  before a new one starts. The `run()` method checks the flag between every
  expensive phase (channel split, peak finding, per-channel RMS cumsum,
  spectrogram) and exits early if set, preventing CPU pileup from stacked
  background threads.
- The GUI contains **zero** analysis, detection, processing, or DSP logic —
  all analysis runs through `sessionpreplib` via `AnalyzeWorker`.

### 18.5 Persistent Configuration (`sessionprep.config.json`)

The GUI stores all settings in a JSON config file in the OS-specific user
preferences directory:

| OS      | Path |
|---------|------|
| Windows | `%APPDATA%\sessionprep\sessionprep.config.json` |
| macOS   | `~/Library/Application Support/sessionprep/sessionprep.config.json` |
| Linux   | `$XDG_CONFIG_HOME/sessionprep/sessionprep.config.json` (defaults to `~/.config/`) |

**Four-section format** — separates global settings from named presets:

```json
{
    "app": {
        "scale_factor": 1.0,
        "report_verbosity": "normal",
        "output_folder": "",
        "spectrogram_colormap": "magma",
        "invert_scroll": "default",
        "default_project_dir": "",
        "active_config_preset": "Default",
        "active_group_preset": "Default"
    },
    "colors": [
        { "name": "Guardsman Red", "argb": "#ffcc0000" },
        { "name": "Dodger Blue Light", "argb": "#ff3399ff" },
        "..."
    ],
    "config_presets": {
        "Default": {
            "analysis": { "window": 400, "stereo_mode": "avg", "..." : "..." },
            "detectors": {
                "clipping": { "clip_consecutive": 3 },
                "dc_offset": { "dc_offset_warn_db": -40.0 },
                "...": "..."
            },
            "processors": {
                "bimodal_normalize": { "target_rms": -18.0, "target_peak": -6.0 }
            },
            "daw_processors": {
                "protools": { "protools_enabled": true },
                "...": "..."
            },
            "presentation": {
                "show_clean_detectors": false
            }
        }
    },
    "group_presets": {
        "Default": [
            { "name": "Kick", "color": "Guardsman Red", "gain_linked": true, "daw_target": "Kick" },
            { "name": "Snare", "color": "Dodger Blue Light", "gain_linked": true, "daw_target": "Snare" },
            "..."
        ]
    }
}
```

- **`app`** — global application settings (HiDPI scale, colormap, scroll
  direction, default project directory, active preset names); not consumed
  by the analysis pipeline
- **`colors`** — global color palette (name + ARGB hex pairs), referenced
  by group presets
- **`config_presets`** — named config presets, each containing five
  sub-sections: `analysis`, `detectors`, `processors`, `daw_processors`,
  `presentation`. The active preset is identified by `app.active_config_preset`.
  Managed via the Preferences dialog (Config Presets tab) and the toolbar
  "Config:" combo.
- **`group_presets`** — named group presets (lists of track group dicts
  with name, color, gain-linked, daw-target). Managed via the Preferences
  dialog (Global → Groups) and the toolbar "Group:" combo.

Session-specific values (`force_transient`, `force_sustained`, `group`,
`anchor`) are **not** stored in the config file — they are per-session
and will be saved in session project files in the future.

**Config presets vs. session config:**

Config presets define the global defaults for analysis parameters, detector
thresholds, processor targets, and DAW processor settings. When analysis
starts, the active config preset is snapshot into the session's Config tab
(`_session_config`). The user can then tweak per-session overrides without
affecting the global preset. `_flat_config()` reads from the session config
widgets when available, falling back to the global preset otherwise.

**Lifecycle:**

1. On first launch, `build_defaults()` creates the file with all built-in
   defaults from every component's `config_params()`. A "Default" config
   preset and "Default" group preset are created.
2. On subsequent launches, the file is deep-merged with current defaults
   (forward-compatible for new keys/detectors). Legacy flat-format configs
   are migrated to the four-section structure by `_migrate_legacy_config()`.
3. If the file is corrupt, it is backed up as `*.bak` and reset to defaults.
4. `resolve_config_preset(config, name)` retrieves a named preset, merged
   with defaults. `flatten_structured_config()` converts a preset into a
   flat key-value dict for the pipeline and `AnalyzeWorker`.
5. The **Preferences dialog** provides a two-tab UI for editing all settings.
   On save, changes are written to the config file. If analysis-relevant
   parameters changed and a session is loaded, re-analysis is triggered
   automatically; presentation-only changes trigger a lightweight refresh.

The CLI is **not** affected by this file — it continues to use its own
`default_config()` + command-line arguments.

---

## 19. Migration Notes

### 19.1 Mapping from Original Code to Library

| Original (`sessionprep.py`) | Library location |
|------|-------------|
| `parse_arguments()` | `sessionprep.py` (CLI only) |
| `analyze_audio()` | `audio.py` (DSP) + individual detectors |
| `check_clipping()` / `detect_clipping_ranges()` | `audio.py` + `detectors/clipping.py` |
| `subsonic_ratio_db()` | `audio.py` (`subsonic_stft_analysis`) + `detectors/subsonic.py` |
| `calculate_gain()` | `processors/bimodal_normalize.py` |
| `matches_keywords()` | `utils.py` |
| `parse_group_specs()` / `assign_groups()` | `utils.py` |
| `build_session_overview()` | Session-level detectors + `rendering.py` |
| `build_diagnostic_summary()` | `rendering.py` |
| `render_diagnostic_summary_text()` | `rendering.py` |
| `print_diagnostic_summary()` | `sessionprep.py` (Rich) |
| `generate_report()` / `save_json()` | `sessionprep.py` |
| `process_files()` | `pipeline.py` + `sessionprep.py` |
| `protools_sort_key()` | `utils.py` |
| `db_to_linear()` / `linear_to_db()` / `format_duration()` | `audio.py` |

### 19.2 Dependencies

| Package | Used by |
|---------|---------|
| `numpy` | `sessionpreplib` (core dependency) |
| `soundfile` | `sessionpreplib/audio.py` (audio I/O) |
| `scipy` | `sessionpreplib/audio.py` (subsonic STFT), `sessionprepgui/waveform.py` (mel spectrogram) — core dependency |
| `rich` | `sessionprep.py` only — **not** a library dependency |
| `PySide6` | `sessionprepgui` only — optional GUI dependency |
| `sounddevice` | `sessionprepgui/playback.py` only — optional GUI dependency |

### 19.3 Layer Cake

```
SessionQueue                          manages N jobs, priority-ordered
  +-- SessionJob                      one session dir + config + status
       +-- Pipeline                   configured per-job via factory
            |-- analyze()             -> TrackDetectors (topo-sorted)
            |                         -> SessionDetectors
            |-- plan()                -> AudioProcessors (priority-sorted)
            |                         -> Group levelling + fader offsets
            |-- prepare()             -> Apply per-track processors, write to output dir
            |                         -> Respects processor_skip, sets prepare_state
            +-- execute()             -> AudioProcessors.apply() + file write (CLI legacy)

DawProcessor (orchestrated by GUI/CLI, outside Pipeline)
  |-- check_connectivity()            -> verify DAW is reachable
  |-- fetch(session)                  -> pull DAW state into session.daw_state
  |-- transfer(session)               -> full push (builds + executes DawCommands)
  |                                   -> uses processed files when _use_processed is on
  |-- sync(session)                   -> incremental delta push
  +-- execute_commands(session, cmds)  -> ad-hoc commands from GUI tools
```
