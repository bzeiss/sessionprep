"""Preferences subpackage."""

from .dialog import PreferencesDialog
from .param_form import PathPicker, PathPickerMode, _argb_to_qcolor
from .config_pages import build_config_pages, load_config_widgets, read_config_widgets

__all__ = [
    "PreferencesDialog",
    "PathPicker", "PathPickerMode",
    "_argb_to_qcolor",
    "build_config_pages", "load_config_widgets", "read_config_widgets",
]
