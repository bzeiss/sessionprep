"""SessionPrep-specific preference widgets and config-page builders.

Depends on sessionpreplib.  Not portable to other apps as-is.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .param_form import (
    _build_param_page,
    _color_swatch_icon,
    _read_widget,
    _set_widget_value,
)


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

# Returns (color_names, argb_lookup_by_name).
ColorProvider = Callable[[], tuple[list[str], Callable[[str], str | None]]]


# ---------------------------------------------------------------------------
# GroupsTableWidget
# ---------------------------------------------------------------------------

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
        return self._read_groups()

    @property
    def table(self) -> QTableWidget:
        return self._table

    # ── Row helpers ───────────────────────────────────────────────────

    def _set_row(self, row: int, name: str, color: str,
                 gain_linked: bool, daw_target: str = "",
                 match_method: str = "contains",
                 match_pattern: str = ""):
        name_item = QTableWidgetItem(name)
        self._table.setItem(row, 0, name_item)

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

        chk = QCheckBox()
        chk.setChecked(gain_linked)
        chk_container = QWidget()
        chk_layout = QHBoxLayout(chk_container)
        chk_layout.setContentsMargins(0, 0, 0, 0)
        chk_layout.setAlignment(Qt.AlignCenter)
        chk_layout.addWidget(chk)
        self._table.setCellWidget(row, 2, chk_container)

        self._table.setItem(row, 3, QTableWidgetItem(daw_target))

        match_combo = QComboBox()
        match_combo.addItems(["contains", "regex"])
        mi = match_combo.findText(match_method)
        if mi >= 0:
            match_combo.setCurrentIndex(mi)
        match_combo.currentTextChanged.connect(
            lambda _text, r=row: self._validate_pattern_cell(r))
        self._table.setCellWidget(row, 4, match_combo)

        pattern_item = QTableWidgetItem(match_pattern)
        self._table.setItem(row, 5, pattern_item)
        self._validate_pattern_cell(row)

    def _read_groups(self) -> list[dict]:
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
                "name": name, "color": color, "gain_linked": gain_linked,
                "daw_target": daw_target, "match_method": match_method,
                "match_pattern": match_pattern,
            })
        return groups

    def _read_groups_visual_order(self) -> list[dict]:
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
        existing = self._group_names_in_table(self._table)
        if base not in existing:
            return base
        n = 2
        while f"{base} {n}" in existing:
            n += 1
        return f"{base} {n}"

    def _on_cell_changed(self, row: int, col: int):
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
        match_combo = self._table.cellWidget(row, 4)
        pattern_item = self._table.item(row, 5)
        if not pattern_item:
            return
        method = match_combo.currentText() if match_combo else "contains"
        pattern = pattern_item.text().strip()
        if method == "regex" and pattern:
            try:
                re.compile(pattern)
                pattern_item.setForeground(QColor("#4ec94e"))
                pattern_item.setToolTip("")
            except re.error as e:
                pattern_item.setForeground(QColor("#e05050"))
                pattern_item.setToolTip(f"Invalid regex: {e}")
        else:
            pattern_item.setForeground(QColor("#cccccc"))
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

    def _on_row_moved(self, logical: int, old_visual: int, new_visual: int):
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


# ---------------------------------------------------------------------------
# DawProjectTemplatesWidget
# ---------------------------------------------------------------------------

class DawProjectTemplatesWidget(QWidget):
    """Editable table of DAWProject mix templates."""

    templates_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(QLabel("<b>Mix Templates</b>"))

        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(
            ["Name", "Template Path", "Fader Ceiling (dB)"])
        gh = self._table.horizontalHeader()
        gh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        gh.setSectionResizeMode(0, QHeaderView.Interactive)
        gh.resizeSection(0, 180)
        gh.setSectionResizeMode(1, QHeaderView.Stretch)
        gh.setSectionResizeMode(2, QHeaderView.Fixed)
        gh.resizeSection(2, 120)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        layout.addWidget(self._table, 1)

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
        layout.addLayout(btn_row)

    def set_templates(self, templates: list[dict]):
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._table.setRowCount(len(templates))
        for row, tpl in enumerate(templates):
            self._set_row(
                row, tpl.get("name", ""),
                tpl.get("template_path", ""),
                float(tpl.get("fader_ceiling_db", 6.0)))
        self._table.blockSignals(False)

    def get_templates(self) -> list[dict]:
        templates: list[dict] = []
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            name = name_item.text().strip() if name_item else ""
            path = ""
            path_container = self._table.cellWidget(row, 1)
            if path_container:
                le = path_container.findChild(QLineEdit)
                if le:
                    path = le.text().strip()
            ceiling_widget = self._table.cellWidget(row, 2)
            ceiling = ceiling_widget.value() if ceiling_widget else 24.0
            if name or path:
                templates.append({
                    "name": name,
                    "template_path": path,
                    "fader_ceiling_db": ceiling,
                })
        return templates

    def _set_row(self, row: int, name: str, template_path: str,
                 fader_ceiling_db: float = 6.0):
        self._table.setItem(row, 0, QTableWidgetItem(name))

        path_container = QWidget()
        path_layout = QHBoxLayout(path_container)
        path_layout.setContentsMargins(2, 0, 2, 0)
        path_layout.setSpacing(4)
        path_edit = QLineEdit(template_path)
        path_edit.setPlaceholderText("Path to .dawproject file")
        path_layout.addWidget(path_edit, 1)
        browse_btn = QPushButton("Browse\u2026")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(
            lambda _checked=False, le=path_edit: self._browse_template(le))
        path_layout.addWidget(browse_btn)
        self._table.setCellWidget(row, 1, path_container)

        ceiling_spin = QDoubleSpinBox()
        ceiling_spin.setRange(0.0, 48.0)
        ceiling_spin.setDecimals(1)
        ceiling_spin.setSuffix(" dB")
        ceiling_spin.setValue(fader_ceiling_db)
        self._table.setCellWidget(row, 2, ceiling_spin)

    def _browse_template(self, line_edit: QLineEdit):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select DAWProject Template",
            line_edit.text(),
            "DAWProject Files (*.dawproject);;All Files (*)")
        if path:
            line_edit.setText(path)
            self.templates_changed.emit()

    def _on_add(self):
        row = self._table.rowCount()
        self._table.setRowCount(row + 1)
        self._set_row(row, "", "", 6.0)
        self.templates_changed.emit()

    def _on_remove(self):
        row = self._table.currentRow()
        if row < 0:
            return
        self._table.removeRow(row)
        self.templates_changed.emit()


# ---------------------------------------------------------------------------
# Shared config page builder / loader / reader
# ---------------------------------------------------------------------------

def build_config_pages(
    tree,
    preset: dict[str, Any],
    widgets_dict: dict,
    register_page: Callable[[QTreeWidgetItem, QWidget], None],
    *,
    on_processor_enabled: Callable | None = None,
    on_daw_config_changed: Callable | None = None,
) -> DawProjectTemplatesWidget | None:
    """Build the common config tree pages (Analysis, Detectors, Processors, DAW Processors).

    Returns the DawProjectTemplatesWidget if created, otherwise None.
    """
    from sessionpreplib.config import ANALYSIS_PARAMS, PRESENTATION_PARAMS
    from sessionpreplib.detectors import default_detectors
    from sessionpreplib.processors import default_processors
    from sessionpreplib.daw_processors import default_daw_processors

    dawproject_tpl_widget: DawProjectTemplatesWidget | None = None

    item = QTreeWidgetItem(tree, ["Analysis"])
    item.setFont(0, QFont("", -1, QFont.Bold))
    pg, wdg = _build_param_page(ANALYSIS_PARAMS, preset.get("analysis", {}))
    widgets_dict["analysis"] = wdg
    register_page(item, pg)

    det_parent = QTreeWidgetItem(tree, ["Detectors"])
    det_parent.setFont(0, QFont("", -1, QFont.Bold))
    pg, wdg = _build_param_page(PRESENTATION_PARAMS, preset.get("presentation", {}))
    widgets_dict["_presentation"] = wdg
    register_page(det_parent, pg)

    det_sections = preset.get("detectors", {})
    for det in default_detectors():
        params = det.config_params()
        if not params:
            continue
        child = QTreeWidgetItem(det_parent, [det.name])
        pg, wdg = _build_param_page(params, det_sections.get(det.id, {}))
        widgets_dict[f"detectors.{det.id}"] = wdg
        register_page(child, pg)

    proc_parent = QTreeWidgetItem(tree, ["Processors"])
    proc_parent.setFont(0, QFont("", -1, QFont.Bold))
    placeholder = QWidget()
    pl = QVBoxLayout(placeholder)
    pl.setContentsMargins(12, 12, 12, 12)
    pl.addWidget(QLabel("Select a processor from the tree to configure."))
    pl.addStretch()
    register_page(proc_parent, placeholder)

    proc_sections = preset.get("processors", {})
    for proc in default_processors():
        params = proc.config_params()
        if not params:
            continue
        child = QTreeWidgetItem(proc_parent, [proc.name])
        pg, wdg = _build_param_page(params, proc_sections.get(proc.id, {}))
        widgets_dict[f"processors.{proc.id}"] = wdg
        register_page(child, pg)
        if on_processor_enabled is not None:
            enabled_key = f"{proc.id}_enabled"
            for key, widget in wdg:
                if key == enabled_key and isinstance(widget, QCheckBox):
                    widget.toggled.connect(on_processor_enabled)
                    break

    daw_parent = QTreeWidgetItem(tree, ["DAW Processors"])
    daw_parent.setFont(0, QFont("", -1, QFont.Bold))
    placeholder2 = QWidget()
    pl2 = QVBoxLayout(placeholder2)
    pl2.setContentsMargins(12, 12, 12, 12)
    pl2.addWidget(QLabel("Select a DAW processor from the tree to configure."))
    pl2.addStretch()
    register_page(daw_parent, placeholder2)

    dp_sections = preset.get("daw_processors", {})
    for dp in default_daw_processors():
        params = dp.config_params()
        if not params:
            continue
        child = QTreeWidgetItem(daw_parent, [dp.name])
        pg, wdg = _build_param_page(params, dp_sections.get(dp.id, {}))
        widgets_dict[f"daw_processors.{dp.id}"] = wdg
        if on_daw_config_changed is not None:
            enabled_key = f"{dp.id}_enabled"
            for key, widget in wdg:
                if key == enabled_key and isinstance(widget, QCheckBox):
                    widget.toggled.connect(on_daw_config_changed)
                    break
        if dp.id == "dawproject":
            tpl_widget = DawProjectTemplatesWidget()
            tpl_widget.set_templates(dp_sections.get(dp.id, {}).get("dawproject_templates", []))
            dawproject_tpl_widget = tpl_widget
            if on_daw_config_changed is not None:
                tpl_widget.templates_changed.connect(on_daw_config_changed)
            pg.layout().insertWidget(pg.layout().count() - 1, tpl_widget)
        register_page(child, pg)

    return dawproject_tpl_widget


def load_config_widgets(
    widgets_dict: dict,
    preset: dict[str, Any],
    dawproject_tpl_widget: DawProjectTemplatesWidget | None = None,
) -> None:
    """Load values from *preset* into widgets stored in *widgets_dict*."""
    from sessionpreplib.detectors import default_detectors
    from sessionpreplib.processors import default_processors
    from sessionpreplib.daw_processors import default_daw_processors

    for key, widget in widgets_dict.get("analysis", []):
        if key in preset.get("analysis", {}):
            _set_widget_value(widget, preset["analysis"][key])

    for key, widget in widgets_dict.get("_presentation", []):
        if key in preset.get("presentation", {}):
            _set_widget_value(widget, preset["presentation"][key])

    det_sections = preset.get("detectors", {})
    for det in default_detectors():
        wkey = f"detectors.{det.id}"
        if wkey not in widgets_dict:
            continue
        vals = det_sections.get(det.id, {})
        for key, widget in widgets_dict[wkey]:
            if key in vals:
                _set_widget_value(widget, vals[key])

    proc_sections = preset.get("processors", {})
    for proc in default_processors():
        wkey = f"processors.{proc.id}"
        if wkey not in widgets_dict:
            continue
        vals = proc_sections.get(proc.id, {})
        for key, widget in widgets_dict[wkey]:
            if key in vals:
                _set_widget_value(widget, vals[key])

    dp_sections = preset.get("daw_processors", {})
    for dp in default_daw_processors():
        wkey = f"daw_processors.{dp.id}"
        if wkey not in widgets_dict:
            continue
        vals = dp_sections.get(dp.id, {})
        for key, widget in widgets_dict[wkey]:
            if key in vals:
                _set_widget_value(widget, vals[key])
        if dp.id == "dawproject" and dawproject_tpl_widget is not None:
            dawproject_tpl_widget.set_templates(vals.get("dawproject_templates", []))


def read_config_widgets(
    widgets_dict: dict,
    dawproject_tpl_widget: DawProjectTemplatesWidget | None = None,
    fallback_daw_sections: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Read current widget values into a structured config dict."""
    from sessionpreplib.detectors import default_detectors
    from sessionpreplib.processors import default_processors
    from sessionpreplib.daw_processors import default_daw_processors

    cfg: dict[str, Any] = {}

    analysis: dict[str, Any] = {}
    for key, widget in widgets_dict.get("analysis", []):
        analysis[key] = _read_widget(widget)
    cfg["analysis"] = analysis

    presentation: dict[str, Any] = {}
    for key, widget in widgets_dict.get("_presentation", []):
        presentation[key] = _read_widget(widget)
    cfg["presentation"] = presentation

    detectors: dict[str, dict] = {}
    for det in default_detectors():
        wkey = f"detectors.{det.id}"
        if wkey not in widgets_dict:
            continue
        section: dict[str, Any] = {}
        for key, widget in widgets_dict[wkey]:
            section[key] = _read_widget(widget)
        detectors[det.id] = section
    cfg["detectors"] = detectors

    processors: dict[str, dict] = {}
    for proc in default_processors():
        wkey = f"processors.{proc.id}"
        if wkey not in widgets_dict:
            continue
        section = {}
        for key, widget in widgets_dict[wkey]:
            section[key] = _read_widget(widget)
        processors[proc.id] = section
    cfg["processors"] = processors

    daw_procs: dict[str, dict] = {}
    for dp in default_daw_processors():
        wkey = f"daw_processors.{dp.id}"
        if wkey not in widgets_dict:
            continue
        section = {}
        for key, widget in widgets_dict[wkey]:
            section[key] = _read_widget(widget)
        if dp.id == "dawproject" and dawproject_tpl_widget is not None:
            section["dawproject_templates"] = dawproject_tpl_widget.get_templates()
        if fallback_daw_sections:
            for gk, gv in fallback_daw_sections.get(dp.id, {}).items():
                if gk not in section:
                    section[gk] = gv
        daw_procs[dp.id] = section
    cfg["daw_processors"] = daw_procs

    return cfg
