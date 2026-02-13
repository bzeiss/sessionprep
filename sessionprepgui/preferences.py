"""Preferences dialog for SessionPrep GUI."""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sessionpreplib.config import ANALYSIS_PARAMS, ParamSpec
from sessionpreplib.detectors import default_detectors
from sessionpreplib.processors import default_processors
from .theme import PT_DEFAULT_COLORS


# ---------------------------------------------------------------------------
# ARGB color helper
# ---------------------------------------------------------------------------

def _argb_to_qcolor(argb: str) -> QColor:
    """Parse a ``#AARRGGBB`` hex string into a QColor."""
    s = argb.lstrip("#")
    if len(s) == 8:
        a, r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), int(s[6:8], 16)
        return QColor(r, g, b, a)
    return QColor(argb)


# ---------------------------------------------------------------------------
# Widget builders for ParamSpec
# ---------------------------------------------------------------------------

def _build_widget(spec: ParamSpec, value: Any) -> QWidget:
    """Create an appropriate input widget for a ParamSpec and set its value."""
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
        # Adaptive decimals: enough to represent default and current value
        decimals = 2
        for ref in (spec.default, value, lo if spec.min is not None else None):
            if ref is not None and ref != 0:
                try:
                    d = Decimal(str(float(ref)))
                    # Number of decimal places (negative exponent)
                    exp = -d.as_tuple().exponent
                    decimals = max(decimals, exp)
                except Exception:
                    pass
        w.setDecimals(min(decimals, 10))
        w.setMinimum(lo)
        w.setMaximum(hi)
        # Smart step size based on range and precision
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

    # Fallback: string
    w = QLineEdit()
    w.setText(str(value) if value is not None else "")
    w._param_spec = spec
    return w


def _set_widget_value(widget: QWidget, value: Any):
    """Set a widget's value programmatically."""
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


def _build_tooltip(spec: ParamSpec) -> str:
    """Build a rich tooltip with key, default, and range info."""
    parts = []
    parts.append(f"<b>{spec.label}</b>")
    if spec.description:
        parts.append(f"<br/>{spec.description}")
    parts.append(f"<br/><br/>Config key: <code>{spec.key}</code>")
    parts.append(f"<br/>Default: <b>{spec.default}</b>")
    if spec.min is not None or spec.max is not None:
        lo = str(spec.min) if spec.min is not None else "−∞"
        hi = str(spec.max) if spec.max is not None else "∞"
        parts.append(f"<br/>Range: {lo} \u2013 {hi}")
    if spec.choices:
        parts.append(f"<br/>Choices: {', '.join(str(c) for c in spec.choices)}")
    return "".join(parts)


def _read_widget(widget: QWidget) -> Any:
    """Read the current value from a widget created by _build_widget."""
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
# Page builders
# ---------------------------------------------------------------------------

def _type_label(t) -> str:
    """Human-readable type name."""
    if isinstance(t, tuple):
        return " or ".join(x.__name__ for x in t)
    return t.__name__


def _build_subtext(spec: ParamSpec) -> str:
    """Build visible subtext with description, type, and range info."""
    parts = []
    if spec.description:
        parts.append(spec.description)
    meta = []
    meta.append(f"Type: {_type_label(spec.type)}")
    if spec.min is not None or spec.max is not None:
        lo = str(spec.min) if spec.min is not None else "\u2212\u221e"
        hi = str(spec.max) if spec.max is not None else "\u221e"
        meta.append(f"Range: {lo} \u2013 {hi}")
    if spec.choices:
        meta.append(f"Choices: {', '.join(str(c) for c in spec.choices)}")
    meta.append(f"Default: {spec.default}")
    if parts:
        parts.append("  \u2022  ".join(meta))
    else:
        parts.append("  \u2022  ".join(meta))
    return "\n".join(parts)


def _build_param_page(params: list[ParamSpec], values: dict[str, Any]) -> tuple[QWidget, list[tuple[str, QWidget]]]:
    """Build a form page for a list of ParamSpecs. Returns (page_widget, [(key, widget)])."""
    page = QWidget()
    outer = QVBoxLayout(page)
    outer.setContentsMargins(12, 12, 12, 12)
    outer.setSpacing(12)
    widgets = []
    for spec in params:
        val = values.get(spec.key, spec.default)
        w = _build_widget(spec, val)
        tooltip = _build_tooltip(spec)
        w.setToolTip(tooltip)

        # Row 1: label + widget + reset button
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        name_label = QLabel(f"<b>{spec.label}</b>")
        name_label.setToolTip(tooltip)
        row.addWidget(name_label, 1)
        row.addWidget(w, 0)
        reset_btn = QPushButton()
        reset_btn.setIcon(page.style().standardIcon(page.style().StandardPixmap.SP_BrowserReload))
        reset_btn.setFixedSize(26, 26)
        reset_btn.setToolTip(f"Reset to default ({spec.default})")
        _default = spec.default
        _widget = w
        reset_btn.clicked.connect(lambda checked=False, ww=_widget, dv=_default: _set_widget_value(ww, dv))
        row.addWidget(reset_btn)

        # Row 2: subtext (description + type + range)
        param_box = QVBoxLayout()
        param_box.setContentsMargins(0, 0, 0, 0)
        param_box.setSpacing(2)
        param_box.addLayout(row)

        subtext = _build_subtext(spec)
        sub_label = QLabel(subtext)
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

    Returns the stripped name on success, or ``None`` if the name is
    invalid.  Rejects empty strings, path traversals, path separators,
    illegal Windows characters, control characters, and reserved names.
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


# ---------------------------------------------------------------------------
# Preferences Dialog
# ---------------------------------------------------------------------------

class PreferencesDialog(QDialog):
    """Hierarchical preferences dialog with tree navigation."""

    def __init__(self, config: dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.resize(750, 500)
        self._config = copy.deepcopy(config)
        self._widgets: dict[str, list[tuple[str, QWidget]]] = {}
        self._general_widgets: list[tuple[str, QWidget]] = []
        self._saved = False

        self._init_ui()

    @property
    def saved(self) -> bool:
        return self._saved

    def result_config(self) -> dict[str, Any]:
        """Return the edited config (only valid after save)."""
        return self._config

    # ── UI setup ──────────────────────────────────────────────────────

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Horizontal)

        # -- Tree --
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumWidth(180)
        self._tree.setMaximumWidth(250)
        self._tree.currentItemChanged.connect(self._on_tree_selection)
        splitter.addWidget(self._tree)

        # -- Stacked pages --
        self._stack = QStackedWidget()
        splitter.addWidget(self._stack)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        root_layout.addWidget(splitter, 1)

        # -- Build pages --
        self._page_index: dict[int, int] = {}  # tree item id -> stack index
        self._build_general_page()
        self._build_analysis_page()
        self._build_detector_pages()
        self._build_processor_pages()
        self._build_colors_page()
        self._build_groups_page()

        # Select first item
        self._tree.expandAll()
        first = self._tree.topLevelItem(0)
        if first:
            self._tree.setCurrentItem(first)

        # -- Buttons --
        btn_box = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Save
        )
        btn_box.button(QDialogButtonBox.Save).setDefault(True)
        btn_box.accepted.connect(self._on_save)
        btn_box.rejected.connect(self.reject)
        root_layout.addWidget(btn_box)

    def _add_page(self, tree_item: QTreeWidgetItem, page: QWidget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(page)
        idx = self._stack.addWidget(scroll)
        self._page_index[id(tree_item)] = idx

    def _on_tree_selection(self, current, _previous):
        if current is None:
            return
        idx = self._page_index.get(id(current))
        if idx is not None:
            self._stack.setCurrentIndex(idx)

    # ── General page ──────────────────────────────────────────────────

    def _build_general_page(self):
        item = QTreeWidgetItem(self._tree, ["General"])
        item.setFont(0, QFont("", -1, QFont.Bold))

        gui_params = [
            ParamSpec(
                key="scale_factor", type=(int, float), default=1.0,
                min=0.5, max=4.0,
                label="HiDPI scale factor",
                description="Scale factor for the application UI. Requires a restart to take effect.",
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
        values = self._config.get("gui", {})
        page, widgets = _build_param_page(gui_params, values)

        # --- Default project directory (custom row with browse button) ---
        dir_spec = ParamSpec(
            key="default_project_dir", type=str, default="",
            label="Default project directory",
            description=(
                "When set, the Open Folder dialog starts in this directory. "
                "Leave empty to use the system default."
            ),
        )
        cur_dir = values.get("default_project_dir", "")
        dir_edit = QLineEdit()
        dir_edit.setText(str(cur_dir) if cur_dir else "")
        dir_edit.setPlaceholderText("(system default)")
        dir_edit._param_spec = dir_spec
        dir_edit.setToolTip(_build_tooltip(dir_spec))

        browse_btn = QPushButton("Browse\u2026")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(
            lambda: self._browse_project_dir(dir_edit))

        clear_btn = QPushButton()
        clear_btn.setIcon(page.style().standardIcon(
            QStyle.StandardPixmap.SP_DialogCloseButton))
        clear_btn.setFixedSize(26, 26)
        clear_btn.setToolTip("Clear (use system default)")
        clear_btn.clicked.connect(lambda: dir_edit.setText(""))

        dir_row = QHBoxLayout()
        dir_row.setContentsMargins(0, 0, 0, 0)
        dir_row.setSpacing(8)
        dir_name_label = QLabel(f"<b>{dir_spec.label}</b>")
        dir_name_label.setToolTip(_build_tooltip(dir_spec))
        dir_row.addWidget(dir_name_label, 0)
        dir_row.addWidget(dir_edit, 1)
        dir_row.addWidget(browse_btn, 0)
        dir_row.addWidget(clear_btn, 0)

        dir_box = QVBoxLayout()
        dir_box.setContentsMargins(0, 0, 0, 0)
        dir_box.setSpacing(2)
        dir_box.addLayout(dir_row)
        dir_sub = QLabel(_build_subtext(dir_spec))
        dir_sub.setWordWrap(True)
        dir_sub.setStyleSheet("color: #888; font-size: 9pt;")
        dir_sub.setToolTip(_build_tooltip(dir_spec))
        dir_box.addWidget(dir_sub)

        # Insert before the stretch at the end of the page layout
        outer = page.layout()
        outer.insertLayout(outer.count() - 1, dir_box)

        widgets.append(("default_project_dir", dir_edit))
        self._general_widgets = widgets
        self._add_page(item, page)

    # ── Analysis page ─────────────────────────────────────────────────

    def _build_analysis_page(self):
        item = QTreeWidgetItem(self._tree, ["Analysis"])
        item.setFont(0, QFont("", -1, QFont.Bold))

        values = self._config.get("analysis", {})
        page, widgets = _build_param_page(ANALYSIS_PARAMS, values)
        self._widgets["analysis"] = widgets
        self._add_page(item, page)

    # ── Detector pages ────────────────────────────────────────────────

    def _build_detector_pages(self):
        parent_item = QTreeWidgetItem(self._tree, ["Detectors"])
        parent_item.setFont(0, QFont("", -1, QFont.Bold))

        # Parent page: general detector display settings
        det_gui_params = [
            ParamSpec(
                key="show_clean_detectors", type=bool, default=False,
                label="Show clean detector results",
                description=(
                    "When enabled, detectors that found no issues (OK) are "
                    "shown in the file detail view and summary. Disable to "
                    "reduce clutter and focus on problems, warnings, and "
                    "informational findings only."
                ),
            ),
        ]
        gui_values = self._config.get("gui", {})
        parent_page, det_gui_widgets = _build_param_page(det_gui_params, gui_values)
        self._widgets["_det_gui"] = det_gui_widgets
        self._add_page(parent_item, parent_page)

        det_sections = self._config.get("detectors", {})
        for det in default_detectors():
            params = det.config_params()
            if not params:
                continue
            child = QTreeWidgetItem(parent_item, [det.name])
            values = det_sections.get(det.id, {})
            page, widgets = _build_param_page(params, values)
            self._widgets[f"detectors.{det.id}"] = widgets
            self._add_page(child, page)

    # ── Processor pages ───────────────────────────────────────────────

    def _build_processor_pages(self):
        parent_item = QTreeWidgetItem(self._tree, ["Processors"])
        parent_item.setFont(0, QFont("", -1, QFont.Bold))

        parent_page = QWidget()
        pl = QVBoxLayout(parent_page)
        pl.setContentsMargins(12, 12, 12, 12)
        pl.addWidget(QLabel("Select a processor from the tree to configure it."))
        pl.addStretch()
        self._add_page(parent_item, parent_page)

        proc_sections = self._config.get("processors", {})
        for proc in default_processors():
            params = proc.config_params()
            if not params:
                continue
            child = QTreeWidgetItem(parent_item, [proc.name])
            values = proc_sections.get(proc.id, {})
            page, widgets = _build_param_page(params, values)
            self._widgets[f"processors.{proc.id}"] = widgets
            self._add_page(child, page)

    # ── Colors page ────────────────────────────────────────────────────

    def _build_colors_page(self):
        item = QTreeWidgetItem(self._tree, ["Colors"])
        item.setFont(0, QFont("", -1, QFont.Bold))

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        desc = QLabel(
            "Color palette used for track groups. "
            "Double-click a swatch to edit."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(desc)

        self._colors_table = QTableWidget()
        self._colors_table.setColumnCount(3)
        self._colors_table.setHorizontalHeaderLabels(["#", "Name", "Color"])
        self._colors_table.verticalHeader().setVisible(False)
        self._colors_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._colors_table.setSelectionMode(QTableWidget.SingleSelection)
        ch = self._colors_table.horizontalHeader()
        ch.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        ch.setSectionResizeMode(0, QHeaderView.Fixed)
        ch.resizeSection(0, 36)
        ch.setSectionResizeMode(1, QHeaderView.Stretch)
        ch.setSectionResizeMode(2, QHeaderView.Fixed)
        ch.resizeSection(2, 60)

        self._colors_table.cellDoubleClicked.connect(
            self._on_color_swatch_dbl_click)

        # Populate from config
        colors = self._config.get("gui", {}).get("colors", [])
        if not colors:
            colors = copy.deepcopy(PT_DEFAULT_COLORS)
        self._colors_table.setRowCount(len(colors))
        for row, entry in enumerate(colors):
            self._set_color_row(row, entry.get("name", ""), entry.get("argb", "#ff888888"))

        layout.addWidget(self._colors_table, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._on_color_add)
        btn_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._on_color_remove)
        btn_row.addWidget(remove_btn)

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self._on_colors_reset)
        btn_row.addWidget(reset_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._add_page(item, page)

    def _set_color_row(self, row: int, name: str, argb: str):
        """Populate a single row in the colors table."""
        idx_item = QTableWidgetItem(str(row + 1))
        idx_item.setFlags(idx_item.flags() & ~Qt.ItemIsEditable)
        idx_item.setForeground(QColor("#888888"))
        self._colors_table.setItem(row, 0, idx_item)

        name_item = QTableWidgetItem(name)
        self._colors_table.setItem(row, 1, name_item)

        swatch_item = QTableWidgetItem()
        swatch_item.setFlags(swatch_item.flags() & ~Qt.ItemIsEditable)
        swatch_item.setBackground(_argb_to_qcolor(argb))
        swatch_item.setData(Qt.UserRole, argb)
        swatch_item.setToolTip(argb)
        self._colors_table.setItem(row, 2, swatch_item)

    def _on_color_swatch_dbl_click(self, row: int, col: int):
        """Open QColorDialog when the swatch column is double-clicked."""
        if col != 2:
            return
        item = self._colors_table.item(row, 2)
        if not item:
            return
        current = _argb_to_qcolor(item.data(Qt.UserRole) or "#ff888888")
        color = QColorDialog.getColor(
            current, self, "Select Color",
            QColorDialog.ShowAlphaChannel)
        if color.isValid():
            argb = "#{:02x}{:02x}{:02x}{:02x}".format(
                color.alpha(), color.red(), color.green(), color.blue())
            item.setBackground(color)
            item.setData(Qt.UserRole, argb)
            item.setToolTip(argb)

    def _on_color_add(self):
        row = self._colors_table.rowCount()
        self._colors_table.insertRow(row)
        self._set_color_row(row, "New Color", "#ff888888")
        self._colors_table.scrollToBottom()
        self._colors_table.editItem(self._colors_table.item(row, 1))

    def _on_color_remove(self):
        row = self._colors_table.currentRow()
        if row >= 0:
            self._colors_table.removeRow(row)

    def _on_colors_reset(self):
        self._colors_table.setRowCount(0)
        self._colors_table.setRowCount(len(PT_DEFAULT_COLORS))
        for row, entry in enumerate(PT_DEFAULT_COLORS):
            self._set_color_row(row, entry["name"], entry["argb"])

    def _read_colors(self) -> list[dict[str, str]]:
        """Read the colors table into a list of {name, argb} dicts."""
        colors = []
        for row in range(self._colors_table.rowCount()):
            name_item = self._colors_table.item(row, 1)
            swatch_item = self._colors_table.item(row, 2)
            if not name_item or not swatch_item:
                continue
            name = name_item.text().strip()
            argb = swatch_item.data(Qt.UserRole) or "#ff888888"
            if name:
                colors.append({"name": name, "argb": argb})
        return colors

    # ── Groups page ──────────────────────────────────────────────────

    def _build_groups_page(self):
        item = QTreeWidgetItem(self._tree, ["Groups"])
        item.setFont(0, QFont("", -1, QFont.Bold))

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        desc = QLabel(
            "Default track groups used when analyzing a session. "
            "Groups reference colors from the Colors page."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(desc)

        self._groups_table = QTableWidget()
        self._groups_table.setColumnCount(3)
        self._groups_table.setHorizontalHeaderLabels(
            ["Name", "Color", "Gain-Linked"])
        self._groups_table.verticalHeader().setVisible(False)
        self._groups_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._groups_table.setSelectionMode(QTableWidget.SingleSelection)
        gh = self._groups_table.horizontalHeader()
        gh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        gh.setSectionResizeMode(0, QHeaderView.Stretch)
        gh.setSectionResizeMode(1, QHeaderView.Fixed)
        gh.resizeSection(1, 160)
        gh.setSectionResizeMode(2, QHeaderView.Fixed)
        gh.resizeSection(2, 80)

        # Populate from config
        groups = self._config.get("gui", {}).get("default_groups", [])
        self._groups_table.setRowCount(len(groups))
        for row, entry in enumerate(groups):
            self._set_group_row(
                row,
                entry.get("name", ""),
                entry.get("color", ""),
                entry.get("gain_linked", False),
            )

        layout.addWidget(self._groups_table, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._on_group_add)
        btn_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._on_group_remove)
        btn_row.addWidget(remove_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._add_page(item, page)

    def _color_names(self) -> list[str]:
        """Return the list of color names currently in the colors table."""
        names = []
        for row in range(self._colors_table.rowCount()):
            item = self._colors_table.item(row, 1)
            if item:
                name = item.text().strip()
                if name:
                    names.append(name)
        return names

    @staticmethod
    def _color_swatch_icon(argb: str, size: int = 16) -> QIcon:
        """Create a small square QIcon filled with the given ARGB color."""
        pm = QPixmap(size, size)
        pm.fill(_argb_to_qcolor(argb))
        return QIcon(pm)

    def _color_argb_for_name(self, name: str) -> str | None:
        """Look up an ARGB value by color name from the colors table."""
        for row in range(self._colors_table.rowCount()):
            item = self._colors_table.item(row, 1)
            if item and item.text().strip() == name:
                swatch = self._colors_table.item(row, 2)
                if swatch:
                    return swatch.data(Qt.UserRole)
        return None

    def _set_group_row(self, row: int, name: str, color: str,
                       gain_linked: bool):
        """Populate a single row in the groups table."""
        name_item = QTableWidgetItem(name)
        self._groups_table.setItem(row, 0, name_item)

        # Color dropdown
        color_combo = QComboBox()
        color_combo.setIconSize(QSize(16, 16))
        color_names = self._color_names()
        for cn in color_names:
            argb = self._color_argb_for_name(cn)
            icon = self._color_swatch_icon(argb) if argb else QIcon()
            color_combo.addItem(icon, cn)
        # Select the matching color
        ci = color_combo.findText(color)
        if ci >= 0:
            color_combo.setCurrentIndex(ci)
        self._groups_table.setCellWidget(row, 1, color_combo)

        # Gain-linked checkbox
        chk = QCheckBox()
        chk.setChecked(gain_linked)
        chk_container = QWidget()
        chk_layout = QHBoxLayout(chk_container)
        chk_layout.setContentsMargins(0, 0, 0, 0)
        chk_layout.setAlignment(Qt.AlignCenter)
        chk_layout.addWidget(chk)
        self._groups_table.setCellWidget(row, 2, chk_container)

    def _on_group_add(self):
        row = self._groups_table.rowCount()
        self._groups_table.insertRow(row)
        color_names = self._color_names()
        default_color = color_names[0] if color_names else ""
        self._set_group_row(row, "New Group", default_color, False)
        self._groups_table.scrollToBottom()
        self._groups_table.editItem(self._groups_table.item(row, 0))

    def _on_group_remove(self):
        row = self._groups_table.currentRow()
        if row >= 0:
            self._groups_table.removeRow(row)

    def _read_groups(self) -> list[dict[str, Any]]:
        """Read the groups table into a list of dicts."""
        groups: list[dict[str, Any]] = []
        for row in range(self._groups_table.rowCount()):
            name_item = self._groups_table.item(row, 0)
            if not name_item:
                continue
            name = name_item.text().strip()
            if not name:
                continue
            color_combo = self._groups_table.cellWidget(row, 1)
            color = color_combo.currentText() if color_combo else ""
            chk_container = self._groups_table.cellWidget(row, 2)
            gain_linked = False
            if chk_container:
                chk = chk_container.findChild(QCheckBox)
                if chk:
                    gain_linked = chk.isChecked()
            groups.append({
                "name": name,
                "color": color,
                "gain_linked": gain_linked,
            })
        return groups

    # ── Helpers ────────────────────────────────────────────────────────

    def _browse_project_dir(self, line_edit: QLineEdit):
        """Open a directory picker and set the result into *line_edit*."""
        start = line_edit.text().strip() or ""
        path = QFileDialog.getExistingDirectory(
            self, "Select Default Project Directory", start,
            QFileDialog.ShowDirsOnly,
        )
        if path:
            line_edit.setText(path)

    # ── Save ──────────────────────────────────────────────────────────

    def _on_save(self):
        # General
        gui = self._config.setdefault("gui", {})
        for key, widget in self._general_widgets:
            gui[key] = _read_widget(widget)

        # Validate output folder name
        raw_folder = gui.get("output_folder", "")
        clean_folder = sanitize_output_folder(str(raw_folder))
        if clean_folder is None:
            QMessageBox.warning(
                self, "Invalid output folder",
                "The output folder name is invalid.\n\n"
                "It must be a simple folder name without path separators, "
                "special characters, or reserved names.",
            )
            return
        gui["output_folder"] = clean_folder

        # Detector display settings (stored in gui section)
        for key, widget in self._widgets.get("_det_gui", []):
            gui[key] = _read_widget(widget)

        # Analysis
        analysis = self._config.setdefault("analysis", {})
        for key, widget in self._widgets.get("analysis", []):
            analysis[key] = _read_widget(widget)

        # Detectors
        detectors = self._config.setdefault("detectors", {})
        for det in default_detectors():
            wkey = f"detectors.{det.id}"
            if wkey not in self._widgets:
                continue
            section = detectors.setdefault(det.id, {})
            for key, widget in self._widgets[wkey]:
                section[key] = _read_widget(widget)

        # Processors
        processors = self._config.setdefault("processors", {})
        for proc in default_processors():
            wkey = f"processors.{proc.id}"
            if wkey not in self._widgets:
                continue
            section = processors.setdefault(proc.id, {})
            for key, widget in self._widgets[wkey]:
                section[key] = _read_widget(widget)

        # Colors
        gui["colors"] = self._read_colors()

        # Groups
        gui["default_groups"] = self._read_groups()

        self._saved = True
        self.accept()
