"""Backward-compatibility re-export shim.

All symbols have moved to param_form.py (generic widget factory) or
config_pages.py (SessionPrep-specific builders).  Import from those
modules directly in new code.
"""

# ruff: noqa: F401
from .param_form import (
    _argb_to_qcolor,
    _build_param_page,
    _build_subtext,
    _build_tooltip,
    _build_widget,
    _color_swatch_icon,
    _read_widget,
    _set_widget_value,
    _type_label,
    _ILLEGAL_CHARS,
    _WINDOWS_RESERVED,
    sanitize_output_folder,
)
from .config_pages import (
    build_config_pages,
    ColorProvider,
    DawProjectTemplatesWidget,
    GroupsTableWidget,
    load_config_widgets,
    read_config_widgets,
)
