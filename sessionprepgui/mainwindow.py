"""Main application window for SessionPrep GUI."""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from typing import Any

from PySide6.QtCore import Qt, Slot, QSize
from PySide6.QtGui import (
    QAction, QFont, QIcon, QKeySequence, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QStatusBar,
    QWidget,
)

from sessionpreplib.config import (
    default_config,
    flatten_structured_config,
)
from sessionpreplib.detectors import detector_help_map

from .settings import (
    load_config, save_config,
    resolve_config_preset, build_defaults,
)
from .theme import COLORS, apply_dark_theme
from .log import dbg
from .prefs import PreferencesDialog
from .detail import render_track_detail_html, PlaybackController, DetailMixin
from .waveform import WaveformPanel, WaveformLoadWorker
from .widgets import ProgressPanel
from .analysis import (
    AnalysisMixin,
    AudioLoadWorker, BatchReanalyzeWorker, DawCheckWorker,
    DawFetchWorker, DawTransferWorker, PrepareWorker,
)
from .tracks import (
    TrackColumnsMixin, GroupsMixin,
    _HelpBrowser, _DraggableTrackTable, _TAB_FILE, _TAB_GROUPS, _TAB_SESSION,
    _PAGE_TABS,
    _PHASE_ANALYSIS, _PHASE_SETUP,
)
from .daw import DawMixin
from .topology import TopologyMixin
from .batch import BatchQueueDock, BatchManager


class SessionPrepWindow(  # pylint: disable=too-many-ancestors
    QMainWindow,
    AnalysisMixin, TrackColumnsMixin,
                        GroupsMixin, DawMixin, TopologyMixin, DetailMixin):
    def __init__(self):
        t_init = time.perf_counter()
        super().__init__()
        self.setWindowTitle("SessionPrep")
        self.setWindowIcon(_app_icon())

        # Size and center on the primary screen, clamped to available space
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            w = min(1600, avail.width() - 40)
            h = min(950, avail.height() - 40)
            self.resize(w, h)
            self.move(
                avail.x() + (avail.width() - w) // 2,
                avail.y() + (avail.height() - h) // 2,
            )
        else:
            self.resize(1600, 950)

        self._session = None
        self._summary = None
        self._source_dir = None
        self._topology_dir = None  # path to sp_01_tracklayout/ after Phase 1 Apply
        self._topo_source_tracks = []  # original source tracks for Phase 1 input table
        self._topo_topology = None  # Phase 1 topology (separate from session.topology)
        self._worker = None
        self._batch_worker: BatchReanalyzeWorker | None = None
        self._batch_filenames: set[str] = set()
        self._wf_worker: WaveformLoadWorker | None = None
        self._audio_load_worker: AudioLoadWorker | None = None
        self._current_track = None
        self._session_groups: list[dict] = []
        self._prev_group_assignments: dict[str, str | None] = {}
        self._active_session_preset: str = "Default"
        self._recursive_scan: bool = False
        self._session_config: dict[str, Any] | None = None
        self._session_widgets: dict[str, list[tuple[str, QWidget]]] = {}
        self._pt_utils_window = None  # singleton Pro Tools Utils window

        t0 = time.perf_counter()
        self._detector_help = detector_help_map()
        dbg(f"detector_help_map: {(time.perf_counter() - t0) * 1000:.1f} ms")

        self._daw_check_worker: DawCheckWorker | None = None
        self._pending_after_check = None
        self._daw_fetch_worker: DawFetchWorker | None = None
        self._daw_transfer_worker: DawTransferWorker | None = None
        self._prepare_worker: PrepareWorker | None = None

        self._batch_manager = BatchManager(self)
        self._batch_manager.finished.connect(self._on_batch_finished)
        self._batch_manager.item_finished.connect(self._on_batch_item_finished)

        # Load persistent GUI configuration (four-section structure)
        t0 = time.perf_counter()
        self._config = load_config()
        dbg(f"load_config: {(time.perf_counter() - t0) * 1000:.1f} ms")
        self._active_config_preset_name: str = self._config.get(
            "app", {}).get("active_config_preset", "Default")
        self._recursive_scan = self._config.get(
            "app", {}).get("recursive_scan", False)

        # Instantiate and configure DAW processors
        t0 = time.perf_counter()
        self._daw_processors: list = []
        self._active_daw_processor = None
        self._configure_daw_processors()
        dbg(f"daw_processors: {(time.perf_counter() - t0) * 1000:.1f} ms")

        # Playback controller
        t0 = time.perf_counter()
        self._playback = PlaybackController(self)
        self._playback.cursor_updated.connect(self._on_cursor_updated)
        self._playback.playback_finished.connect(self._on_playback_finished)
        self._playback.error.connect(self._on_playback_error)
        dbg(f"PlaybackController (sounddevice): "
            f"{(time.perf_counter() - t0) * 1000:.1f} ms")

        t0 = time.perf_counter()
        self._init_ui()
        dbg(f"_init_ui: {(time.perf_counter() - t0) * 1000:.1f} ms")

        self._batch_dock = BatchQueueDock(self)
        self._batch_dock.load_requested.connect(self._on_load_batch_item)
        self._batch_dock.run_batch_requested.connect(self._batch_manager.start_batch)
        self._batch_dock.run_single_requested.connect(self._batch_manager.start_single)
        self._batch_dock.open_project_requested.connect(self._on_open_batch_project_folder)
        self.addDockWidget(Qt.RightDockWidgetArea, self._batch_dock)
        self._batch_dock.hide()  # hidden by default

        self._batch_manager.started.connect(self._on_batch_started)
        self._batch_manager.batch_progress_value.connect(self._batch_dock.update_progress)
        self._batch_manager.batch_progress_message.connect(self._status_bar.showMessage)

        t0 = time.perf_counter()
        apply_dark_theme(self)
        dbg(f"apply_dark_theme: {(time.perf_counter() - t0) * 1000:.1f} ms")

        # Spacebar toggles play/stop
        self._space_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self._space_shortcut.activated.connect(self._on_toggle_play)

        dbg(f"SessionPrepWindow.__init__ total: "
            f"{(time.perf_counter() - t_init) * 1000:.1f} ms")

    # ── Config helpers ───────────────────────────────────────────────────

    def _flat_config(self) -> dict[str, Any]:
        """Return a flat config dict from the active config preset.

        If a session config exists (user edited session Config tab), the
        current widget values take precedence over the global config preset.
        """
        if self._session_config is not None:
            # Read live widget values so edits take effect immediately
            structured = self._read_session_config()
            flat = dict(default_config())
            flat.update(flatten_structured_config(structured))
            return flat
        preset = resolve_config_preset(
            self._config, self._active_config_preset_name)
        flat = dict(default_config())
        flat.update(flatten_structured_config(preset))
        return flat

    def _active_preset(self) -> dict[str, Any]:
        """Return the active config preset's structured dict."""
        return resolve_config_preset(
            self._config, self._active_config_preset_name)

    # ── UI setup ──────────────────────────────────────────────────────────

    def _init_ui(self):
        self._init_menus()

        # ── Top-level phase tabs ──────────────────────────────────────────
        self._phase_tabs = QTabWidget()
        self._phase_tabs.setObjectName("phaseTabs")
        self._phase_tabs.setDocumentMode(True)

        # Tab 0 — Phase 1: Track Layout (landing page)
        self._phase_tabs.addTab(
            self._build_topology_page(),
            "Phase 1: Track Layout")

        # Tab 1 — Phase 2: Analysis & Preparation
        analysis_page = QWidget()
        analysis_layout = QVBoxLayout(analysis_page)
        analysis_layout.setContentsMargins(0, 0, 0, 0)
        analysis_layout.setSpacing(0)
        self._init_analysis_toolbar()
        analysis_layout.addWidget(self._analysis_toolbar)
        self._main_splitter = QSplitter(Qt.Horizontal)
        self._main_splitter.addWidget(self._build_left_panel())
        self._main_splitter.addWidget(self._build_right_panel())
        self._main_splitter.setStretchFactor(0, 3)
        self._main_splitter.setStretchFactor(1, 2)
        self._main_splitter.setSizes([620, 480])
        analysis_layout.addWidget(self._main_splitter, 1)
        self._phase_tabs.addTab(
            analysis_page, "Phase 2: Analysis && Preparation")
        self._phase_tabs.setTabEnabled(_PHASE_ANALYSIS, False)

        # Tab 2 — Phase 3: DAW Transfer
        self._phase_tabs.addTab(
            self._build_setup_page(), "Phase 3: DAW Transfer")
        self._phase_tabs.setTabEnabled(_PHASE_SETUP, False)
        self._phase_tabs.currentChanged.connect(self._on_phase_tab_changed)

        self.setCentralWidget(self._phase_tabs)

        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet(
            "QStatusBar { background-color: #1e1e1e; border-top: 1px solid #444; }"
            "QStatusBar::item { border: none; }")
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Open a directory containing .wav / .aif files to begin.")

    def _init_menus(self):
        file_menu = self.menuBar().addMenu("&File")

        open_action = QAction("Open &Folder...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._on_open_path)
        file_menu.addAction(open_action)

        load_session_action = QAction("&Load Session...", self)
        load_session_action.setShortcut("Ctrl+Shift+O")
        load_session_action.triggered.connect(self._on_load_session)
        file_menu.addAction(load_session_action)

        load_batch_action = QAction("Load Batch Queue...", self)
        load_batch_action.triggered.connect(self._on_load_batch_queue)
        file_menu.addAction(load_batch_action)

        file_menu.addSeparator()

        self._save_session_action = QAction("&Save Session...", self)
        self._save_session_action.setShortcut("Ctrl+S")
        self._save_session_action.setEnabled(False)
        self._save_session_action.triggered.connect(self._on_save_session)
        file_menu.addAction(self._save_session_action)

        self._save_batch_action = QAction("Save Batch Queue...", self)
        self._save_batch_action.triggered.connect(self._on_save_batch_queue)
        file_menu.addAction(self._save_batch_action)

        file_menu.addSeparator()

        self._batch_mode_action = QAction("Batch Processing Mode", self)
        self._batch_mode_action.setCheckable(True)
        self._batch_mode_action.toggled.connect(self._on_batch_mode_toggled)
        file_menu.addAction(self._batch_mode_action)

        file_menu.addSeparator()

        prefs_action = QAction("&Preferences...", self)
        prefs_action.setShortcut("Ctrl+,")
        prefs_action.setMenuRole(QAction.MenuRole.PreferencesRole)
        prefs_action.triggered.connect(self._on_preferences)
        file_menu.addAction(prefs_action)

        file_menu.addSeparator()

        about_action = QAction("&About SessionPrep", self)
        about_action.setMenuRole(QAction.MenuRole.AboutRole)
        about_action.triggered.connect(self._on_about)
        file_menu.addAction(about_action)

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # ── Tools menu ────────────────────────────────────────────────
        tools_menu = self.menuBar().addMenu("&Tools")

        self._pt_utils_action = QAction("Pro Tools Utils\u2026", self)
        self._pt_utils_action.triggered.connect(self._on_open_pt_utils)
        tools_menu.addAction(self._pt_utils_action)
        self._update_tools_menu()

    @Slot()
    def _on_save_batch_queue(self):
        if not self._batch_dock.has_items:
            QMessageBox.information(self, "Save Batch Queue", "The batch queue is empty.")
            return

        start_dir = self._config.get("app", {}).get("default_project_dir", "") or ""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Batch Queue", start_dir,
            "Batch Queue Files (*.spbatch);;All Files (*)"
        )
        if not path:
            return

        try:
            state = self._batch_dock.get_state()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            self._status_bar.showMessage(f"Batch queue saved to {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Batch Queue Failed", f"Could not save batch queue:\n\n{exc}")

    @Slot()
    def _on_load_batch_queue(self):
        start_dir = self._config.get("app", {}).get("default_project_dir", "") or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Batch Queue", start_dir,
            "Batch Queue Files (*.spbatch);;All Files (*)"
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception as exc:
            QMessageBox.critical(self, "Load Batch Queue Failed", f"Could not load batch queue:\n\n{exc}")
            return

        if not isinstance(state, list):
            QMessageBox.critical(self, "Invalid File", "The selected file is not a valid batch queue format.")
            return

        append = False
        if self._batch_dock.has_items:
            msg = QMessageBox(self)
            msg.setWindowTitle("Load Batch Queue")
            msg.setText("How would you like to load the batch queue?")

            replace_btn = msg.addButton("Replace", QMessageBox.AcceptRole)
            append_btn = msg.addButton("Append", QMessageBox.AcceptRole)
            cancel_btn = msg.addButton("Cancel", QMessageBox.RejectRole)

            msg.exec()

            if msg.clickedButton() == cancel_btn:
                return
            if msg.clickedButton() == append_btn:
                append = True

        self._batch_dock.load_state(state, append=append)

        self._batch_mode_action.setChecked(True)
        self._clear_workspace()
        self._status_bar.showMessage(f"Batch queue loaded from {path}")

    @Slot(bool)
    def _on_batch_mode_toggled(self, checked: bool):
        self._batch_dock.setVisible(checked)
        self._update_daw_lifecycle_buttons()

    @Slot(object)
    def _on_load_batch_item(self, item):
        if self._session:
            reply = QMessageBox.question(
                self, "Load Session",
                "You have an active session in the workspace. Discard current workspace and load from queue?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
        self._restore_session_state(item.session_state)
        self._batch_dock.remove_item(item.id)

    @Slot(object)
    def _on_open_batch_project_folder(self, item):
        daw_processor = None
        for dp in self._daw_processors:
            if dp.id == item.daw_processor_id:
                daw_processor = dp
                break

        if not daw_processor:
            QMessageBox.warning(
                self, "Open Project Folder",
                f"The configured DAW processor '{item.daw_processor_name}' is not available."
            )
            return

        project_dir = getattr(daw_processor, "project_dir", "").strip()

        if not project_dir:
            QMessageBox.information(
                self, "Open Project Folder",
                f"No project directory configured for {daw_processor.name}."
            )
            return

        import os
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        # For DAWs like DAWproject that specify a full output path (e.g. .dawproject file)
        # we try to just open the directory containing it if the output path exists.
        if item.output_path and os.path.exists(os.path.dirname(item.output_path)):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(item.output_path)))
            return

        # Otherwise, try the conventional project directory approach
        target_path = os.path.join(project_dir, item.project_name) if item.project_name else project_dir

        if os.path.isdir(target_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(target_path))
        elif os.path.isdir(project_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(project_dir))
        else:
            QMessageBox.warning(
                self, "Open Project Folder",
                f"The directory does not exist:\n\n{project_dir}"
            )

    @Slot()
    def _on_batch_started(self):
        self._batch_dock.set_running_state(True)

    @Slot()
    def _on_batch_finished(self):
        self._batch_dock.set_running_state(False)
        self._status_bar.showMessage("Batch processing complete.")

    @Slot(str, str, str)
    def _on_batch_item_finished(self, item_id: str, status: str, result_text: str):
        self._batch_dock.update_item(item_id, status, result_text)

    def _init_analysis_toolbar(self):
        self._analysis_toolbar = QToolBar("Analysis")
        self._analysis_toolbar.setIconSize(QSize(16, 16))
        self._analysis_toolbar.setMovable(False)
        self._analysis_toolbar.setFloatable(False)

        self._open_action = QAction("Open Folder", self)
        self._open_action.triggered.connect(self._on_open_path)
        self._analysis_toolbar.addAction(self._open_action)

        self._analysis_toolbar.addSeparator()

        self._analyze_action = QAction("Reanalyze", self)
        self._analyze_action.setEnabled(False)
        self._analyze_action.triggered.connect(self._on_analyze)
        self._analysis_toolbar.addAction(self._analyze_action)

        self._analysis_toolbar.addSeparator()

        self._analysis_toolbar.addWidget(QLabel("  Group:"))
        self._group_preset_combo = QComboBox()
        self._group_preset_combo.setMinimumWidth(120)
        self._populate_group_preset_combo()
        self._group_preset_combo.currentTextChanged.connect(
            self._on_group_preset_changed)
        self._analysis_toolbar.addWidget(self._group_preset_combo)

        self._analysis_toolbar.addSeparator()

        self._analysis_toolbar.addWidget(QLabel("  Config:"))
        self._config_preset_combo = QComboBox()
        self._config_preset_combo.setMinimumWidth(120)
        self._populate_config_preset_combo()
        self._config_preset_combo.currentTextChanged.connect(
            self._on_toolbar_config_preset_changed)
        self._analysis_toolbar.addWidget(self._config_preset_combo)

        # ── Spacer ─────────────────────────────────────────────────────
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._analysis_toolbar.addWidget(spacer)

        # ── Right: Auto-Group + Prepare buttons ──────────────────────
        self._auto_group_action = QAction("Auto-Group", self)
        self._auto_group_action.setEnabled(False)
        self._auto_group_action.triggered.connect(self._on_auto_group)
        self._analysis_toolbar.addAction(self._auto_group_action)

        self._prepare_action = QAction("Prepare", self)
        self._prepare_action.setEnabled(False)
        self._prepare_action.triggered.connect(self._on_prepare)
        self._analysis_toolbar.addAction(self._prepare_action)

    def _populate_config_preset_combo(self):
        """Fill the config-preset combo from config, preserving the current selection."""
        presets = self._config.get("config_presets",
                                   build_defaults().get("config_presets", {}))
        active = self._active_config_preset_name
        self._config_preset_combo.blockSignals(True)
        self._config_preset_combo.clear()
        for name in presets:
            self._config_preset_combo.addItem(name)
        idx = self._config_preset_combo.findText(active)
        if idx >= 0:
            self._config_preset_combo.setCurrentIndex(idx)
        elif self._config_preset_combo.count() > 0:
            self._config_preset_combo.setCurrentIndex(0)
        self._config_preset_combo.blockSignals(False)

    def _populate_group_preset_combo(self):
        """Fill the group-preset combo from config, preserving the current selection."""
        presets = self._config.get("group_presets",
                                   build_defaults().get("group_presets", {}))
        active = self._active_session_preset
        self._group_preset_combo.blockSignals(True)
        self._group_preset_combo.clear()
        for name in presets:
            self._group_preset_combo.addItem(name)
        idx = self._group_preset_combo.findText(active)
        if idx >= 0:
            self._group_preset_combo.setCurrentIndex(idx)
        elif self._group_preset_combo.count() > 0:
            self._group_preset_combo.setCurrentIndex(0)
        self._group_preset_combo.blockSignals(False)

    # ── Panel builders ────────────────────────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Track table
        self._track_table = _DraggableTrackTable()
        self._track_table.setColumnCount(8)
        self._track_table.setHorizontalHeaderLabels(
            ["File", "Ch", "Analysis", "Classification", "Gain",
             "RMS Anchor", "Group", "Processing"]
        )
        self._track_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._track_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self._track_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._track_table.verticalHeader().setDefaultSectionSize(24)
        self._track_table.verticalHeader().setVisible(False)
        self._track_table.setMinimumWidth(300)
        self._track_table.setShowGrid(True)
        self._track_table.setAlternatingRowColors(True)
        self._track_table.setSortingEnabled(True)

        header = self._track_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        header.setSectionResizeMode(4, QHeaderView.Interactive)
        header.setSectionResizeMode(5, QHeaderView.Interactive)
        header.setSectionResizeMode(6, QHeaderView.Interactive)
        header.setSectionResizeMode(7, QHeaderView.Interactive)
        header.resizeSection(1, 30)
        header.resizeSection(2, 150)
        header.resizeSection(3, 120)
        header.resizeSection(4, 90)
        header.resizeSection(5, 100)
        header.resizeSection(6, 140)
        header.resizeSection(7, 130)

        self._track_table.cellClicked.connect(self._on_row_clicked)
        self._track_table.currentCellChanged.connect(self._on_current_cell_changed)
        layout.addWidget(self._track_table)

        return panel

    def _build_right_panel(self) -> QWidget:
        """Build the right-hand side: a stacked widget that toggles between
        a progress page (during analysis) and a tab widget (after analysis).
        """
        self._right_stack = QStackedWidget()
        self._right_stack.setMinimumWidth(400)

        # ── Page 0: progress ──────────────────────────────────────────────
        progress_page = QWidget()
        progress_layout = QVBoxLayout(progress_page)
        progress_layout.setContentsMargins(40, 0, 40, 0)

        progress_layout.addStretch(2)

        self._progress_label = QLabel("Analyzing\u2026")
        self._progress_label.setAlignment(Qt.AlignCenter)
        self._progress_label.setStyleSheet(
            f"color: {COLORS['dim']}; font-size: 11pt;"
        )
        progress_layout.addWidget(self._progress_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # indeterminate
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(6)
        progress_layout.addWidget(self._progress_bar)

        progress_layout.addStretch(3)
        self._right_stack.addWidget(progress_page)  # index 0

        # ── Page 1: tabs (Summary / File) ─────────────────────────────────
        self._detail_tabs = QTabWidget()
        self._detail_tabs.setDocumentMode(True)
        self._detail_tabs.currentChanged.connect(self._on_detail_tab_changed)

        # Summary tab — single QTextBrowser
        self._summary_view = self._make_report_browser()
        self._detail_tabs.addTab(self._summary_view, "Summary")

        # File tab — vertical splitter (report + waveform)
        self._file_splitter = QSplitter(Qt.Vertical)

        self._file_report = self._make_report_browser()
        self._file_splitter.addWidget(self._file_report)

        # Waveform panel (toolbar + waveform + transport)
        self._wf_panel = WaveformPanel(analysis_mode=True)
        self._wf_panel.play_clicked.connect(self._on_play)
        self._wf_panel.stop_clicked.connect(self._on_stop)
        self._wf_panel.position_clicked.connect(self._on_waveform_seek)
        self._wf_panel.display_mode_changed.connect(self._on_display_mode_changed)
        self._wf_panel.waveform.set_invert_scroll(
            self._config.get("app", {}).get("invert_scroll", "default"))

        # Backward-compat aliases for DetailMixin / other mixins
        self._waveform = self._wf_panel.waveform
        self._wf_container = self._wf_panel
        self._overlay_btn = self._wf_panel.overlay_btn
        self._overlay_menu = self._wf_panel.overlay_menu
        self._markers_toggle = self._wf_panel.markers_toggle
        self._rms_lr_toggle = self._wf_panel.rms_lr_toggle
        self._rms_avg_toggle = self._wf_panel.rms_avg_toggle
        self._wf_settings_btn = self._wf_panel.wf_settings_btn
        self._spec_settings_btn = self._wf_panel.spec_settings_btn
        self._wf_action = self._wf_panel.wf_action
        self._spec_action = self._wf_panel.spec_action
        self._display_mode_btn = self._wf_panel.display_mode_btn
        self._cmap_group = self._wf_panel.cmap_group
        self._play_btn = self._wf_panel.play_btn
        self._stop_btn = self._wf_panel.stop_btn
        self._time_label = self._wf_panel.time_label

        # Connect spectrogram action groups to DetailMixin slots
        self._wf_panel.fft_group.triggered.connect(self._on_spec_fft_changed)
        self._wf_panel.win_group.triggered.connect(self._on_spec_window_changed)
        self._wf_panel.cmap_group.triggered.connect(self._on_spec_cmap_changed)
        self._wf_panel.floor_group.triggered.connect(self._on_spec_floor_changed)
        self._wf_panel.ceil_group.triggered.connect(self._on_spec_ceil_changed)

        self._file_splitter.addWidget(self._wf_panel)

        self._file_splitter.setStretchFactor(0, 3)
        self._file_splitter.setStretchFactor(1, 1)
        self._file_splitter.setSizes([500, 180])

        self._detail_tabs.addTab(self._file_splitter, "File")
        self._detail_tabs.setTabEnabled(_TAB_FILE, False)

        # Groups tab — session-local group editor
        self._detail_tabs.addTab(self._build_groups_tab(), "Groups")
        self._detail_tabs.setTabEnabled(_TAB_GROUPS, False)

        # Config tab — per-session config overrides
        self._detail_tabs.addTab(
            self._build_session_settings_tab(), "Config")
        self._detail_tabs.setTabEnabled(_TAB_SESSION, False)

        # Container for tabs + prepare progress panel
        tabs_container = QWidget()
        tabs_layout = QVBoxLayout(tabs_container)
        tabs_layout.setContentsMargins(0, 0, 0, 0)
        tabs_layout.setSpacing(0)
        tabs_layout.addWidget(self._detail_tabs, 1)

        # Prepare progress panel (hidden by default)
        self._prepare_progress = ProgressPanel()
        tabs_layout.addWidget(self._prepare_progress)

        self._right_stack.addWidget(tabs_container)  # index 1

        # Start on the tabs page (summary empty until first analysis)
        self._right_stack.setCurrentIndex(_PAGE_TABS)

        return self._right_stack

    @Slot(int)
    def _on_detail_tab_changed(self, index: int):
        """Explicitly hide the File tab content when leaving it.

        QTabWidget with setDocumentMode(True) can fail to fully hide
        the previous tab's widget (QSplitter + custom-painted waveform),
        causing visual bleed-through on other tabs.  Work around this by
        explicitly managing _file_splitter visibility.
        """
        fs = getattr(self, "_file_splitter", None)
        if fs is not None:
            fs.setVisible(index == _TAB_FILE)

    def _make_report_browser(self):
        """Create a consistently styled QTextBrowser for reports."""
        browser = _HelpBrowser(self._detector_help)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.Monospace)
        browser.setFont(font)
        return browser

    # ── Slots: phase tabs ─────────────────────────────────────────────────

    @Slot(int)
    def _on_phase_tab_changed(self, index: int):
        if index == _PHASE_SETUP:
            self._setup_table.resizeColumnsToContents()
            # Shrink the splitter's left pane to fit the table content
            total = sum(
                self._setup_table.columnWidth(c)
                for c in range(self._setup_table.columnCount())
            ) + self._setup_table.verticalHeader().width() + 30  # margin
            remaining = self._setup_splitter.width() - total
            if remaining > 0:
                self._setup_splitter.setSizes([total, remaining])

    # ── HTML helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _wrap_html(body: str) -> str:
        """Wrap HTML content in a styled <body> tag."""
        return (
            f'<body style="background-color:{COLORS["bg"]}; color:{COLORS["text"]};'
            f' font-family:Consolas,monospace; font-size:10pt; padding:12px;">'
            f'{body}</body>'
        )

    # ── Preferences ───────────────────────────────────────────────────────

    @Slot()
    def _on_preferences(self):
        old_scale = self._config.get("app", {}).get("scale_factor", 1.0)
        old_preset = copy.deepcopy(self._active_preset())

        dlg = PreferencesDialog(self._config, parent=self)
        dlg.exec()
        if dlg.saved:
            self._config = dlg.result_config()
            save_config(self._config)
            self._active_config_preset_name = self._config.get(
                "app", {}).get("active_config_preset", "Default")
            self._status_bar.showMessage("Preferences saved.")
            self._waveform.set_invert_scroll(
                self._config.get("app", {}).get("invert_scroll", "default"))

            # Refresh preset combos (presets may have been added/removed/renamed)
            self._populate_group_preset_combo()
            self._populate_config_preset_combo()

            # Offer to merge if the session's active preset was modified
            if self._session:
                preset_name = self._active_session_preset
                presets = self._config.get("group_presets",
                                          build_defaults().get("group_presets", {}))
                if preset_name in presets:
                    preset_groups = presets[preset_name]
                    if preset_groups != self._session_groups:
                        ans = QMessageBox.question(
                            self, "Update session groups?",
                            f'The group preset \u201c{preset_name}\u201d'
                            " has changed.\n\n"
                            "Update the current session\u2019s groups"
                            " to match?\n\n"
                            "\u2022 Track assignments will be preserved"
                            " where group names match.\n"
                            "\u2022 Unmatched tracks will be set to"
                            " (None).",
                            QMessageBox.Yes | QMessageBox.No,
                            QMessageBox.Yes,
                        )
                        if ans == QMessageBox.Yes:
                            self._merge_groups_from_preset()
                            self._status_bar.showMessage(
                                "Session groups updated from preset.")

            # Re-configure DAW processors (enabled flag may have changed)
            self._configure_daw_processors()
            self._populate_daw_combo()
            self._daw_check_label.setText("")
            self._update_daw_lifecycle_buttons()
            self._update_tools_menu()

            # Update Pro Tools Utils window if open
            if self._pt_utils_window is not None:
                self._pt_utils_window.update_config(self._config)

            if self._source_dir:
                from sessionpreplib.config import strip_presentation_keys
                new_preset = self._active_preset()
                old_stripped = strip_presentation_keys(old_preset)
                new_stripped = strip_presentation_keys(new_preset)
                if new_stripped != old_stripped:
                    if self._session_config is not None:
                        # Session has local config — don't auto-re-analyze
                        preset_name = self._active_config_preset_name
                        QMessageBox.information(
                            self, "Config preset updated",
                            f"The config preset \u201c{preset_name}\u201d"
                            " has been updated in Preferences.\n\n"
                            "Your current session still uses its own"
                            " config. To apply the new preset defaults,"
                            " use \u201cReset to Preset Defaults\u201d"
                            " in the Config tab or switch presets via"
                            " the toolbar.",
                        )
                    else:
                        self._on_analyze()
                elif new_preset != old_preset:
                    # Only presentation keys changed — lightweight refresh
                    self._refresh_presentation()
                else:
                    # GUI-only change — just refresh reports and colormap
                    self._render_summary()
                    cmap = self._config.get("app", {}).get(
                        "spectrogram_colormap", "magma")
                    self._waveform.set_colormap(cmap)
                    if self._current_track:
                        html = render_track_detail_html(
                            self._current_track, self._session,
                            show_clean=self._show_clean,
                            verbose=self._verbose)
                        self._file_report.setHtml(self._wrap_html(html))

            # Prompt restart if scale factor changed
            new_scale = self._config.get("app", {}).get("scale_factor", 1.0)
            if new_scale != old_scale:
                QMessageBox.information(
                    self, "Restart required",
                    f"HiDPI scale factor changed from {old_scale} to {new_scale}.\n"
                    "Please restart SessionPrep for the new scaling to take effect.",
                )
    # ── Tools menu ─────────────────────────────────────────────────────────

    def _update_tools_menu(self):
        """Enable/disable Tools menu entries based on active config."""
        preset = self._active_preset()
        pt_section = preset.get("daw_processors", {}).get("protools", {})
        pt_enabled = pt_section.get("protools_enabled", False)
        self._pt_utils_action.setEnabled(pt_enabled)

    @Slot()
    def _on_open_pt_utils(self):
        """Open (or activate) the Pro Tools Utils window."""
        from .daw_tools.protools.window import ProToolsUtilsWindow
        if self._pt_utils_window is None:
            self._pt_utils_window = ProToolsUtilsWindow(
                self._config, parent=self)
        else:
            self._pt_utils_window.update_config(self._config)
        self._pt_utils_window.show()
        self._pt_utils_window.raise_()
        self._pt_utils_window.activateWindow()

    @Slot()
    def _on_about(self):
        from sessionpreplib import __version__ as ver
        QMessageBox.about(
            self,
            "About SessionPrep",
            f"<h2>SessionPrep</h2>"
            f"<p>Version {ver}</p>"
            f"<p>Batch audio analyzer and normalizer<br/>"
            f"for mix session preparation.</p>",
        )

    def closeEvent(self, event):
        if self._batch_dock.has_items:
            reply = QMessageBox.warning(
                self, "Pending Batch Items",
                "You have pending sessions in the batch queue. Are you sure you want to quit?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        self._playback.stop()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _app_icon() -> QIcon:
    """Load the application icon from the res/ directory."""
    res_dir = os.path.join(os.path.dirname(__file__), "res")
    icon = QIcon()
    svg = os.path.join(res_dir, "sessionprep.svg")
    png = os.path.join(res_dir, "sessionprep.png")
    if os.path.isfile(svg):
        icon = QIcon(svg)
    if os.path.isfile(png):
        icon.addFile(png)
    return icon


def main():
    from sessionpreplib.logging_setup import setup_logging
    setup_logging()

    t_main = time.perf_counter()

    # Apply HiDPI scale factor before QApplication is created.
    # Read directly from JSON to avoid the validate-and-overwrite path
    # in load_config() which could reset the file to defaults.
    import json as _json
    from .settings import config_path as _cfg_path
    try:
        with open(_cfg_path(), "r", encoding="utf-8") as _f:
            _raw = _json.load(_f)
        scale = _raw.get("app", {}).get("scale_factor")
        if scale is not None and float(scale) != 1.0:
            os.environ["QT_SCALE_FACTOR"] = str(float(scale))
    except Exception:
        pass

    t0 = time.perf_counter()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(_app_icon())
    dbg(f"QApplication created: {(time.perf_counter() - t0) * 1000:.1f} ms")

    t0 = time.perf_counter()
    window = SessionPrepWindow()
    dbg(f"SessionPrepWindow created: {(time.perf_counter() - t0) * 1000:.1f} ms")

    t0 = time.perf_counter()
    window.show()
    dbg(f"window.show: {(time.perf_counter() - t0) * 1000:.1f} ms")

    dbg(f"main() total: {(time.perf_counter() - t_main) * 1000:.1f} ms")
    sys.exit(app.exec())
