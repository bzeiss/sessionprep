"""Persistent GUI configuration (sessionprep.config.json).

On first launch the file is created in the OS-specific user preferences
directory with all built-in defaults.  On subsequent launches it is validated,
loaded, and merged with the current defaults so that newly added keys always
receive a value.

The config file uses a four-section JSON format::

    {
        "app":            { ... },          # application-level settings
        "colors":         [ ... ],          # global color palette
        "config_presets":  { "Default": {...}, ... },
        "group_presets":   { "Default": [...], ... },
    }

Locations:
    Windows : %APPDATA%\\sessionprep\\sessionprep.config.json
    macOS   : ~/Library/Application Support/sessionprep/sessionprep.config.json
    Linux   : $XDG_CONFIG_HOME/sessionprep/sessionprep.config.json
              (defaults to ~/.config/sessionprep/sessionprep.config.json)
"""

from __future__ import annotations

import copy
import json
import logging
import os
import platform
from typing import Any

from sessionpreplib.config import (
    PRESENTATION_PARAMS,
    build_structured_defaults,
    flatten_structured_config,
    validate_structured_config,
)
from .theme import PT_DEFAULT_COLORS

log = logging.getLogger(__name__)

CONFIG_FILENAME = "sessionprep.config.json"

# ---------------------------------------------------------------------------
# Presentation defaults  (config-preset-scoped)
# ---------------------------------------------------------------------------

_PRESENTATION_DEFAULTS: dict[str, Any] = {
    p.key: p.default for p in PRESENTATION_PARAMS
}

# ---------------------------------------------------------------------------
# Application defaults  (global, never per-session)
# ---------------------------------------------------------------------------

_APP_DEFAULTS: dict[str, Any] = {
    "scale_factor": 1.0,
    "report_verbosity": "normal",
    "phase1_output_folder": "sp_01_topology",
    "phase2_output_folder": "sp_02_processed",
    "spectrogram_colormap": "magma",
    "default_project_dir": "",
    "invert_scroll": "default",
    "active_config_preset": "Default",
    "active_group_preset": "Default",
}

# ---------------------------------------------------------------------------
# Default group presets
# ---------------------------------------------------------------------------

_DEFAULT_GROUPS: list[dict[str, Any]] = [
    # Drums
    {"name": "Kick",    "color": "Guardsman Red",        "gain_linked": True,  "daw_target": "Kick", "match_method": "contains", "match_pattern": "kick,kik,kck,bd"},
    {"name": "Snare",   "color": "Dodger Blue Light",    "gain_linked": True,  "daw_target": "Snare", "match_method": "contains", "match_pattern": "snare,snr"},
    {"name": "Toms",    "color": "Tia Maria",            "gain_linked": True,  "daw_target": "Toms", "match_method": "contains", "match_pattern": "tom,floor tom"},
    {"name": "OH",      "color": "Java",                 "gain_linked": True,  "daw_target": "OH", "match_method": "contains", "match_pattern": "oh,overhead,hh,hihat,hi-hat,hi hat,cymbal"},
    {"name": "Room",    "color": "Purple",               "gain_linked": False, "daw_target": "Room", "match_method": "contains", "match_pattern": "room,rm,ambient"},
    {"name": "Perc",    "color": "Corn Harvest",         "gain_linked": False, "daw_target": "Perc", "match_method": "contains", "match_pattern": "perc,shaker,tamb,conga,bongo"},
    {"name": "Loops",   "color": "Cafe Royale Light",              "gain_linked": False, "daw_target": "Loops", "match_method": "contains", "match_pattern": "loop"},
    # Bass
    {"name": "Bass",    "color": "Christi",              "gain_linked": False, "daw_target": "Bass", "match_method": "contains", "match_pattern": "bass,bas"},
    # Guitars
    {"name": "E.Gtr",   "color": "Pizza",               "gain_linked": False, "daw_target": "E.Gtr", "match_method": "contains", "match_pattern": "e.gtr,egtr,elecgtr,elec gtr,electric guitar,dist gtr"},
    {"name": "A.Gtr",   "color": "Lima Dark",            "gain_linked": False, "daw_target": "A.Gtr", "match_method": "contains", "match_pattern": "a.gtr,agtr,acoustic gtr,ac gtr,acoustic guitar,nylon"},
    # Keys & Synths
    {"name": "Keys",    "color": "Malachite",            "gain_linked": False, "daw_target": "Keys", "match_method": "contains", "match_pattern": "keys,piano,pno,organ,rhodes,wurli"},
    {"name": "Synths",  "color": "Electric Violet Light", "gain_linked": False, "daw_target": "Synths", "match_method": "contains", "match_pattern": "synth,moog"},
    {"name": "Leads",   "color": "Electric Violet Dark",       "gain_linked": False, "daw_target": "Leads", "match_method": "contains", "match_pattern": "lead"},
    # Strings & Pads
    {"name": "Strings", "color": "Eastern Blue",         "gain_linked": False, "daw_target": "Strings", "match_method": "contains", "match_pattern": "string,violin,viola,cello,fiddle"},
    {"name": "Pads",    "color": "Flirt",                "gain_linked": False, "daw_target": "Pads", "match_method": "contains", "match_pattern": "pad"},
    {"name": "Brass",   "color": "Milano Red",           "gain_linked": False, "daw_target": "Brass", "match_method": "contains", "match_pattern": "brass,trumpet,trombone,sax,horn"},
    # Vocals
    {"name": "VOX",     "color": "Dodger Blue Dark",     "gain_linked": False, "daw_target": "VOX", "match_method": "contains", "match_pattern": "vox,vocal,lead voc,main voc,voice,leadvox"},
    {"name": "BGs",     "color": "Matisse",              "gain_linked": False, "daw_target": "BGs", "match_method": "contains", "match_pattern": "bg vox,backingvox,bgv,backing,harmony,choir,bg,backingvox"},
    # Effects
    {"name": "FX",      "color": "Lipstick",             "gain_linked": False, "daw_target": "FX", "match_method": "contains", "match_pattern": "fx,sfx,effect"},
]


def _build_default_config_preset() -> dict[str, Any]:
    """Build the "Default" config preset from lib defaults + presentation."""
    preset = build_structured_defaults()
    preset["presentation"] = copy.deepcopy(_PRESENTATION_DEFAULTS)
    return preset


def build_defaults() -> dict[str, Any]:
    """Build the full default config with four top-level sections."""
    return {
        "app": copy.deepcopy(_APP_DEFAULTS),
        "colors": copy.deepcopy(PT_DEFAULT_COLORS),
        "config_presets": {
            "Default": _build_default_config_preset(),
        },
        "group_presets": {
            "Default": copy.deepcopy(_DEFAULT_GROUPS),
        },
    }


def resolve_config_preset(
    config: dict[str, Any],
    preset_name: str,
) -> dict[str, Any]:
    """Resolve a config preset by name into a structured dict.

    Returns a dict with keys ``analysis``, ``detectors``, ``processors``,
    ``daw_processors`` — the same shape that
    :func:`~sessionpreplib.config.flatten_structured_config` expects.
    Falls back to "Default", then built-in defaults.
    """
    presets = config.get("config_presets", {})
    preset = presets.get(preset_name)
    if preset is None:
        preset = presets.get("Default")
    if preset is None:
        preset = _build_default_config_preset()
    return copy.deepcopy(preset)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _config_dir() -> str:
    """Return the OS-specific configuration directory for SessionPrep."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA")
        if not base:
            base = os.path.expanduser("~")
        return os.path.join(base, "sessionprep")
    elif system == "Darwin":
        return os.path.join(
            os.path.expanduser("~"),
            "Library",
            "Application Support",
            "sessionprep",
        )
    else:  # Linux / BSD / …
        base = os.environ.get("XDG_CONFIG_HOME")
        if not base:
            base = os.path.join(os.path.expanduser("~"), ".config")
        return os.path.join(base, "sessionprep")


def config_path() -> str:
    """Return the full path to the GUI config file."""
    return os.path.join(_config_dir(), CONFIG_FILENAME)


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------

def load_config() -> dict[str, Any]:
    """Load the four-section GUI config, creating it with defaults if needed.

    Returns a config dict with keys ``app``, ``colors``,
    ``config_presets``, ``group_presets``.

    If the file is corrupt or fails validation it is backed up as
    ``*.bak`` and recreated from defaults.
    """
    path = config_path()
    defaults = build_defaults()

    if not os.path.isfile(path):
        log.info("Config file not found — creating %s", path)
        save_config(defaults)
        return copy.deepcopy(defaults)

    # -- Read --
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Cannot read config (%s) — recreating from defaults", exc)
        _backup_corrupt(path)
        save_config(defaults)
        return copy.deepcopy(defaults)

    if not isinstance(data, dict):
        log.warning("Config root is %s, expected object — recreating",
                     type(data).__name__)
        _backup_corrupt(path)
        save_config(defaults)
        return copy.deepcopy(defaults)

    # -- Migrate old flat config if needed --
    if "gui" in data or ("analysis" in data and "config_presets" not in data):
        data = _migrate_legacy_config(data)

    # -- Migrate old output_folder → phase2_output_folder --
    app_data = data.get("app", {})
    if "output_folder" in app_data and "phase2_output_folder" not in app_data:
        app_data["phase2_output_folder"] = app_data.pop("output_folder")

    # -- Merge: defaults ← file overrides --
    merged = _merge_structured(defaults, data)

    # -- Validate each config preset --
    valid = True
    for name, preset in merged.get("config_presets", {}).items():
        errors = validate_structured_config(preset)
        if errors:
            msgs = "; ".join(e.message for e in errors)
            log.warning("Config preset %r validation failed (%s) — resetting",
                         name, msgs)
            valid = False
            break

    if not valid:
        _backup_corrupt(path)
        # Keep app settings and colors, reset presets to defaults
        defaults["app"] = copy.deepcopy(merged.get("app", _APP_DEFAULTS))
        defaults["colors"] = copy.deepcopy(
            merged.get("colors", PT_DEFAULT_COLORS))
        save_config(defaults)
        return copy.deepcopy(defaults)

    # Persist if merge introduced new keys (e.g. new defaults)
    if merged != data:
        save_config(merged)

    return merged


def save_config(config: dict[str, Any]) -> str:
    """Save a structured config to the user preferences file.

    Returns the path written.
    """
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
        f.write("\n")

    log.info("Config saved to %s", path)
    return path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _merge_structured(
    defaults: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Deep-merge *overrides* into *defaults* for the four-section config.

    Sections:
    - ``app``: flat dict, known keys only
    - ``colors``: replaced wholesale if present
    - ``config_presets``: per-preset, per-section merge (same as old pipeline sections)
    - ``group_presets``: replaced wholesale per preset
    """
    merged = copy.deepcopy(defaults)

    # -- app: merge known keys only --
    if "app" in overrides and isinstance(overrides["app"], dict):
        app_defaults = merged.get("app", {})
        for k, v in overrides["app"].items():
            if k in app_defaults:
                app_defaults[k] = v

    # -- colors: replace wholesale --
    if "colors" in overrides and isinstance(overrides["colors"], list):
        merged["colors"] = copy.deepcopy(overrides["colors"])

    # -- config_presets: deep merge per preset --
    if "config_presets" in overrides and isinstance(overrides["config_presets"], dict):
        default_presets = merged.get("config_presets", {})
        for preset_name, preset_data in overrides["config_presets"].items():
            if not isinstance(preset_data, dict):
                continue
            if preset_name not in default_presets:
                # User-created preset — take as-is
                default_presets[preset_name] = copy.deepcopy(preset_data)
                continue
            # Merge into default preset structure
            dp = default_presets[preset_name]
            _merge_config_preset(dp, preset_data)

    # -- group_presets: replace wholesale per preset --
    if "group_presets" in overrides and isinstance(overrides["group_presets"], dict):
        merged["group_presets"] = copy.deepcopy(overrides["group_presets"])

    return merged


def _merge_config_preset(
    target: dict[str, Any],
    source: dict[str, Any],
) -> None:
    """Merge *source* config preset values into *target* in place.

    Handles analysis (flat), detectors/processors/daw_processors
    (two-level), and presentation (flat).
    """
    # analysis — flat merge of known keys
    if "analysis" in source and isinstance(source["analysis"], dict):
        t_analysis = target.get("analysis", {})
        for k, v in source["analysis"].items():
            if k in t_analysis:
                t_analysis[k] = v

    # detectors, processors, daw_processors — two-level merge
    for section in ("detectors", "processors", "daw_processors"):
        if section in source and isinstance(source[section], dict):
            t_section = target.get(section, {})
            for comp_id, comp_vals in source[section].items():
                if comp_id in t_section and isinstance(comp_vals, dict):
                    for k, v in comp_vals.items():
                        if k in t_section[comp_id]:
                            t_section[comp_id][k] = v
                elif isinstance(comp_vals, dict):
                    # Unknown component — keep it (user plugin)
                    t_section[comp_id] = copy.deepcopy(comp_vals)

    # presentation — flat merge
    if "presentation" in source and isinstance(source["presentation"], dict):
        t_pres = target.setdefault("presentation", {})
        for k, v in source["presentation"].items():
            t_pres[k] = v


def _migrate_legacy_config(data: dict[str, Any]) -> dict[str, Any]:
    """Convert an old flat-format config to the new four-section structure.

    Old format had top-level ``gui``, ``analysis``, ``detectors``,
    ``processors``, ``daw_processors`` keys.  The ``gui`` section contained
    app settings, colors, group presets, and presentation params mixed together.
    """
    log.info("Migrating legacy config to four-section format")
    gui = data.get("gui", {})

    # -- app settings --
    app: dict[str, Any] = {}
    for key in _APP_DEFAULTS:
        if key in gui:
            app[key] = gui[key]
    # Migrate active_group_preset from gui
    if "active_group_preset" in gui:
        app["active_group_preset"] = gui["active_group_preset"]

    # -- colors --
    colors = gui.get("colors", [])

    # -- group presets --
    group_presets = gui.get("group_presets", {})

    # -- config preset: build "Default" from old top-level sections --
    preset: dict[str, Any] = {}
    if "analysis" in data and isinstance(data["analysis"], dict):
        preset["analysis"] = data["analysis"]
    if "detectors" in data and isinstance(data["detectors"], dict):
        preset["detectors"] = data["detectors"]
    if "processors" in data and isinstance(data["processors"], dict):
        preset["processors"] = data["processors"]
    if "daw_processors" in data and isinstance(data["daw_processors"], dict):
        preset["daw_processors"] = data["daw_processors"]

    # Migrate presentation params from gui into preset
    pres: dict[str, Any] = {}
    for key in _PRESENTATION_DEFAULTS:
        if key in gui:
            pres[key] = gui[key]
    if pres:
        preset["presentation"] = pres

    result: dict[str, Any] = {}
    if app:
        result["app"] = app
    if colors:
        result["colors"] = colors
    result["config_presets"] = {"Default": preset} if preset else {}
    if group_presets:
        result["group_presets"] = group_presets

    return result


def _backup_corrupt(path: str) -> None:
    """Rename a corrupt config file to ``*.bak`` (best-effort)."""
    backup = path + ".bak"
    try:
        if os.path.isfile(backup):
            os.remove(backup)
        os.rename(path, backup)
        log.info("Backed up corrupt config to %s", backup)
    except OSError:
        pass
