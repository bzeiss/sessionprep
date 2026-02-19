"""Analysis mixin: open/save/load session, analyze, prepare, session config tab."""

from __future__ import annotations

import copy
import os
from typing import Any

from PySide6.QtCore import Qt, Slot, QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from sessionpreplib.audio import AUDIO_EXTENSIONS
from sessionpreplib.config import default_config, flatten_structured_config
from sessionpreplib.detectors import default_detectors
from sessionpreplib.processors import default_processors
from sessionpreplib.utils import protools_sort_key

from .helpers import track_analysis_label
from .param_widgets import build_config_pages, load_config_widgets, read_config_widgets
from .report import render_track_detail_html
from .session_io import save_session as _save_session_file, load_session as _load_session_file
from .settings import build_defaults, resolve_config_preset
from .table_widgets import (
    _SortableItem, _make_analysis_cell,
    _TAB_FILE, _TAB_GROUPS, _TAB_SESSION, _TAB_SUMMARY,
    _PAGE_PROGRESS, _PAGE_TABS,
    _PHASE_ANALYSIS, _PHASE_SETUP,
)
from .theme import COLORS, FILE_COLOR_OK, FILE_COLOR_ERROR
from .worker import AnalyzeWorker, PrepareWorker


class AnalysisMixin:
    """Session lifecycle: open, save, load, analyze, prepare, session config tab.

    Mixed into ``SessionPrepWindow`` — not meant to be used standalone.
    """

    # ── Session config tab (per-session overrides) ────────────────────────

    def _build_session_settings_tab(self) -> QWidget:
        """Build a tree+stack config editor for per-session overrides."""
        page = QWidget()
        page.setAutoFillBackground(True)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)

        # Header row
        header = QHBoxLayout()
        header.setSpacing(8)
        self._session_preset_label = QLabel("Config Preset: —")
        self._session_preset_label.setStyleSheet(
            f"color: {COLORS['dim']}; font-style: italic;")
        header.addWidget(self._session_preset_label)
        header.addStretch()
        reset_btn = QPushButton("Reset to Preset Defaults")
        reset_btn.setToolTip(
            "Discard all session-specific changes and reload from the "
            "global config preset.")
        reset_btn.clicked.connect(self._on_session_config_reset)
        header.addWidget(reset_btn)
        layout.addLayout(header)

        # Tree + Stack
        splitter = QSplitter(Qt.Horizontal)

        self._session_tree = QTreeWidget()
        self._session_tree.setHeaderHidden(True)
        self._session_tree.setMinimumWidth(160)
        self._session_tree.setMaximumWidth(220)
        self._session_tree.currentItemChanged.connect(
            self._on_session_tree_selection)
        splitter.addWidget(self._session_tree)

        self._session_stack = QStackedWidget()
        splitter.addWidget(self._session_stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter, 1)

        # Build initial pages from the active global preset
        self._session_page_index: dict[int, int] = {}
        self._build_session_pages()

        self._session_tree.expandAll()
        first = self._session_tree.topLevelItem(0)
        if first:
            self._session_tree.setCurrentItem(first)

        return page

    def _build_session_pages(self):
        """Populate the session config tree + stack from the active preset."""

        def _register_page(tree_item, page):
            idx = self._session_stack.addWidget(page)
            self._session_page_index[id(tree_item)] = idx

        self._session_dawproject_templates_widget = build_config_pages(
            self._session_tree,
            self._active_preset(),
            self._session_widgets,
            _register_page,
            on_processor_enabled=self._on_processor_enabled_changed,
        )

    def _on_session_tree_selection(self, current, _previous):
        if current is None:
            return
        idx = self._session_page_index.get(id(current))
        if idx is not None:
            self._session_stack.setCurrentIndex(idx)

    def _init_session_config(self):
        """Snapshot the active global config preset into session config."""
        self._session_config = copy.deepcopy(self._active_preset())
        name = self._active_config_preset_name
        self._session_preset_label.setText(f"Config Preset: {name}")
        self._session_preset_label.setStyleSheet("")
        self._load_session_widgets(self._session_config)
        self._detail_tabs.setTabEnabled(_TAB_SESSION, True)

    def _load_session_widgets(self, preset: dict[str, Any]):
        """Load values from a config preset dict into session widgets."""
        self._loading_session_widgets = True
        try:
            self._load_session_widgets_inner(preset)
        finally:
            self._loading_session_widgets = False
        # Single refresh after all widgets are set
        if self._session:
            self._on_processor_enabled_changed(False)

    def _load_session_widgets_inner(self, preset: dict[str, Any]):
        """Inner loader — sets widget values without triggering column refresh."""
        load_config_widgets(
            self._session_widgets, preset,
            self._session_dawproject_templates_widget)

    def _read_session_config(self) -> dict[str, Any]:
        """Read current session widget values into a structured config dict."""
        return read_config_widgets(
            self._session_widgets,
            self._session_dawproject_templates_widget,
            fallback_daw_sections=self._active_preset().get(
                "daw_processors", {}),
        )

    def _on_session_config_reset(self):
        """Reset session config to the global config preset defaults."""
        preset = self._active_preset()
        self._session_config = copy.deepcopy(preset)
        self._load_session_widgets(self._session_config)
        self._status_bar.showMessage("Session config reset to preset defaults.")

    # ── Slots: file / analysis ────────────────────────────────────────────

    @Slot()
    def _on_open_path(self):
        start_dir = self._config.get("app", {}).get("default_project_dir", "") or ""
        path = QFileDialog.getExistingDirectory(
            self, "Select Session Directory", start_dir,
            QFileDialog.ShowDirsOnly,
        )
        if not path:
            return

        self._on_stop()
        self._source_dir = path
        self._track_table.set_source_dir(path)
        self._session = None
        self._summary = None
        self._current_track = None

        # Reset UI
        self._phase_tabs.setCurrentIndex(_PHASE_ANALYSIS)
        self._phase_tabs.setTabEnabled(_PHASE_SETUP, False)
        self._track_table.setRowCount(0)
        self._setup_table.setRowCount(0)
        self._summary_view.clear()
        self._file_report.clear()
        self._wf_container.setVisible(False)
        self._play_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._detail_tabs.setTabEnabled(_TAB_FILE, False)
        self._detail_tabs.setTabEnabled(_TAB_GROUPS, False)
        self._detail_tabs.setTabEnabled(_TAB_SESSION, False)
        self._detail_tabs.setCurrentIndex(_TAB_SUMMARY)
        self._right_stack.setCurrentIndex(_PAGE_TABS)
        self._session_config = None  # reset session overrides for new directory
        self._session_groups = []
        self._groups_tab_table.setRowCount(0)

        wav_files = sorted(
            f for f in os.listdir(path) if f.lower().endswith(AUDIO_EXTENSIONS)
        )

        if not wav_files:
            self._status_bar.showMessage(f"No audio files found in {path}")
            self._analyze_action.setEnabled(False)
            return

        self._track_table.setSortingEnabled(False)
        self._track_table.setRowCount(len(wav_files))
        for row, fname in enumerate(wav_files):
            item = _SortableItem(fname, protools_sort_key(fname))
            item.setForeground(FILE_COLOR_OK)
            self._track_table.setItem(row, 0, item)
            for col in range(1, 6):
                cell = _SortableItem("", "")
                cell.setForeground(QColor(COLORS["dim"]))
                self._track_table.setItem(row, col, cell)
        self._track_table.setSortingEnabled(True)
        self._auto_fit_track_table()

        self._analyze_action.setEnabled(True)
        self._status_bar.showMessage(
            f"Loaded {len(wav_files)} file(s) from {path}"
        )
        self.setWindowTitle("SessionPrep")

        # Auto-start analysis
        self._on_analyze()

    @Slot()
    def _on_save_session(self):
        """Save the current session state to a .spsession file."""
        if not self._session or not self._source_dir:
            return
        default_path = os.path.join(self._source_dir, "session.spsession")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Session", default_path,
            "SessionPrep Session (*.spsession);;All Files (*)",
        )
        if not path:
            return
        try:
            _save_session_file(path, {
                "source_dir": self._source_dir,
                "active_config_preset": self._active_config_preset_name,
                "session_config": self._session_config,
                "session_groups": self._session_groups,
                "daw_state": self._session.daw_state,
                "tracks": self._session.tracks,
            })
            self._status_bar.showMessage(f"Session saved to {path}")
        except Exception as exc:
            QMessageBox.critical(
                self, "Save Session Failed",
                f"Could not save session:\n\n{exc}",
            )

    @Slot()
    def _on_load_session(self):
        """Load a .spsession file and restore the full session state."""
        start_dir = self._source_dir or self._config.get("app", {}).get(
            "default_project_dir", "") or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Session", start_dir,
            "SessionPrep Session (*.spsession);;All Files (*)",
        )
        if not path:
            return

        try:
            data = _load_session_file(path)
        except Exception as exc:
            QMessageBox.critical(
                self, "Load Session Failed",
                f"Could not load session:\n\n{exc}",
            )
            return

        source_dir = data["source_dir"]
        if not os.path.isdir(source_dir):
            QMessageBox.warning(
                self, "Load Session",
                f"The session's audio directory no longer exists:\n\n{source_dir}\n\n"
                "Please move the files back or open the directory manually.",
            )
            return

        # ── Reset UI (same as _on_open_path but without auto-analyze) ────────
        self._on_stop()
        self._source_dir = source_dir
        self._track_table.set_source_dir(source_dir)
        self._session = None
        self._summary = None
        self._current_track = None

        self._phase_tabs.setCurrentIndex(_PHASE_ANALYSIS)
        self._phase_tabs.setTabEnabled(_PHASE_SETUP, False)
        self._track_table.setRowCount(0)
        self._setup_table.setRowCount(0)
        self._summary_view.clear()
        self._file_report.clear()
        self._wf_container.setVisible(False)
        self._play_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._detail_tabs.setTabEnabled(_TAB_FILE, False)
        self._detail_tabs.setTabEnabled(_TAB_GROUPS, False)
        self._detail_tabs.setTabEnabled(_TAB_SESSION, False)
        self._detail_tabs.setCurrentIndex(_TAB_SUMMARY)
        self._right_stack.setCurrentIndex(_PAGE_TABS)
        self._groups_tab_table.setRowCount(0)

        # ── Restore session-level state ───────────────────────────────────────
        preset_name = data.get("active_config_preset", "Default")
        self._active_config_preset_name = preset_name
        self._session_config = data.get("session_config")
        self._session_groups = data.get("session_groups", [])

        # ── Reconstruct SessionContext from saved tracks ──────────────────────
        from sessionpreplib.models import SessionContext
        from sessionpreplib.rendering import build_diagnostic_summary

        tracks = data["tracks"]
        flat = self._flat_config()

        # Re-instantiate detectors and processors (needed for label filtering)
        all_detectors = default_detectors()
        for d in all_detectors:
            d.configure(flat)
        all_processors = []
        for proc in default_processors():
            proc.configure(flat)
            if proc.enabled:
                all_processors.append(proc)
        all_processors.sort(key=lambda p: p.priority)

        session_config_flat = dict(default_config())
        session_config_flat.update(flat)
        session_config_flat["_source_dir"] = source_dir

        session = SessionContext(
            tracks=tracks,
            config=session_config_flat,
            groups={},
            detectors=all_detectors,
            processors=all_processors,
            daw_state=data.get("daw_state", {}),
            prepare_state="none",
        )

        self._session = session
        self._summary = build_diagnostic_summary(session)

        # ── Populate file list in track table ─────────────────────────────────
        self._track_table.setSortingEnabled(False)
        self._track_table.setRowCount(len(tracks))
        for row, track in enumerate(tracks):
            item = _SortableItem(track.filename, protools_sort_key(track.filename))
            item.setForeground(FILE_COLOR_OK if track.status == "OK" else FILE_COLOR_ERROR)
            self._track_table.setItem(row, 0, item)
            for col in range(1, 8):
                cell = _SortableItem("", "")
                cell.setForeground(QColor(COLORS["dim"]))
                self._track_table.setItem(row, col, cell)
        self._track_table.setSortingEnabled(True)

        # ── Populate all table widgets and tabs ───────────────────────────────
        self._populate_groups_tab()
        self._populate_group_preset_combo()
        self._populate_table(session)
        self._render_summary()

        # ── Enable post-analysis UI ───────────────────────────────────────────
        self._right_stack.setCurrentIndex(_PAGE_TABS)
        self._detail_tabs.setCurrentIndex(_TAB_SUMMARY)
        self._detail_tabs.setTabEnabled(_TAB_GROUPS, True)
        self._detail_tabs.setTabEnabled(_TAB_SESSION, True)
        self._phase_tabs.setTabEnabled(_PHASE_SETUP, True)
        self._populate_setup_table()
        self._analyze_action.setEnabled(True)
        self._save_session_action.setEnabled(True)
        self._update_prepare_button()
        self._auto_fit_track_table()

        ok_count = sum(1 for t in tracks if t.status == "OK")
        self._status_bar.showMessage(
            f"Session loaded: {ok_count}/{len(tracks)} tracks OK"
            " — click Reanalyze to refresh results"
        )
        self.setWindowTitle("SessionPrep")

    # ── Analyze ──────────────────────────────────────────────────────────

    @Slot()
    def _on_analyze(self):
        if not self._source_dir:
            return

        # Snapshot existing group assignments so we can restore after re-analysis
        self._prev_group_assignments = {}
        if self._session:
            self._prev_group_assignments = {
                t.filename: t.group for t in self._session.tracks if t.group}

        self._analyze_action.setEnabled(False)
        self._current_track = None
        self._detail_tabs.setTabEnabled(_TAB_FILE, False)

        # Initialise session config from global preset (first analysis)
        # or keep existing session config (re-analysis with user edits)
        if self._session_config is None:
            self._init_session_config()

        # Show progress page
        self._progress_label.setText("Analyzing…")
        self._right_stack.setCurrentIndex(_PAGE_PROGRESS)

        config = self._flat_config()
        config["_source_dir"] = self._source_dir
        if self._active_daw_processor:
            config["_fader_ceiling_db"] = self._active_daw_processor.fader_ceiling_db

        self._progress_bar.setRange(0, 0)  # indeterminate until first value

        self._worker = AnalyzeWorker(self._source_dir, config)
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.progress_value.connect(self._on_worker_progress_value)
        self._worker.track_analyzed.connect(self._on_track_analyzed)
        self._worker.track_planned.connect(self._on_track_planned)
        self._worker.finished.connect(self._on_analyze_done)
        self._worker.error.connect(self._on_analyze_error)
        self._worker.start()

    @Slot(str)
    def _on_worker_progress(self, message: str):
        self._progress_label.setText(message)
        self._status_bar.showMessage(message)

    @Slot(int, int)
    def _on_worker_progress_value(self, current: int, total: int):
        if self._progress_bar.maximum() != total:
            self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(current)

    @Slot(str, object)
    def _on_track_analyzed(self, filename: str, track):
        """Update the severity column for a track after detectors complete."""
        row = self._find_table_row(filename)
        if row < 0:
            return
        # Ch column
        ch_item = _SortableItem(str(track.channels), track.channels)
        ch_item.setForeground(QColor(COLORS["dim"]))
        self._track_table.setItem(row, 1, ch_item)
        # Analysis column
        _plain, html, _color, sort_key = track_analysis_label(track)
        lbl, item = _make_analysis_cell(html, sort_key)
        self._track_table.setItem(row, 2, item)
        self._track_table.setCellWidget(row, 2, lbl)

    @Slot(str, object)
    def _on_track_planned(self, filename: str, track):
        """Update classification and gain columns after processors complete."""
        row = self._find_table_row(filename)
        if row < 0:
            return

        # Re-evaluate severity now that processor results inform is_relevant()
        dets = self._session.detectors if self._session else None
        _plain, html, _color, sort_key = track_analysis_label(track, dets)
        lbl, item = _make_analysis_cell(html, sort_key)
        self._track_table.setItem(row, 2, item)
        self._track_table.setCellWidget(row, 2, lbl)

        # Remove previous cell widgets
        self._track_table.removeCellWidget(row, 3)
        self._track_table.removeCellWidget(row, 4)
        self._track_table.removeCellWidget(row, 5)

        from PySide6.QtWidgets import QDoubleSpinBox
        from .widgets import BatchComboBox
        from .theme import (
            FILE_COLOR_SILENT, FILE_COLOR_TRANSIENT, FILE_COLOR_SUSTAINED,
        )

        pr = (
            next(iter(track.processor_results.values()), None)
            if track.processor_results
            else None
        )
        if track.status != "OK":
            cls_item = _SortableItem("Error", "error")
            cls_item.setForeground(FILE_COLOR_ERROR)
            self._track_table.setItem(row, 3, cls_item)
            gain_item = _SortableItem("", 0.0)
            gain_item.setForeground(QColor(COLORS["dim"]))
            self._track_table.setItem(row, 4, gain_item)
        elif pr and pr.classification == "Silent":
            cls_item = _SortableItem("Silent", "silent")
            cls_item.setForeground(FILE_COLOR_SILENT)
            self._track_table.setItem(row, 3, cls_item)
            gain_item = _SortableItem("0.0 dB", 0.0)
            gain_item.setForeground(QColor(COLORS["dim"]))
            self._track_table.setItem(row, 4, gain_item)
        elif pr:
            cls_text = pr.classification or "Unknown"
            if "Transient" in cls_text:
                base_cls = "Transient"
            elif cls_text == "Skip":
                base_cls = "Skip"
            elif "Sustained" in cls_text:
                base_cls = "Sustained"
            else:
                base_cls = "Sustained"

            sort_item = _SortableItem(base_cls, base_cls.lower())
            self._track_table.setItem(row, 3, sort_item)

            combo = BatchComboBox()
            combo.addItems(["Transient", "Sustained", "Skip"])
            combo.blockSignals(True)
            combo.setCurrentText(base_cls)
            combo.blockSignals(False)
            combo.setProperty("track_filename", track.filename)
            self._style_classification_combo(combo, base_cls)
            combo.textActivated.connect(
                lambda text, c=combo: self._on_classification_changed(text, c))
            self._track_table.setCellWidget(row, 3, combo)

            gain_db = pr.gain_db
            gain_sort = _SortableItem(f"{gain_db:+.1f}", gain_db)
            self._track_table.setItem(row, 4, gain_sort)

            spin = QDoubleSpinBox()
            spin.setRange(-60.0, 60.0)
            spin.setSingleStep(0.1)
            spin.setDecimals(1)
            spin.setSuffix(" dB")
            spin.blockSignals(True)
            spin.setValue(gain_db)
            spin.blockSignals(False)
            spin.setProperty("track_filename", track.filename)
            spin.setEnabled(base_cls != "Skip")
            spin.setStyleSheet(
                f"QDoubleSpinBox {{ color: {COLORS['text']}; }}"
            )
            spin.valueChanged.connect(
                lambda value, s=spin: self._on_gain_changed(value, s))
            self._track_table.setCellWidget(row, 4, spin)

            # RMS Anchor combo (column 5)
            self._create_anchor_combo(row, track)

            # Group combo (column 6)
            self._create_group_combo(row, track)

            # Row background from group color
            self._apply_row_group_color(row, track.group)

        self._auto_fit_group_column()

    @Slot(object, object)
    def _on_analyze_done(self, session, summary):
        self._session = session
        self._summary = summary
        self._analyze_action.setEnabled(True)
        self._worker = None

        if not self._session_groups:
            # First analysis — load from Default group preset
            self._active_session_preset = "Default"
            self._merge_groups_from_preset()
            self._populate_group_preset_combo()
        else:
            # Re-analysis — restore previous group assignments by filename
            prev = self._prev_group_assignments
            for track in session.tracks:
                track.group = prev.get(track.filename)
            self._populate_groups_tab()
            self._refresh_group_combos()

        self._populate_table(session)
        self._render_summary()

        # Switch to tabs — summary tab
        self._right_stack.setCurrentIndex(_PAGE_TABS)
        self._detail_tabs.setCurrentIndex(_TAB_SUMMARY)
        self._detail_tabs.setTabEnabled(_TAB_GROUPS, True)
        self._detail_tabs.setTabEnabled(_TAB_SESSION, True)

        # Enable Session Setup phase now that analysis is available
        self._phase_tabs.setTabEnabled(_PHASE_SETUP, True)
        self._populate_setup_table()

        # Enable Prepare button; mark stale if previously prepared
        if session.prepare_state == "ready":
            session.prepare_state = "stale"
        self._update_prepare_button()

        self._save_session_action.setEnabled(True)

        ok_count = sum(1 for t in session.tracks if t.status == "OK")
        self._status_bar.showMessage(
            f"Analysis complete: {ok_count}/{len(session.tracks)} tracks OK"
        )

    @Slot(str)
    def _on_analyze_error(self, message: str):
        self._analyze_action.setEnabled(True)
        self._worker = None

        from .helpers import esc

        self._right_stack.setCurrentIndex(_PAGE_TABS)
        self._detail_tabs.setCurrentIndex(_TAB_SUMMARY)
        self._summary_view.setHtml(self._wrap_html(
            f'<div style="color:{COLORS["problems"]}; font-weight:bold;">'
            f'Analysis Error</div>'
            f'<div style="margin-top:8px;">{esc(message)}</div>'
        ))
        self._status_bar.showMessage(f"Error: {message}")

    # ── Prepare handlers ─────────────────────────────────────────────────

    @Slot()
    def _on_prepare(self):
        """Run the Prepare pipeline to generate processed audio files."""
        if not self._session or not self._source_dir:
            return
        if self._prepare_worker is not None:
            return  # already running

        output_folder = self._config.get("app", {}).get(
            "output_folder", "processed")
        output_dir = os.path.join(self._source_dir, output_folder)

        # Refresh pipeline config from current session widgets so that
        # processor enabled/disabled changes made after analysis take effect.
        self._session.config.update(self._flat_config())

        # Use the session's configured processors
        processors = list(self._session.processors) if self._session.processors else []
        if not processors:
            self._status_bar.showMessage("No audio processors enabled.")
            return

        self._prepare_action.setEnabled(False)
        self._status_bar.showMessage("Preparing processed files\u2026")
        self._prepare_progress.start("Preparing\u2026")

        self._prepare_worker = PrepareWorker(
            self._session, processors, output_dir)
        self._prepare_worker.progress.connect(self._on_prepare_progress)
        self._prepare_worker.progress_value.connect(
            self._on_prepare_progress_value)
        self._prepare_worker.finished.connect(self._on_prepare_done)
        self._prepare_worker.error.connect(self._on_prepare_error)
        self._prepare_worker.start()

    @Slot(str)
    def _on_prepare_progress(self, message: str):
        self._prepare_progress.set_message(message)
        self._status_bar.showMessage(message)

    @Slot(int, int)
    def _on_prepare_progress_value(self, current: int, total: int):
        self._prepare_progress.set_progress(current, total)

    @Slot()
    def _on_prepare_done(self):
        self._prepare_worker = None
        self._update_prepare_button()
        self._update_use_processed_action()
        prepared = sum(
            1 for t in self._session.tracks
            if t.processed_filepath is not None
        )
        errors = self._session.config.get("_prepare_errors", [])
        if errors:
            msg = f"Prepare complete: {prepared} file(s) written, {len(errors)} error(s)"
            self._prepare_progress.finish(msg)
            self._status_bar.showMessage(msg)
            detail = "\n".join(f"• {fn}: {err}" for fn, err in errors)
            QMessageBox.warning(
                self, "Prepare — errors",
                f"{len(errors)} file(s) could not be written:\n\n{detail}\n\n"
                "This is usually caused by a file being open in another "
                "application (e.g. the waveform player). Close the file "
                "and try again.",
            )
        else:
            msg = f"Prepare complete: {prepared} file(s) written"
            self._prepare_progress.finish(msg)
            self._status_bar.showMessage(msg)
        self._populate_setup_table()

    @Slot(str)
    def _on_prepare_error(self, message: str):
        self._prepare_worker = None
        self._prepare_action.setEnabled(True)
        self._prepare_progress.fail(message)
        self._status_bar.showMessage(f"Prepare failed: {message}")

    def _update_prepare_button(self):
        """Update the Prepare button text and enabled state based on prepare_state."""
        if not self._session:
            self._prepare_action.setEnabled(False)
            self._prepare_action.setText("Prepare")
            self._auto_group_action.setEnabled(False)
            return

        state = self._session.prepare_state
        self._prepare_action.setEnabled(True)
        self._auto_group_action.setEnabled(True)
        if state == "ready":
            self._prepare_action.setText("Prepare \u2713")
        elif state == "stale":
            self._prepare_action.setText("Prepare (!)")
        else:
            self._prepare_action.setText("Prepare")

    def _mark_prepare_stale(self):
        """Mark prepared files as stale if they were previously ready."""
        if self._session and self._session.prepare_state == "ready":
            self._session.prepare_state = "stale"
            self._update_prepare_button()
            self._update_use_processed_action()

    @Slot(bool)
    def _on_processor_enabled_changed(self, _checked: bool):
        """Live-update session.processors and Processing column when a
        processor enabled toggle changes in the session config widgets."""
        if not self._session:
            return
        if getattr(self, "_loading_session_widgets", False):
            return
        # Re-evaluate which processors are enabled from current widget values
        flat = self._flat_config()
        new_processors = []
        for proc in default_processors():
            proc.configure(flat)
            if proc.enabled:
                new_processors.append(proc)
        new_processors.sort(key=lambda p: p.priority)
        self._session.processors = new_processors
        self._refresh_processing_column()
        self._mark_prepare_stale()

    def _refresh_processing_column(self):
        """Rebuild all Processing column buttons from the current
        session.processors list."""
        if not self._session:
            return
        processors = self._session.processors
        for row in range(self._track_table.rowCount()):
            fname_item = self._track_table.item(row, 0)
            if not fname_item:
                continue
            track = next(
                (t for t in self._session.tracks if t.filename == fname_item.text()),
                None,
            )
            if not track or track.status != "OK":
                continue
            # Remove old widget and recreate
            self._track_table.removeCellWidget(row, 7)
            self._create_processing_button(row, track)

    # ── Presentation refresh ─────────────────────────────────────────────

    def _refresh_presentation(self):
        """Re-render all UI after presentation-only config changes (e.g. report_as).

        Reconfigures detector instances in-place, rebuilds the diagnostic
        summary, and refreshes all visible components — without re-reading
        audio or re-running analysis.
        """
        if not self._session:
            return

        # 1. Reconfigure detector instances with updated flat config
        flat = self._flat_config()
        for d in self._session.detectors:
            d.configure(flat)

        # 2. Rebuild diagnostic summary (bucketing depends on report_as)
        from sessionpreplib.rendering import build_diagnostic_summary
        self._summary = build_diagnostic_summary(self._session)

        # 3. Re-render summary HTML
        self._render_summary()

        # 4. Refresh track table Analysis column
        self._refresh_analysis_column()

        # 5. Re-render current track detail
        if self._current_track:
            html = render_track_detail_html(
                self._current_track, self._session,
                show_clean=self._show_clean, verbose=self._verbose)
            self._file_report.setHtml(self._wrap_html(html))

        # 6. Refresh overlay menu (skipped detectors filtered out)
        if self._current_track:
            all_issues = []
            for det_result in self._current_track.detector_results.values():
                all_issues.extend(getattr(det_result, "issues", []))
            self._update_overlay_menu(all_issues)

        # 7. Apply any concurrent GUI-only changes
        cmap = self._config.get("app", {}).get("spectrogram_colormap", "magma")
        self._waveform.set_colormap(cmap)

        self._status_bar.showMessage("Preferences saved (display refreshed).")

    def _refresh_analysis_column(self):
        """Update the Analysis column for all rows using current detector config."""
        if not self._session:
            return
        track_map = {t.filename: t for t in self._session.tracks}
        dets = self._session.detectors if hasattr(self._session, 'detectors') else None
        self._track_table.setSortingEnabled(False)
        for row in range(self._track_table.rowCount()):
            fname_item = self._track_table.item(row, 0)
            if not fname_item:
                continue
            track = track_map.get(fname_item.text())
            if not track:
                continue
            _plain, html, _color, sort_key = track_analysis_label(track, dets)
            lbl, item = _make_analysis_cell(html, sort_key)
            self._track_table.setItem(row, 2, item)
            self._track_table.setCellWidget(row, 2, lbl)
        self._track_table.setSortingEnabled(True)
