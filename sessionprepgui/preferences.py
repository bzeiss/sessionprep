"""Preferences dialog for SessionPrep GUI."""

from __future__ import annotations

import copy
import re
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sessionpreplib.config import ANALYSIS_PARAMS, PRESENTATION_PARAMS, ParamSpec
from sessionpreplib.detectors import default_detectors
from sessionpreplib.processors import default_processors
from sessionpreplib.daw_processors import default_daw_processors
from .param_widgets import (
    _argb_to_qcolor,
    _build_param_page,
    _build_subtext,
    _build_tooltip,
    _read_widget,
    _set_widget_value,
    GroupsTableWidget,
    sanitize_output_folder,
)
from .settings import (
    _APP_DEFAULTS,
    _PRESENTATION_DEFAULTS,
    _build_default_config_preset,
    build_defaults,
)
from .theme import PT_DEFAULT_COLORS



# ---------------------------------------------------------------------------
# Preferences Dialog
# ---------------------------------------------------------------------------

class PreferencesDialog(QDialog):
    """Hierarchical preferences dialog with tree navigation."""

    def __init__(self, config: dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.resize(1150, 550)
        self._config = copy.deepcopy(config)
        self._widgets: dict[str, list[tuple[str, QWidget]]] = {}
        self._general_widgets: list[tuple[str, QWidget]] = []
        self._saved = False

        # Working copy of config presets  {name: structured_dict}
        self._config_presets_data: dict[str, dict[str, Any]] = copy.deepcopy(
            self._config.get("config_presets", {}))
        if "Default" not in self._config_presets_data:
            self._config_presets_data["Default"] = _build_default_config_preset()

        self._init_ui()

    @property
    def saved(self) -> bool:
        return self._saved

    def result_config(self) -> dict[str, Any]:
        """Return the edited config (only valid after save)."""
        return self._config

    # ── Active config preset helpers ──────────────────────────────────

    def _active_preset(self) -> dict[str, Any]:
        """Return the structured dict for the currently selected config preset."""
        name = self._cfg_preset_combo.currentText()
        return self._config_presets_data.get(
            name, self._config_presets_data.get("Default", {}))

    # ── UI setup ──────────────────────────────────────────────────────

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)

        # ── Top-level tabs: Global / Config Presets ───────────────────
        self._top_tabs = QTabWidget()
        self._top_tabs.setDocumentMode(True)
        root_layout.addWidget(self._top_tabs, 1)

        # ── Global tab ────────────────────────────────────────────────
        self._global_page_index: dict[int, int] = {}
        global_tab = QWidget()
        g_layout = QVBoxLayout(global_tab)
        g_layout.setContentsMargins(0, 4, 0, 0)
        g_splitter = QSplitter(Qt.Horizontal)

        self._global_tree = QTreeWidget()
        self._global_tree.setHeaderHidden(True)
        self._global_tree.setMinimumWidth(140)
        self._global_tree.setMaximumWidth(200)
        self._global_tree.currentItemChanged.connect(
            self._on_global_tree_selection)
        g_splitter.addWidget(self._global_tree)

        self._global_stack = QStackedWidget()
        g_splitter.addWidget(self._global_stack)
        g_splitter.setStretchFactor(0, 0)
        g_splitter.setStretchFactor(1, 1)
        g_layout.addWidget(g_splitter, 1)
        self._top_tabs.addTab(global_tab, "Global")

        # ── Config Presets tab ────────────────────────────────────────
        self._preset_page_index: dict[int, int] = {}
        preset_tab = QWidget()
        p_layout = QVBoxLayout(preset_tab)
        p_layout.setContentsMargins(0, 4, 0, 0)

        # Config preset toolbar
        cfg_preset_row = QHBoxLayout()
        cfg_preset_row.setContentsMargins(0, 0, 0, 4)
        cfg_preset_row.setSpacing(6)

        cfg_preset_row.addWidget(QLabel("Config Preset:"))
        self._cfg_preset_combo = QComboBox()
        self._cfg_preset_combo.setMinimumWidth(180)
        cfg_preset_row.addWidget(self._cfg_preset_combo, 1)

        add_btn = QPushButton("+")
        add_btn.setFixedWidth(36)
        add_btn.setToolTip("New config preset")
        add_btn.clicked.connect(self._on_cfg_preset_add)
        cfg_preset_row.addWidget(add_btn)

        dup_btn = QPushButton("Duplicate")
        dup_btn.clicked.connect(self._on_cfg_preset_duplicate)
        cfg_preset_row.addWidget(dup_btn)

        self._cfg_rename_btn = QPushButton("Rename")
        self._cfg_rename_btn.clicked.connect(self._on_cfg_preset_rename)
        cfg_preset_row.addWidget(self._cfg_rename_btn)

        self._cfg_delete_btn = QPushButton("Delete")
        self._cfg_delete_btn.clicked.connect(self._on_cfg_preset_delete)
        cfg_preset_row.addWidget(self._cfg_delete_btn)

        p_layout.addLayout(cfg_preset_row)

        # Populate config preset combo
        self._cfg_preset_combo.blockSignals(True)
        for name in self._config_presets_data:
            self._cfg_preset_combo.addItem(name)
        active = self._config.get("app", {}).get(
            "active_config_preset", "Default")
        idx = self._cfg_preset_combo.findText(active)
        if idx >= 0:
            self._cfg_preset_combo.setCurrentIndex(idx)
        self._cfg_preset_combo.blockSignals(False)
        self._prev_cfg_preset: str | None = self._cfg_preset_combo.currentText()
        self._update_cfg_preset_buttons()

        p_splitter = QSplitter(Qt.Horizontal)

        self._preset_tree = QTreeWidget()
        self._preset_tree.setHeaderHidden(True)
        self._preset_tree.setMinimumWidth(180)
        self._preset_tree.setMaximumWidth(250)
        self._preset_tree.currentItemChanged.connect(
            self._on_preset_tree_selection)
        p_splitter.addWidget(self._preset_tree)

        self._preset_stack = QStackedWidget()
        p_splitter.addWidget(self._preset_stack)
        p_splitter.setStretchFactor(0, 0)
        p_splitter.setStretchFactor(1, 1)

        p_layout.addWidget(p_splitter, 1)
        self._top_tabs.addTab(preset_tab, "Config Presets")

        # ── Build pages ───────────────────────────────────────────────
        self._build_general_page()
        self._build_colors_page()
        self._build_groups_page()

        self._build_analysis_page()
        self._build_detector_pages()
        self._build_processor_pages()
        self._build_daw_processor_pages()

        # Select first items
        self._global_tree.expandAll()
        first_g = self._global_tree.topLevelItem(0)
        if first_g:
            self._global_tree.setCurrentItem(first_g)

        self._preset_tree.expandAll()
        first_p = self._preset_tree.topLevelItem(0)
        if first_p:
            self._preset_tree.setCurrentItem(first_p)

        # Connect config preset switching (after pages are built)
        self._cfg_preset_combo.currentTextChanged.connect(
            self._on_cfg_preset_switched)

        # -- Buttons --
        btn_box = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Save
        )
        btn_box.button(QDialogButtonBox.Save).setDefault(True)
        btn_box.accepted.connect(self._on_save)
        btn_box.rejected.connect(self.reject)
        root_layout.addWidget(btn_box)

    def _add_global_page(self, tree_item: QTreeWidgetItem, page: QWidget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(page)
        idx = self._global_stack.addWidget(scroll)
        self._global_page_index[id(tree_item)] = idx

    def _add_preset_page(self, tree_item: QTreeWidgetItem, page: QWidget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(page)
        idx = self._preset_stack.addWidget(scroll)
        self._preset_page_index[id(tree_item)] = idx

    def _on_global_tree_selection(self, current, _previous):
        if current is None:
            return
        idx = self._global_page_index.get(id(current))
        if idx is not None:
            self._global_stack.setCurrentIndex(idx)

    def _on_preset_tree_selection(self, current, _previous):
        if current is None:
            return
        idx = self._preset_page_index.get(id(current))
        if idx is not None:
            self._preset_stack.setCurrentIndex(idx)

    # ── General page ──────────────────────────────────────────────────

    def _build_general_page(self):
        item = QTreeWidgetItem(self._global_tree, ["General"])
        item.setFont(0, QFont("", -1, QFont.Bold))

        app_params = [
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
        values = self._config.get("app", {})
        page, widgets = _build_param_page(app_params, values)

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
        self._add_global_page(item, page)

    # ── Analysis page ─────────────────────────────────────────────────

    def _build_analysis_page(self):
        item = QTreeWidgetItem(self._preset_tree, ["Analysis"])
        item.setFont(0, QFont("", -1, QFont.Bold))

        preset = self._active_preset()
        values = preset.get("analysis", {})
        page, widgets = _build_param_page(ANALYSIS_PARAMS, values)
        self._widgets["analysis"] = widgets
        self._add_preset_page(item, page)

    # ── Detector pages ────────────────────────────────────────────────

    def _build_detector_pages(self):
        parent_item = QTreeWidgetItem(self._preset_tree, ["Detectors"])
        parent_item.setFont(0, QFont("", -1, QFont.Bold))

        # Parent page: presentation params (config-preset-scoped)
        preset = self._active_preset()
        pres_values = preset.get("presentation", {})
        parent_page, pres_widgets = _build_param_page(
            PRESENTATION_PARAMS, pres_values)
        self._widgets["_presentation"] = pres_widgets
        self._add_preset_page(parent_item, parent_page)

        det_sections = preset.get("detectors", {})
        for det in default_detectors():
            params = det.config_params()
            if not params:
                continue
            child = QTreeWidgetItem(parent_item, [det.name])
            values = det_sections.get(det.id, {})
            page, widgets = _build_param_page(params, values)
            self._widgets[f"detectors.{det.id}"] = widgets
            self._add_preset_page(child, page)

    # ── Processor pages ───────────────────────────────────────────────

    def _build_processor_pages(self):
        parent_item = QTreeWidgetItem(self._preset_tree, ["Processors"])
        parent_item.setFont(0, QFont("", -1, QFont.Bold))

        parent_page = QWidget()
        pl = QVBoxLayout(parent_page)
        pl.setContentsMargins(12, 12, 12, 12)
        pl.addWidget(QLabel("Select a processor from the tree to configure it."))
        pl.addStretch()
        self._add_preset_page(parent_item, parent_page)

        preset = self._active_preset()
        proc_sections = preset.get("processors", {})
        for proc in default_processors():
            params = proc.config_params()
            if not params:
                continue
            child = QTreeWidgetItem(parent_item, [proc.name])
            values = proc_sections.get(proc.id, {})
            page, widgets = _build_param_page(params, values)
            self._widgets[f"processors.{proc.id}"] = widgets
            self._add_preset_page(child, page)

    def _build_daw_processor_pages(self):
        parent_item = QTreeWidgetItem(self._preset_tree, ["DAW Processors"])
        parent_item.setFont(0, QFont("", -1, QFont.Bold))

        parent_page = QWidget()
        pl = QVBoxLayout(parent_page)
        pl.setContentsMargins(12, 12, 12, 12)
        pl.addWidget(QLabel(
            "Select a DAW processor from the tree to configure it."))
        pl.addStretch()
        self._add_preset_page(parent_item, parent_page)

        preset = self._active_preset()
        dp_sections = preset.get("daw_processors", {})
        for dp in default_daw_processors():
            params = dp.config_params()
            if not params:
                continue
            child = QTreeWidgetItem(parent_item, [dp.name])
            values = dp_sections.get(dp.id, {})
            page, widgets = _build_param_page(params, values)
            self._widgets[f"daw_processors.{dp.id}"] = widgets
            self._add_preset_page(child, page)

    # ── Colors page ────────────────────────────────────────────────────

    def _build_colors_page(self):
        item = QTreeWidgetItem(self._global_tree, ["Colors"])
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
        colors = self._config.get("colors", [])
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

        self._add_global_page(item, page)

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
        item = QTreeWidgetItem(self._global_tree, ["Groups"])
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

        # ── Preset toolbar ────────────────────────────────────────────
        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(6)

        preset_row.addWidget(QLabel("Preset:"))
        self._group_preset_combo = QComboBox()
        self._group_preset_combo.setMinimumWidth(160)
        preset_row.addWidget(self._group_preset_combo, 1)

        add_preset_btn = QPushButton("+")
        add_preset_btn.setFixedWidth(36)
        add_preset_btn.setToolTip("New preset")
        add_preset_btn.clicked.connect(self._on_group_preset_add)
        preset_row.addWidget(add_preset_btn)

        dup_preset_btn = QPushButton("Duplicate")
        dup_preset_btn.clicked.connect(self._on_group_preset_duplicate)
        preset_row.addWidget(dup_preset_btn)

        self._rename_preset_btn = QPushButton("Rename")
        self._rename_preset_btn.clicked.connect(self._on_group_preset_rename)
        preset_row.addWidget(self._rename_preset_btn)

        self._delete_preset_btn = QPushButton("Delete")
        self._delete_preset_btn.clicked.connect(self._on_group_preset_delete)
        preset_row.addWidget(self._delete_preset_btn)

        preset_row.addStretch()

        reset_default_btn = QPushButton("Reset to Default")
        reset_default_btn.setToolTip(
            "Replace the current preset's groups with the built-in defaults")
        reset_default_btn.clicked.connect(self._on_group_preset_reset_default)
        preset_row.addWidget(reset_default_btn)

        layout.addLayout(preset_row)

        # ── Groups table (reusable widget) ───────────────────────────
        self._groups_widget = GroupsTableWidget(
            color_provider=self._group_color_provider)
        layout.addWidget(self._groups_widget, 1)

        self._add_global_page(item, page)

        # ── Initialise preset data ────────────────────────────────────
        defaults = build_defaults()
        presets = self._config.get("group_presets",
                                   defaults.get("group_presets", {}))
        self._group_presets_data: dict[str, list[dict]] = copy.deepcopy(presets)
        active = self._config.get("app", {}).get(
            "active_group_preset", "Default")
        if active not in self._group_presets_data:
            active = "Default"

        self._group_preset_combo.blockSignals(True)
        for name in self._group_presets_data:
            self._group_preset_combo.addItem(name)
        idx = self._group_preset_combo.findText(active)
        if idx >= 0:
            self._group_preset_combo.setCurrentIndex(idx)
        self._group_preset_combo.blockSignals(False)

        self._group_preset_combo.currentTextChanged.connect(
            self._on_group_preset_switched)
        self._load_groups_for_preset(active)
        self._update_group_preset_buttons()

    # ── Preset helpers ─────────────────────────────────────────────

    def _group_color_provider(self):
        """Color provider callable for GroupsTableWidget."""
        return self._color_names(), self._color_argb_for_name

    def _load_groups_for_preset(self, preset_name: str):
        """Load groups from *preset_name* into the groups table."""
        groups = self._group_presets_data.get(preset_name, [])
        self._groups_widget.set_groups(groups)

    def _save_current_preset(self):
        """Save the current groups table state back into _group_presets_data."""
        name = self._group_preset_combo.currentText()
        if name:
            self._group_presets_data[name] = self._groups_widget.get_groups()

    def _update_group_preset_buttons(self):
        """Enable/disable Rename and Delete based on current preset."""
        is_default = self._group_preset_combo.currentText() == "Default"
        self._rename_preset_btn.setEnabled(not is_default)
        self._delete_preset_btn.setEnabled(not is_default)

    def _on_group_preset_switched(self, text: str):
        """Save current table state, load newly selected preset."""
        # Save previous preset before switching
        prev = getattr(self, "_prev_group_preset", None)
        if prev and prev in self._group_presets_data:
            self._group_presets_data[prev] = self._groups_widget.get_groups()
        self._prev_group_preset = text
        self._load_groups_for_preset(text)
        self._update_group_preset_buttons()

    def _on_group_preset_add(self):
        """Create a new empty preset."""
        name, ok = QInputDialog.getText(
            self, "New Group Preset", "Preset name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._group_presets_data:
            QMessageBox.warning(
                self, "Duplicate Name",
                f"A preset named \u201c{name}\u201d already exists.")
            return
        self._save_current_preset()
        self._group_presets_data[name] = []
        self._group_preset_combo.blockSignals(True)
        self._group_preset_combo.addItem(name)
        self._group_preset_combo.setCurrentText(name)
        self._group_preset_combo.blockSignals(False)
        self._prev_group_preset = name
        self._load_groups_for_preset(name)
        self._update_group_preset_buttons()

    def _on_group_preset_duplicate(self):
        """Duplicate the current preset under a new name."""
        current = self._group_preset_combo.currentText()
        name, ok = QInputDialog.getText(
            self, "Duplicate Group Preset", "New preset name:",
            text=f"{current} Copy")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._group_presets_data:
            QMessageBox.warning(
                self, "Duplicate Name",
                f"A preset named \u201c{name}\u201d already exists.")
            return
        self._save_current_preset()
        self._group_presets_data[name] = copy.deepcopy(
            self._group_presets_data.get(current, []))
        self._group_preset_combo.blockSignals(True)
        self._group_preset_combo.addItem(name)
        self._group_preset_combo.setCurrentText(name)
        self._group_preset_combo.blockSignals(False)
        self._prev_group_preset = name
        self._load_groups_for_preset(name)
        self._update_group_preset_buttons()

    def _on_group_preset_rename(self):
        """Rename the current preset (not allowed for Default)."""
        current = self._group_preset_combo.currentText()
        if current == "Default":
            return
        name, ok = QInputDialog.getText(
            self, "Rename Group Preset", "New name:", text=current)
        if not ok or not name.strip():
            return
        name = name.strip()
        if name == current:
            return
        if name in self._group_presets_data:
            QMessageBox.warning(
                self, "Duplicate Name",
                f"A preset named \u201c{name}\u201d already exists.")
            return
        self._save_current_preset()
        self._group_presets_data[name] = self._group_presets_data.pop(current)
        idx = self._group_preset_combo.findText(current)
        self._group_preset_combo.blockSignals(True)
        self._group_preset_combo.setItemText(idx, name)
        self._group_preset_combo.blockSignals(False)
        self._prev_group_preset = name
        self._update_group_preset_buttons()

    def _on_group_preset_delete(self):
        """Delete the current preset (not allowed for Default)."""
        current = self._group_preset_combo.currentText()
        if current == "Default":
            return
        reply = QMessageBox.question(
            self, "Delete Preset",
            f"Delete the preset \u201c{current}\u201d?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self._group_presets_data.pop(current, None)
        idx = self._group_preset_combo.findText(current)
        self._group_preset_combo.blockSignals(True)
        self._group_preset_combo.removeItem(idx)
        self._group_preset_combo.setCurrentText("Default")
        self._group_preset_combo.blockSignals(False)
        self._prev_group_preset = "Default"
        self._load_groups_for_preset("Default")
        self._update_group_preset_buttons()

    def _on_group_preset_reset_default(self):
        """Replace the current preset's groups with the built-in defaults."""
        current = self._group_preset_combo.currentText()
        reply = QMessageBox.question(
            self, "Reset to Default",
            f"Replace all groups in \u201c{current}\u201d with the "
            f"built-in defaults?\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        from .settings import _DEFAULT_GROUPS
        self._group_presets_data[current] = copy.deepcopy(_DEFAULT_GROUPS)
        self._load_groups_for_preset(current)

    # ── Config preset helpers ──────────────────────────────────────

    def _save_cfg_preset_widgets(self):
        """Save current pipeline widget values into the active config preset."""
        name = self._cfg_preset_combo.currentText()
        if not name:
            return
        preset = self._config_presets_data.setdefault(name, {})

        # Analysis
        analysis = preset.setdefault("analysis", {})
        for key, widget in self._widgets.get("analysis", []):
            analysis[key] = _read_widget(widget)

        # Detectors
        detectors = preset.setdefault("detectors", {})
        for det in default_detectors():
            wkey = f"detectors.{det.id}"
            if wkey not in self._widgets:
                continue
            section = detectors.setdefault(det.id, {})
            for key, widget in self._widgets[wkey]:
                section[key] = _read_widget(widget)

        # Processors
        processors = preset.setdefault("processors", {})
        for proc in default_processors():
            wkey = f"processors.{proc.id}"
            if wkey not in self._widgets:
                continue
            section = processors.setdefault(proc.id, {})
            for key, widget in self._widgets[wkey]:
                section[key] = _read_widget(widget)

        # DAW Processors
        daw_procs = preset.setdefault("daw_processors", {})
        for dp in default_daw_processors():
            wkey = f"daw_processors.{dp.id}"
            if wkey not in self._widgets:
                continue
            section = daw_procs.setdefault(dp.id, {})
            for key, widget in self._widgets[wkey]:
                section[key] = _read_widget(widget)

        # Presentation
        presentation = preset.setdefault("presentation", {})
        for key, widget in self._widgets.get("_presentation", []):
            presentation[key] = _read_widget(widget)

    def _load_cfg_preset_widgets(self, preset_name: str):
        """Load config preset values into pipeline widgets."""
        preset = self._config_presets_data.get(preset_name, {})

        # Analysis
        analysis = preset.get("analysis", {})
        for key, widget in self._widgets.get("analysis", []):
            if key in analysis:
                _set_widget_value(widget, analysis[key])

        # Detectors
        det_sections = preset.get("detectors", {})
        for det in default_detectors():
            wkey = f"detectors.{det.id}"
            if wkey not in self._widgets:
                continue
            values = det_sections.get(det.id, {})
            for key, widget in self._widgets[wkey]:
                if key in values:
                    _set_widget_value(widget, values[key])

        # Processors
        proc_sections = preset.get("processors", {})
        for proc in default_processors():
            wkey = f"processors.{proc.id}"
            if wkey not in self._widgets:
                continue
            values = proc_sections.get(proc.id, {})
            for key, widget in self._widgets[wkey]:
                if key in values:
                    _set_widget_value(widget, values[key])

        # DAW Processors
        dp_sections = preset.get("daw_processors", {})
        for dp in default_daw_processors():
            wkey = f"daw_processors.{dp.id}"
            if wkey not in self._widgets:
                continue
            values = dp_sections.get(dp.id, {})
            for key, widget in self._widgets[wkey]:
                if key in values:
                    _set_widget_value(widget, values[key])

        # Presentation
        pres = preset.get("presentation", {})
        for key, widget in self._widgets.get("_presentation", []):
            if key in pres:
                _set_widget_value(widget, pres[key])

    def _update_cfg_preset_buttons(self):
        """Enable/disable Rename and Delete for config presets."""
        is_default = self._cfg_preset_combo.currentText() == "Default"
        self._cfg_rename_btn.setEnabled(not is_default)
        self._cfg_delete_btn.setEnabled(not is_default)

    def _on_cfg_preset_switched(self, text: str):
        """Save current widgets, load newly selected config preset."""
        prev = self._prev_cfg_preset
        if prev and prev in self._config_presets_data:
            self._save_cfg_preset_widgets()
        self._prev_cfg_preset = text
        self._load_cfg_preset_widgets(text)
        self._update_cfg_preset_buttons()

    def _on_cfg_preset_add(self):
        """Create a new config preset from built-in defaults."""
        name, ok = QInputDialog.getText(
            self, "New Config Preset", "Preset name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._config_presets_data:
            QMessageBox.warning(
                self, "Duplicate Name",
                f"A preset named \u201c{name}\u201d already exists.")
            return
        self._save_cfg_preset_widgets()
        self._config_presets_data[name] = _build_default_config_preset()
        self._cfg_preset_combo.blockSignals(True)
        self._cfg_preset_combo.addItem(name)
        self._cfg_preset_combo.setCurrentText(name)
        self._cfg_preset_combo.blockSignals(False)
        self._prev_cfg_preset = name
        self._load_cfg_preset_widgets(name)
        self._update_cfg_preset_buttons()

    def _on_cfg_preset_duplicate(self):
        """Duplicate the current config preset under a new name."""
        current = self._cfg_preset_combo.currentText()
        name, ok = QInputDialog.getText(
            self, "Duplicate Config Preset", "New preset name:",
            text=f"{current} Copy")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._config_presets_data:
            QMessageBox.warning(
                self, "Duplicate Name",
                f"A preset named \u201c{name}\u201d already exists.")
            return
        self._save_cfg_preset_widgets()
        self._config_presets_data[name] = copy.deepcopy(
            self._config_presets_data.get(current, {}))
        self._cfg_preset_combo.blockSignals(True)
        self._cfg_preset_combo.addItem(name)
        self._cfg_preset_combo.setCurrentText(name)
        self._cfg_preset_combo.blockSignals(False)
        self._prev_cfg_preset = name
        self._load_cfg_preset_widgets(name)
        self._update_cfg_preset_buttons()

    def _on_cfg_preset_rename(self):
        """Rename the current config preset (not allowed for Default)."""
        current = self._cfg_preset_combo.currentText()
        if current == "Default":
            return
        name, ok = QInputDialog.getText(
            self, "Rename Config Preset", "New name:", text=current)
        if not ok or not name.strip():
            return
        name = name.strip()
        if name == current:
            return
        if name in self._config_presets_data:
            QMessageBox.warning(
                self, "Duplicate Name",
                f"A preset named \u201c{name}\u201d already exists.")
            return
        self._save_cfg_preset_widgets()
        self._config_presets_data[name] = self._config_presets_data.pop(current)
        idx = self._cfg_preset_combo.findText(current)
        self._cfg_preset_combo.blockSignals(True)
        self._cfg_preset_combo.setItemText(idx, name)
        self._cfg_preset_combo.blockSignals(False)
        self._prev_cfg_preset = name
        self._update_cfg_preset_buttons()

    def _on_cfg_preset_delete(self):
        """Delete the current config preset (not allowed for Default)."""
        current = self._cfg_preset_combo.currentText()
        if current == "Default":
            return
        reply = QMessageBox.question(
            self, "Delete Config Preset",
            f"Delete the config preset \u201c{current}\u201d?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self._config_presets_data.pop(current, None)
        idx = self._cfg_preset_combo.findText(current)
        self._cfg_preset_combo.blockSignals(True)
        self._cfg_preset_combo.removeItem(idx)
        self._cfg_preset_combo.setCurrentText("Default")
        self._cfg_preset_combo.blockSignals(False)
        self._prev_cfg_preset = "Default"
        self._load_cfg_preset_widgets("Default")
        self._update_cfg_preset_buttons()

    # ── Color helpers ─────────────────────────────────────────────────

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

    def _color_argb_for_name(self, name: str) -> str | None:
        """Look up an ARGB value by color name from the colors table."""
        for row in range(self._colors_table.rowCount()):
            item = self._colors_table.item(row, 1)
            if item and item.text().strip() == name:
                swatch = self._colors_table.item(row, 2)
                if swatch:
                    return swatch.data(Qt.UserRole)
        return None

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
        # ── App settings ──────────────────────────────────────────────
        app = self._config.setdefault("app", {})
        for key, widget in self._general_widgets:
            app[key] = _read_widget(widget)

        # Validate output folder name
        raw_folder = app.get("output_folder", "")
        clean_folder = sanitize_output_folder(str(raw_folder))
        if clean_folder is None:
            QMessageBox.warning(
                self, "Invalid output folder",
                "The output folder name is invalid.\n\n"
                "It must be a simple folder name without path separators, "
                "special characters, or reserved names.",
            )
            return
        app["output_folder"] = clean_folder

        # Remember active preset names
        app["active_config_preset"] = self._cfg_preset_combo.currentText()
        app["active_group_preset"] = self._group_preset_combo.currentText()

        # ── Config presets ────────────────────────────────────────────
        self._save_cfg_preset_widgets()
        self._config["config_presets"] = copy.deepcopy(
            self._config_presets_data)

        # ── Colors ────────────────────────────────────────────────────
        self._config["colors"] = self._read_colors()

        # ── Group presets ─────────────────────────────────────────────
        self._save_current_preset()

        # Validate regex patterns in all group presets
        for preset_name, groups in self._group_presets_data.items():
            for g in groups:
                if g.get("match_method") == "regex" and g.get("match_pattern", ""):
                    try:
                        re.compile(g["match_pattern"])
                    except re.error as e:
                        QMessageBox.warning(
                            self, "Invalid Regex Pattern",
                            f"Group \u201c{g['name']}\u201d in preset "
                            f"\u201c{preset_name}\u201d has an invalid "
                            f"regular expression:\n\n"
                            f"{g['match_pattern']}\n\n{e}",
                        )
                        return

        self._config["group_presets"] = copy.deepcopy(
            self._group_presets_data)

        # Remove legacy keys if present
        self._config.pop("gui", None)
        self._config.pop("analysis", None)
        self._config.pop("detectors", None)
        self._config.pop("processors", None)
        self._config.pop("daw_processors", None)

        self._saved = True
        self.accept()
