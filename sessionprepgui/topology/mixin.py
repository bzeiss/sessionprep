"""Topology tab mixin: orchestrates input/output trees, waveform preview, Apply."""

from __future__ import annotations

import os
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QLabel,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..widgets import ProgressPanel

from sessionpreplib.topology import build_default_topology
from sessionpreplib.utils import protools_sort_key

from ..theme import COLORS
from ..tracks.table_widgets import _PHASE_TOPOLOGY, _PHASE_ANALYSIS, _PHASE_SETUP
from ..waveform import WaveformPanel

from .input_tree import InputTree
from .output_tree import OutputTree
from . import operations as ops


class TopologyMixin:
    """Mixin that adds the Track Layout tab to the main window.

    Expects the host class to provide:
      - ``self._session`` (SessionContext | None)
      - ``self._topo_topology`` (TopologyMapping | None)
      - ``self._topo_source_tracks`` (list[TrackContext])
      - ``self._phase_tabs`` (QTabWidget)
      - ``self._mark_prepare_stale()``
      - ``self._source_dir`` (str | None)
      - ``self._playback`` (PlaybackController)
      - ``self._config`` (dict)
      - ``self._status_bar`` (QStatusBar)
    """

    # ── Build ─────────────────────────────────────────────────────────

    def _build_topology_page(self) -> QWidget:
        """Create and return the Track Layout tab widget."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(toolbar.iconSize())

        topo_open_action = QAction("Open Folder", self)
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

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        self._topo_wf_toggle = QAction("\u25B6 Waveform", self)
        self._topo_wf_toggle.setCheckable(True)
        self._topo_wf_toggle.setChecked(False)
        self._topo_wf_toggle.setToolTip("Show / hide the waveform preview")
        self._topo_wf_toggle.toggled.connect(self._on_topo_wf_toggle)
        toolbar.addAction(self._topo_wf_toggle)

        toolbar.addSeparator()

        self._topo_apply_action = QAction("Apply", self)
        self._topo_apply_action.setToolTip(
            "Write channel-rerouted files to the topology output folder")
        self._topo_apply_action.triggered.connect(self._on_topo_apply)
        self._topo_apply_action.setEnabled(False)
        toolbar.addAction(self._topo_apply_action)

        layout.addWidget(toolbar)

        # ── Dual-panel splitter (input + output trees) ────────────────
        h_splitter = QSplitter(Qt.Horizontal)

        # Left: Input tree
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_label = QLabel("Input Tracks")
        left_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        left_label.setStyleSheet(
            f"QLabel {{ color: {COLORS['text']}; font-weight: bold; }}")
        left_layout.addWidget(left_label)

        self._topo_input_tree = InputTree()
        self._topo_input_tree.context_menu_requested.connect(
            self._on_topo_input_context_menu)
        left_layout.addWidget(self._topo_input_tree)
        h_splitter.addWidget(left_panel)

        # Right: Output tree
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_label = QLabel("Output Tracks")
        right_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        right_label.setStyleSheet(
            f"QLabel {{ color: {COLORS['text']}; font-weight: bold; }}")
        right_layout.addWidget(right_label)

        self._topo_output_tree = OutputTree()
        self._topo_output_tree.topology_modified.connect(self._topo_changed)
        right_layout.addWidget(self._topo_output_tree)
        h_splitter.addWidget(right_panel)

        h_splitter.setSizes([400, 400])

        # Cross-tree exclusive selection
        self._topo_input_tree.selectionModel().selectionChanged.connect(
            lambda sel, desel: self._on_topo_selection_changed("input"))
        self._topo_output_tree.selectionModel().selectionChanged.connect(
            lambda sel, desel: self._on_topo_selection_changed("output"))

        # Waveform preview panel (starts collapsed)
        self._topo_wf_panel = WaveformPanel(analysis_mode=False)
        self._topo_wf_panel.setVisible(False)
        self._topo_wf_expanded = False
        self._topo_wf_panel.play_clicked.connect(self._on_topo_play)
        self._topo_wf_panel.stop_clicked.connect(self._on_topo_stop)
        self._topo_wf_panel.position_clicked.connect(self._on_topo_wf_seek)

        # Vertical splitter: trees on top, waveform at bottom
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
        """Refresh both trees from source tracks and topology."""
        if not self._session:
            return

        # Auto-build default topology if none exists
        if self._topo_topology is None:
            ok = [t for t in self._topo_source_tracks if t.status == "OK"]
            if ok:
                self._topo_topology = build_default_topology(
                    self._topo_source_tracks)

        track_map = self._topo_track_map()

        self._topo_input_tree.populate(
            self._topo_source_tracks, self._topo_topology)
        self._topo_output_tree.populate(self._topo_topology, track_map)

        # Status + Apply button
        ok_tracks = [t for t in self._topo_source_tracks if t.status == "OK"]
        n_in = len(ok_tracks)
        n_out = len(self._topo_topology.entries) if self._topo_topology else 0
        self._topo_status_label.setText(
            f"{n_in} input \u2192 {n_out} output tracks")
        self._topo_apply_action.setEnabled(n_out > 0)

    # ── Helpers ────────────────────────────────────────────────────────

    def _topo_track_map(self) -> dict:
        """Return {filename: TrackContext} for OK original source tracks."""
        return {t.filename: t for t in self._topo_source_tracks
                if t.status == "OK"}

    def _topo_changed(self):
        """Refresh UI and invalidate downstream phases after a topology edit."""
        self._populate_topology_tab()
        # If topology was already applied, the output is now stale
        if self._topology_dir is not None:
            self._topology_dir = None
            self._phase_tabs.setTabEnabled(_PHASE_ANALYSIS, False)
            self._phase_tabs.setTabEnabled(_PHASE_SETUP, False)
        self._mark_prepare_stale()

        # Re-apply usage highlights if input side is active
        if self._topo_selected_side == "input":
            items = self._topo_input_tree.selectedItems()
            file_items = [
                it for it in items
                if (it.data(0, Qt.UserRole) or (None,))[0] == "file"
            ]
            channel_items = [
                it for it in items
                if (it.data(0, Qt.UserRole) or (None,))[0] == "channel"
            ]
            self._update_output_highlights(file_items, channel_items)

        # Auto-refresh waveform preview if an output track was selected
        if self._topo_selected_side == "output":
            items = self._topo_output_tree.selectedItems()
            file_items = [
                it for it in items
                if (it.data(0, Qt.UserRole) or (None,))[0] == "file"
            ]
            channel_items = [
                it for it in items
                if (it.data(0, Qt.UserRole) or (None,))[0] == "channel"
            ]
            if file_items or channel_items:
                self._topo_load_output_from_items(file_items, channel_items)
            else:
                self._topo_cancel_workers()
                self._topo_cached_audio = None
                self._topo_wf_panel.waveform.set_audio(None, 44100)
                self._topo_wf_panel.play_btn.setEnabled(False)
                self._topo_selected_side = None

    # ── Apply topology ────────────────────────────────────────────────

    @Slot()
    def _on_topo_apply(self):
        """Write channel-rerouted files to sp_01_topology/ folder."""
        if not self._session or not self._source_dir:
            return
        if self._topo_apply_worker is not None:
            return

        from ..analysis.worker import TopologyApplyWorker

        output_folder = self._config.get("app", {}).get(
            "phase1_output_folder", "sp_01_topology")
        output_dir = os.path.join(self._source_dir, output_folder)

        self._topo_apply_action.setEnabled(False)
        self._topo_reset_action.setEnabled(False)
        self._topo_status_label.setText("Applying topology\u2026")
        self._topo_progress.start("Applying topology\u2026")

        # Put Phase 1 topology on session for the worker to read
        self._session.topology = self._topo_topology
        self._topo_apply_worker = TopologyApplyWorker(
            self._session, output_dir, source_dir=self._source_dir)
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
            detail = "\n".join(f"\u2022 {fn}: {err}" for fn, err in errors)
            QMessageBox.warning(
                self, "Apply Topology \u2014 errors",
                f"{msg}\n\n{detail}")
        else:
            msg = f"Topology applied: {n_out} file(s) written"
            self._topo_progress.finish(msg)
            self._status_bar.showMessage(msg)

        output_folder = self._config.get("app", {}).get(
            "phase1_output_folder", "sp_01_topology")
        self._topology_dir = os.path.join(self._source_dir, output_folder)

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

    # ── Input tree context menu ───────────────────────────────────────

    @Slot(str, list, object)
    def _on_topo_input_context_menu(self, filename: str, selected: list[str],
                                     global_pos=None):
        """Build and show context menu for input tree file items."""
        if not self._session or not self._topo_topology:
            return

        track_map = self._topo_track_map()
        track = track_map.get(filename)
        if not track:
            return

        topo = self._topo_topology
        is_excluded = not any(
            src.input_filename == filename
            for entry in topo.entries for src in entry.sources
        )

        menu = QMenu(self)

        if is_excluded:
            act = menu.addAction("Include in Session")
            act.triggered.connect(
                lambda checked, fn=filename:
                    self._input_action(ops.include_input, fn))
        else:
            # Reset to Passthrough
            act_reset = menu.addAction("Reset to Passthrough")
            act_reset.triggered.connect(
                lambda checked, fn=filename:
                    self._input_action(ops.reset_to_passthrough, fn))

            # Split stereo (2+ channels)
            if track.channels >= 2:
                act_split = menu.addAction("Split to L/R Mono")
                act_split.triggered.connect(
                    lambda checked, fn=filename:
                        self._input_action(ops.split_stereo, fn))

                dm_menu = menu.addMenu("Downmix to Mono")
                for ch in range(track.channels):
                    label = {0: "Keep Left", 1: "Keep Right"}.get(
                        ch, f"Keep Ch {ch}")
                    act = dm_menu.addAction(label)
                    act.triggered.connect(
                        lambda checked, fn=filename, c=ch:
                            self._input_action_ch(ops.extract_channel, fn, c))
                sum_act = dm_menu.addAction("Sum All Channels")
                sum_act.triggered.connect(
                    lambda checked, fn=filename:
                        self._input_action(ops.sum_to_mono, fn))

            # Merge two mono tracks to stereo
            if len(selected) == 2:
                fn_a, fn_b = selected
                t_a = track_map.get(fn_a)
                t_b = track_map.get(fn_b)
                if (t_a and t_b
                        and t_a.channels == 1 and t_b.channels == 1):
                    menu.addSeparator()
                    act_merge = menu.addAction("Merge to Stereo")
                    act_merge.triggered.connect(
                        lambda checked, a=fn_a, b=fn_b:
                            self._input_action_merge(a, b))

            # Exclude
            menu.addSeparator()
            act_excl = menu.addAction("Exclude from Session")
            act_excl.triggered.connect(
                lambda checked, fn=filename:
                    self._input_action_exclude(fn))

        if menu.actions() and global_pos is not None:
            menu.exec(global_pos)

    def _input_action(self, op_fn, filename: str):
        """Call an operations function that takes (topo, track_map, filename)."""
        topo = self._topo_topology
        if not topo:
            return
        op_fn(topo, self._topo_track_map(), filename)
        self._topo_changed()

    def _input_action_ch(self, op_fn, filename: str, channel: int):
        """Call an operations function that takes (topo, track_map, filename, channel)."""
        topo = self._topo_topology
        if not topo:
            return
        op_fn(topo, self._topo_track_map(), filename, channel)
        self._topo_changed()

    def _input_action_merge(self, left: str, right: str):
        topo = self._topo_topology
        if not topo:
            return
        ops.merge_stereo(topo, self._topo_track_map(), left, right)
        self._topo_changed()

    def _input_action_exclude(self, filename: str):
        topo = self._topo_topology
        if not topo:
            return
        ops.exclude_input(topo, filename)
        self._topo_changed()

    # ── Cross-tree exclusive selection ────────────────────────────────

    def _on_topo_selection_changed(self, side: str):
        """Handle selection change in input or output tree."""
        if side == "input":
            tree = self._topo_input_tree
            other = self._topo_output_tree
        else:
            tree = self._topo_output_tree
            other = self._topo_input_tree

        items = tree.selectedItems()
        if not items:
            return

        # Clear other tree's selection
        if self._topo_selected_side != side:
            other.blockSignals(True)
            other.clearSelection()
            other.blockSignals(False)
        self._topo_selected_side = side

        # Determine what's selected
        file_items = []
        channel_items = []
        for it in items:
            data = it.data(0, Qt.UserRole)
            if not data:
                continue
            if data[0] == "file":
                file_items.append(it)
            elif data[0] == "channel":
                channel_items.append(it)

        # Usage highlighting on the output tree
        if side == "input":
            self._update_output_highlights(file_items, channel_items)
        else:
            self._topo_output_tree.clear_highlights()

        if side == "input":
            self._topo_load_input_from_items(file_items, channel_items)
        else:
            self._topo_load_output_from_items(file_items, channel_items)

    def _on_topo_wf_toggle(self, checked: bool):
        """Show or hide the waveform preview panel."""
        self._topo_wf_expanded = checked
        self._topo_wf_panel.setVisible(checked)
        self._topo_wf_toggle.setText(
            "\u25BC Waveform" if checked else "\u25B6 Waveform")

    def _update_output_highlights(self, file_items, channel_items):
        """Highlight output tree items that reference the selected input."""
        if channel_items:
            # Single channel selected — highlight that specific channel
            data = channel_items[0].data(0, Qt.UserRole)
            if data and data[0] == "channel":
                self._topo_output_tree.highlight_usages(data[1], data[2])
                return
        if file_items:
            # File selected — highlight all usages of that file
            data = file_items[0].data(0, Qt.UserRole)
            if data and data[0] == "file":
                self._topo_output_tree.highlight_usages(data[1])
                return
        self._topo_output_tree.clear_highlights()

    # ── Waveform loading: cancel helpers ──────────────────────────────

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

    # ── Input waveform loading ────────────────────────────────────────

    def _topo_load_input_from_items(self, file_items, channel_items=None):
        """Load waveform for selected input items."""
        if not file_items and not channel_items:
            return

        if len(file_items) == 1 and not channel_items:
            data = file_items[0].data(0, Qt.UserRole)
            self._topo_load_input_waveform(data[1])
        elif len(file_items) > 1:
            self._topo_load_multi_input(file_items)
        elif channel_items:
            # Single channel selected — load full file then show one channel
            data = channel_items[0].data(0, Qt.UserRole)
            self._topo_load_input_waveform(data[1])

    def _topo_load_input_waveform(self, filename: str):
        """Load waveform for a single input file."""
        self._topo_cancel_workers()
        self._on_topo_stop()

        track_map = self._topo_track_map()
        track = track_map.get(filename)
        if not track:
            return

        cached = self._topo_cached_audio
        if cached and cached[0] == track.filepath:
            self._topo_show_waveform(cached[1], cached[3])
            return

        if self._topo_wf_expanded:
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

    # ── Multi-track input loading ─────────────────────────────────────

    def _topo_load_multi_input(self, file_items):
        """Load and stack waveforms for multiple input files."""
        self._topo_cancel_workers()
        self._on_topo_stop()

        track_map = self._topo_track_map()
        items = []
        for fi in file_items:
            data = fi.data(0, Qt.UserRole)
            if not data or data[0] != "file":
                continue
            filename = data[1]
            track = track_map.get(filename)
            if track:
                stem = os.path.splitext(filename)[0]
                items.append((track.filepath, stem, track.channels))
        if not items:
            return

        if self._topo_wf_expanded:
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

    # ── Output waveform loading ───────────────────────────────────────

    def _topo_load_output_from_items(self, file_items, channel_items=None):
        """Load waveform for selected output items."""
        if not file_items and not channel_items:
            return

        if len(file_items) == 1 and not channel_items:
            data = file_items[0].data(0, Qt.UserRole)
            self._topo_load_output_waveform(data[1])
        elif len(file_items) > 1:
            self._topo_load_multi_output(file_items)
        elif channel_items:
            data = channel_items[0].data(0, Qt.UserRole)
            self._topo_load_output_waveform(data[1])

    def _topo_load_output_waveform(self, output_filename: str):
        """Resolve and display waveform for an output entry."""
        self._topo_cancel_workers()
        self._on_topo_stop()

        topo = self._topo_topology
        if not topo or not self._source_dir:
            return
        entry = None
        for e in topo.entries:
            if e.output_filename == output_filename:
                entry = e
                break
        if entry is None:
            return

        if self._topo_wf_expanded:
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
        self._topo_cached_audio = (
            "__output__", audio_data, audio_data, samplerate)
        self._topo_show_waveform(audio_data, samplerate)

    def _on_topo_resolve_error(self, message: str):
        self._topo_resolve_worker = None
        self._topo_wf_panel.waveform.set_loading(False)
        self._status_bar.showMessage(f"Preview error: {message}")

    # ── Multi-track output loading ────────────────────────────────────

    def _topo_load_multi_output(self, file_items):
        """Load and stack waveforms for multiple output entries."""
        self._topo_cancel_workers()
        self._on_topo_stop()

        topo = self._topo_topology
        if not topo or not self._source_dir:
            return

        items = []
        for fi in file_items:
            data = fi.data(0, Qt.UserRole)
            if not data or data[0] != "file":
                continue
            filename = data[1]
            entry = next((e for e in topo.entries
                          if e.output_filename == filename), None)
            if entry:
                stem = os.path.splitext(filename)[0]
                items.append((entry, stem))
        if not items:
            return

        if self._topo_wf_expanded:
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

    # ── Common waveform display ───────────────────────────────────────

    def _topo_show_waveform(self, audio_data, samplerate: int,
                            labels: list[str] | None = None):
        """Run WaveformLoadWorker and display result."""
        import numpy as np
        if audio_data is None or (isinstance(audio_data, np.ndarray)
                                  and audio_data.size == 0):
            self._topo_wf_panel.waveform.set_loading(False)
            return

        from ..waveform.compute import WaveformLoadWorker

        if self._topo_wf_expanded:
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

    # ── Playback ──────────────────────────────────────────────────────

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
