"""Persistent GUI configuration (sessionprep.config.json).

On first launch the file is created in the OS-specific user preferences
directory with all built-in defaults.  On subsequent launches it is validated,
loaded, and merged with the current defaults so that newly added keys always
receive a value.

The config file uses a **structured** JSON format organised by section::

    {
        "analysis":   { ... },          # shared analysis + global defaults
        "detectors":  { "<id>": {...}, ... },
        "processors": { "<id>": {...}, ... },
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
    build_structured_defaults,
    validate_structured_config,
)
from .theme import PT_DEFAULT_COLORS

log = logging.getLogger(__name__)

CONFIG_FILENAME = "sessionprep.config.json"

_GUI_DEFAULTS: dict[str, Any] = {
    "scale_factor": 1.0,
    "show_clean_detectors": False,
    "report_verbosity": "normal",
    "output_folder": "processed",
    "spectrogram_colormap": "magma",
    "default_project_dir": "",
    "invert_scroll": "default",
    "colors": copy.deepcopy(PT_DEFAULT_COLORS),
    "default_groups": [
        # Drums
        {"name": "Kick",    "color": "Guardsman Red",        "gain_linked": True},
        {"name": "Snare",   "color": "Dodger Blue Light",    "gain_linked": True},
        {"name": "Toms",    "color": "Tia Maria",            "gain_linked": True},
        {"name": "HH",      "color": "La Rioja",             "gain_linked": False},
        {"name": "OH",      "color": "Java",                 "gain_linked": True},
        {"name": "Room",    "color": "Purple",               "gain_linked": False},
        {"name": "Perc",    "color": "Corn Harvest",         "gain_linked": False},
        {"name": "Loops",   "color": "Apricot",              "gain_linked": False},
        # Bass
        {"name": "Bass",    "color": "Christi",              "gain_linked": False},
        # Guitars
        {"name": "E.Gtr",   "color": "Pizza",               "gain_linked": False},
        {"name": "A.Gtr",   "color": "Lima Dark",            "gain_linked": False},
        # Keys & Synths
        {"name": "Keys",    "color": "Malachite",            "gain_linked": False},
        {"name": "Synths",  "color": "Electric Violet Light", "gain_linked": False},
        # Strings & Pads
        {"name": "Strings", "color": "Eastern Blue",         "gain_linked": False},
        {"name": "Pads",    "color": "Flirt",                "gain_linked": False},
        {"name": "Brass",   "color": "Milano Red",           "gain_linked": False},
        # Vocals
        {"name": "VOX",     "color": "Dodger Blue Dark",     "gain_linked": False},
        {"name": "BGs",     "color": "Matisse",              "gain_linked": False},
        # Effects
        {"name": "FX",      "color": "Lipstick",             "gain_linked": False},
    ],
}


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
    """Load the structured GUI config, creating it with defaults if needed.

    Returns a **structured** config dict (analysis / detectors / processors /
    session).  Missing sections or keys are filled from built-in defaults.

    If the file is corrupt or fails validation it is backed up as
    ``*.bak`` and recreated from defaults.
    """
    path = config_path()
    defaults = build_structured_defaults()
    defaults.setdefault("gui", _GUI_DEFAULTS.copy())

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

    # -- Merge: defaults ← file overrides (section by section) --
    merged = _merge_structured(defaults, data)

    # -- Validate --
    errors = validate_structured_config(merged)
    if errors:
        msgs = "; ".join(e.message for e in errors)
        log.warning("Config validation failed (%s) — resetting invalid sections",
                     msgs)
        _backup_corrupt(path)
        # Reset analysis/detectors/processors to defaults but keep gui
        gui_section = copy.deepcopy(merged.get("gui", _GUI_DEFAULTS.copy()))
        defaults["gui"] = gui_section
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
    """Deep-merge *overrides* into *defaults* (two levels deep).

    Only known top-level sections (``analysis``, ``detectors``,
    ``processors``) are merged.  Within ``detectors`` and
    ``processors`` only known sub-section IDs are merged.
    """
    merged = copy.deepcopy(defaults)

    if "analysis" in overrides and isinstance(overrides["analysis"], dict):
        default_section = merged.get("analysis", {})
        for k, v in overrides["analysis"].items():
            if k in default_section:
                default_section[k] = v

    for section in ("detectors", "processors", "daw_processors"):
        if section in overrides and isinstance(overrides[section], dict):
            default_section = merged.get(section, {})
            for comp_id, comp_vals in overrides[section].items():
                if comp_id in default_section and isinstance(comp_vals, dict):
                    for k, v in comp_vals.items():
                        if k in default_section[comp_id]:
                            default_section[comp_id][k] = v

    # GUI section — merge overrides into defaults
    gui_defaults = merged.get("gui", {})
    if "gui" in overrides and isinstance(overrides["gui"], dict):
        gui_defaults.update(overrides["gui"])
    merged["gui"] = gui_defaults

    return merged


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
