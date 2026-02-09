"""Preferences dialog for SessionPrep GUI."""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sessionpreplib.config import ANALYSIS_PARAMS, ParamSpec
from sessionpreplib.detectors import default_detectors
from sessionpreplib.processors import default_processors


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
        ]
        values = self._config.get("gui", {})
        page, widgets = _build_param_page(gui_params, values)
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

    # ── Save ──────────────────────────────────────────────────────────

    def _on_save(self):
        # General
        gui = self._config.setdefault("gui", {})
        for key, widget in self._general_widgets:
            gui[key] = _read_widget(widget)
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

        self._saved = True
        self.accept()
