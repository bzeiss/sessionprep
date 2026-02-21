"""Generic PySide6 widget factory for parameter-spec-driven forms.

No dependency on sessionpreplib.  Works with any object that satisfies the
ParamSpec protocol (key, label, type, default, choices, min, max, description).
Portable: copy param_form.py to any PySide6 project.
"""

from __future__ import annotations

import enum
import re
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------------------
# ParamSpec protocol  (duck-typed — sessionpreplib.config.ParamSpec satisfies it)
# ---------------------------------------------------------------------------

@runtime_checkable
class ParamSpec(Protocol):
    key: str
    label: str
    type: type
    default: Any
    choices: list | None
    min: float | None
    max: float | None
    description: str | None
    widget_hint: str | None   # rendering hint consumed by _build_widget; never read by the library


# ---------------------------------------------------------------------------
# PathPickerMode
# ---------------------------------------------------------------------------

class PathPickerMode(enum.Enum):
    """Controls which QFileDialog variant PathPicker opens."""

    FOLDER    = "folder"      # QFileDialog.getExistingDirectory
    OPEN_FILE = "open_file"   # QFileDialog.getOpenFileName
    SAVE_FILE = "save_file"   # QFileDialog.getSaveFileName


# ---------------------------------------------------------------------------
# ARGB / color helpers
# ---------------------------------------------------------------------------

def _argb_to_qcolor(argb: str) -> QColor:
    """Parse a ``#AARRGGBB`` hex string into a QColor."""
    s = argb.lstrip("#")
    if len(s) == 8:
        a, r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), int(s[6:8], 16)
        return QColor(r, g, b, a)
    return QColor(argb)


def _color_swatch_icon(argb: str, size: int = 16) -> QIcon:
    """Create a small square QIcon filled with the given ARGB color."""
    pm = QPixmap(size, size)
    pm.fill(_argb_to_qcolor(argb))
    return QIcon(pm)


# ---------------------------------------------------------------------------
# Widget factory
# ---------------------------------------------------------------------------

def _build_widget(spec: Any, value: Any) -> QWidget:
    """Create an appropriate input widget for a ParamSpec and set its value.

    Resolution order
    ----------------
    1. ``widget_hint`` — explicit override; beats all type-based logic.
    2. ``choices``     — QComboBox when an allowed-values list is provided.
    3. ``type``        — bool → QCheckBox, int → QSpinBox, float → QDoubleSpinBox,
                         list → QLineEdit (csv), str/fallback → QLineEdit.

    Supported ``widget_hint`` values
    ---------------------------------
    ``"path_picker_folder"``  → PathPicker(mode=FOLDER)
    ``"path_picker_file"``    → PathPicker(mode=OPEN_FILE)
    ``"path_picker_save"``    → PathPicker(mode=SAVE_FILE)
    """
    # ── 1. widget_hint dispatch ───────────────────────────────────────────────
    # getattr with default keeps third-party ParamSpec implementations working
    # even when they pre-date this field.
    hint = getattr(spec, "widget_hint", None)
    if hint == "path_picker_folder":
        return PathPicker(spec, mode=PathPickerMode.FOLDER)
    if hint == "path_picker_file":
        return PathPicker(spec, mode=PathPickerMode.OPEN_FILE)
    if hint == "path_picker_save":
        return PathPicker(spec, mode=PathPickerMode.SAVE_FILE)

    # ── 2. choices → QComboBox ────────────────────────────────────────────────
    if spec.choices is not None:
        w = QComboBox()
        for c in spec.choices:
            w.addItem(str(c), c)
        idx = w.findData(value)
        if idx >= 0:
            w.setCurrentIndex(idx)
        w._param_spec = spec
        return w

    if spec.type is bool:
        w = QCheckBox()
        w.setChecked(bool(value))
        w._param_spec = spec
        return w

    if spec.type is int:
        w = QSpinBox()
        w.setMinimum(int(spec.min) if spec.min is not None else -999999)
        w.setMaximum(int(spec.max) if spec.max is not None else 999999)
        w.setValue(int(value) if value is not None else int(spec.default))
        w._param_spec = spec
        return w

    if spec.type in ((int, float), float):
        w = QDoubleSpinBox()
        lo = float(spec.min) if spec.min is not None else -999999.0
        hi = float(spec.max) if spec.max is not None else 999999.0
        decimals = 2
        for ref in (spec.default, value, lo if spec.min is not None else None):
            if ref is not None and ref != 0:
                try:
                    d = Decimal(str(float(ref)))
                    exp = -d.as_tuple().exponent
                    decimals = max(decimals, exp)
                except Exception:
                    pass
        w.setDecimals(min(decimals, 10))
        w.setMinimum(lo)
        w.setMaximum(hi)
        span = hi - lo
        if decimals >= 4:
            w.setSingleStep(10 ** -decimals)
        elif span <= 5:
            w.setSingleStep(0.25)
        elif span <= 20:
            w.setSingleStep(0.5)
        elif span <= 200:
            w.setSingleStep(1.0)
        else:
            w.setSingleStep(5.0)
        w.setValue(float(value) if value is not None else float(spec.default))
        w._param_spec = spec
        return w

    if spec.type is list:
        w = QLineEdit()
        if isinstance(value, list):
            w.setText(", ".join(str(x) for x in value))
        else:
            w.setText(str(value) if value else "")
        w.setPlaceholderText("comma-separated values")
        w._param_spec = spec
        return w

    w = QLineEdit()
    w.setText(str(value) if value is not None else "")
    w._param_spec = spec
    return w


def _set_widget_value(widget: QWidget, value: Any) -> None:
    """Set a widget's value programmatically."""
    if isinstance(widget, PathPicker):   # checked before QLineEdit (PathPicker contains one)
        widget.set_value(str(value) if value is not None else "")
        return
    if isinstance(widget, QComboBox):
        idx = widget.findData(value)
        if idx >= 0:
            widget.setCurrentIndex(idx)
    elif isinstance(widget, QCheckBox):
        widget.setChecked(bool(value))
    elif isinstance(widget, QSpinBox):
        widget.setValue(int(value))
    elif isinstance(widget, QDoubleSpinBox):
        widget.setValue(float(value))
    elif isinstance(widget, QLineEdit):
        if isinstance(value, list):
            widget.setText(", ".join(str(x) for x in value))
        else:
            widget.setText(str(value) if value is not None else "")


def _read_widget(widget: QWidget) -> Any:
    """Read the current value from a widget created by _build_widget."""
    if isinstance(widget, PathPicker):   # checked before QLineEdit (PathPicker contains one)
        return widget.value()
    spec = widget._param_spec
    if isinstance(widget, QComboBox):
        return widget.currentData()
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, QSpinBox):
        return widget.value()
    if isinstance(widget, QDoubleSpinBox):
        return widget.value()
    if isinstance(widget, QLineEdit):
        text = widget.text().strip()
        if spec.type is list:
            if not text:
                return []
            return [s.strip() for s in text.split(",") if s.strip()]
        return text
    return None


# ---------------------------------------------------------------------------
# Tooltip / subtext builders
# ---------------------------------------------------------------------------

def _type_label(t: Any) -> str:
    """Human-readable type name."""
    if isinstance(t, tuple):
        return " or ".join(x.__name__ for x in t)
    return t.__name__


def _build_tooltip(spec: Any) -> str:
    """Build a rich tooltip with key, default, and range info."""
    parts = [f"<b>{spec.label}</b>"]
    if spec.description:
        parts.append(f"<br/>{spec.description}")
    parts.append(f"<br/><br/>Config key: <code>{spec.key}</code>")
    parts.append(f"<br/>Default: <b>{spec.default}</b>")
    if spec.min is not None or spec.max is not None:
        lo = str(spec.min) if spec.min is not None else "\u2212\u221e"
        hi = str(spec.max) if spec.max is not None else "\u221e"
        parts.append(f"<br/>Range: {lo} \u2013 {hi}")
    if spec.choices:
        parts.append(f"<br/>Choices: {', '.join(str(c) for c in spec.choices)}")
    return "".join(parts)


def _build_subtext(spec: Any) -> str:
    """Build visible subtext with description, type, and range info."""
    parts = []
    if spec.description:
        parts.append(spec.description)
    meta = [f"Type: {_type_label(spec.type)}"]
    if spec.min is not None or spec.max is not None:
        lo = str(spec.min) if spec.min is not None else "\u2212\u221e"
        hi = str(spec.max) if spec.max is not None else "\u221e"
        meta.append(f"Range: {lo} \u2013 {hi}")
    if spec.choices:
        meta.append(f"Choices: {', '.join(str(c) for c in spec.choices)}")
    meta.append(f"Default: {spec.default}")
    parts.append("  \u2022  ".join(meta))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# PathPicker — self-contained path / file picker widget
# ---------------------------------------------------------------------------

class PathPicker(QWidget):
    """Generic path/file picker row driven by a ParamSpec.

    Renders a bold label, a ``QLineEdit``, a Browse button, and a reset
    button.  When *show_recursive* is ``True`` and *mode* is ``FOLDER`` an
    "Include subfolders" checkbox is appended below the input row.  A grey
    description subtext mirrors the style of ``_build_param_page`` rows,
    making the widget a visual drop-in for any form built with this module.

    This class has **zero dependency on sessionpreplib** — copy
    ``param_form.py`` to any PySide6 project and use it freely.

    Parameters
    ----------
    spec:
        ``ParamSpec`` providing *key*, *label*, *description*, and *default*.
    mode:
        Which ``QFileDialog`` variant the Browse button opens.
    file_filter:
        Qt filter string (e.g. ``"Audio (*.wav *.flac);;All (*.*)"``).
        Only meaningful for ``OPEN_FILE`` / ``SAVE_FILE`` modes.
    show_recursive:
        When ``True`` and *mode* is ``FOLDER``, adds an
        "Include subfolders" ``QCheckBox`` below the input row.
    """

    #: Emitted whenever the path text changes (browse, clear, or manual edit).
    path_changed = Signal(str)

    def __init__(
        self,
        spec: Any,
        *,
        mode: PathPickerMode = PathPickerMode.FOLDER,
        file_filter: str = "",
        show_recursive: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._spec = spec
        self._mode = mode
        self._file_filter = file_filter
        self._show_recursive = show_recursive
        self._line_edit: QLineEdit
        self._recursive_cb: QCheckBox | None = None
        self._build_ui()

    # ── Public API ────────────────────────────────────────────────────

    def value(self) -> str:
        """Return the current path text (stripped)."""
        return self._line_edit.text().strip()

    def set_value(self, path: str) -> None:
        """Set the path text and emit :attr:`path_changed`."""
        self._line_edit.setText(path)
        self.path_changed.emit(path)

    def recursive(self) -> bool:
        """Return the "Include subfolders" checkbox state.

        Always returns ``False`` when *show_recursive* was not set.
        """
        return self._recursive_cb.isChecked() if self._recursive_cb else False

    def set_recursive(self, on: bool) -> None:
        """Set the "Include subfolders" checkbox state."""
        if self._recursive_cb:
            self._recursive_cb.setChecked(on)

    # ── Private ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        spec = self._spec
        tooltip = _build_tooltip(spec)
        placeholder = "" if spec.default else "(system default)"

        # Input row: label | line-edit | Browse… | ↺
        self._line_edit = QLineEdit()
        self._line_edit.setPlaceholderText(placeholder)
        self._line_edit.setToolTip(tooltip)
        self._line_edit.textEdited.connect(self._on_text_edited)

        browse_btn = QPushButton("Browse\u2026")
        browse_btn.setFixedWidth(80)
        browse_btn.setToolTip("Open file browser")
        browse_btn.clicked.connect(self._browse)

        default_repr = repr(spec.default) if spec.default else "empty"
        reset_btn = QPushButton()
        reset_btn.setIcon(
            self.style().standardIcon(
                self.style().StandardPixmap.SP_BrowserReload))
        reset_btn.setFixedSize(26, 26)
        reset_btn.setToolTip(f"Reset to default ({default_repr})")
        reset_btn.clicked.connect(lambda: self.set_value(spec.default))

        name_lbl = QLabel(f"<b>{spec.label}</b>")
        name_lbl.setToolTip(tooltip)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(8)
        input_row.addWidget(name_lbl, 0)
        input_row.addWidget(self._line_edit, 1)
        input_row.addWidget(browse_btn)
        input_row.addWidget(reset_btn)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        outer.addLayout(input_row)

        # Optional recursive checkbox (FOLDER mode only)
        if self._show_recursive and self._mode is PathPickerMode.FOLDER:
            self._recursive_cb = QCheckBox("Include subfolders")
            outer.addWidget(self._recursive_cb)

        # Description subtext — matches _build_param_page visual style
        sub = QLabel(_build_subtext(spec))
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #888; font-size: 9pt;")
        sub.setToolTip(tooltip)
        outer.addWidget(sub)

    def _browse(self) -> None:
        start = self.value() or ""
        path = ""
        if self._mode is PathPickerMode.FOLDER:
            path = QFileDialog.getExistingDirectory(
                self, f"Select {self._spec.label}", start,
                QFileDialog.Option.ShowDirsOnly,
            )
        elif self._mode is PathPickerMode.OPEN_FILE:
            path, _ = QFileDialog.getOpenFileName(
                self, f"Select {self._spec.label}", start, self._file_filter,
            )
        elif self._mode is PathPickerMode.SAVE_FILE:
            path, _ = QFileDialog.getSaveFileName(
                self, f"Select {self._spec.label}", start, self._file_filter,
            )
        if path:
            self.set_value(path)

    def _on_text_edited(self, text: str) -> None:
        self.path_changed.emit(text.strip())


# ---------------------------------------------------------------------------
# Page builder
# ---------------------------------------------------------------------------

def _build_param_page(
    params: list[Any],
    values: dict[str, Any],
) -> tuple[QWidget, list[tuple[str, QWidget]]]:
    """Build a scrollable form page for a list of ParamSpecs.

    Returns ``(page_widget, [(key, widget), ...])``
    """
    page = QWidget()
    outer = QVBoxLayout(page)
    outer.setContentsMargins(12, 12, 12, 12)
    outer.setSpacing(12)
    widgets: list[tuple[str, QWidget]] = []
    for spec in params:
        val = values.get(spec.key, spec.default)
        w = _build_widget(spec, val)

        # Self-contained widgets (e.g. PathPicker) already render their own
        # label, subtext, and reset button — add them directly.
        if isinstance(w, PathPicker):
            outer.addWidget(w)
            widgets.append((spec.key, w))
            continue

        tooltip = _build_tooltip(spec)
        w.setToolTip(tooltip)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        name_label = QLabel(f"<b>{spec.label}</b>")
        name_label.setToolTip(tooltip)
        row.addWidget(name_label, 1)
        row.addWidget(w, 0)

        reset_btn = QPushButton()
        reset_btn.setIcon(
            page.style().standardIcon(
                page.style().StandardPixmap.SP_BrowserReload))
        reset_btn.setFixedSize(26, 26)
        reset_btn.setToolTip(f"Reset to default ({spec.default})")
        reset_btn.clicked.connect(
            lambda _checked=False, ww=w, dv=spec.default: _set_widget_value(ww, dv))
        row.addWidget(reset_btn)

        param_box = QVBoxLayout()
        param_box.setContentsMargins(0, 0, 0, 0)
        param_box.setSpacing(2)
        param_box.addLayout(row)

        sub_label = QLabel(_build_subtext(spec))
        sub_label.setWordWrap(True)
        sub_label.setStyleSheet("color: #888; font-size: 9pt;")
        sub_label.setToolTip(tooltip)
        param_box.addWidget(sub_label)

        outer.addLayout(param_box)
        widgets.append((spec.key, w))

    outer.addStretch()
    return page, widgets


# ---------------------------------------------------------------------------
# Output folder validation
# ---------------------------------------------------------------------------

_WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)
_ILLEGAL_CHARS = frozenset('<>:"|?*')


def sanitize_output_folder(name: str) -> str | None:
    """Validate and clean an output folder name.

    Returns the stripped name on success, or ``None`` if invalid.
    Rejects empty strings, path traversals, separators, illegal Windows
    characters, control characters, and reserved names.
    """
    name = name.strip()
    if not name:
        return None
    if ".." in name:
        return None
    if "/" in name or "\\" in name:
        return None
    if any(c in _ILLEGAL_CHARS for c in name):
        return None
    if any(ord(c) < 32 for c in name):
        return None
    if name.upper() in _WINDOWS_RESERVED:
        return None
    return name
