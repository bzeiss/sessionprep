"""Preferences dialog — thin orchestrator.

Creates the two-tab shell (Global / Config Presets), wires up the
self-contained page classes, and owns config-preset CRUD via NamedPresetPanel.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..settings import _build_default_config_preset
from .config_pages import build_config_pages, load_config_widgets, read_config_widgets
from .page_colors import ColorsPage
from .page_general import GeneralPage
from .page_groups import GroupsPage
from .preset_panel import NamedPresetPanel


class PreferencesDialog(QDialog):
    """Hierarchical preferences dialog with tree navigation."""

    def __init__(self, config: dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.resize(1150, 700)
        self._config = copy.deepcopy(config)
        self._saved = False

        # Config presets working copy {name: structured_dict}
        self._config_presets_data: dict[str, dict[str, Any]] = copy.deepcopy(
            self._config.get("config_presets", {}))
        if "Default" not in self._config_presets_data:
            self._config_presets_data["Default"] = _build_default_config_preset()

        # Pipeline widget registry (built by build_config_pages)
        self._cfg_widgets: dict = {}
        self._cfg_dawproject_widget = None

        # Pages
        self._general_page = GeneralPage()
        self._colors_page = ColorsPage()
        self._groups_page = GroupsPage(
            color_provider=self._colors_page.color_provider)

        self._init_ui()

        # Load all pages after UI is built
        self._general_page.load(self._config)
        self._colors_page.load(self._config)
        self._groups_page.load(self._config)

        active_cfg = self._config.get("app", {}).get(
            "active_config_preset", "Default")
        self._cfg_panel.set_current(active_cfg)
        self._load_cfg_preset_widgets(active_cfg)

    # ── Public API ────────────────────────────────────────────────────

    @property
    def saved(self) -> bool:
        return self._saved

    def result_config(self) -> dict[str, Any]:
        """Return the edited config (only valid after Save)."""
        return self._config

    # ── UI construction ───────────────────────────────────────────────

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        root.addWidget(tabs, 1)

        tabs.addTab(self._build_global_tab(), "Global")
        tabs.addTab(self._build_preset_tab(), "Config Presets")

        btn_box = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        btn_box.button(QDialogButtonBox.Save).setDefault(True)
        btn_box.accepted.connect(self._on_save)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    def _build_global_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 4, 0, 0)

        splitter = QSplitter(Qt.Horizontal)
        self._global_tree = QTreeWidget()
        self._global_tree.setHeaderHidden(True)
        self._global_tree.setMinimumWidth(140)
        self._global_tree.setMaximumWidth(200)
        splitter.addWidget(self._global_tree)

        self._global_stack = QStackedWidget()
        splitter.addWidget(self._global_stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        self._global_page_index: dict[int, int] = {}

        def add(label: str, page: QWidget) -> QTreeWidgetItem:
            item = QTreeWidgetItem(self._global_tree, [label])
            item.setFont(0, QFont("", -1, QFont.Bold))
            self._register_page(item, page,
                                self._global_stack, self._global_page_index)
            return item

        add("General", self._general_page)
        add("Colors", self._colors_page)
        add("Groups", self._groups_page)

        self._global_tree.expandAll()
        first = self._global_tree.topLevelItem(0)
        if first:
            self._global_tree.setCurrentItem(first)
        self._global_tree.currentItemChanged.connect(
            lambda cur, _: self._on_tree_selection(
                cur, self._global_stack, self._global_page_index))

        return tab

    def _build_preset_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 4, 0, 0)

        self._cfg_panel = NamedPresetPanel(
            list(self._config_presets_data),
            label="Config Preset:",
            protected=frozenset({"Default"}),
        )
        self._cfg_panel.preset_switching.connect(self._on_cfg_switching)
        self._cfg_panel.preset_added.connect(self._on_cfg_added)
        self._cfg_panel.preset_duplicated.connect(self._on_cfg_duplicated)
        self._cfg_panel.preset_renamed.connect(self._on_cfg_renamed)
        self._cfg_panel.preset_deleted.connect(self._on_cfg_deleted)
        layout.addWidget(self._cfg_panel)

        splitter = QSplitter(Qt.Horizontal)
        self._preset_tree = QTreeWidget()
        self._preset_tree.setHeaderHidden(True)
        self._preset_tree.setMinimumWidth(180)
        self._preset_tree.setMaximumWidth(250)
        splitter.addWidget(self._preset_tree)

        self._preset_stack = QStackedWidget()
        splitter.addWidget(self._preset_stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        self._preset_page_index: dict[int, int] = {}
        self._cfg_dawproject_widget = build_config_pages(
            self._preset_tree,
            self._active_preset(),
            self._cfg_widgets,
            lambda item, page: self._register_page(
                item, page, self._preset_stack, self._preset_page_index),
        )

        self._preset_tree.expandAll()
        first = self._preset_tree.topLevelItem(0)
        if first:
            self._preset_tree.setCurrentItem(first)
        self._preset_tree.currentItemChanged.connect(
            lambda cur, _: self._on_tree_selection(
                cur, self._preset_stack, self._preset_page_index))

        return tab

    # ── Tree/stack navigation ─────────────────────────────────────────

    def _register_page(self, item: QTreeWidgetItem, page: QWidget,
                       stack: QStackedWidget,
                       index: dict[int, int]) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(page)
        idx = stack.addWidget(scroll)
        index[id(item)] = idx

    def _on_tree_selection(self, current: QTreeWidgetItem | None,
                           stack: QStackedWidget,
                           index: dict[int, int]) -> None:
        if current is None:
            return
        idx = index.get(id(current))
        if idx is not None:
            stack.setCurrentIndex(idx)

    # ── Config preset helpers ─────────────────────────────────────────

    def _active_preset(self) -> dict[str, Any]:
        name = self._cfg_panel.current_name if hasattr(self, "_cfg_panel") else "Default"
        return self._config_presets_data.get(
            name, self._config_presets_data.get("Default", {}))

    def _save_cfg_preset_widgets(self, name: str | None = None) -> None:
        if name is None:
            name = self._cfg_panel.current_name
        if not name:
            return
        preset = self._config_presets_data.setdefault(name, {})
        preset.update(read_config_widgets(
            self._cfg_widgets, self._cfg_dawproject_widget))

    def _load_cfg_preset_widgets(self, name: str) -> None:
        preset = self._config_presets_data.get(name, {})
        load_config_widgets(self._cfg_widgets, preset, self._cfg_dawproject_widget)

    # ── Config preset signal handlers ────────────────────────────────

    def _on_cfg_switching(self, old: str, new: str) -> None:
        if old and old in self._config_presets_data:
            self._save_cfg_preset_widgets(old)  # old name before combo changed
        self._load_cfg_preset_widgets(new)

    def _on_cfg_added(self, name: str) -> None:
        self._config_presets_data[name] = _build_default_config_preset()
        self._load_cfg_preset_widgets(name)

    def _on_cfg_duplicated(self, source: str, new: str) -> None:
        self._save_cfg_preset_widgets(source)  # capture any unsaved widget edits
        self._config_presets_data[new] = copy.deepcopy(
            self._config_presets_data.get(source, {}))
        self._load_cfg_preset_widgets(new)

    def _on_cfg_renamed(self, old: str, new: str) -> None:
        self._config_presets_data[new] = self._config_presets_data.pop(old, {})

    def _on_cfg_deleted(self, name: str) -> None:
        self._config_presets_data.pop(name, None)
        self._load_cfg_preset_widgets(self._cfg_panel.current_name)

    # ── Save ─────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        err = self._general_page.validate()
        if err:
            QMessageBox.warning(self, "Invalid Settings", err)
            return

        self._general_page.commit(self._config)
        self._colors_page.commit(self._config)

        # Validate regex patterns across all group presets before committing
        self._groups_page._save_current()
        for preset_name, groups in self._groups_page._presets_data.items():
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
                            f"{g['match_pattern']}\n\n{e}")
                        return

        self._groups_page.commit(self._config)

        self._save_cfg_preset_widgets()
        self._config["config_presets"] = copy.deepcopy(self._config_presets_data)
        self._config.setdefault("app", {})[
            "active_config_preset"] = self._cfg_panel.current_name

        # Remove legacy keys
        for legacy in ("gui", "analysis", "detectors", "processors", "daw_processors"):
            self._config.pop(legacy, None)

        self._saved = True
        self.accept()
