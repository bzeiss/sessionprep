"""DAW integration mixin: processors, fetch, transfer, folder tree, assignments."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Slot, QSize
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sessionpreplib.daw_processors import create_runtime_daw_processors

from ..tracks.table_widgets import (
    _FolderDropTree, _SetupDragTable,
    _SETUP_RIGHT_PLACEHOLDER, _SETUP_RIGHT_TREE,
)
from ..theme import COLORS, PT_DEFAULT_COLORS
from ..widgets import ProgressPanel
from ..analysis.worker import DawCheckWorker, DawFetchWorker, DawTransferWorker


class DawMixin:
    """DAW integration: processors, fetch, transfer, folder tree, assignments.

    Mixed into ``SessionPrepWindow`` — not meant to be used standalone.
    """

    # ── Setup page builder ───────────────────────────────────────────────

    def _build_setup_page(self) -> QWidget:
        """Build the Session Setup phase page with its own toolbar."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Setup toolbar (embedded in page)
        self._setup_toolbar = QToolBar("Session Setup")
        self._setup_toolbar.setIconSize(QSize(16, 16))
        self._setup_toolbar.setMovable(False)
        self._setup_toolbar.setFloatable(False)

        # ── Left: DAW processor selection + status label ────────────────
        self._daw_combo = QComboBox()
        self._daw_combo.setMinimumWidth(140)
        self._setup_toolbar.addWidget(self._daw_combo)

        self._daw_check_label = QLabel("")
        self._daw_check_label.setContentsMargins(6, 0, 0, 0)
        self._daw_check_label.setMaximumWidth(260)
        self._setup_toolbar.addWidget(self._daw_check_label)

        self._setup_toolbar.addSeparator()

        # ── Use Processed checkbox ─────────────────────────────────────
        self._use_processed_cb = QCheckBox("Use Processed")
        self._use_processed_cb.setLayoutDirection(Qt.RightToLeft)
        self._use_processed_cb.setEnabled(False)
        self._use_processed_cb.toggled.connect(self._on_use_processed_toggled)
        self._setup_toolbar.addWidget(self._use_processed_cb)

        # ── Spacer ─────────────────────────────────────────────────────
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._setup_toolbar.addWidget(spacer)

        # ── Right: lifecycle actions ───────────────────────────────────
        self._fetch_action = QAction("Fetch", self)
        self._fetch_action.setEnabled(False)
        self._fetch_action.triggered.connect(self._on_daw_fetch)
        self._setup_toolbar.addAction(self._fetch_action)

        self._auto_assign_action = QAction("Auto-Assign", self)
        self._auto_assign_action.setEnabled(False)
        self._auto_assign_action.triggered.connect(self._on_auto_assign)
        self._setup_toolbar.addAction(self._auto_assign_action)

        self._transfer_action = QAction("Transfer", self)
        self._transfer_action.setEnabled(False)
        self._transfer_action.triggered.connect(self._on_daw_transfer)
        self._setup_toolbar.addAction(self._transfer_action)

        self._sync_action = QAction("Sync", self)
        self._sync_action.setEnabled(False)
        self._setup_toolbar.addAction(self._sync_action)

        # Populate combo after ALL toolbar widgets exist, then connect signal
        self._populate_daw_combo()
        self._daw_combo.currentIndexChanged.connect(self._on_daw_combo_changed)

        layout.addWidget(self._setup_toolbar)

        # Splitter: track table (left) + routing panel placeholder (right)
        self._setup_splitter = setup_splitter = QSplitter(Qt.Horizontal)

        # ── Left: track table ─────────────────────────────────────────────
        self._setup_table = _SetupDragTable()
        self._setup_table.setColumnCount(7)
        self._setup_table.setHorizontalHeaderLabels(
            ["", "File", "Track Name", "Ch", "Clip Gain", "Fader Gain", "Group"]
        )
        self._setup_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._setup_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self._setup_table.setEditTriggers(QTableWidget.DoubleClicked)
        self._setup_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._setup_table.customContextMenuRequested.connect(
            self._on_setup_table_context_menu)
        self._setup_table.itemChanged.connect(
            self._on_setup_table_item_changed)
        self._setup_table_populating = False
        self._setup_table.verticalHeader().setVisible(False)
        self._setup_table.setMinimumWidth(300)
        self._setup_table.setShowGrid(True)
        self._setup_table.setAlternatingRowColors(True)
        self._setup_table.setSortingEnabled(True)

        sh = self._setup_table.horizontalHeader()
        sh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        sh.setSectionResizeMode(0, QHeaderView.Fixed)
        sh.resizeSection(0, 24)
        sh.setSectionResizeMode(1, QHeaderView.Stretch)
        sh.setSectionResizeMode(2, QHeaderView.Interactive)
        sh.setSectionResizeMode(3, QHeaderView.Fixed)
        sh.setSectionResizeMode(4, QHeaderView.Interactive)
        sh.setSectionResizeMode(5, QHeaderView.Interactive)
        sh.setSectionResizeMode(6, QHeaderView.Interactive)
        sh.resizeSection(2, 150)
        sh.resizeSection(3, 30)
        sh.resizeSection(4, 90)
        sh.resizeSection(5, 90)
        sh.resizeSection(6, 110)

        setup_splitter.addWidget(self._setup_table)

        # ── Right: stacked widget (placeholder / folder tree) ─────────────
        self._setup_right_stack = QStackedWidget()

        # Page 0: placeholder
        right_placeholder = QWidget()
        right_layout = QVBoxLayout(right_placeholder)
        right_layout.setContentsMargins(40, 0, 40, 0)
        right_layout.addStretch(2)
        placeholder_label = QLabel("Connect to a DAW to configure routing")
        placeholder_label.setAlignment(Qt.AlignCenter)
        placeholder_label.setStyleSheet(
            f"color: {COLORS['dim']}; font-size: 13pt;")
        right_layout.addWidget(placeholder_label)
        right_layout.addStretch(3)
        self._setup_right_stack.addWidget(right_placeholder)

        # Page 1: folder tree + transfer progress panel
        tree_page = QWidget()
        tree_page_layout = QVBoxLayout(tree_page)
        tree_page_layout.setContentsMargins(0, 0, 0, 0)
        tree_page_layout.setSpacing(0)

        self._folder_tree = _FolderDropTree()
        self._folder_tree.setHeaderLabels(["Folder / Track"])
        self._folder_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self._folder_tree.setAlternatingRowColors(True)
        # Match visual size to the setup table; semi-transparent selection
        self._folder_tree.setStyleSheet(
            "QTreeWidget { font-size: 10pt; }"
            "QTreeWidget::item { min-height: 22px; }"
            "QTreeWidget::item:selected {"
            "  background-color: rgba(42, 109, 181, 128);"
            "}"
        )
        self._folder_tree.tracks_dropped.connect(self._assign_tracks_to_folder)
        self._folder_tree.tracks_unassigned.connect(self._unassign_tracks)
        tree_page_layout.addWidget(self._folder_tree, 1)

        # Transfer progress panel (hidden by default)
        self._transfer_progress = ProgressPanel()
        tree_page_layout.addWidget(self._transfer_progress)

        self._setup_right_stack.addWidget(tree_page)

        self._setup_right_stack.setCurrentIndex(_SETUP_RIGHT_PLACEHOLDER)

        setup_splitter.addWidget(self._setup_right_stack)
        setup_splitter.setStretchFactor(0, 3)
        setup_splitter.setStretchFactor(1, 2)
        setup_splitter.setSizes([620, 480])

        layout.addWidget(setup_splitter, 1)

        return page

    # ── DAW processor helpers ─────────────────────────────────────────────

    def _configure_daw_processors(self):
        """Rebuild DAW processor list from the current flat config.

        Uses the runtime factory so DAWProject templates are expanded
        into individual processor instances.
        """
        flat = self._flat_config()
        self._daw_processors = create_runtime_daw_processors(flat)

    def _populate_daw_combo(self):
        """Fill the DAW dropdown with enabled processors."""
        self._daw_combo.blockSignals(True)
        self._daw_combo.clear()
        for i, dp in enumerate(self._daw_processors):
            if dp.enabled:
                self._daw_combo.addItem(dp.name, i)
        self._daw_combo.blockSignals(False)
        if self._daw_combo.count() > 0:
            self._on_daw_combo_changed(0)
        else:
            self._active_daw_processor = None

    def _update_daw_lifecycle_buttons(self):
        """Enable/disable Fetch/Transfer/Sync based on active processor state."""
        has_processor = self._active_daw_processor is not None
        self._fetch_action.setEnabled(has_processor)
        dp_id = self._active_daw_processor.id if has_processor else None
        dp_state = (
            self._session.daw_state.get(dp_id, {})
            if self._session and dp_id else {}
        )
        has_folders = bool(dp_state.get("folders"))
        has_assignments = bool(dp_state.get("assignments"))
        self._auto_assign_action.setEnabled(has_folders)
        self._transfer_action.setEnabled(has_processor and has_assignments)
        self._sync_action.setEnabled(False)

    @Slot(int)
    def _on_daw_combo_changed(self, index: int):
        if index < 0 or index >= self._daw_combo.count():
            self._active_daw_processor = None
        else:
            proc_idx = self._daw_combo.itemData(index)
            self._active_daw_processor = self._daw_processors[proc_idx]
        self._daw_check_label.setText("")
        self._update_daw_lifecycle_buttons()

    def _run_daw_check_then(self, on_success):
        """Run a connectivity check; on success call *on_success*."""
        if not self._active_daw_processor:
            return
        self._pending_after_check = on_success
        self._daw_check_label.setText("Connecting\u2026")
        self._daw_check_label.setStyleSheet(f"color: {COLORS['dim']};")
        self._daw_check_worker = DawCheckWorker(self._active_daw_processor)
        self._daw_check_worker.result.connect(self._on_daw_check_result)
        self._daw_check_worker.start()

    @Slot(bool, str)
    def _on_daw_check_result(self, ok: bool, message: str):
        self._daw_check_worker = None
        if ok:
            self._daw_check_label.setText(message)
            self._daw_check_label.setStyleSheet(f"color: {COLORS['clean']};")
            cb = self._pending_after_check
            self._pending_after_check = None
            if cb:
                cb()
        else:
            self._daw_check_label.setText("Connection failed")
            self._daw_check_label.setStyleSheet(f"color: {COLORS['problems']};")
            self._pending_after_check = None
            QMessageBox.warning(
                self, "Connection Failed",
                f"{self._active_daw_processor.name} connection could "
                f"not be established.\n\n{message}")
        self._update_daw_lifecycle_buttons()

    # ── DAW Fetch + Folder Tree ───────────────────────────────────────────

    @Slot()
    def _on_daw_fetch(self):
        if not self._active_daw_processor or not self._session:
            return
        self._fetch_action.setEnabled(False)
        self._run_daw_check_then(self._do_daw_fetch)

    def _do_daw_fetch(self):
        """Actually start the fetch (called after successful connectivity check)."""
        self._status_bar.showMessage("Fetching folder structure\u2026")
        self._daw_fetch_worker = DawFetchWorker(
            self._active_daw_processor, self._session)
        self._daw_fetch_worker.result.connect(self._on_daw_fetch_result)
        self._daw_fetch_worker.start()

    @Slot(bool, str, object)
    def _on_daw_fetch_result(self, ok: bool, message: str, session):
        self._daw_fetch_worker = None
        self._fetch_action.setEnabled(True)
        if ok and session is not None:
            self._session = session
            self._populate_folder_tree()
            self._setup_right_stack.setCurrentIndex(_SETUP_RIGHT_TREE)
            self._populate_setup_table()
            self._status_bar.showMessage(message)
        else:
            self._status_bar.showMessage(f"Fetch failed: {message}")
        self._update_daw_lifecycle_buttons()

    # ── Use Processed checkbox ──────────────────────────────────────────

    @Slot(bool)
    def _on_use_processed_toggled(self, checked: bool):
        if self._session:
            self._session.config["_use_processed"] = checked
        self._update_use_processed_action()

    def _update_use_processed_action(self):
        """Update the Use Processed checkbox enabled state and stale indicator."""
        if not self._session:
            self._use_processed_cb.setEnabled(False)
            self._use_processed_cb.setText("Use Processed")
            return

        state = self._session.prepare_state
        has_prepared = state in ("ready", "stale")
        self._use_processed_cb.setEnabled(has_prepared)

        if state == "stale" and self._use_processed_cb.isChecked():
            self._use_processed_cb.setText("Use Processed (!)")
        else:
            self._use_processed_cb.setText("Use Processed")

    # ── DAW Transfer ─────────────────────────────────────────────────────

    @Slot()
    def _on_daw_transfer(self):
        if not self._active_daw_processor or not self._session:
            return
        self._transfer_action.setEnabled(False)
        self._fetch_action.setEnabled(False)
        self._run_daw_check_then(self._do_daw_transfer)

    def _do_daw_transfer(self):
        """Actually start the transfer (called after successful connectivity check)."""
        if not self._active_daw_processor or not self._session:
            return

        output_folder = self._config.get("app", {}).get(
            "phase2_output_folder", "sp_02_processed")

        # Refresh pipeline config from current session widgets so that
        # processor enabled/disabled changes made after analysis take effect.
        self._session.config.update(self._flat_config())
        # Inject GUI config (groups + colors) into session.config so
        # transfer() can resolve group → color ARGB
        self._session.config.setdefault("gui", {})["groups"] = list(
            self._session_groups)
        colors = self._config.get("colors", PT_DEFAULT_COLORS)
        self._session.config["gui"]["colors"] = colors
        # Keep source dir / output folder in config for processor.resolve_output_path()
        self._session.config["_source_dir"] = self._source_dir
        self._session.config["_output_folder"] = output_folder

        # ── Let the processor decide the output path (shows dialog if needed) ─
        output_path = self._active_daw_processor.resolve_output_path(
            self._session, self)
        if output_path is None:
            self._update_daw_lifecycle_buttons()
            return

        dp_name = self._active_daw_processor.name
        self._status_bar.showMessage(f"Transferring to {dp_name}\u2026")
        self._transfer_progress.start("Preparing\u2026")

        self._daw_transfer_worker = DawTransferWorker(
            self._active_daw_processor, self._session, output_path)
        self._daw_transfer_worker.progress.connect(self._on_transfer_progress)
        self._daw_transfer_worker.progress_value.connect(
            self._on_transfer_progress_value)
        self._daw_transfer_worker.result.connect(self._on_daw_transfer_result)
        self._daw_transfer_worker.start()

    @Slot(str)
    def _on_transfer_progress(self, message: str):
        self._transfer_progress.set_message(message)
        self._status_bar.showMessage(message)

    @Slot(int, int)
    def _on_transfer_progress_value(self, current: int, total: int):
        self._transfer_progress.set_progress(current, total)

    @Slot(bool, str, object)
    def _on_daw_transfer_result(self, ok: bool, message: str, results):
        self._daw_transfer_worker = None
        self._update_daw_lifecycle_buttons()
        if ok:
            self._transfer_progress.finish(message)
            self._status_bar.showMessage(message)
        else:
            self._transfer_progress.fail(message)
            self._status_bar.showMessage(f"Transfer failed: {message}")

    # ── Folder tree ──────────────────────────────────────────────────────

    def _populate_folder_tree(self):
        """Build the folder tree from the active DAW processor's daw_state."""
        self._folder_tree.clear()
        if not self._session or not self._active_daw_processor:
            return
        dp_state = self._session.daw_state.get(self._active_daw_processor.id, {})
        folders = dp_state.get("folders", [])
        assignments = dp_state.get("assignments", {})

        # Build lookup: id -> folder dict
        folder_map = {f["id"]: f for f in folders}
        # Build children map: parent_id -> [child folders]
        children_map: dict[str | None, list] = {}
        for f in folders:
            parent = f["parent_id"]
            children_map.setdefault(parent, []).append(f)

        # Sort children by index
        for k in children_map:
            children_map[k].sort(key=lambda f: f["index"])

        # Build inverse assignments: folder_id -> [filenames]
        # Use track_order for stable ordering, fall back to sorted
        track_order = dp_state.get("track_order", {})
        folder_tracks: dict[str, list[str]] = {}
        for fname, fid in assignments.items():
            folder_tracks.setdefault(fid, []).append(fname)
        for fid, fnames in folder_tracks.items():
            order = track_order.get(fid, [])
            order_map = {n: i for i, n in enumerate(order)}
            fnames.sort(key=lambda n: (order_map.get(n, len(order)), n))

        # Group color map for track items
        gcm = self._group_color_map()
        track_map = {}
        entry_map: dict[str, Any] = {}
        if self._session:
            track_map = {t.filename: t for t in self._session.output_tracks}
            entry_map = {e.entry_id: e for e in self._session.transfer_manifest}

        # Icons – small colored squares to distinguish folder types
        def _folder_icon(color_hex: str) -> QIcon:
            sz = 14
            pix = QPixmap(sz, sz)
            pix.fill(Qt.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing)
            p.setBrush(QColor(color_hex))
            p.setPen(QPen(QColor(color_hex).darker(130), 1))
            p.drawRoundedRect(1, 1, sz - 2, sz - 2, 3, 3)
            p.end()
            return QIcon(pix)

        routing_icon = _folder_icon(COLORS["information"])  # blue
        basic_icon = _folder_icon(COLORS["dim"])             # grey

        def add_folder(parent_widget, folder):
            item = QTreeWidgetItem(parent_widget)
            item.setText(0, folder["name"])
            item.setData(0, Qt.UserRole, folder["id"])
            item.setData(0, Qt.UserRole + 1, "folder")
            if folder["folder_type"] == "routing":
                item.setIcon(0, routing_icon)
            else:
                item.setIcon(0, basic_icon)
            item.setFlags(
                (item.flags() | Qt.ItemIsDropEnabled)
                & ~Qt.ItemIsDragEnabled)

            # Add assigned tracks as children
            for fname in folder_tracks.get(folder["id"], []):
                track_item = QTreeWidgetItem(item)
                # Show daw_track_name alongside source filename
                te = entry_map.get(fname)
                if te and te.daw_track_name != te.output_filename:
                    import os
                    stem = os.path.splitext(te.daw_track_name)[0]
                    track_item.setText(0, f"{stem}  \u2190 {te.output_filename}")
                else:
                    track_item.setText(0, fname)
                track_item.setData(0, Qt.UserRole, fname)
                track_item.setData(0, Qt.UserRole + 1, "track")
                track_item.setFlags(
                    (track_item.flags() | Qt.ItemIsDragEnabled)
                    & ~Qt.ItemIsDropEnabled)
                # Row background from group color (matches table tint)
                out_fn = te.output_filename if te else fname
                tc = track_map.get(out_fn)
                if tc and tc.group:
                    tint = self._tint_group_color(tc.group, gcm)
                    if tint:
                        track_item.setBackground(0, tint)

            # Recurse into child folders
            for child in children_map.get(folder["id"], []):
                add_folder(item, child)

            item.setExpanded(True)

        # Top-level folders (no parent)
        for f in children_map.get(None, []):
            add_folder(self._folder_tree, f)

        self._folder_tree.expandAll()

    # ── Track assignments ────────────────────────────────────────────────

    @Slot(list, str, int)
    def _assign_tracks_to_folder(self, filenames: list[str],
                                  folder_id: str, insert_index: int = -1):
        """Assign session tracks to a DAW folder in the local data model."""
        if not self._session or not self._active_daw_processor:
            return
        dp_state = self._session.daw_state.setdefault(self._active_daw_processor.id, {})
        assignments = dp_state.setdefault("assignments", {})
        track_order = dp_state.setdefault("track_order", {})

        # Remove tracks from their previous folder order lists
        for fname in filenames:
            old_fid = assignments.get(fname)
            if old_fid and old_fid in track_order:
                try:
                    track_order[old_fid].remove(fname)
                except ValueError:
                    pass

        # Update assignment mapping
        for fname in filenames:
            assignments[fname] = folder_id

        # Insert into track_order for the target folder
        order = track_order.setdefault(folder_id, [])
        # Remove duplicates already in the list
        for fname in filenames:
            try:
                order.remove(fname)
            except ValueError:
                pass
        if insert_index < 0 or insert_index >= len(order):
            order.extend(filenames)
        else:
            for i, fname in enumerate(filenames):
                order.insert(insert_index + i, fname)

        self._populate_folder_tree()
        self._populate_setup_table()
        self._update_daw_lifecycle_buttons()

    @Slot(list)
    def _unassign_tracks(self, filenames: list[str]):
        """Remove track-to-folder assignments and refresh UI."""
        if not self._session or not self._active_daw_processor:
            return
        dp_state = self._session.daw_state.get(self._active_daw_processor.id)
        if not dp_state:
            return
        assignments = dp_state.get("assignments", {})
        track_order = dp_state.get("track_order", {})
        for fname in filenames:
            fid = assignments.pop(fname, None)
            if fid and fid in track_order:
                try:
                    track_order[fid].remove(fname)
                except ValueError:
                    pass
        self._populate_folder_tree()
        self._populate_setup_table()
        self._update_daw_lifecycle_buttons()

    # ── Auto-Assign ──────────────────────────────────────────────────────

    @Slot()
    def _on_auto_assign(self):
        """Auto-assign unassigned tracks to folders based on group DAW targets."""
        if not self._session or not self._active_daw_processor:
            return
        dp_id = self._active_daw_processor.id
        dp_state = self._session.daw_state.get(dp_id, {})
        folders = dp_state.get("folders", [])
        assignments = dp_state.get("assignments", {})
        if not folders:
            return

        # Build folder name lookup: lowered+trimmed name → folder id
        folder_by_name: dict[str, str] = {}
        for f in folders:
            key = f["name"].strip().lower()
            if key and key not in folder_by_name:
                folder_by_name[key] = f["id"]

        # Build group → daw_target lookup from session groups
        group_target: dict[str, str] = {}
        for g in self._session_groups:
            dt = g.get("daw_target", "").strip()
            if dt:
                group_target[g["name"]] = dt.lower()

        if not group_target:
            QMessageBox.information(
                self, "Auto-Assign",
                "No DAW targets are configured.\n\n"
                "Open the Groups tab and set a DAW Target for each "
                "group that should be mapped to a DAW folder.")
            return

        # Collect assignments: folder_id → [entry_ids]
        batch: dict[str, list[str]] = {}
        no_group = 0
        no_target = 0
        no_folder = 0
        already_assigned = 0
        for entry in self._session.transfer_manifest:
            # Skip already-assigned entries
            if entry.entry_id in assignments:
                already_assigned += 1
                continue
            # Skip entries without a group or without a DAW target
            if not entry.group:
                no_group += 1
                continue
            target_key = group_target.get(entry.group)
            if not target_key:
                no_target += 1
                continue
            folder_id = folder_by_name.get(target_key)
            if not folder_id:
                no_folder += 1
                continue
            batch.setdefault(folder_id, []).append(entry.entry_id)

        if not batch:
            reasons: list[str] = []
            if no_group:
                reasons.append(
                    f"\u2022 {no_group} track(s) have no group assigned.")
            if no_target:
                reasons.append(
                    f"\u2022 {no_target} track(s) belong to groups without "
                    "a DAW target.")
            if no_folder:
                reasons.append(
                    f"\u2022 {no_folder} track(s) have DAW targets that "
                    "don\u2019t match any fetched folder name.")
            if already_assigned:
                reasons.append(
                    f"\u2022 {already_assigned} track(s) are already "
                    "assigned.")
            detail = "\n".join(reasons) if reasons else (
                "No unassigned tracks found.")
            QMessageBox.information(
                self, "Auto-Assign",
                f"Nothing to assign.\n\n{detail}")
            return

        # Apply assignments in bulk
        total = 0
        for folder_id, fnames in batch.items():
            self._assign_tracks_to_folder(fnames, folder_id)
            total += len(fnames)

        self._status_bar.showMessage(
            f"Auto-Assign: assigned {total} track(s) to "
            f"{len(batch)} folder(s).")

    # ── Setup table context menu ─────────────────────────────────────────

    @Slot()
    def _on_setup_table_context_menu(self, pos):
        """Show context menu for the Session Setup track table."""
        if not self._session:
            return
        row = self._setup_table.rowAt(pos.y())
        if row < 0:
            return

        # Get entry_id from UserRole on column 1
        fname_item = self._setup_table.item(row, 1)
        if not fname_item:
            return
        entry_id = fname_item.data(Qt.UserRole)
        if not entry_id:
            return

        # Find the TransferEntry
        manifest = self._session.transfer_manifest
        entry = None
        entry_idx = None
        for i, e in enumerate(manifest):
            if e.entry_id == entry_id:
                entry = e
                entry_idx = i
                break
        if entry is None:
            return

        menu = QMenu(self)

        # Duplicate for DAW — creates a new manifest entry referencing
        # the same output file but with a unique entry_id and editable name
        dup_act = menu.addAction("Duplicate for DAW")
        dup_act.triggered.connect(
            lambda checked, eid=entry_id: self._duplicate_transfer_entry(eid))

        # Remove Duplicate — only for user-added entries (entry_id != output_filename)
        if entry.entry_id != entry.output_filename:
            menu.addSeparator()
            remove_act = menu.addAction("Remove Duplicate")
            remove_act.triggered.connect(
                lambda checked, eid=entry_id: self._remove_transfer_entry(eid))

        menu.exec(self._setup_table.viewport().mapToGlobal(pos))

    def _duplicate_transfer_entry(self, source_entry_id: str):
        """Duplicate a transfer entry for multi-track DAW scenarios.

        Naming convention: ``stem-[1]``, ``stem-[2]``, etc.
        On first duplication the original is also renamed to ``-[1]``.
        """
        from sessionpreplib.models import TransferEntry
        import os
        import re
        import uuid

        manifest = self._session.transfer_manifest
        source = None
        source_idx = None
        for i, e in enumerate(manifest):
            if e.entry_id == source_entry_id:
                source = e
                source_idx = i
                break
        if source is None:
            return

        # Collect all siblings (same output_filename)
        siblings = [e for e in manifest
                    if e.output_filename == source.output_filename]

        # Find highest existing -[N] suffix among siblings
        suffix_re = re.compile(r"-\[(\d+)\]$")
        max_n = 0
        for sib in siblings:
            m = suffix_re.search(sib.daw_track_name)
            if m:
                max_n = max(max_n, int(m.group(1)))

        # Derive the base stem (strip extension if it matches filename,
        # strip any existing -[N] suffix)
        stem = source.daw_track_name
        if stem == source.output_filename:
            stem = os.path.splitext(stem)[0]
        stem = suffix_re.sub("", stem)

        if max_n == 0:
            # First duplication — rename the original to -[1]
            source.daw_track_name = f"{stem}-[1]"
            new_n = 2
        else:
            new_n = max_n + 1

        new_entry = TransferEntry(
            entry_id=f"{source.output_filename}:{uuid.uuid4().hex[:8]}",
            output_filename=source.output_filename,
            daw_track_name=f"{stem}-[{new_n}]",
            group=source.group,
        )

        # Insert right after the source entry
        manifest.insert(source_idx + 1, new_entry)
        self._populate_setup_table()
        self._populate_folder_tree()
        self._status_bar.showMessage(
            f"Duplicated → '{new_entry.daw_track_name}'")

    def _on_setup_table_item_changed(self, item):
        """Commit inline edit of the Track Name column."""
        if self._setup_table_populating:
            return
        if item.column() != 2:  # Track Name column
            return
        entry_id = item.data(Qt.UserRole)
        if not entry_id or not self._session:
            return
        new_name = item.text().strip()
        if not new_name:
            # Revert — find old name
            for e in self._session.transfer_manifest:
                if e.entry_id == entry_id:
                    self._setup_table.blockSignals(True)
                    item.setText(e.daw_track_name)
                    self._setup_table.blockSignals(False)
                    return
            return
        for e in self._session.transfer_manifest:
            if e.entry_id == entry_id:
                if e.daw_track_name != new_name:
                    e.daw_track_name = new_name
                    self._populate_folder_tree()
                return

    def _remove_transfer_entry(self, entry_id: str):
        """Remove a user-added duplicate transfer entry."""
        manifest = self._session.transfer_manifest
        for i, e in enumerate(manifest):
            if e.entry_id == entry_id:
                # Safety: only remove user-added duplicates
                if e.entry_id != e.output_filename:
                    del manifest[i]
                    self._populate_setup_table()
                    self._status_bar.showMessage(
                        f"Removed duplicate '{e.daw_track_name}'")
                break
