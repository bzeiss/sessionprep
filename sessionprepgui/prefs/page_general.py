"""GeneralPage — application-level settings (app + waveform prefs)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QVBoxLayout,
    QWidget,
)

from sessionpreplib.config import ParamSpec

from .param_form import (
    _build_param_page,
    _read_widget,
    _set_widget_value,
    sanitize_output_folder,
)

# ---------------------------------------------------------------------------
# Page-level param specs (data, not UI)
# ---------------------------------------------------------------------------

_APP_PARAMS = [
    ParamSpec(
        key="default_project_dir", type=str, default="",
        label="Default project directory",
        description=(
            "When set, the Open Folder dialog starts in this directory. "
            "Leave empty to use the system default."
        ),
        widget_hint="path_picker_folder",
    ),
    ParamSpec(
        key="scale_factor", type=(int, float), default=1.0,
        min=0.5, max=4.0,
        label="HiDPI scale factor",
        description=(
            "Scale factor for the application UI. "
            "Requires a restart to take effect."
        ),
    ),
    ParamSpec(
        key="report_verbosity", type=str, default="normal",
        choices=["normal", "verbose"],
        label="Report verbosity",
        description=(
            "Controls the level of detail shown in track reports. "
            "Verbose mode includes additional analytical data such as "
            "classification metrics."
        ),
    ),
    ParamSpec(
        key="output_folder", type=str, default="processed",
        label="Output folder name",
        description=(
            "Name of the subfolder (relative to the project directory) "
            "where processed audio files are written. "
            "Must be a simple folder name without path separators."
        ),
    ),
    ParamSpec(
        key="spectrogram_colormap", type=str, default="magma",
        choices=["magma", "viridis", "grayscale"],
        label="Spectrogram color theme",
        description="Color palette used for the spectrogram display.",
    ),
    ParamSpec(
        key="invert_scroll", type=str, default="default",
        choices=["default", "horizontal", "vertical", "both"],
        label="Invert mouse-wheel scrolling",
        description=(
            "Reverses the scroll direction in the waveform/spectrogram view. "
            "'horizontal' inverts Shift+wheel (timeline panning), "
            "'vertical' inverts Shift+Alt+wheel (frequency panning), "
            "'both' inverts both axes."
        ),
    ),
]


class GeneralPage(QWidget):
    """App-level preference form.

    Implements the standard page interface:
        load(config)   — populate from config["app"]
        commit(config) — write back to config["app"]
        validate()     — returns error string or None
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._widgets: list[tuple[str, QWidget]] = []
        self._init_ui()

    # ── Page interface ────────────────────────────────────────────────

    def load(self, config: dict) -> None:
        values = config.get("app", {})
        for key, widget in self._widgets:
            if key in values:
                _set_widget_value(widget, values[key])

    def commit(self, config: dict) -> None:
        app = config.setdefault("app", {})
        for key, widget in self._widgets:
            app[key] = _read_widget(widget)

    def validate(self) -> str | None:
        """Return an error message if output_folder is invalid, else None."""
        for key, widget in self._widgets:
            if key == "output_folder":
                raw = _read_widget(widget)
                if sanitize_output_folder(str(raw)) is None:
                    return (
                        "The output folder name is invalid.\n\n"
                        "It must be a simple folder name without path "
                        "separators, special characters, or reserved names."
                    )
        return None

    # ── UI setup ─────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        page, self._widgets = _build_param_page(_APP_PARAMS, {})
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(page)
