"""Preferences subpackage."""

from .dialog import PreferencesDialog, _argb_to_qcolor
from .param_widgets import build_config_pages, load_config_widgets, read_config_widgets

__all__ = [
    "PreferencesDialog", "_argb_to_qcolor",
    "build_config_pages", "load_config_widgets", "read_config_widgets",
]
