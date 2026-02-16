"""Reusable widget builders and GroupsTableWidget for SessionPrep GUI.

Extracted from preferences.py to be shared between the Preferences dialog
and the Session Settings tab.
"""

from __future__ import annotations

import copy
import re
from decimal import Decimal
from typing import Any, Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sessionpreplib.config import ParamSpec


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


def _color_swatch_icon(argb: str, size: int = 16) -> QIcon:
    """Create a small square QIcon filled with the given ARGB color."""
    pm = QPixmap(size, size)
    pm.fill(_argb_to_qcolor(argb))
    return QIcon(pm)


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
# GroupsTableWidget — reusable group table with Add/Remove/Sort
# ---------------------------------------------------------------------------

# Type alias for the color provider callable.
# Returns (list_of_color_names, lookup_argb_by_name).
ColorProvider = Callable[[], tuple[list[str], Callable[[str], str | None]]]


class GroupsTableWidget(QWidget):
    """Reusable widget for editing a list of track groups.

    The *color_provider* callable must return
    ``(color_names, argb_lookup)`` where *color_names* is a list of
    available color names and *argb_lookup* maps a name to an ARGB hex
    string (or ``None``).
    """

    groups_changed = Signal()

    def __init__(self, color_provider: ColorProvider, parent=None):
        super().__init__(parent)
        self._color_provider = color_provider
        self._init_ui()

    # ── UI setup ──────────────────────────────────────────────────────

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["Name", "Color", "Gain-Linked", "DAW Target",
             "Match", "Match Pattern"])
        vh = self._table.verticalHeader()
        vh.setSectionsMovable(True)
        vh.sectionMoved.connect(self._on_row_moved)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        gh = self._table.horizontalHeader()
        gh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        gh.setSectionResizeMode(0, QHeaderView.Stretch)
        gh.setSectionResizeMode(1, QHeaderView.Fixed)
        gh.resizeSection(1, 160)
        gh.setSectionResizeMode(2, QHeaderView.Fixed)
        gh.resizeSection(2, 80)
        gh.setSectionResizeMode(3, QHeaderView.Interactive)
        gh.resizeSection(3, 140)
        gh.setSectionResizeMode(4, QHeaderView.Fixed)
        gh.resizeSection(4, 90)
        gh.setSectionResizeMode(5, QHeaderView.Interactive)
        gh.resizeSection(5, 200)

        self._table.cellChanged.connect(self._on_cell_changed)

        layout.addWidget(self._table, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._on_remove)
        btn_row.addWidget(remove_btn)

        btn_row.addStretch()

        az_btn = QPushButton("Sort A\u2192Z")
        az_btn.clicked.connect(self._on_sort_az)
        btn_row.addWidget(az_btn)

        layout.addLayout(btn_row)

    # ── Public API ────────────────────────────────────────────────────

    def set_groups(self, groups: list[dict]):
        """Populate the table from a list of group dicts."""
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._table.setRowCount(len(groups))
        for row, g in enumerate(groups):
            self._set_row(
                row, g.get("name", ""), g.get("color", ""),
                g.get("gain_linked", False), g.get("daw_target", ""),
                g.get("match_method", "contains"),
                g.get("match_pattern", ""))
        self._table.blockSignals(False)

    def get_groups(self) -> list[dict]:
        """Read the table back into a list of group dicts."""
        return self._read_groups()

    @property
    def table(self) -> QTableWidget:
        """Direct access to the underlying QTableWidget (for selection, etc.)."""
        return self._table

    # ── Row helpers ───────────────────────────────────────────────────

    def _set_row(self, row: int, name: str, color: str,
                 gain_linked: bool, daw_target: str = "",
                 match_method: str = "contains",
                 match_pattern: str = ""):
        """Populate one row in the groups table."""
        name_item = QTableWidgetItem(name)
        self._table.setItem(row, 0, name_item)

        # Color dropdown with swatch icons
        color_names, argb_lookup = self._color_provider()
        color_combo = QComboBox()
        color_combo.setIconSize(QSize(16, 16))
        for cn in color_names:
            argb = argb_lookup(cn)
            icon = _color_swatch_icon(argb) if argb else QIcon()
            color_combo.addItem(icon, cn)
        ci = color_combo.findText(color)
        if ci >= 0:
            color_combo.setCurrentIndex(ci)
        self._table.setCellWidget(row, 1, color_combo)

        # Gain-linked checkbox (centered)
        chk = QCheckBox()
        chk.setChecked(gain_linked)
        chk_container = QWidget()
        chk_layout = QHBoxLayout(chk_container)
        chk_layout.setContentsMargins(0, 0, 0, 0)
        chk_layout.setAlignment(Qt.AlignCenter)
        chk_layout.addWidget(chk)
        self._table.setCellWidget(row, 2, chk_container)

        # DAW Target name
        daw_item = QTableWidgetItem(daw_target)
        self._table.setItem(row, 3, daw_item)

        # Match method dropdown
        match_combo = QComboBox()
        match_combo.addItems(["contains", "regex"])
        mi = match_combo.findText(match_method)
        if mi >= 0:
            match_combo.setCurrentIndex(mi)
        match_combo.setProperty("_row", row)
        match_combo.currentTextChanged.connect(
            lambda _text, r=row: self._validate_pattern_cell(r))
        self._table.setCellWidget(row, 4, match_combo)

        # Match pattern text
        pattern_item = QTableWidgetItem(match_pattern)
        self._table.setItem(row, 5, pattern_item)
        self._validate_pattern_cell(row)

    def _read_groups(self) -> list[dict]:
        """Read all rows (logical order) into a list of group dicts."""
        groups: list[dict] = []
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            if not name_item:
                continue
            name = name_item.text().strip()
            if not name:
                continue
            color_combo = self._table.cellWidget(row, 1)
            color = color_combo.currentText() if color_combo else ""
            chk_container = self._table.cellWidget(row, 2)
            gain_linked = False
            if chk_container:
                chk = chk_container.findChild(QCheckBox)
                if chk:
                    gain_linked = chk.isChecked()
            daw_item = self._table.item(row, 3)
            daw_target = daw_item.text().strip() if daw_item else ""
            match_combo = self._table.cellWidget(row, 4)
            match_method = match_combo.currentText() if match_combo else "contains"
            pattern_item = self._table.item(row, 5)
            match_pattern = pattern_item.text().strip() if pattern_item else ""
            groups.append({
                "name": name,
                "color": color,
                "gain_linked": gain_linked,
                "daw_target": daw_target,
                "match_method": match_method,
                "match_pattern": match_pattern,
            })
        return groups

    def _read_groups_visual_order(self) -> list[dict]:
        """Read groups in current visual (display) order."""
        vh = self._table.verticalHeader()
        n = self._table.rowCount()
        visual_to_logical = sorted(range(n), key=lambda i: vh.visualIndex(i))
        groups: list[dict] = []
        for logical in visual_to_logical:
            name_item = self._table.item(logical, 0)
            if not name_item:
                continue
            name = name_item.text().strip()
            if not name:
                continue
            cc = self._table.cellWidget(logical, 1)
            color = cc.currentText() if cc else ""
            chk_c = self._table.cellWidget(logical, 2)
            gl = False
            if chk_c:
                chk = chk_c.findChild(QCheckBox)
                if chk:
                    gl = chk.isChecked()
            daw_item = self._table.item(logical, 3)
            dt = daw_item.text().strip() if daw_item else ""
            mc = self._table.cellWidget(logical, 4)
            mm = mc.currentText() if mc else "contains"
            pi = self._table.item(logical, 5)
            mp = pi.text().strip() if pi else ""
            groups.append({"name": name, "color": color,
                           "gain_linked": gl, "daw_target": dt,
                           "match_method": mm, "match_pattern": mp})
        return groups

    # ── Name dedup ────────────────────────────────────────────────────

    @staticmethod
    def _group_names_in_table(table: QTableWidget,
                              exclude_row: int = -1) -> set[str]:
        """Collect all group names from a table, optionally excluding one row."""
        names: set[str] = set()
        for r in range(table.rowCount()):
            if r == exclude_row:
                continue
            item = table.item(r, 0)
            if item:
                n = item.text().strip()
                if n:
                    names.add(n)
        return names

    def _unique_name(self, base: str = "New Group") -> str:
        """Generate a unique group name for the table."""
        existing = self._group_names_in_table(self._table)
        if base not in existing:
            return base
        n = 2
        while f"{base} {n}" in existing:
            n += 1
        return f"{base} {n}"

    def _on_cell_changed(self, row: int, col: int):
        """Handle cell edits: name dedup (col 0), pattern validation (col 5)."""
        if col == 0:
            item = self._table.item(row, 0)
            if not item:
                return
            name = item.text().strip()
            others = self._group_names_in_table(self._table, exclude_row=row)
            if name in others:
                self._table.blockSignals(True)
                item.setText(self._unique_name(name))
                self._table.blockSignals(False)
        elif col == 5:
            self._validate_pattern_cell(row)
        self.groups_changed.emit()

    def _validate_pattern_cell(self, row: int):
        """Validate the match pattern cell and set visual indicator.

        When match_method is "regex", tries to compile the pattern.
        Sets the cell foreground to green (valid / empty) or red (invalid).
        For "contains" mode, always shows green.
        """
        match_combo = self._table.cellWidget(row, 4)
        pattern_item = self._table.item(row, 5)
        if not pattern_item:
            return
        method = match_combo.currentText() if match_combo else "contains"
        pattern = pattern_item.text().strip()

        if method == "regex" and pattern:
            try:
                re.compile(pattern)
                pattern_item.setForeground(QColor("#4ec94e"))  # green
                pattern_item.setToolTip("")
            except re.error as e:
                pattern_item.setForeground(QColor("#e05050"))  # red
                pattern_item.setToolTip(f"Invalid regex: {e}")
        else:
            pattern_item.setForeground(QColor("#cccccc"))  # default
            pattern_item.setToolTip("")

    # ── Row operations ────────────────────────────────────────────────

    def _on_add(self):
        row = self._table.rowCount()
        self._table.insertRow(row)
        color_names, _ = self._color_provider()
        default_color = color_names[0] if color_names else ""
        self._set_row(row, self._unique_name(), default_color, False)
        self._table.scrollToBottom()
        self._table.editItem(self._table.item(row, 0))
        self.groups_changed.emit()

    def _on_remove(self):
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)
            self.groups_changed.emit()

    def _on_row_moved(self, logical: int, old_visual: int,
                      new_visual: int):
        """Handle drag-and-drop row reorder."""
        vh = self._table.verticalHeader()
        ordered = self._read_groups_visual_order()
        vh.blockSignals(True)
        self._table.blockSignals(True)
        for i in range(self._table.rowCount()):
            vh.moveSection(vh.visualIndex(i), i)
        self._table.setRowCount(0)
        self._table.setRowCount(len(ordered))
        for row, entry in enumerate(ordered):
            self._set_row(
                row, entry["name"], entry["color"],
                entry["gain_linked"], entry.get("daw_target", ""),
                entry.get("match_method", "contains"),
                entry.get("match_pattern", ""))
        self._table.blockSignals(False)
        vh.blockSignals(False)
        self.groups_changed.emit()

    def _on_sort_az(self):
        groups = self._read_groups()
        groups.sort(key=lambda g: g["name"].lower())
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._table.setRowCount(len(groups))
        for row, entry in enumerate(groups):
            self._set_row(
                row, entry["name"], entry["color"],
                entry["gain_linked"], entry.get("daw_target", ""),
                entry.get("match_method", "contains"),
                entry.get("match_pattern", ""))
        self._table.blockSignals(False)
        self.groups_changed.emit()
