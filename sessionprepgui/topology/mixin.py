"""Topology tab mixin: dual-panel view of input → output track mapping."""

from __future__ import annotations

import os
from PySide6.QtCore import Qt, Slot, QPoint
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..widgets import ProgressPanel

from sessionpreplib.topology import (
    build_default_topology,
    ChannelRoute,
    TopologyEntry,
    TopologySource,
    extract_channel,
    passthrough_routes,
    sum_to_mono,
)
from sessionpreplib.utils import protools_sort_key

from ..theme import COLORS, FILE_COLOR_OK
from ..tracks.table_widgets import _PHASE_TOPOLOGY, _PHASE_ANALYSIS, _PHASE_SETUP
from ..waveform import WaveformPanel


class TopologyMixin:
    """Mixin that adds the Channel Topology tab to the main window.

    Expects the host class to provide:
      - ``self._session`` (SessionContext | None)
      - ``self._phase_tabs`` (QTabWidget)
      - ``self._mark_prepare_stale()``
    """

    # ── Build ─────────────────────────────────────────────────────────

    def _build_topology_page(self) -> QWidget:
        """Create and return the Channel Topology tab widget."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(toolbar.iconSize())

        topo_open_action = QAction("Open", self)
        topo_open_action.setToolTip("Open a directory containing audio files")
        topo_open_action.triggered.connect(self._on_open_path)
        toolbar.addAction(topo_open_action)

        toolbar.addSeparator()

        self._topo_reset_action = QAction("Reset to Default", self)
        self._topo_reset_action.setToolTip(
            "Rebuild the default passthrough topology from input tracks")
        self._topo_reset_action.triggered.connect(self._on_topo_reset)
        toolbar.addAction(self._topo_reset_action)

        toolbar.addSeparator()

        self._topo_status_label = QLabel("")
        self._topo_status_label.setStyleSheet(
            f"QLabel {{ color: {COLORS['dim']}; padding: 0 8px; }}")
        toolbar.addWidget(self._topo_status_label)

        # Spacer pushes Apply to the right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        self._topo_apply_action = QAction("Apply", self)
        self._topo_apply_action.setToolTip(
            "Write channel-rerouted files to the topology output folder")
        self._topo_apply_action.triggered.connect(self._on_topo_apply)
        self._topo_apply_action.setEnabled(False)
        toolbar.addAction(self._topo_apply_action)

        layout.addWidget(toolbar)

        # Dual-panel splitter
        h_splitter = QSplitter(Qt.Horizontal)

        # Left: input tracks (read-only)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_label = QLabel("Input Tracks")
        left_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        left_label.setStyleSheet(
            f"QLabel {{ color: {COLORS['text']}; font-weight: bold; }}")
        left_layout.addWidget(left_label)

        self._topo_input_table = QTableWidget()
        self._topo_input_table.setColumnCount(3)
        self._topo_input_table.setHorizontalHeaderLabels(
            ["File", "Ch", "Routing"])
        self._topo_input_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignLeft | Qt.AlignVCenter)
        ih = self._topo_input_table.horizontalHeader()
        ih.setSectionResizeMode(0, QHeaderView.Stretch)
        ih.setSectionResizeMode(1, QHeaderView.Fixed)
        ih.resizeSection(1, 32)
        ih.setSectionResizeMode(2, QHeaderView.Interactive)
        ih.resizeSection(2, 120)
        self._topo_input_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._topo_input_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._topo_input_table.verticalHeader().setVisible(False)
        self._topo_input_table.setAlternatingRowColors(True)
        self._topo_input_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._topo_input_table.customContextMenuRequested.connect(
            self._on_topo_input_context_menu)
        left_layout.addWidget(self._topo_input_table)
        h_splitter.addWidget(left_panel)

        # Right: output tracks (topology entries)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_label = QLabel("Output Tracks")
        right_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        right_label.setStyleSheet(
            f"QLabel {{ color: {COLORS['text']}; font-weight: bold; }}")
        right_layout.addWidget(right_label)

        self._topo_output_table = QTableWidget()
        self._topo_output_table.setColumnCount(3)
        self._topo_output_table.setHorizontalHeaderLabels(
            ["File", "Ch", "Sources"])
        self._topo_output_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignLeft | Qt.AlignVCenter)
        oh = self._topo_output_table.horizontalHeader()
        oh.setSectionResizeMode(0, QHeaderView.Stretch)
        oh.setSectionResizeMode(1, QHeaderView.Fixed)
        oh.resizeSection(1, 32)
        oh.setSectionResizeMode(2, QHeaderView.Stretch)
        self._topo_output_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._topo_output_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._topo_output_table.verticalHeader().setVisible(False)
        self._topo_output_table.setAlternatingRowColors(True)
        self._topo_output_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._topo_output_table.customContextMenuRequested.connect(
            self._on_topo_output_context_menu)
        right_layout.addWidget(self._topo_output_table)
        h_splitter.addWidget(right_panel)

        h_splitter.setSizes([400, 400])

        # Cross-table exclusive selection (multi-select aware)
        self._topo_input_table.selectionModel().selectionChanged.connect(
            lambda sel, desel: self._on_topo_selection_changed("input"))
        self._topo_output_table.selectionModel().selectionChanged.connect(
            lambda sel, desel: self._on_topo_selection_changed("output"))

        # Waveform preview panel (no analysis overlays)
        self._topo_wf_panel = WaveformPanel(analysis_mode=False)
        self._topo_wf_panel.setVisible(False)
        self._topo_wf_panel.play_clicked.connect(self._on_topo_play)
        self._topo_wf_panel.stop_clicked.connect(self._on_topo_stop)
        self._topo_wf_panel.position_clicked.connect(self._on_topo_wf_seek)

        # Vertical splitter: tables on top, waveform at bottom
        v_splitter = QSplitter(Qt.Vertical)
        v_splitter.addWidget(h_splitter)
        v_splitter.addWidget(self._topo_wf_panel)
        v_splitter.setSizes([700, 300])
        layout.addWidget(v_splitter, 1)

        # Progress panel for Apply operation
        self._topo_progress = ProgressPanel()
        layout.addWidget(self._topo_progress)

        # Worker references
        self._topo_apply_worker = None
        self._topo_audio_worker = None
        self._topo_wf_worker = None
        self._topo_resolve_worker = None
        self._topo_multi_worker = None
        self._topo_selected_side: str | None = None
        # (cache_key, display_audio, playback_audio, samplerate)
        self._topo_cached_audio: tuple[str, object, object, int] | None = None
        self._topo_cached_labels: list[str] | None = None
        self._topo_pending_labels: list[str] | None = None

        return page

    # ── Populate ──────────────────────────────────────────────────────

    def _populate_topology_tab(self):
        """Refresh both panels of the topology tab from the session."""
        if not self._session:
            return

        # Auto-build default topology if none exists
        if self._topo_topology is None:
            ok = [t for t in self._topo_source_tracks if t.status == "OK"]
            if ok:
                self._topo_topology = build_default_topology(
                    self._topo_source_tracks)

        topo = self._topo_topology
        ok_tracks = [t for t in self._topo_source_tracks if t.status == "OK"]

        # ── Input panel ───────────────────────────────────────────────
        self._topo_input_table.setSortingEnabled(False)
        self._topo_input_table.setRowCount(len(ok_tracks))

        for row, track in enumerate(ok_tracks):
            # File
            item = QTableWidgetItem(track.filename)
            item.setData(Qt.UserRole, protools_sort_key(track.filename))
            item.setForeground(FILE_COLOR_OK)
            self._topo_input_table.setItem(row, 0, item)

            # Ch
            ch_item = QTableWidgetItem(str(track.channels))
            ch_item.setForeground(QColor(COLORS["dim"]))
            self._topo_input_table.setItem(row, 1, ch_item)

            # Routing description
            routing = self._describe_routing(track.filename, topo)
            routing_item = QTableWidgetItem(routing)
            is_excluded = routing == "Excluded"
            routing_color = COLORS["dim"] if is_excluded else COLORS["clean"]
            routing_item.setForeground(QColor(routing_color))
            self._topo_input_table.setItem(row, 2, routing_item)

            # Dim the whole row if excluded
            if is_excluded:
                item.setForeground(QColor(COLORS["dim"]))

        self._topo_input_table.setSortingEnabled(True)
        self._topo_input_table.sortByColumn(0, Qt.AscendingOrder)

        # Auto-fit
        ih = self._topo_input_table.horizontalHeader()
        ih.setSectionResizeMode(0, QHeaderView.Stretch)
        ih.setSectionResizeMode(1, QHeaderView.Fixed)
        ih.resizeSection(1, 32)
        ih.setSectionResizeMode(2, QHeaderView.Interactive)
        ih.resizeSection(2, 120)

        # ── Output panel ──────────────────────────────────────────────
        entries = topo.entries if topo else []
        self._topo_output_table.setSortingEnabled(False)
        self._topo_output_table.setRowCount(len(entries))

        for row, entry in enumerate(entries):
            # File
            item = QTableWidgetItem(entry.output_filename)
            item.setForeground(FILE_COLOR_OK)
            self._topo_output_table.setItem(row, 0, item)

            # Ch
            ch_item = QTableWidgetItem(str(entry.output_channels))
            ch_item.setForeground(QColor(COLORS["dim"]))
            self._topo_output_table.setItem(row, 1, ch_item)

            # Sources summary
            src_names = [s.input_filename for s in entry.sources]
            src_text = ", ".join(src_names) if src_names else "\u2014"
            src_item = QTableWidgetItem(src_text)
            src_item.setForeground(QColor(COLORS["dim"]))
            self._topo_output_table.setItem(row, 2, src_item)

        self._topo_output_table.setSortingEnabled(True)
        self._topo_output_table.sortByColumn(0, Qt.AscendingOrder)

        # Auto-fit
        oh = self._topo_output_table.horizontalHeader()
        oh.setSectionResizeMode(0, QHeaderView.Stretch)
        oh.setSectionResizeMode(1, QHeaderView.Fixed)
        oh.resizeSection(1, 32)
        oh.setSectionResizeMode(2, QHeaderView.Stretch)

        # Status + Apply button
        n_in = len(ok_tracks)
        n_out = len(entries)
        self._topo_status_label.setText(
            f"{n_in} input → {n_out} output tracks")
        self._topo_apply_action.setEnabled(n_out > 0)

    # ── Helpers ────────────────────────────────────────────────────────

    def _describe_routing(self, input_filename: str, topo) -> str:
        """Return a human-readable routing label for an input track."""
        if not topo:
            return "Excluded"

        # Collect all entries that reference this input
        related = []
        for entry in topo.entries:
            for src in entry.sources:
                if src.input_filename == input_filename:
                    related.append((entry, src))

        if not related:
            return "Excluded"

        # Single entry, single source — inspect routes
        if len(related) == 1:
            entry, src = related[0]
            routes = src.routes
            if entry.output_channels == 1 and len(routes) == 1:
                r = routes[0]
                if r.source_channel == 0 and r.target_channel == 0 and r.gain == 1.0:
                    # Could be passthrough-mono or extract
                    track_map = self._topo_track_map()
                    t = track_map.get(input_filename)
                    if t and t.channels == 1:
                        return "Passthrough"
                    # Stereo source extracting one channel
                    ch_name = {0: "Left", 1: "Right"}.get(
                        r.source_channel, f"Ch {r.source_channel}")
                    return f"Extract {ch_name}"
                elif r.target_channel == 0:
                    ch_name = {0: "Left", 1: "Right"}.get(
                        r.source_channel, f"Ch {r.source_channel}")
                    return f"Extract {ch_name}"
            # Check for sum-to-mono (multiple routes to target 0)
            if (entry.output_channels == 1
                    and all(r.target_channel == 0 for r in routes)
                    and len(routes) > 1):
                return "Sum to Mono"
            # Check for passthrough (N:N identity mapping)
            if (entry.output_channels == len(routes)
                    and len(entry.sources) == 1
                    and all(r.source_channel == r.target_channel
                            and r.gain == 1.0 for r in routes)):
                return "Passthrough"
            return "Custom"

        # Two entries from one input — likely Split L/R
        if len(related) == 2:
            e0, s0 = related[0]
            e1, s1 = related[1]
            if (e0.output_channels == 1 and e1.output_channels == 1
                    and len(s0.routes) == 1 and len(s1.routes) == 1):
                chs = sorted([s0.routes[0].source_channel,
                               s1.routes[0].source_channel])
                if chs == [0, 1]:
                    return "Split to L/R Mono"

        # Multi-source entry — this input is merged with another
        for entry, src in related:
            if len(entry.sources) > 1:
                return "Merge to Stereo"

        return "Custom"

    def _topo_track_map(self) -> dict:
        """Return {filename: TrackContext} for OK original source tracks."""
        return {t.filename: t for t in self._topo_source_tracks if t.status == "OK"}

    def _topo_changed(self):
        """Refresh UI and invalidate downstream phases after a topology edit."""
        self._populate_topology_tab()
        # If topology was already applied, the output is now stale —
        # disable Phase 2 + 3 until user re-applies.
        if self._topology_dir is not None:
            self._topology_dir = None
            self._phase_tabs.setTabEnabled(_PHASE_ANALYSIS, False)
            self._phase_tabs.setTabEnabled(_PHASE_SETUP, False)
        self._mark_prepare_stale()

        # Auto-refresh waveform preview if an output track was selected
        if self._topo_selected_side == "output":
            rows = sorted(set(
                idx.row() for idx in
                self._topo_output_table.selectedIndexes()))
            if rows:
                if len(rows) == 1:
                    self._topo_load_output_waveform(rows[0])
                else:
                    self._topo_load_multi_output(rows)
            else:
                # Selected row no longer exists — clear waveform
                self._topo_cancel_workers()
                self._topo_cached_audio = None
                self._topo_wf_panel.waveform.set_audio(None, 44100)
                self._topo_wf_panel.play_btn.setEnabled(False)
                self._topo_selected_side = None

    def _topo_output_names(self) -> set[str]:
        """Return set of current output filenames in the topology."""
        topo = self._topo_topology
        if not topo:
            return set()
        return {e.output_filename for e in topo.entries}

    def _unique_output_name(self, base: str, ext: str) -> str:
        """Generate a unique output filename by appending _N if needed."""
        existing = self._topo_output_names()
        candidate = f"{base}{ext}"
        if candidate not in existing:
            return candidate
        n = 2
        while f"{base}_{n}{ext}" in existing:
            n += 1
        return f"{base}_{n}{ext}"

    # ── Apply topology ─────────────────────────────────────────────────

    @Slot()
    def _on_topo_apply(self):
        """Write channel-rerouted files to sp_01_topology/ folder."""
        if not self._session or not self._source_dir:
            return
        if self._topo_apply_worker is not None:
            return  # already running

        from ..analysis.worker import TopologyApplyWorker

        output_folder = self._config.get("app", {}).get(
            "phase1_output_folder", "sp_01_topology")
        output_dir = os.path.join(self._source_dir, output_folder)

        self._topo_apply_action.setEnabled(False)
        self._topo_reset_action.setEnabled(False)
        self._topo_status_label.setText("Applying topology…")
        self._topo_progress.start("Applying topology…")

        # Put Phase 1 topology on session for the worker to read
        self._session.topology = self._topo_topology
        self._topo_apply_worker = TopologyApplyWorker(
            self._session, output_dir)
        self._topo_apply_worker.progress.connect(self._on_topo_apply_progress)
        self._topo_apply_worker.progress_value.connect(
            self._on_topo_apply_progress_value)
        self._topo_apply_worker.finished.connect(self._on_topo_apply_done)
        self._topo_apply_worker.error.connect(self._on_topo_apply_error)
        self._topo_apply_worker.start()

    @Slot(str)
    def _on_topo_apply_progress(self, message: str):
        self._topo_progress.set_message(message)
        self._status_bar.showMessage(message)

    @Slot(int, int)
    def _on_topo_apply_progress_value(self, current: int, total: int):
        self._topo_progress.set_progress(current, total)

    @Slot()
    def _on_topo_apply_done(self):
        self._topo_apply_worker = None
        self._topo_apply_action.setEnabled(True)
        self._topo_reset_action.setEnabled(True)

        errors = self._session.config.get("_topology_apply_errors", [])
        n_out = len(self._session.output_tracks)
        if errors:
            msg = (f"Topology applied: {n_out} file(s) written, "
                   f"{len(errors)} error(s)")
            self._topo_progress.finish(msg)
            self._status_bar.showMessage(msg)
            detail = "\n".join(f"• {fn}: {err}" for fn, err in errors)
            QMessageBox.warning(
                self, "Apply Topology — errors",
                f"{msg}\n\n{detail}")
        else:
            msg = f"Topology applied: {n_out} file(s) written"
            self._topo_progress.finish(msg)
            self._status_bar.showMessage(msg)

        # Store topology output dir for Phase 2
        output_folder = self._config.get("app", {}).get(
            "phase1_output_folder", "sp_01_topology")
        self._topology_dir = os.path.join(self._source_dir, output_folder)

        # Enable Phase 2, switch to it, auto-trigger analysis
        self._phase_tabs.setTabEnabled(_PHASE_ANALYSIS, True)
        self._phase_tabs.setCurrentIndex(_PHASE_ANALYSIS)
        self._on_analyze()

    @Slot(str)
    def _on_topo_apply_error(self, message: str):
        self._topo_apply_worker = None
        self._topo_apply_action.setEnabled(True)
        self._topo_reset_action.setEnabled(True)
        self._topo_progress.fail(message)
        self._status_bar.showMessage(f"Apply topology error: {message}")

    # ── Actions ───────────────────────────────────────────────────────

    @Slot()
    def _on_topo_reset(self):
        """Reset topology to default passthrough."""
        if not self._session:
            return
        self._topo_topology = build_default_topology(self._topo_source_tracks)
        self._topo_changed()

    # ── Input table context menu ──────────────────────────────────────

    @Slot(QPoint)
    def _on_topo_input_context_menu(self, pos: QPoint):
        """Show context menu for the input tracks table."""
        if not self._session or not self._topo_topology:
            return
        row = self._topo_input_table.rowAt(pos.y())
        if row < 0:
            return

        item = self._topo_input_table.item(row, 0)
        if not item:
            return
        filename = item.text()
        track_map = self._topo_track_map()
        track = track_map.get(filename)
        if not track:
            return

        topo = self._topo_topology
        routing = self._describe_routing(filename, topo)
        is_excluded = routing == "Excluded"

        menu = QMenu(self)

        if is_excluded:
            # Re-include: add default passthrough entry
            incl_act = menu.addAction("Include in Session")
            incl_act.triggered.connect(
                lambda checked, fn=filename: self._topo_include_input(fn))
        else:
            # Reset to Passthrough (only if not already passthrough)
            if routing != "Passthrough":
                reset_act = menu.addAction("Reset to Passthrough")
                reset_act.triggered.connect(
                    lambda checked, fn=filename:
                        self._topo_reset_to_passthrough(fn))

            # Split stereo to L/R mono (only for stereo+ tracks)
            if track.channels >= 2:
                split_action = menu.addAction("Split to L/R Mono")
                split_action.triggered.connect(
                    lambda checked, fn=filename:
                        self._topo_split_stereo(fn))

                # Downmix submenu
                dm_menu = menu.addMenu("Downmix to Mono")
                for ch in range(track.channels):
                    label = {0: "Keep Left", 1: "Keep Right"}.get(
                        ch, f"Keep Ch {ch}")
                    act = dm_menu.addAction(label)
                    act.triggered.connect(
                        lambda checked, fn=filename, c=ch:
                            self._topo_extract_channel(fn, c))
                sum_act = dm_menu.addAction("Sum All Channels")
                sum_act.triggered.connect(
                    lambda checked, fn=filename:
                        self._topo_sum_to_mono(fn))

            # Merge two selected mono tracks to stereo
            selected_rows = set(
                idx.row()
                for idx in self._topo_input_table.selectedIndexes())
            if len(selected_rows) == 2:
                rows = sorted(selected_rows)
                fn_l = self._topo_input_table.item(rows[0], 0)
                fn_r = self._topo_input_table.item(rows[1], 0)
                if fn_l and fn_r:
                    t_l = track_map.get(fn_l.text())
                    t_r = track_map.get(fn_r.text())
                    if (t_l and t_r
                            and t_l.channels == 1 and t_r.channels == 1):
                        menu.addSeparator()
                        merge_act = menu.addAction("Merge to Stereo")
                        merge_act.triggered.connect(
                            lambda checked, a=fn_l.text(), b=fn_r.text():
                                self._topo_merge_stereo(a, b))

            # Exclude from session
            menu.addSeparator()
            excl_action = menu.addAction("Exclude from Session")
            excl_action.triggered.connect(
                lambda checked, fn=filename:
                    self._topo_exclude_input(fn))

        if menu.actions():
            menu.exec(self._topo_input_table.viewport().mapToGlobal(pos))

    # ── Output table context menu ─────────────────────────────────────

    @Slot(QPoint)
    def _on_topo_output_context_menu(self, pos: QPoint):
        """Show context menu for the output tracks table."""
        if not self._session or not self._topo_topology:
            return
        row = self._topo_output_table.rowAt(pos.y())
        if row < 0:
            return

        item = self._topo_output_table.item(row, 0)
        if not item:
            return
        filename = item.text()

        menu = QMenu(self)

        rename_act = menu.addAction("Rename Output…")
        rename_act.triggered.connect(
            lambda checked, fn=filename: self._topo_rename_output(fn))

        menu.addSeparator()
        remove_act = menu.addAction("Remove Output")
        remove_act.triggered.connect(
            lambda checked, fn=filename: self._topo_remove_output(fn))

        menu.exec(self._topo_output_table.viewport().mapToGlobal(pos))

    # ── Topology operations ───────────────────────────────────────────

    def _topo_split_stereo(self, input_filename: str):
        """Replace a stereo passthrough with two mono extract entries."""
        topo = self._topo_topology
        track_map = self._topo_track_map()
        track = track_map.get(input_filename)
        if not track or track.channels < 2:
            return

        stem, ext = os.path.splitext(input_filename)

        # Remove the existing entry for this input (if any)
        topo.entries = [
            e for e in topo.entries
            if not (len(e.sources) == 1
                    and e.sources[0].input_filename == input_filename)
        ]

        # Add L and R mono entries
        for ch, suffix in enumerate(["_L", "_R"]):
            if ch >= track.channels:
                break
            out_name = self._unique_output_name(f"{stem}{suffix}", ext)
            topo.entries.append(TopologyEntry(
                output_filename=out_name,
                output_channels=1,
                sources=[TopologySource(
                    input_filename=input_filename,
                    routes=extract_channel(ch),
                )],
            ))

        self._topo_changed()

    def _topo_extract_channel(self, input_filename: str, channel: int):
        """Replace entry with a mono extract of a single channel."""
        topo = self._topo_topology
        track_map = self._topo_track_map()
        track = track_map.get(input_filename)
        if not track or channel >= track.channels:
            return

        stem, ext = os.path.splitext(input_filename)
        suffix = {0: "_L", 1: "_R"}.get(channel, f"_ch{channel}")

        # Remove existing entry for this input
        topo.entries = [
            e for e in topo.entries
            if not (len(e.sources) == 1
                    and e.sources[0].input_filename == input_filename)
        ]

        out_name = self._unique_output_name(f"{stem}{suffix}", ext)
        topo.entries.append(TopologyEntry(
            output_filename=out_name,
            output_channels=1,
            sources=[TopologySource(
                input_filename=input_filename,
                routes=extract_channel(channel),
            )],
        ))
        self._topo_changed()

    def _topo_sum_to_mono(self, input_filename: str):
        """Replace entry with a mono sum of all channels."""
        topo = self._topo_topology
        track_map = self._topo_track_map()
        track = track_map.get(input_filename)
        if not track:
            return

        stem, ext = os.path.splitext(input_filename)

        # Remove existing entry for this input
        topo.entries = [
            e for e in topo.entries
            if not (len(e.sources) == 1
                    and e.sources[0].input_filename == input_filename)
        ]

        out_name = self._unique_output_name(f"{stem}_mono", ext)
        topo.entries.append(TopologyEntry(
            output_filename=out_name,
            output_channels=1,
            sources=[TopologySource(
                input_filename=input_filename,
                routes=sum_to_mono(track.channels),
            )],
        ))
        self._topo_changed()

    def _topo_merge_stereo(self, left_filename: str, right_filename: str):
        """Merge two mono inputs into one stereo output."""
        topo = self._topo_topology
        track_map = self._topo_track_map()
        t_l = track_map.get(left_filename)
        t_r = track_map.get(right_filename)
        if not t_l or not t_r or t_l.channels != 1 or t_r.channels != 1:
            return

        stem_l, ext = os.path.splitext(left_filename)

        # Remove existing entries for both inputs
        remove_fns = {left_filename, right_filename}
        topo.entries = [
            e for e in topo.entries
            if not (len(e.sources) == 1
                    and e.sources[0].input_filename in remove_fns)
        ]

        out_name = self._unique_output_name(f"{stem_l}_stereo", ext)
        topo.entries.append(TopologyEntry(
            output_filename=out_name,
            output_channels=2,
            sources=[
                TopologySource(
                    input_filename=left_filename,
                    routes=[ChannelRoute(0, 0)],
                ),
                TopologySource(
                    input_filename=right_filename,
                    routes=[ChannelRoute(0, 1)],
                ),
            ],
        ))
        self._topo_changed()

    def _topo_include_input(self, input_filename: str):
        """Re-include an excluded input track as a passthrough entry."""
        topo = self._topo_topology
        track_map = self._topo_track_map()
        track = track_map.get(input_filename)
        if not track:
            return

        out_name = self._unique_output_name(
            *os.path.splitext(input_filename))
        topo.entries.append(TopologyEntry(
            output_filename=out_name,
            output_channels=track.channels,
            sources=[TopologySource(
                input_filename=input_filename,
                routes=passthrough_routes(track.channels),
            )],
        ))
        self._topo_changed()

    def _topo_reset_to_passthrough(self, input_filename: str):
        """Reset an input track's routing back to default passthrough."""
        topo = self._topo_topology
        track_map = self._topo_track_map()
        track = track_map.get(input_filename)
        if not track:
            return

        # Remove all existing entries that reference this input
        topo.entries = [
            e for e in topo.entries
            if not any(s.input_filename == input_filename
                       for s in e.sources)
        ]

        # Add back as passthrough
        out_name = self._unique_output_name(
            *os.path.splitext(input_filename))
        topo.entries.append(TopologyEntry(
            output_filename=out_name,
            output_channels=track.channels,
            sources=[TopologySource(
                input_filename=input_filename,
                routes=passthrough_routes(track.channels),
            )],
        ))
        self._topo_changed()

    def _topo_exclude_input(self, input_filename: str):
        """Remove all topology entries that reference the given input."""
        topo = self._topo_topology
        topo.entries = [
            e for e in topo.entries
            if not any(s.input_filename == input_filename
                       for s in e.sources)
        ]
        self._topo_changed()

    def _topo_rename_output(self, output_filename: str):
        """Rename an output file in the topology via input dialog."""
        topo = self._topo_topology
        entry = next((e for e in topo.entries
                      if e.output_filename == output_filename), None)
        if entry is None:
            return

        new_name, ok = QInputDialog.getText(
            self, "Rename Output",
            "New output filename:",
            text=entry.output_filename,
        )
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()

        # Check for duplicates
        existing = self._topo_output_names() - {entry.output_filename}
        if new_name in existing:
            QMessageBox.warning(
                self, "Rename Output",
                f"An output named '{new_name}' already exists.")
            return

        entry.output_filename = new_name
        self._topo_changed()

    def _topo_remove_output(self, output_filename: str):
        """Remove an output entry from the topology."""
        topo = self._topo_topology
        topo.entries = [e for e in topo.entries
                        if e.output_filename != output_filename]
        self._topo_changed()

    # ── Cross-table exclusive selection (multi-select aware) ─────────

    def _on_topo_selection_changed(self, side: str):
        """Handle selection change in input or output table."""
        if side == "input":
            table = self._topo_input_table
            other = self._topo_output_table
        else:
            table = self._topo_output_table
            other = self._topo_input_table

        rows = sorted(set(idx.row() for idx in table.selectedIndexes()))
        if not rows:
            return

        # Clear other table's selection
        if self._topo_selected_side != side:
            other.blockSignals(True)
            other.clearSelection()
            other.setCurrentCell(-1, -1)
            other.blockSignals(False)
        self._topo_selected_side = side

        if len(rows) == 1:
            if side == "input":
                self._topo_load_input_waveform(rows[0])
            else:
                self._topo_load_output_waveform(rows[0])
        else:
            if side == "input":
                self._topo_load_multi_input(rows)
            else:
                self._topo_load_multi_output(rows)

    # ── Waveform loading helpers ───────────────────────────────────────

    def _topo_cancel_workers(self):
        """Cancel all in-flight topology waveform workers."""
        for attr in ("_topo_audio_worker", "_topo_wf_worker",
                     "_topo_resolve_worker", "_topo_multi_worker"):
            w = getattr(self, attr, None)
            if w is not None:
                w.cancel()
                try:
                    w.finished.disconnect()
                except RuntimeError:
                    pass
                setattr(self, attr, None)

    # ── Single-track input loading ─────────────────────────────────────

    def _topo_load_input_waveform(self, row: int):
        """Load waveform for the input track at *row*."""
        self._topo_cancel_workers()
        self._on_topo_stop()

        item = self._topo_input_table.item(row, 0)
        if not item:
            return
        filename = item.text()
        track_map = self._topo_track_map()
        track = track_map.get(filename)
        if not track:
            return

        # Check cache
        cached = self._topo_cached_audio
        if cached and cached[0] == track.filepath:
            self._topo_show_waveform(cached[1], cached[3])
            return

        # Need to load from disk
        self._topo_wf_panel.setVisible(True)
        self._topo_wf_panel.waveform.set_loading(True)
        self._topo_wf_panel.play_btn.setEnabled(False)

        from ..analysis.worker import AudioLoadWorker
        worker = AudioLoadWorker(track, parent=self)
        self._topo_audio_worker = worker
        worker.finished.connect(
            lambda t, fp=track.filepath: self._on_topo_audio_loaded(t, fp))
        worker.error.connect(self._on_topo_audio_error)
        worker.start()

    def _on_topo_audio_loaded(self, track, filepath: str):
        self._topo_audio_worker = None
        if track.audio_data is None:
            return
        self._topo_cached_audio = (
            filepath, track.audio_data, track.audio_data, track.samplerate)
        self._topo_show_waveform(track.audio_data, track.samplerate)

    def _on_topo_audio_error(self, message: str):
        self._topo_audio_worker = None
        self._topo_wf_panel.waveform.set_loading(False)
        self._status_bar.showMessage(f"Audio load error: {message}")

    # ── Multi-track loading ──────────────────────────────────────────────

    def _topo_load_multi_input(self, rows: list[int]):
        """Load and stack waveforms for multiple input tracks."""
        self._topo_cancel_workers()
        self._on_topo_stop()

        track_map = self._topo_track_map()
        items = []
        for row in rows:
            item = self._topo_input_table.item(row, 0)
            if not item:
                continue
            filename = item.text()
            track = track_map.get(filename)
            if track:
                stem = os.path.splitext(filename)[0]
                items.append((track.filepath, stem, track.channels))
        if not items:
            return

        self._topo_wf_panel.setVisible(True)
        self._topo_wf_panel.waveform.set_loading(True)
        self._topo_wf_panel.play_btn.setEnabled(False)

        from ..analysis.worker import TopoMultiAudioWorker
        worker = TopoMultiAudioWorker(
            items, "input", self._source_dir or "", parent=self)
        self._topo_multi_worker = worker
        worker.finished.connect(self._on_topo_multi_done)
        worker.error.connect(self._on_topo_multi_error)
        worker.start()

    def _topo_load_multi_output(self, rows: list[int]):
        """Load and stack waveforms for multiple output entries."""
        self._topo_cancel_workers()
        self._on_topo_stop()

        topo = self._topo_topology if self._session else None
        if not topo or not self._source_dir:
            return

        items = []
        for row in rows:
            item = self._topo_output_table.item(row, 0)
            if not item:
                continue
            filename = item.text()
            entry = next((e for e in topo.entries
                          if e.output_filename == filename), None)
            if entry:
                stem = os.path.splitext(filename)[0]
                items.append((entry, stem))
        if not items:
            return

        self._topo_wf_panel.setVisible(True)
        self._topo_wf_panel.waveform.set_loading(True)
        self._topo_wf_panel.play_btn.setEnabled(False)

        from ..analysis.worker import TopoMultiAudioWorker
        worker = TopoMultiAudioWorker(
            items, "output", self._source_dir, parent=self)
        self._topo_multi_worker = worker
        worker.finished.connect(self._on_topo_multi_done)
        worker.error.connect(self._on_topo_multi_error)
        worker.start()

    def _on_topo_multi_done(self, display_audio, playback_audio,
                            samplerate: int, labels: list[str]):
        self._topo_multi_worker = None
        self._topo_cached_audio = (
            "__multi__", display_audio, playback_audio, samplerate)
        self._topo_cached_labels = labels
        self._topo_show_waveform(display_audio, samplerate, labels=labels)

    def _on_topo_multi_error(self, message: str):
        self._topo_multi_worker = None
        self._topo_wf_panel.waveform.set_loading(False)
        self._status_bar.showMessage(f"Multi-track load error: {message}")

    # ── Waveform loading (output tracks — virtual preview) ─────────────

    def _topo_load_output_waveform(self, row: int):
        """Resolve and display waveform for the output entry at *row*."""
        self._topo_cancel_workers()
        self._on_topo_stop()

        item = self._topo_output_table.item(row, 0)
        if not item:
            return
        filename = item.text()
        topo = self._topo_topology if self._session else None
        if not topo:
            return
        entry = None
        for e in topo.entries:
            if e.output_filename == filename:
                entry = e
                break
        if entry is None:
            return

        if not self._source_dir:
            return

        self._topo_wf_panel.setVisible(True)
        self._topo_wf_panel.waveform.set_loading(True)
        self._topo_wf_panel.play_btn.setEnabled(False)

        from ..analysis.worker import TopoAudioResolveWorker
        worker = TopoAudioResolveWorker(entry, self._source_dir, parent=self)
        self._topo_resolve_worker = worker
        worker.finished.connect(self._on_topo_resolve_done)
        worker.error.connect(self._on_topo_resolve_error)
        worker.start()

    def _on_topo_resolve_done(self, audio_data, samplerate: int):
        self._topo_resolve_worker = None
        # Cache under a synthetic key so we don't confuse with input files
        self._topo_cached_audio = (
            "__output__", audio_data, audio_data, samplerate)
        self._topo_show_waveform(audio_data, samplerate)

    def _on_topo_resolve_error(self, message: str):
        self._topo_resolve_worker = None
        self._topo_wf_panel.waveform.set_loading(False)
        self._status_bar.showMessage(f"Preview error: {message}")

    # ── Common waveform display ────────────────────────────────────────

    def _topo_show_waveform(self, audio_data, samplerate: int,
                            labels: list[str] | None = None):
        """Run WaveformLoadWorker (lightweight, no RMS) and display result."""
        from ..waveform.compute import WaveformLoadWorker

        self._topo_wf_panel.setVisible(True)
        self._topo_wf_panel.waveform.set_loading(True)
        self._topo_pending_labels = labels

        worker = WaveformLoadWorker(
            audio_data, samplerate, 0,
            spec_n_fft=self._topo_wf_panel.waveform.spec_n_fft,
            spec_window=self._topo_wf_panel.waveform.spec_window,
            parent=self)
        self._topo_wf_worker = worker
        worker.finished.connect(self._on_topo_wf_loaded)
        worker.start()

    def _on_topo_wf_loaded(self, result: dict):
        self._topo_wf_worker = None
        self._topo_wf_panel.waveform.set_precomputed(result)
        n_ch = len(result["channels"])
        labels = getattr(self, '_topo_pending_labels', None)
        self._topo_wf_panel.update_play_mode_channels(n_ch, labels=labels)
        self._topo_wf_panel.play_btn.setEnabled(True)
        self._topo_update_time_label(0)

    # ── Playback ───────────────────────────────────────────────────────

    @Slot()
    def _on_topo_play(self):
        cached = self._topo_cached_audio
        if not cached:
            return
        _, _display, playback_audio, samplerate = cached
        self._on_topo_stop()
        start = self._topo_wf_panel.waveform._cursor_sample
        mode, channel = self._topo_wf_panel.play_mode
        self._playback.play(playback_audio, samplerate, start,
                            mode=mode, channel=channel)
        if self._playback.is_playing:
            self._topo_wf_panel.play_btn.setEnabled(False)
            self._topo_wf_panel.stop_btn.setEnabled(True)

    @Slot()
    def _on_topo_stop(self):
        was_playing = self._playback.is_playing
        start_sample = self._playback.play_start_sample
        self._playback.stop()
        self._topo_wf_panel.stop_btn.setEnabled(False)
        self._topo_wf_panel.play_btn.setEnabled(
            self._topo_cached_audio is not None)
        if was_playing:
            self._topo_wf_panel.waveform.set_cursor(start_sample)
            self._topo_update_time_label(start_sample)

    @Slot(int)
    def _on_topo_wf_seek(self, sample: int):
        if self._playback.is_playing:
            self._on_topo_stop()
        self._topo_update_time_label(sample)

    def _topo_update_time_label(self, current_sample: int):
        cached = self._topo_cached_audio
        if not cached:
            return
        _, display_audio, _playback, sr = cached
        total = display_audio.shape[0] if display_audio is not None else 0
        from sessionpreplib.audio import format_duration
        cur_str = format_duration(current_sample, sr)
        tot_str = format_duration(total, sr)
        self._topo_wf_panel.time_label.setText(f"{cur_str} / {tot_str}")
