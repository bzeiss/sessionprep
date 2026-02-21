"""GroupsPage — named group presets with add/duplicate/rename/delete."""

from __future__ import annotations

import copy
from typing import Callable

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .config_pages import GroupsTableWidget
from .preset_panel import NamedPresetPanel


class GroupsPage(QWidget):
    """Editable group preset list.

    Implements the standard page interface:
        load(config)   — populate from config["group_presets"]
        commit(config) — write back to config["group_presets"] and
                         config["app"]["active_group_preset"]

    Parameters
    ----------
    color_provider:
        Callable returning ``(color_names, argb_lookup)`` — delegated
        from ColorsPage so the group color dropdowns always reflect the
        live color table.
    """

    def __init__(self, color_provider: Callable, parent=None):
        super().__init__(parent)
        self._color_provider = color_provider
        self._presets_data: dict[str, list[dict]] = {}
        self._init_ui()

    # ── Page interface ────────────────────────────────────────────────

    def load(self, config: dict) -> None:
        from ..settings import build_defaults
        defaults = build_defaults()
        presets = config.get("group_presets", defaults.get("group_presets", {}))
        self._presets_data = copy.deepcopy(presets)
        active = config.get("app", {}).get("active_group_preset", "Default")
        if active not in self._presets_data:
            active = "Default"

        self._panel.reset(list(self._presets_data), current=active)
        self._load_preset(active)

    def commit(self, config: dict) -> None:
        self._save_current()
        config["group_presets"] = copy.deepcopy(self._presets_data)
        config.setdefault("app", {})["active_group_preset"] = (
            self._panel.current_name)

    def active_preset_name(self) -> str:
        return self._panel.current_name

    # ── UI setup ─────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        desc = QLabel(
            "Default track groups used when analyzing a session. "
            "Groups reference colors from the Colors page."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(desc)

        self._panel = NamedPresetPanel(
            [],
            label="Preset:",
            protected=frozenset({"Default"}),
        )
        self._panel.preset_switching.connect(self._on_switching)
        self._panel.preset_added.connect(self._on_added)
        self._panel.preset_duplicated.connect(self._on_duplicated)
        self._panel.preset_renamed.connect(self._on_renamed)
        self._panel.preset_deleted.connect(self._on_deleted)
        layout.addWidget(self._panel)

        # Reset-to-default button (groups-specific, not generic)
        from PySide6.QtWidgets import QHBoxLayout, QPushButton
        reset_row = QHBoxLayout()
        reset_row.setContentsMargins(0, 0, 0, 0)
        reset_btn = QPushButton("Reset to Default")
        reset_btn.setToolTip(
            "Replace the current preset's groups with the built-in defaults")
        reset_btn.clicked.connect(self._on_reset_default)
        reset_row.addStretch()
        reset_row.addWidget(reset_btn)
        layout.addLayout(reset_row)

        self._groups_widget = GroupsTableWidget(
            color_provider=self._color_provider)
        layout.addWidget(self._groups_widget, 1)

    # ── Preset helpers ────────────────────────────────────────────────

    def _load_preset(self, name: str) -> None:
        groups = self._presets_data.get(name, [])
        self._groups_widget.set_groups(groups)

    def _save_current(self) -> None:
        name = self._panel.current_name
        if name:
            self._presets_data[name] = self._groups_widget.get_groups()

    # ── Signal handlers ───────────────────────────────────────────────

    def _on_switching(self, old: str, new: str) -> None:
        if old and old in self._presets_data:
            self._presets_data[old] = self._groups_widget.get_groups()
        self._load_preset(new)

    def _on_added(self, name: str) -> None:
        self._presets_data[name] = []
        self._load_preset(name)

    def _on_duplicated(self, source: str, new: str) -> None:
        self._presets_data[new] = copy.deepcopy(
            self._presets_data.get(source, []))
        self._load_preset(new)

    def _on_renamed(self, old: str, new: str) -> None:
        self._presets_data[new] = self._presets_data.pop(old, [])

    def _on_deleted(self, name: str) -> None:
        self._presets_data.pop(name, None)
        self._load_preset(self._panel.current_name)

    def _on_reset_default(self) -> None:
        from ..settings import _DEFAULT_GROUPS
        from PySide6.QtWidgets import QMessageBox
        current = self._panel.current_name
        reply = QMessageBox.question(
            self, "Reset to Default",
            f"Replace all groups in \u201c{current}\u201d with the "
            f"built-in defaults?\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self._presets_data[current] = copy.deepcopy(_DEFAULT_GROUPS)
        self._load_preset(current)
