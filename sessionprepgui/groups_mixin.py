"""Groups mixin: group management, colors, group column, auto-group, linked levels."""

from __future__ import annotations

import copy
import os
import re
from typing import Any

from PySide6.QtCore import Qt, Slot, QSize
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .preferences import _argb_to_qcolor
from .settings import build_defaults, save_config
from .table_widgets import _SortableItem
from .theme import COLORS, PT_DEFAULT_COLORS
from .widgets import BatchComboBox


class GroupsMixin:
    """Group management: groups tab, colors, group column, auto-group, linked levels.

    Mixed into ``SessionPrepWindow`` — not meant to be used standalone.
    """

    # ── Groups tab (session-local group editor) ─────────────────────────

    def _build_groups_tab(self) -> QWidget:
        """Build the session-local Groups editor tab."""
        page = QWidget()
        page.setAutoFillBackground(True)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        desc = QLabel(
            "Session-local track groups. Changes here apply only to "
            "the current session."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(desc)

        self._groups_tab_table = QTableWidget()
        self._groups_tab_table.setColumnCount(6)
        self._groups_tab_table.setHorizontalHeaderLabels(
            ["Name", "Color", "Gain-Linked", "DAW Target",
             "Match", "Match Pattern"])
        vh = self._groups_tab_table.verticalHeader()
        vh.setSectionsMovable(True)
        vh.sectionMoved.connect(self._on_groups_tab_row_moved)
        self._groups_tab_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._groups_tab_table.setSelectionMode(QTableWidget.SingleSelection)
        gh = self._groups_tab_table.horizontalHeader()
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

        self._groups_tab_table.cellChanged.connect(
            self._on_groups_tab_name_changed)

        layout.addWidget(self._groups_tab_table, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._on_groups_tab_add)
        btn_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._on_groups_tab_remove)
        btn_row.addWidget(remove_btn)

        reset_btn = QPushButton("Reset from Preset")
        reset_btn.clicked.connect(self._on_groups_tab_reset)
        btn_row.addWidget(reset_btn)

        btn_row.addStretch()

        az_btn = QPushButton("Sort A→Z")
        az_btn.clicked.connect(self._on_groups_tab_sort_az)
        btn_row.addWidget(az_btn)

        layout.addLayout(btn_row)

        return page

    # ── Color helpers ─────────────────────────────────────────────────────

    def _color_names_from_config(self) -> list[str]:
        """Return color names from the current config (or defaults)."""
        colors = self._config.get("colors", PT_DEFAULT_COLORS)
        return [c["name"] for c in colors if c.get("name")]

    def _color_argb_by_name(self, name: str) -> str | None:
        """Look up ARGB hex by color name from config, falling back to defaults."""
        colors = self._config.get("colors", PT_DEFAULT_COLORS)
        for c in colors:
            if c.get("name") == name:
                return c.get("argb")
        # Fallback: check built-in defaults (handles stale saved configs)
        for c in PT_DEFAULT_COLORS:
            if c.get("name") == name:
                return c.get("argb")
        return None

    @staticmethod
    def _color_swatch_icon(argb: str, size: int = 16) -> QIcon:
        """Create a small QIcon swatch from an ARGB hex string."""
        pm = QPixmap(size, size)
        pm.fill(_argb_to_qcolor(argb))
        return QIcon(pm)

    _TINT_FACTOR = 0.15  # fraction of source alpha → subtle wash

    def _tint_group_color(self, group_name: str | None,
                          gcm: dict[str, str] | None = None) -> QColor | None:
        """Return a pre-blended tint QColor for *group_name*, or None."""
        if gcm is None:
            gcm = self._group_color_map()
        argb = gcm.get(group_name) if group_name else None
        if not argb:
            return None
        qc = _argb_to_qcolor(argb)
        a = (qc.alpha() / 255.0) * self._TINT_FACTOR
        bg_r, bg_g, bg_b = 0x1e, 0x1e, 0x1e  # COLORS["bg"]
        return QColor(
            int(qc.red() * a + bg_r * (1 - a)),
            int(qc.green() * a + bg_g * (1 - a)),
            int(qc.blue() * a + bg_b * (1 - a)),
        )

    def _apply_row_group_color(self, row: int, group_name: str | None,
                               gcm: dict[str, str] | None = None,
                               table=None):
        """Set tinted group background on *row* of *table* (default: track table)."""
        if table is None:
            table = self._track_table
        table.apply_row_color(row, self._tint_group_color(group_name, gcm))

    # ── Groups tab row helpers ───────────────────────────────────────────

    def _set_groups_tab_row(self, row: int, name: str, color: str,
                            gain_linked: bool, daw_target: str = "",
                            match_method: str = "contains",
                            match_pattern: str = ""):
        """Populate one row in the session-local groups table."""
        name_item = QTableWidgetItem(name)
        self._groups_tab_table.setItem(row, 0, name_item)

        # Color dropdown with swatch icons
        color_combo = QComboBox()
        color_combo.setIconSize(QSize(16, 16))
        for cn in self._color_names_from_config():
            argb = self._color_argb_by_name(cn)
            icon = self._color_swatch_icon(argb) if argb else QIcon()
            color_combo.addItem(icon, cn)
        ci = color_combo.findText(color)
        if ci >= 0:
            color_combo.setCurrentIndex(ci)
        self._groups_tab_table.setCellWidget(row, 1, color_combo)

        # Gain-linked checkbox (centered)
        chk = QCheckBox()
        chk.setChecked(gain_linked)
        chk_container = QWidget()
        chk_layout = QHBoxLayout(chk_container)
        chk_layout.setContentsMargins(0, 0, 0, 0)
        chk_layout.setAlignment(Qt.AlignCenter)
        chk_layout.addWidget(chk)
        self._groups_tab_table.setCellWidget(row, 2, chk_container)

        # DAW Target name
        daw_item = QTableWidgetItem(daw_target)
        self._groups_tab_table.setItem(row, 3, daw_item)

        # Match method dropdown
        match_combo = QComboBox()
        match_combo.addItems(["contains", "regex"])
        mi = match_combo.findText(match_method)
        if mi >= 0:
            match_combo.setCurrentIndex(mi)
        match_combo.setProperty("_row", row)
        match_combo.currentTextChanged.connect(
            lambda _text, r=row: self._validate_groups_tab_pattern(r))
        self._groups_tab_table.setCellWidget(row, 4, match_combo)

        # Match pattern text
        pattern_item = QTableWidgetItem(match_pattern)
        self._groups_tab_table.setItem(row, 5, pattern_item)
        self._validate_groups_tab_pattern(row)

    def _populate_groups_tab(self):
        """Populate the groups tab table from self._session_groups."""
        self._groups_tab_table.blockSignals(True)
        self._groups_tab_table.setRowCount(0)
        self._groups_tab_table.setRowCount(len(self._session_groups))
        for row, g in enumerate(self._session_groups):
            self._set_groups_tab_row(
                row, g["name"], g.get("color", ""),
                g.get("gain_linked", False), g.get("daw_target", ""),
                g.get("match_method", "contains"),
                g.get("match_pattern", ""),
            )
        self._groups_tab_table.blockSignals(False)

    def _read_session_groups(self) -> list[dict]:
        """Read the session groups table back into a list of dicts."""
        groups: list[dict] = []
        for row in range(self._groups_tab_table.rowCount()):
            name_item = self._groups_tab_table.item(row, 0)
            if not name_item:
                continue
            name = name_item.text().strip()
            if not name:
                continue
            color_combo = self._groups_tab_table.cellWidget(row, 1)
            color = color_combo.currentText() if color_combo else ""
            chk_container = self._groups_tab_table.cellWidget(row, 2)
            gain_linked = False
            if chk_container:
                chk = chk_container.findChild(QCheckBox)
                if chk:
                    gain_linked = chk.isChecked()
            daw_item = self._groups_tab_table.item(row, 3)
            daw_target = daw_item.text().strip() if daw_item else ""
            match_combo = self._groups_tab_table.cellWidget(row, 4)
            match_method = match_combo.currentText() if match_combo else "contains"
            pattern_item = self._groups_tab_table.item(row, 5)
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

    def _unique_session_group_name(self, base: str = "New Group") -> str:
        """Generate a unique group name for the session groups table."""
        existing = self._group_names_in_table(self._groups_tab_table)
        if base not in existing:
            return base
        n = 2
        while f"{base} {n}" in existing:
            n += 1
        return f"{base} {n}"

    def _on_groups_tab_name_changed(self, row: int, col: int):
        """Handle cell edits in the groups tab (name, DAW target, pattern)."""
        if col == 3:
            # DAW Target changed — sync groups so auto-assign picks it up
            self._sync_session_groups()
            return
        if col == 5:
            # Match pattern changed — validate and sync
            self._validate_groups_tab_pattern(row)
            self._sync_session_groups()
            return
        if col != 0:
            return
        item = self._groups_tab_table.item(row, 0)
        if not item:
            return
        name = item.text().strip()
        others = self._group_names_in_table(self._groups_tab_table,
                                            exclude_row=row)
        if name in others:
            self._groups_tab_table.blockSignals(True)
            item.setText(self._unique_session_group_name(name))
            self._groups_tab_table.blockSignals(False)
        self._sync_session_groups()

    def _validate_groups_tab_pattern(self, row: int):
        """Validate the match pattern cell and set visual indicator.

        When match_method is "regex", tries to compile the pattern.
        Sets the cell foreground to green (valid / empty) or red (invalid).
        For "contains" mode, always shows default color.
        """
        match_combo = self._groups_tab_table.cellWidget(row, 4)
        pattern_item = self._groups_tab_table.item(row, 5)
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

    def _sync_session_groups(self):
        """Read the groups tab table into _session_groups and refresh combos."""
        self._session_groups = self._read_session_groups()
        self._refresh_group_combos()

    def _on_groups_tab_add(self):
        row = self._groups_tab_table.rowCount()
        self._groups_tab_table.insertRow(row)
        color_names = self._color_names_from_config()
        default_color = color_names[0] if color_names else ""
        self._set_groups_tab_row(
            row, self._unique_session_group_name(), default_color, False)
        self._groups_tab_table.scrollToBottom()
        self._groups_tab_table.editItem(self._groups_tab_table.item(row, 0))
        self._sync_session_groups()

    def _on_groups_tab_remove(self):
        row = self._groups_tab_table.currentRow()
        if row >= 0:
            self._groups_tab_table.removeRow(row)
            self._sync_session_groups()

    def _on_groups_tab_row_moved(self, logical: int, old_visual: int,
                                new_visual: int):
        """Handle drag-and-drop row reorder on the session groups table."""
        table = self._groups_tab_table
        vh = table.verticalHeader()
        n = table.rowCount()
        # Build visual order → logical index mapping
        visual_to_logical = sorted(range(n), key=lambda i: vh.visualIndex(i))
        ordered: list[dict] = []
        for log_idx in visual_to_logical:
            name_item = table.item(log_idx, 0)
            if not name_item:
                continue
            name = name_item.text().strip()
            if not name:
                continue
            cc = table.cellWidget(log_idx, 1)
            color = cc.currentText() if cc else ""
            chk_c = table.cellWidget(log_idx, 2)
            gl = False
            if chk_c:
                chk = chk_c.findChild(QCheckBox)
                if chk:
                    gl = chk.isChecked()
            daw_item = table.item(log_idx, 3)
            dt = daw_item.text().strip() if daw_item else ""
            mc = table.cellWidget(log_idx, 4)
            mm = mc.currentText() if mc else "contains"
            pi = table.item(log_idx, 5)
            mp = pi.text().strip() if pi else ""
            ordered.append({"name": name, "color": color,
                            "gain_linked": gl, "daw_target": dt,
                            "match_method": mm, "match_pattern": mp})
        # Reset visual mapping, repopulate
        vh.blockSignals(True)
        table.blockSignals(True)
        for i in range(n):
            vh.moveSection(vh.visualIndex(i), i)
        table.setRowCount(0)
        table.setRowCount(len(ordered))
        for row, entry in enumerate(ordered):
            self._set_groups_tab_row(
                row, entry["name"], entry["color"],
                entry["gain_linked"], entry.get("daw_target", ""),
                entry.get("match_method", "contains"),
                entry.get("match_pattern", ""))
        table.blockSignals(False)
        vh.blockSignals(False)
        self._session_groups = ordered
        self._refresh_group_combos()

    def _on_groups_tab_sort_az(self):
        groups = self._read_session_groups()
        groups.sort(key=lambda g: g["name"].lower())
        self._session_groups = groups
        self._populate_groups_tab()
        self._refresh_group_combos()

    def _on_groups_tab_reset(self):
        """Reset session groups to the active preset from preferences."""
        self._merge_groups_from_preset()

    def _merge_groups_from_preset(self):
        """Replace session groups with the active preset and name-match tracks."""
        presets = self._config.get("group_presets",
                                   build_defaults().get("group_presets", {}))
        preset = presets.get(self._active_session_preset,
                             presets.get("Default", []))
        new_groups = copy.deepcopy(preset)
        new_names = {g["name"].strip().lower() for g in new_groups}

        if self._session:
            for track in self._session.tracks:
                if track.group is not None:
                    if track.group.strip().lower() not in new_names:
                        track.group = None

        self._session_groups = new_groups
        self._populate_groups_tab()
        self._refresh_group_combos()
        self._populate_setup_table()

    # ── Auto-Group ────────────────────────────────────────────────────

    @Slot()
    def _on_auto_group(self):
        """Auto-assign groups to all tracks based on filename matching rules."""
        if not self._session:
            return
        ok_tracks = [t for t in self._session.tracks if t.status == "OK"]
        if not ok_tracks:
            return

        reply = QMessageBox.question(
            self, "Auto-Group",
            f"Auto-Group will reassign all {len(ok_tracks)} tracks "
            f"based on matching rules.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply != QMessageBox.Yes:
            return

        assigned = 0
        glm = self._gain_linked_map()
        gcm = self._group_color_map()
        grm = self._group_rank_map()

        self._track_table.setSortingEnabled(False)

        for track in ok_tracks:
            stem = os.path.splitext(track.filename)[0].lower()
            matched_group: str | None = None
            best_len = 0

            for g in self._session_groups:
                pattern = g.get("match_pattern", "").strip()
                if not pattern:
                    continue
                method = g.get("match_method", "contains")

                if method == "regex":
                    try:
                        m = re.search(pattern, stem, re.IGNORECASE)
                        if m:
                            span = m.end() - m.start()
                            if span > best_len:
                                best_len = span
                                matched_group = g["name"]
                    except re.error:
                        continue
                else:
                    # contains: comma-separated tokens — pick longest hit
                    tokens = [t.strip().lower() for t in pattern.split(",")
                              if t.strip()]
                    for tok in tokens:
                        if tok in stem and len(tok) > best_len:
                            best_len = len(tok)
                            matched_group = g["name"]

            # Apply the match (or clear to None)
            track.group = matched_group
            if matched_group:
                assigned += 1

            # Update table combo
            row = self._find_table_row(track.filename)
            if row >= 0:
                w = self._track_table.cellWidget(row, 6)
                if isinstance(w, BatchComboBox):
                    w.blockSignals(True)
                    if matched_group:
                        for ci in range(w.count()):
                            if w.itemData(ci, Qt.UserRole) == matched_group:
                                w.setCurrentIndex(ci)
                                break
                    else:
                        w.setCurrentIndex(0)  # (None)
                    w.blockSignals(False)

                # Update sort item
                display = (self._group_display_name(matched_group, glm)
                           if matched_group else self._GROUP_NONE_LABEL)
                rank = (grm.get(matched_group, len(grm))
                        if matched_group else len(grm))
                sort_item = self._track_table.item(row, 6)
                if sort_item:
                    sort_item.setText(display)
                    sort_item._sort_key = rank

                # Update row color
                self._apply_row_group_color(row, matched_group, gcm)

        self._track_table.setSortingEnabled(True)
        self._auto_fit_group_column()
        self._apply_linked_group_levels()
        self._populate_setup_table()

        self._status_bar.showMessage(
            f"Auto-Group: assigned {assigned} of {len(ok_tracks)} tracks")

    # ── Group preset switching (Analysis toolbar) ─────────────────────

    @Slot(str)
    def _on_group_preset_changed(self, preset_name: str):
        """Switch the active group preset from the Analysis toolbar combo."""
        presets = self._config.get("group_presets",
                                   build_defaults().get("group_presets", {}))
        if preset_name not in presets:
            return
        self._active_session_preset = preset_name
        self._merge_groups_from_preset()

    # ── Config preset switching (Analysis toolbar) ────────────────────

    @Slot(str)
    def _on_toolbar_config_preset_changed(self, name: str):
        """Switch the active config preset from the Analysis toolbar combo."""
        presets = self._config.get("config_presets",
                                   build_defaults().get("config_presets", {}))
        if name not in presets:
            return

        if self._session is not None:
            ans = QMessageBox.question(
                self, "Switch config preset?",
                f"Switching to \u201c{name}\u201d will overwrite your "
                "session config and re-analyze.\n\n"
                "Group assignments will be preserved.\n\n"
                "Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                # Revert combo to the current preset
                self._config_preset_combo.blockSignals(True)
                self._config_preset_combo.setCurrentText(
                    self._active_config_preset_name)
                self._config_preset_combo.blockSignals(False)
                return

        self._active_config_preset_name = name
        self._config.setdefault("app", {})["active_config_preset"] = name
        save_config(self._config)

        if self._session is not None:
            self._session_config = None  # re-init from new preset
            self._on_analyze()

    # ── Group column (col 6) ────────────────────────────────────────────

    _GROUP_NONE_LABEL = "(None)"
    _LINK_INDICATOR = " 🔗"

    def _group_combo_items(self) -> list[str]:
        """Return the items list for Group combo boxes."""
        return [self._GROUP_NONE_LABEL] + [
            g["name"] for g in self._session_groups]

    def _gain_linked_map(self) -> dict[str, bool]:
        """Return {group_name: gain_linked} for all session groups."""
        return {g["name"]: g.get("gain_linked", False)
                for g in self._session_groups}

    def _group_display_name(self, name: str,
                            glm: dict[str, bool] | None = None) -> str:
        """Return display name with link indicator if gain-linked."""
        if glm is None:
            glm = self._gain_linked_map()
        if glm.get(name, False):
            return name + self._LINK_INDICATOR
        return name

    def _group_rank_map(self) -> dict[str, int]:
        """Return {group_name: position_index} for sort-by-rank ordering."""
        return {g["name"]: i for i, g in enumerate(self._session_groups)}

    def _group_color_map(self) -> dict[str, str]:
        """Return {group_name: argb_hex} for all session groups."""
        result: dict[str, str] = {}
        for g in self._session_groups:
            color_name = g.get("color", "")
            argb = self._color_argb_by_name(color_name)
            if argb:
                result[g["name"]] = argb
        return result

    def _create_group_combo(self, row: int, track):
        """Create and install a Group combo in column 6."""
        glm = self._gain_linked_map()
        display = self._group_display_name(track.group, glm) if track.group else self._GROUP_NONE_LABEL
        grm = self._group_rank_map()
        rank = grm.get(track.group, len(grm)) if track.group else len(grm)
        sort_item = _SortableItem(display, rank)
        self._track_table.setItem(row, 6, sort_item)

        combo = BatchComboBox()
        combo.setIconSize(QSize(16, 16))
        gcm = self._group_color_map()
        combo.addItem(self._GROUP_NONE_LABEL)
        combo.setItemData(0, None, Qt.UserRole)
        for i, gname in enumerate([g["name"] for g in self._session_groups]):
            disp = self._group_display_name(gname, glm)
            argb = gcm.get(gname)
            if argb:
                combo.addItem(self._color_swatch_icon(argb), disp)
            else:
                combo.addItem(disp)
            combo.setItemData(i + 1, gname, Qt.UserRole)
        combo.blockSignals(True)
        # Find item by UserRole (clean name)
        for ci in range(combo.count()):
            if combo.itemData(ci, Qt.UserRole) == track.group:
                combo.setCurrentIndex(ci)
                break
        combo.blockSignals(False)
        combo.setProperty("track_filename", track.filename)
        combo.setStyleSheet(
            f"QComboBox {{ color: {COLORS['text']}; }}"
        )
        combo.textActivated.connect(
            lambda text, c=combo: self._on_group_changed(text, c))
        self._track_table.setCellWidget(row, 6, combo)

    def _on_group_changed(self, text: str, combo=None):
        """Handle user changing the Group dropdown."""
        if combo is None:
            combo = self.sender()
        if not combo or not self._session:
            return
        fname = combo.property("track_filename")
        if not fname:
            return
        track = next(
            (t for t in self._session.tracks if t.filename == fname), None
        )
        if not track:
            return

        # Read clean group name from UserRole
        new_group = combo.currentData(Qt.UserRole)
        display = text  # display text (with link indicator)

        # Batch path: synchronous — no reanalysis needed
        if getattr(combo, 'batch_mode', False) or combo.property("_batch_mode"):
            combo.setProperty("_batch_mode", False)
            combo.batch_mode = False
            track.group = new_group
            batch_keys = self._track_table.batch_selected_keys()
            track_map = {t.filename: t for t in self._session.tracks}
            gcm = self._group_color_map()
            grm = self._group_rank_map()
            rank = grm.get(new_group, len(grm)) if new_group else len(grm)
            self._track_table.setSortingEnabled(False)
            for bfname in batch_keys:
                bt = track_map.get(bfname)
                if not bt or bt.status != "OK":
                    continue
                bt.group = new_group
                row = self._find_table_row(bfname)
                if row >= 0:
                    w = self._track_table.cellWidget(row, 6)
                    if isinstance(w, BatchComboBox):
                        w.blockSignals(True)
                        # Find matching item by UserRole
                        for ci in range(w.count()):
                            if w.itemData(ci, Qt.UserRole) == new_group:
                                w.setCurrentIndex(ci)
                                break
                        w.blockSignals(False)
                    sort_item = self._track_table.item(row, 6)
                    if sort_item:
                        sort_item.setText(display)
                        sort_item._sort_key = rank
                    self._apply_row_group_color(row, new_group, gcm)
            self._track_table.setSortingEnabled(True)
            self._track_table.restore_selection(batch_keys)
            self._auto_fit_group_column()
            self._apply_linked_group_levels()
        else:
            if track.group == new_group:
                return
            track.group = new_group
            # Update sort item + row color
            grm = self._group_rank_map()
            rank = grm.get(new_group, len(grm)) if new_group else len(grm)
            row = self._find_table_row(fname)
            if row >= 0:
                sort_item = self._track_table.item(row, 6)
                if sort_item:
                    sort_item.setText(display)
                    sort_item._sort_key = rank
                self._apply_row_group_color(row, new_group)
            self._auto_fit_group_column()
            self._apply_linked_group_levels()

    def _refresh_group_combos(self):
        """Refresh the items in all Group combo boxes from _session_groups."""
        gcm = self._group_color_map()
        grm = self._group_rank_map()
        glm = self._gain_linked_map()
        for row in range(self._track_table.rowCount()):
            w = self._track_table.cellWidget(row, 6)
            if isinstance(w, BatchComboBox):
                # Read clean group name via UserRole
                old_group = w.currentData(Qt.UserRole)
                w.blockSignals(True)
                w.clear()
                w.setIconSize(QSize(16, 16))
                w.addItem(self._GROUP_NONE_LABEL)
                w.setItemData(0, None, Qt.UserRole)
                for i, gname in enumerate(
                        [g["name"] for g in self._session_groups]):
                    disp = self._group_display_name(gname, glm)
                    argb = gcm.get(gname)
                    if argb:
                        w.addItem(self._color_swatch_icon(argb), disp)
                    else:
                        w.addItem(disp)
                    w.setItemData(i + 1, gname, Qt.UserRole)
                # Restore selection by UserRole match
                restored = False
                if old_group is not None:
                    for ci in range(w.count()):
                        if w.itemData(ci, Qt.UserRole) == old_group:
                            w.setCurrentIndex(ci)
                            restored = True
                            break
                if not restored:
                    w.setCurrentIndex(0)  # (None)
                    # Also clear the track's group assignment
                    fname = w.property("track_filename")
                    if fname and self._session:
                        track = next(
                            (t for t in self._session.tracks
                             if t.filename == fname), None)
                        if track:
                            track.group = None
                w.blockSignals(False)
                # Update sort key, display text + row color
                gname = w.currentData(Qt.UserRole)
                sort_item = self._track_table.item(row, 6)
                if sort_item:
                    rank = grm.get(gname, len(grm)) if gname else len(grm)
                    sort_item._sort_key = rank
                    sort_item.setText(w.currentText())
                self._apply_row_group_color(row, gname, gcm)

        self._auto_fit_group_column()
        self._apply_linked_group_levels()

    # ── Linked group levels ──────────────────────────────────────────────

    def _apply_linked_group_levels(self):
        """Apply group levels for gain-linked groups and update fader offsets.

        1. Restore every track's ``gain_db`` to its ``original_gain_db``.
        2. For gain-linked groups, set all members to the group minimum.
        3. Recompute ``fader_offset`` using the stored anchor offset.
        4. Update the gain spin-boxes and the Session Setup table.
        """
        if not self._session or not self._session.processors:
            return

        glm = self._gain_linked_map()
        linked_names = {name for name, linked in glm.items() if linked}

        for proc in self._session.processors:
            pid = proc.id
            # 1. Restore originals
            for track in self._session.tracks:
                if track.status != "OK":
                    continue
                pr = track.processor_results.get(pid)
                if pr is None or pr.classification == "Silent":
                    continue
                if "original_gain_db" not in pr.data:
                    pr.data["original_gain_db"] = pr.gain_db
                pr.gain_db = pr.data["original_gain_db"]

            # 2. Apply group levels for linked groups
            by_group: dict[str, list] = {}
            for track in self._session.tracks:
                if track.status != "OK" or track.group is None:
                    continue
                pr = track.processor_results.get(pid)
                if pr is None or pr.classification == "Silent":
                    continue
                by_group.setdefault(track.group, []).append(track)

            for gname, members in by_group.items():
                if gname not in linked_names:
                    continue
                orig = [m.processor_results[pid].data["original_gain_db"]
                        for m in members]
                group_gain = min(orig) if orig else 0.0
                for m in members:
                    m.processor_results[pid].gain_db = float(group_gain)

            # 3. Recompute fader offsets with headroom rebalancing
            valid = []
            for track in self._session.tracks:
                if track.status != "OK":
                    continue
                pr = track.processor_results.get(pid)
                if pr is None:
                    continue
                if pr.classification == "Silent":
                    pr.data["fader_offset"] = 0.0
                else:
                    pr.data["fader_offset"] = -float(pr.gain_db)
                    valid.append(track)

            # Headroom rebalancing
            ceiling = self._session.config.get("_fader_ceiling_db", 12.0)
            headroom = self._session.config.get("fader_headroom_db", 8.0)
            max_allowed = ceiling - headroom
            rebalance_shift = 0.0
            if headroom > 0.0 and valid:
                fader_offsets = [
                    t.processor_results[pid].data.get("fader_offset", 0.0)
                    for t in valid
                ]
                max_fader = max(fader_offsets)
                if max_fader > max_allowed:
                    rebalance_shift = max_fader - max_allowed
                    for track in valid:
                        pr = track.processor_results.get(pid)
                        if pr:
                            pr.data["fader_offset"] -= rebalance_shift
                            pr.data["fader_rebalance_shift"] = rebalance_shift
            self._session.config[f"_fader_rebalance_{pid}"] = rebalance_shift

            # Anchor-track adjustment
            anchor_offset = self._session.config.get(
                f"_anchor_offset_{pid}", 0.0)
            if anchor_offset != 0.0:
                for track in valid:
                    pr = track.processor_results.get(pid)
                    if pr:
                        pr.data["fader_offset"] = pr.data.get("fader_offset", 0.0) - anchor_offset

        # 4. Update UI
        self._track_table.setSortingEnabled(False)
        for row in range(self._track_table.rowCount()):
            fname_item = self._track_table.item(row, 0)
            if not fname_item:
                continue
            fname = fname_item.text()
            track = next(
                (t for t in self._session.tracks if t.filename == fname), None)
            if not track or track.status != "OK":
                continue
            pr = next(iter(track.processor_results.values()), None)
            if not pr:
                continue
            new_gain = pr.gain_db
            spin = self._track_table.cellWidget(row, 4)
            if isinstance(spin, QDoubleSpinBox):
                spin.blockSignals(True)
                spin.setValue(new_gain)
                spin.blockSignals(False)
            gain_sort = self._track_table.item(row, 4)
            if gain_sort:
                gain_sort.setText(f"{new_gain:+.1f}")
                gain_sort._sort_key = new_gain
        self._track_table.setSortingEnabled(True)
        self._populate_setup_table()

        # Refresh the File detail tab so it reflects the updated gain
        if self._current_track and self._current_track.status == "OK":
            self._refresh_file_tab(self._current_track)
