from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

PRESET_SCHEMA_VERSION = "1.0"

# Keys that are internal/CLI-only and should not be saved in presets
_INTERNAL_KEYS = {
    "execute", "overwrite", "output_folder", "backup",
    "report", "json", "_source_dir",
}


class ConfigError(Exception):
    """Raised when configuration validation fails."""
    pass


@dataclass
class ConfigFieldError:
    """A single validation error for one configuration field.

    Attributes:
        key:     The config key that failed validation.
        value:   The offending value.
        message: Human-readable explanation of what is wrong.
    """
    key: str
    value: Any
    message: str


@dataclass(frozen=True)
class ParamSpec:
    """Declarative specification for a single configuration parameter.

    Used by detectors, processors, and the shared analysis / session
    sections to describe their parameters — including type, default,
    valid range, allowed values, and human-readable labels.
    """
    key: str
    type: type | tuple              # expected Python type(s)
    default: Any
    label: str                       # short UI label
    description: str = ""            # longer tooltip / help text
    min: float | int | None = None   # inclusive lower bound (unless min_exclusive)
    max: float | int | None = None   # inclusive upper bound (unless max_exclusive)
    min_exclusive: bool = False
    max_exclusive: bool = False
    choices: list | None = None      # allowed string values
    item_type: type | None = None    # element type for list fields
    nullable: bool = False           # True if None is valid


def default_config() -> dict[str, Any]:
    """Returns the built-in default configuration."""
    return {
        "target_rms": -18.0,
        "target_peak": -6.0,
        "crest_threshold": 12.0,
        "clip_consecutive": 3,
        "clip_report_max_ranges": 10,
        "dc_offset_warn_db": -40.0,
        "corr_warn": -0.3,
        "dual_mono_eps": 1e-5,
        "mono_loss_warn_db": 6.0,
        "one_sided_silence_db": -80.0,
        "subsonic_hz": 30.0,
        "subsonic_warn_ratio_db": -20.0,
        "window": 400,
        "stereo_mode": "avg",
        "rms_anchor": "percentile",
        "rms_percentile": 95.0,
        "gate_relative_db": 40.0,
        "tail_max_regions": 20,
        "tail_min_exceed_db": 3.0,
        "tail_hop_ms": 10,
        "force_transient": [],
        "force_sustained": [],
        "group": [],
        "group_overlap": "warn",
        "anchor": None,
        "normalize_faders": False,
        "execute": False,
        "overwrite": False,
        "output_folder": "processed",
        "backup": "_originals",
        "report": "sessionprep.txt",
        "json": "sessionprep.json",
    }


def merge_configs(*configs: dict[str, Any]) -> dict[str, Any]:
    """
    Merge multiple config dicts left-to-right.
    Later values override earlier ones.
    List values (force_transient, force_sustained, group) are concatenated.
    """
    _LIST_KEYS = {"force_transient", "force_sustained", "group"}
    result: dict[str, Any] = {}
    for cfg in configs:
        for k, v in cfg.items():
            if k in _LIST_KEYS and k in result and isinstance(result[k], list) and isinstance(v, list):
                result[k] = result[k] + v
            else:
                result[k] = v
    return result


def load_preset(path: str) -> dict[str, Any]:
    """
    Load a JSON preset file. Returns a partial config dict.
    Raises ConfigError if the file cannot be read or parsed.
    """
    if not os.path.isfile(path):
        raise ConfigError(f"Preset file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in preset file {path}: {e}")
    except OSError as e:
        raise ConfigError(f"Cannot read preset file {path}: {e}")

    if not isinstance(data, dict):
        raise ConfigError(f"Preset file must contain a JSON object, got {type(data).__name__}")

    # Strip metadata keys — they are informational, not config
    preset = {k: v for k, v in data.items() if k not in ("schema_version", "_description")}
    return preset


def save_preset(config: dict[str, Any], path: str, *, description: str | None = None) -> None:
    """
    Save a config dict as a JSON preset file.
    Internal/CLI-only keys are excluded automatically.
    """
    preset: dict[str, Any] = {"schema_version": PRESET_SCHEMA_VERSION}
    if description:
        preset["_description"] = description

    defaults = default_config()
    for k, v in config.items():
        if k in _INTERNAL_KEYS:
            continue
        if k.startswith("_"):
            continue
        # Only save values that differ from defaults
        if k in defaults and defaults[k] == v:
            continue
        preset[k] = v

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(preset, f, indent=4, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Shared parameter sections
# ---------------------------------------------------------------------------

ANALYSIS_PARAMS: list[ParamSpec] = [
    ParamSpec(
        key="window", type=int, default=400, min=1,
        label="RMS window size (ms)",
        description="Momentary-loudness window used for RMS analysis.",
    ),
    ParamSpec(
        key="stereo_mode", type=str, default="avg",
        choices=["avg", "sum"],
        label="Stereo RMS mode",
        description="How left/right channels are combined for RMS.",
    ),
    ParamSpec(
        key="rms_anchor", type=str, default="percentile",
        choices=["percentile", "max"],
        label="RMS anchor strategy",
        description=(
            "How to pick the representative RMS level from the distribution of "
            "momentary RMS windows. 'percentile' (default) takes the Nth "
            "percentile of gated windows — robust to outliers like breath pops "
            "or bleed spikes, tracks the chorus-level loudness that drives your "
            "insert processing. 'max' takes the single loudest window — useful "
            "for very short files (single hits) but fragile for longer material "
            "where one anomalous moment can skew the gain decision."
        ),
    ),
    ParamSpec(
        key="rms_percentile", type=(int, float), default=95.0,
        min=0.0, max=100.0, min_exclusive=True, max_exclusive=True,
        label="RMS percentile",
        description=(
            "Which percentile of the gated RMS window distribution to use as "
            "the anchor (only applies when anchor = percentile). P95 means "
            "95% of active windows are at or below the anchor — in practice, "
            "this represents 'what the loud sections typically sound like'. "
            "Lower values (e.g. 90) produce a lower anchor and more aggressive "
            "gain. Higher values approach the max window."
        ),
    ),
    ParamSpec(
        key="gate_relative_db", type=(int, float), default=40.0, min=0.0,
        label="Relative gate (dB)",
        description=(
            "RMS windows more than this many dB below the loudest window are "
            "excluded before computing the anchor and tail statistics. This is "
            "relative to the loudest window, not an absolute dBFS value. "
            "Critical for sparse tracks (FX hits, vocal doubles) where most "
            "windows are near-silent — without gating, the percentile anchor "
            "would be dominated by silence."
        ),
    ),
    ParamSpec(
        key="dbfs_convention", type=str, default="standard",
        choices=["standard", "aes17"],
        label="dBFS convention",
        description=(
            "Standard: 0 dBFS = full-scale digital. "
            "AES17: 0 dBFS = RMS of a full-scale sine (+3.01 dB offset)."
        ),
    ),
    # -- Global processing defaults ------------------------------------------
    ParamSpec(
        key="group_overlap", type=str, default="warn",
        choices=["warn", "error", "merge"],
        label="Group overlap handling",
        description="Default behaviour when a track matches multiple groups.",
    ),
    ParamSpec(
        key="normalize_faders", type=bool, default=False,
        label="Normalize fader offsets",
        description="Shift fader offsets so the smallest is 0 dB.",
    ),
]


# ---------------------------------------------------------------------------
# Validation  (ParamSpec-driven)
# ---------------------------------------------------------------------------

def validate_param_values(
    params: list[ParamSpec],
    values: dict[str, Any],
) -> list[ConfigFieldError]:
    """Validate *values* against a list of :class:`ParamSpec` definitions.

    Returns a (possibly empty) list of :class:`ConfigFieldError` objects.
    Only keys present in *values* are checked; missing keys are not errors
    (they will receive their default).
    """
    errors: list[ConfigFieldError] = []

    for spec in params:
        if spec.key not in values:
            continue

        value = values[spec.key]

        # -- nullable --
        if value is None:
            if spec.nullable:
                continue
            errors.append(ConfigFieldError(
                spec.key, value,
                f"{spec.label} must not be empty.",
            ))
            continue

        # -- type (bool ⊄ int guard) --
        expected = spec.type
        if expected is not bool and isinstance(value, bool):
            errors.append(ConfigFieldError(
                spec.key, value,
                f"{spec.label} must be {_type_label(expected)}, got boolean.",
            ))
            continue
        if not isinstance(value, expected):
            errors.append(ConfigFieldError(
                spec.key, value,
                f"{spec.label} must be {_type_label(expected)}, "
                f"got {type(value).__name__}.",
            ))
            continue

        # -- choices --
        if spec.choices is not None and value not in spec.choices:
            opts = ", ".join(repr(c) for c in spec.choices)
            errors.append(ConfigFieldError(
                spec.key, value,
                f"{spec.label} must be one of {opts}.",
            ))
            continue

        # -- numeric range --
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if spec.min is not None:
                if spec.min_exclusive and value <= spec.min:
                    errors.append(ConfigFieldError(
                        spec.key, value,
                        f"{spec.label} must be greater than {spec.min}.",
                    ))
                    continue
                if not spec.min_exclusive and value < spec.min:
                    errors.append(ConfigFieldError(
                        spec.key, value,
                        f"{spec.label} must be at least {spec.min}.",
                    ))
                    continue
            if spec.max is not None:
                if spec.max_exclusive and value >= spec.max:
                    errors.append(ConfigFieldError(
                        spec.key, value,
                        f"{spec.label} must be less than {spec.max}.",
                    ))
                    continue
                if not spec.max_exclusive and value > spec.max:
                    errors.append(ConfigFieldError(
                        spec.key, value,
                        f"{spec.label} must be at most {spec.max}.",
                    ))
                    continue

        # -- list items --
        if spec.item_type is not None and isinstance(value, list):
            for i, item in enumerate(value):
                if not isinstance(item, spec.item_type):
                    errors.append(ConfigFieldError(
                        spec.key, value,
                        f"{spec.label}[{i}] must be "
                        f"{spec.item_type.__name__}, "
                        f"got {type(item).__name__}.",
                    ))
                    break

    return errors


def _all_param_specs() -> list[ParamSpec]:
    """Collect every :class:`ParamSpec` from analysis, detectors,
    and processors.  Used by the flat-config validators."""
    from .detectors import default_detectors
    from .processors import default_processors

    specs = list(ANALYSIS_PARAMS)
    for det in default_detectors():
        specs.extend(det.config_params())
    for proc in default_processors():
        specs.extend(proc.config_params())
    return specs


def validate_config_fields(config: dict[str, Any]) -> list[ConfigFieldError]:
    """Validate a **flat** config dict against all known :class:`ParamSpec`
    definitions (analysis + every detector + every processor).

    Returns structured errors.  Never raises.
    """
    return validate_param_values(_all_param_specs(), config)


def validate_config(config: dict[str, Any]) -> None:
    """Validate a flat config dict.

    Raises :class:`ConfigError` listing every invalid field.
    Backward-compatible wrapper around :func:`validate_config_fields`.
    """
    errors = validate_config_fields(config)
    if errors:
        lines = [e.message for e in errors]
        raise ConfigError(
            "Configuration has invalid values:\n  • " + "\n  • ".join(lines)
        )


# ---------------------------------------------------------------------------
# Structured config  (GUI config file format)
# ---------------------------------------------------------------------------

def build_structured_defaults() -> dict[str, Any]:
    """Build a structured config dict with all defaults, organized by section.

    Returns::

        {
            "analysis": { ... },
            "detectors": {
                "<detector_id>": { ... },
                ...
            },
            "processors": {
                "<processor_id>": { ... },
                ...
            },
        }
    """
    from .detectors import default_detectors
    from .processors import default_processors

    structured: dict[str, Any] = {
        "analysis": {p.key: p.default for p in ANALYSIS_PARAMS},
        "detectors": {},
        "processors": {},
    }

    for det in default_detectors():
        params = det.config_params()
        if params:
            structured["detectors"][det.id] = {p.key: p.default for p in params}

    for proc in default_processors():
        params = proc.config_params()
        if params:
            structured["processors"][proc.id] = {p.key: p.default for p in params}

    return structured


def flatten_structured_config(structured: dict[str, Any]) -> dict[str, Any]:
    """Flatten a structured config into a flat key-value dict for the pipeline.

    Merges all sections into a single dict.  The pipeline, detectors, and
    processors read from this flat dict via ``config.get(key, default)``.
    """
    flat: dict[str, Any] = {}
    flat.update(structured.get("analysis", {}))
    for section in structured.get("detectors", {}).values():
        if isinstance(section, dict):
            flat.update(section)
    for section in structured.get("processors", {}).values():
        if isinstance(section, dict):
            flat.update(section)
    return flat


def validate_structured_config(
    structured: dict[str, Any],
) -> list[ConfigFieldError]:
    """Validate a structured config dict section by section.

    Returns a flat list of :class:`ConfigFieldError` (with the ``key``
    prefixed by the section for disambiguation, e.g. ``"detectors.clipping.clip_consecutive"``).
    """
    from .detectors import default_detectors
    from .processors import default_processors

    errors: list[ConfigFieldError] = []

    # Analysis section
    errors.extend(validate_param_values(
        ANALYSIS_PARAMS, structured.get("analysis", {}),
    ))

    # Detector sections
    det_map = {d.id: d for d in default_detectors()}
    det_sections = structured.get("detectors", {})
    for det_id, section in det_sections.items():
        det = det_map.get(det_id)
        if det is None or not isinstance(section, dict):
            continue
        for err in validate_param_values(det.config_params(), section):
            errors.append(ConfigFieldError(
                f"detectors.{det_id}.{err.key}", err.value, err.message,
            ))

    # Processor sections
    proc_map = {p.id: p for p in default_processors()}
    proc_sections = structured.get("processors", {})
    for proc_id, section in proc_sections.items():
        proc = proc_map.get(proc_id)
        if proc is None or not isinstance(section, dict):
            continue
        for err in validate_param_values(proc.config_params(), section):
            errors.append(ConfigFieldError(
                f"processors.{proc_id}.{err.key}", err.value, err.message,
            ))

    return errors


def _type_label(t) -> str:
    """Human-readable label for an expected type or tuple of types."""
    if isinstance(t, tuple):
        return " or ".join(x.__name__ for x in t)
    return t.__name__
