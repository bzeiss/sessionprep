"""Main application window for SessionPrep GUI."""

from __future__ import annotations

import copy
import os
import sys

from PySide6.QtCore import Qt, Slot, QSize, QTimer, QUrl, QMimeData
from PySide6.QtGui import QAction, QActionGroup, QFont, QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QStatusBar,
    QWidget,
)

from sessionpreplib.audio import get_window_samples
from sessionpreplib.config import default_config, flatten_structured_config
from sessionpreplib.detector import TrackDetector
from sessionpreplib.detectors import detector_help_map
from sessionpreplib.utils import protools_sort_key

from .settings import load_config, config_path, _GUI_DEFAULTS
from .theme import (
    COLORS,
    FILE_COLOR_OK,
    FILE_COLOR_ERROR,
    FILE_COLOR_SILENT,
    FILE_COLOR_TRANSIENT,
    FILE_COLOR_SUSTAINED,
    PT_DEFAULT_COLORS,
    apply_dark_theme,
)
from .helpers import track_analysis_label, esc, fmt_time
from .preferences import PreferencesDialog, _argb_to_qcolor
from .report import render_summary_html, render_track_detail_html
from .widgets import BatchEditTableWidget, BatchComboBox
from .worker import AnalyzeWorker, BatchReanalyzeWorker
from .waveform import WaveformWidget, WaveformLoadWorker
from sessionpreplib.audio import AUDIO_EXTENSIONS
from .playback import PlaybackController

_TAB_SUMMARY = 0
_TAB_FILE = 1
_TAB_GROUPS = 2

_PAGE_PROGRESS = 0
_PAGE_TABS = 1

_PHASE_ANALYSIS = 0
_PHASE_SETUP = 1

_SEVERITY_SORT = {"PROBLEMS": 0, "Error": 0, "ATTENTION": 1, "OK": 2, "": 3}


class _HelpBrowser(QTextBrowser):
    """QTextBrowser that shows detector help tooltips on hover."""

    def __init__(self, help_map: dict[str, str], parent=None):
        super().__init__(parent)
        self._help_map = help_map
        self.setOpenLinks(False)
        self.setMouseTracking(True)

    def mouseMoveEvent(self, event):
        anchor = self.anchorAt(event.pos())
        if anchor.startswith("detector:"):
            det_id = anchor[len("detector:"):]
            html = self._help_map.get(det_id)
            if html:
                from PySide6.QtWidgets import QToolTip
                QToolTip.showText(event.globalPosition().toPoint(), html, self)
            else:
                from PySide6.QtWidgets import QToolTip
                QToolTip.hideText()
        else:
            from PySide6.QtWidgets import QToolTip
            QToolTip.hideText()
        super().mouseMoveEvent(event)


class _DraggableTrackTable(BatchEditTableWidget):
    """BatchEditTableWidget with file-drag support for external applications."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDefaultDropAction(Qt.CopyAction)
        self._source_dir: str | None = None

    def set_source_dir(self, path: str | None):
        self._source_dir = path

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, items):
        if not self._source_dir:
            return super().mimeData(items)
        filenames: set[str] = set()
        for item in items:
            if item.column() == 0 and item.text():
                filenames.add(item.text())
        if not filenames:
            return super().mimeData(items)
        urls = [QUrl.fromLocalFile(os.path.join(self._source_dir, f))
                for f in filenames]
        mime = QMimeData()
        mime.setUrls(urls)
        return mime

    def supportedDragActions(self):
        return Qt.CopyAction


class _SortableItem(QTableWidgetItem):
    """QTableWidgetItem with a custom sort key."""

    def __init__(self, text: str, sort_key=None):
        super().__init__(text)
        self._sort_key = sort_key if sort_key is not None else text

    def __lt__(self, other):
        if isinstance(other, _SortableItem):
            return self._sort_key < other._sort_key
        return super().__lt__(other)


class SessionPrepWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SessionPrep")
        self.setWindowIcon(_app_icon())

        # Size and center on the primary screen, clamped to available space
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            w = min(1400, avail.width() - 40)
            h = min(900, avail.height() - 40)
            self.resize(w, h)
            self.move(
                avail.x() + (avail.width() - w) // 2,
                avail.y() + (avail.height() - h) // 2,
            )
        else:
            self.resize(1400, 900)

        self._session = None
        self._summary = None
        self._source_dir = None
        self._worker = None
        self._batch_worker: BatchReanalyzeWorker | None = None
        self._batch_filenames: set[str] = set()
        self._wf_worker: WaveformLoadWorker | None = None
        self._current_track = None
        self._session_groups: list[dict] = []
        self._detector_help = detector_help_map()

        # Load persistent GUI configuration
        self._config = load_config()

        # Playback controller
        self._playback = PlaybackController(self)
        self._playback.cursor_updated.connect(self._on_cursor_updated)
        self._playback.playback_finished.connect(self._on_playback_finished)
        self._playback.error.connect(self._on_playback_error)

        self._init_ui()
        apply_dark_theme(self)

        # Spacebar toggles play/stop
        self._space_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self._space_shortcut.activated.connect(self._on_toggle_play)

    # ── UI setup ──────────────────────────────────────────────────────────

    def _init_ui(self):
        self._init_menus()

        # ── Top-level phase tabs ──────────────────────────────────────────
        self._phase_tabs = QTabWidget()
        self._phase_tabs.setObjectName("phaseTabs")
        self._phase_tabs.setDocumentMode(True)

        # Tab 0 — Analysis
        analysis_page = QWidget()
        analysis_layout = QVBoxLayout(analysis_page)
        analysis_layout.setContentsMargins(0, 0, 0, 0)
        analysis_layout.setSpacing(0)
        self._init_analysis_toolbar()
        analysis_layout.addWidget(self._analysis_toolbar)
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(self._build_left_panel())
        main_splitter.addWidget(self._build_right_panel())
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 3)
        main_splitter.setSizes([420, 580])
        analysis_layout.addWidget(main_splitter, 1)
        self._phase_tabs.addTab(analysis_page, "Analysis")

        # Tab 1 — Session Setup (placeholder)
        self._phase_tabs.addTab(self._build_setup_page(), "Session Setup")
        self._phase_tabs.setTabEnabled(_PHASE_SETUP, False)

        self.setCentralWidget(self._phase_tabs)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Open a directory containing .wav / .aif files to begin.")

    def _init_menus(self):
        file_menu = self.menuBar().addMenu("&File")

        open_action = QAction("&Open...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._on_open_path)
        file_menu.addAction(open_action)

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

    def _init_analysis_toolbar(self):
        self._analysis_toolbar = QToolBar("Analysis")
        self._analysis_toolbar.setIconSize(QSize(16, 16))
        self._analysis_toolbar.setMovable(False)
        self._analysis_toolbar.setFloatable(False)

        self._open_action = QAction("Open", self)
        self._open_action.triggered.connect(self._on_open_path)
        self._analysis_toolbar.addAction(self._open_action)

        self._analysis_toolbar.addSeparator()

        self._analyze_action = QAction("Analyze", self)
        self._analyze_action.setEnabled(False)
        self._analyze_action.triggered.connect(self._on_analyze)
        self._analysis_toolbar.addAction(self._analyze_action)

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

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._setup_toolbar.addWidget(spacer)

        self._transfer_action = QAction("Transfer", self)
        self._transfer_action.setEnabled(False)
        self._setup_toolbar.addAction(self._transfer_action)

        layout.addWidget(self._setup_toolbar)

        # Splitter: track table (left) + routing panel placeholder (right)
        setup_splitter = QSplitter(Qt.Horizontal)

        # ── Left: track table ─────────────────────────────────────────────
        self._setup_table = BatchEditTableWidget()
        self._setup_table.setColumnCount(4)
        self._setup_table.setHorizontalHeaderLabels(
            ["File", "Ch", "Clip Gain", "Fader Gain"]
        )
        self._setup_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._setup_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self._setup_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._setup_table.verticalHeader().setVisible(False)
        self._setup_table.setMinimumWidth(300)
        self._setup_table.setShowGrid(True)
        self._setup_table.setAlternatingRowColors(True)
        self._setup_table.setSortingEnabled(True)

        sh = self._setup_table.horizontalHeader()
        sh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        sh.setSectionResizeMode(0, QHeaderView.Stretch)
        sh.setSectionResizeMode(1, QHeaderView.Fixed)
        sh.setSectionResizeMode(2, QHeaderView.Interactive)
        sh.setSectionResizeMode(3, QHeaderView.Interactive)
        sh.resizeSection(1, 30)
        sh.resizeSection(2, 90)
        sh.resizeSection(3, 90)

        setup_splitter.addWidget(self._setup_table)

        # ── Right: placeholder ────────────────────────────────────────────
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

        setup_splitter.addWidget(right_placeholder)
        setup_splitter.setStretchFactor(0, 2)
        setup_splitter.setStretchFactor(1, 3)
        setup_splitter.setSizes([420, 580])

        layout.addWidget(setup_splitter, 1)

        return page

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Track table
        self._track_table = _DraggableTrackTable()
        self._track_table.setColumnCount(6)
        self._track_table.setHorizontalHeaderLabels(
            ["File", "Ch", "Analysis", "Classification", "Gain", "RMS Anchor"]
        )
        self._track_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._track_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self._track_table.setEditTriggers(QTableWidget.NoEditTriggers)
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
        header.resizeSection(1, 30)
        header.resizeSection(2, 150)
        header.resizeSection(3, 120)
        header.resizeSection(4, 90)
        header.resizeSection(5, 100)

        self._track_table.cellClicked.connect(self._on_row_clicked)
        self._track_table.currentCellChanged.connect(self._on_current_cell_changed)
        layout.addWidget(self._track_table)

        # Playback controls
        controls = QHBoxLayout()
        controls.setContentsMargins(4, 4, 4, 4)
        controls.setSpacing(4)

        self._play_btn = QPushButton("\u25B6 Play")
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._on_play)
        controls.addWidget(self._play_btn)

        self._stop_btn = QPushButton("\u25A0 Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        controls.addWidget(self._stop_btn)

        self._time_label = QLabel("00:00 / 00:00")
        self._time_label.setStyleSheet(
            "color: #888888; font-family: Consolas, monospace;"
            " font-size: 9pt; padding: 0 8px;"
        )
        controls.addWidget(self._time_label)
        controls.addStretch()
        layout.addLayout(controls)

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

        self._progress_label = QLabel("Analyzing…")
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

        # Summary tab — single QTextBrowser
        self._summary_view = self._make_report_browser()
        self._detail_tabs.addTab(self._summary_view, "Summary")

        # File tab — vertical splitter (report + waveform)
        file_splitter = QSplitter(Qt.Vertical)

        self._file_report = self._make_report_browser()
        file_splitter.addWidget(self._file_report)

        self._waveform = WaveformWidget()
        self._waveform.position_clicked.connect(self._on_waveform_seek)
        self._waveform.set_invert_scroll(
            self._config.get("gui", {}).get("invert_scroll", "default"))


        # Waveform toolbar + widget container
        wf_container = QWidget()
        wf_layout = QVBoxLayout(wf_container)
        wf_layout.setContentsMargins(0, 0, 0, 0)
        wf_layout.setSpacing(0)

        wf_toolbar = QHBoxLayout()
        wf_toolbar.setContentsMargins(4, 2, 4, 2)

        toggle_style = (
            "QToolButton:checked { background-color: #2a6db5; color: #ffffff; }")

        dropdown_style = (
            "QToolButton { padding-right: 30px; }"
            "QToolButton::menu-indicator { subcontrol-position: right center;"
            " subcontrol-origin: padding; right: 5px; }")

        # Display mode dropdown (leftmost)
        self._display_mode_btn = QToolButton()
        self._display_mode_btn.setText("Waveform")
        self._display_mode_btn.setToolTip("Switch between Waveform and Spectrogram display")
        self._display_mode_btn.setPopupMode(QToolButton.InstantPopup)
        self._display_mode_btn.setAutoRaise(True)
        self._display_mode_btn.setStyleSheet(dropdown_style)
        display_menu = QMenu(self._display_mode_btn)
        self._wf_action = display_menu.addAction("Waveform")
        self._spec_action = display_menu.addAction("Spectrogram")
        self._wf_action.setCheckable(True)
        self._wf_action.setChecked(True)
        self._spec_action.setCheckable(True)
        display_group = QActionGroup(self)
        display_group.addAction(self._wf_action)
        display_group.addAction(self._spec_action)
        display_group.triggered.connect(self._on_display_mode_changed)
        self._display_mode_btn.setMenu(display_menu)
        wf_toolbar.addWidget(self._display_mode_btn)

        wf_toolbar.addSpacing(8)

        # Spectrogram settings dropdown (visible only in spectrogram mode)
        self._spec_settings_btn = QToolButton()
        self._spec_settings_btn.setText("Display")
        self._spec_settings_btn.setToolTip("Configure spectrogram display parameters")
        self._spec_settings_btn.setPopupMode(QToolButton.InstantPopup)
        self._spec_settings_btn.setAutoRaise(True)
        self._spec_settings_btn.setStyleSheet(dropdown_style)
        spec_menu = QMenu(self._spec_settings_btn)

        # -- FFT Size submenu --
        fft_menu = spec_menu.addMenu("FFT Size")
        self._fft_group = QActionGroup(self)
        for sz in (512, 1024, 2048, 4096, 8192):
            act = fft_menu.addAction(str(sz))
            act.setCheckable(True)
            act.setData(sz)
            if sz == 2048:
                act.setChecked(True)
            self._fft_group.addAction(act)
        self._fft_group.triggered.connect(self._on_spec_fft_changed)

        # -- Window submenu --
        win_menu = spec_menu.addMenu("Window")
        self._win_group = QActionGroup(self)
        _WINDOW_MAP = [("Hann", "hann"), ("Hamming", "hamming"),
                       ("Blackman-Harris", "blackmanharris")]
        for label, key in _WINDOW_MAP:
            act = win_menu.addAction(label)
            act.setCheckable(True)
            act.setData(key)
            if key == "hann":
                act.setChecked(True)
            self._win_group.addAction(act)
        self._win_group.triggered.connect(self._on_spec_window_changed)

        # -- Color Theme submenu --
        cmap_menu = spec_menu.addMenu("Color Theme")
        self._cmap_group = QActionGroup(self)
        for name in ("Magma", "Viridis", "Grayscale"):
            act = cmap_menu.addAction(name)
            act.setCheckable(True)
            act.setData(name.lower())
            if name == "Magma":
                act.setChecked(True)
            self._cmap_group.addAction(act)
        self._cmap_group.triggered.connect(self._on_spec_cmap_changed)

        # -- dB Floor submenu --
        floor_menu = spec_menu.addMenu("dB Floor")
        self._floor_group = QActionGroup(self)
        for val in (-120, -100, -80, -60, -50, -40, -30, -20):
            act = floor_menu.addAction(f"{val} dB")
            act.setCheckable(True)
            act.setData(val)
            if val == -80:
                act.setChecked(True)
            self._floor_group.addAction(act)
        self._floor_group.triggered.connect(self._on_spec_floor_changed)

        # -- dB Ceiling submenu --
        ceil_menu = spec_menu.addMenu("dB Ceiling")
        self._ceil_group = QActionGroup(self)
        for val in (-30, -20, -10, -5, 0):
            act = ceil_menu.addAction(f"{val} dB")
            act.setCheckable(True)
            act.setData(val)
            if val == 0:
                act.setChecked(True)
            self._ceil_group.addAction(act)
        self._ceil_group.triggered.connect(self._on_spec_ceil_changed)

        self._spec_settings_btn.setMenu(spec_menu)
        self._spec_settings_btn.setVisible(False)
        wf_toolbar.addWidget(self._spec_settings_btn)

        # Waveform settings dropdown (visible only in waveform mode)
        self._wf_settings_btn = QToolButton()
        self._wf_settings_btn.setText("Display")
        self._wf_settings_btn.setToolTip("Configure waveform display parameters")
        self._wf_settings_btn.setPopupMode(QToolButton.InstantPopup)
        self._wf_settings_btn.setAutoRaise(True)
        self._wf_settings_btn.setStyleSheet(dropdown_style)
        wf_menu = QMenu(self._wf_settings_btn)

        # -- Anti-Aliased Lines toggle --
        self._wf_aa_action = wf_menu.addAction("Anti-Aliased Lines")
        self._wf_aa_action.setCheckable(True)
        self._wf_aa_action.setChecked(False)
        self._wf_aa_action.toggled.connect(self._on_wf_aa_changed)

        # -- Line Thickness submenu --
        thick_menu = wf_menu.addMenu("Line Thickness")
        self._wf_thick_group = QActionGroup(self)
        for label, val in [("Thin (1px)", 1), ("Normal (2px)", 2)]:
            act = thick_menu.addAction(label)
            act.setCheckable(True)
            act.setData(val)
            if val == 1:
                act.setChecked(True)
            self._wf_thick_group.addAction(act)
        self._wf_thick_group.triggered.connect(self._on_wf_line_width_changed)

        self._wf_settings_btn.setMenu(wf_menu)
        wf_toolbar.addWidget(self._wf_settings_btn)

        wf_toolbar.addSpacing(8)

        # Overlay dropdown (populated per-track)
        self._overlay_btn = QToolButton()
        self._overlay_btn.setText("Detector Overlays")
        self._overlay_btn.setToolTip("Select detector overlays to display on the waveform")
        self._overlay_btn.setPopupMode(QToolButton.InstantPopup)
        self._overlay_btn.setAutoRaise(True)
        self._overlay_btn.setStyleSheet(dropdown_style)
        self._overlay_menu = QMenu(self._overlay_btn)
        self._overlay_btn.setMenu(self._overlay_menu)
        wf_toolbar.addWidget(self._overlay_btn)

        wf_toolbar.addSpacing(8)

        # Markers toggle
        self._markers_toggle = QToolButton()
        self._markers_toggle.setText("Peak / RMS Max")
        self._markers_toggle.setToolTip("Toggle peak and maximum RMS markers on the waveform")
        self._markers_toggle.setCheckable(True)
        self._markers_toggle.setChecked(True)
        self._markers_toggle.setAutoRaise(True)
        self._markers_toggle.setStyleSheet(toggle_style)
        self._markers_toggle.toggled.connect(self._waveform.toggle_markers)
        wf_toolbar.addWidget(self._markers_toggle)

        wf_toolbar.addSpacing(8)

        # RMS L/R toggle
        self._rms_lr_toggle = QToolButton()
        self._rms_lr_toggle.setText("RMS L/R")
        self._rms_lr_toggle.setToolTip("Toggle per-channel RMS envelope overlay")
        self._rms_lr_toggle.setCheckable(True)
        self._rms_lr_toggle.setAutoRaise(True)
        self._rms_lr_toggle.setStyleSheet(toggle_style)
        self._rms_lr_toggle.toggled.connect(self._waveform.toggle_rms_lr)
        wf_toolbar.addWidget(self._rms_lr_toggle)

        wf_toolbar.addSpacing(4)

        # RMS AVG toggle
        self._rms_avg_toggle = QToolButton()
        self._rms_avg_toggle.setText("RMS AVG")
        self._rms_avg_toggle.setToolTip("Toggle combined (average) RMS envelope overlay")
        self._rms_avg_toggle.setCheckable(True)
        self._rms_avg_toggle.setAutoRaise(True)
        self._rms_avg_toggle.setStyleSheet(toggle_style)
        self._rms_avg_toggle.toggled.connect(self._waveform.toggle_rms_avg)
        wf_toolbar.addWidget(self._rms_avg_toggle)

        wf_toolbar.addStretch()  # push buttons to the right

        style = self.style()

        def _tb(text: str, tooltip: str, icon=None):
            btn = QToolButton()
            if icon is not None:
                btn.setIcon(style.standardIcon(icon))
            else:
                btn.setText(text)
            btn.setToolTip(tooltip)
            btn.setAutoRaise(True)
            wf_toolbar.addWidget(btn)
            return btn

        _tb("Fit", "Zoom to fit entire file", QStyle.SP_BrowserReload
             ).clicked.connect(self._waveform.zoom_fit)
        _tb("+", "Zoom in at cursor").clicked.connect(self._waveform.zoom_in)
        _tb("\u2212", "Zoom out at cursor").clicked.connect(self._waveform.zoom_out)
        _tb("", "Scale up (vertical)", QStyle.SP_ArrowUp
             ).clicked.connect(self._waveform.scale_up)
        _tb("", "Scale down (vertical)", QStyle.SP_ArrowDown
             ).clicked.connect(self._waveform.scale_down)

        toolbar_widget = QWidget()
        toolbar_widget.setLayout(wf_toolbar)
        toolbar_widget.setFixedHeight(28)
        toolbar_widget.setStyleSheet(
            "background-color: #2d2d2d; border-bottom: 1px solid #555;")
        wf_layout.addWidget(toolbar_widget)
        wf_layout.addWidget(self._waveform, 1)

        file_splitter.addWidget(wf_container)

        file_splitter.setStretchFactor(0, 3)
        file_splitter.setStretchFactor(1, 1)
        file_splitter.setSizes([500, 180])

        self._detail_tabs.addTab(file_splitter, "File")
        self._detail_tabs.setTabEnabled(_TAB_FILE, False)

        # Groups tab — session-local group editor
        self._detail_tabs.addTab(self._build_groups_tab(), "Groups")
        self._detail_tabs.setTabEnabled(_TAB_GROUPS, False)

        self._right_stack.addWidget(self._detail_tabs)  # index 1

        # Start on the tabs page (summary empty until first analysis)
        self._right_stack.setCurrentIndex(_PAGE_TABS)

        return self._right_stack

    # ── Groups tab (session-local group editor) ─────────────────────────

    def _build_groups_tab(self) -> QWidget:
        """Build the session-local Groups editor tab."""
        page = QWidget()
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
        self._groups_tab_table.setColumnCount(3)
        self._groups_tab_table.setHorizontalHeaderLabels(
            ["Name", "Color", "Gain-Linked"])
        self._groups_tab_table.verticalHeader().setVisible(False)
        self._groups_tab_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._groups_tab_table.setSelectionMode(QTableWidget.SingleSelection)
        gh = self._groups_tab_table.horizontalHeader()
        gh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        gh.setSectionResizeMode(0, QHeaderView.Stretch)
        gh.setSectionResizeMode(1, QHeaderView.Fixed)
        gh.resizeSection(1, 160)
        gh.setSectionResizeMode(2, QHeaderView.Fixed)
        gh.resizeSection(2, 80)

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

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self._on_groups_tab_reset)
        btn_row.addWidget(reset_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        return page

    def _color_names_from_config(self) -> list[str]:
        """Return color names from the current config (or defaults)."""
        colors = self._config.get("gui", {}).get("colors", PT_DEFAULT_COLORS)
        return [c["name"] for c in colors if c.get("name")]

    def _color_argb_by_name(self, name: str) -> str | None:
        """Look up ARGB hex by color name from config."""
        colors = self._config.get("gui", {}).get("colors", PT_DEFAULT_COLORS)
        for c in colors:
            if c.get("name") == name:
                return c.get("argb")
        return None

    @staticmethod
    def _color_swatch_icon(argb: str, size: int = 16) -> QIcon:
        """Create a small QIcon swatch from an ARGB hex string."""
        pm = QPixmap(size, size)
        pm.fill(_argb_to_qcolor(argb))
        return QIcon(pm)

    def _set_groups_tab_row(self, row: int, name: str, color: str,
                            gain_linked: bool):
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

    def _populate_groups_tab(self):
        """Populate the groups tab table from self._session_groups."""
        self._groups_tab_table.setRowCount(0)
        self._groups_tab_table.setRowCount(len(self._session_groups))
        for row, g in enumerate(self._session_groups):
            self._set_groups_tab_row(
                row, g["name"], g.get("color", ""), g.get("gain_linked", False)
            )

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
            groups.append({
                "name": name,
                "color": color,
                "gain_linked": gain_linked,
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
        """Revert a group name edit if it creates a duplicate."""
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

    def _on_groups_tab_add(self):
        row = self._groups_tab_table.rowCount()
        self._groups_tab_table.insertRow(row)
        color_names = self._color_names_from_config()
        default_color = color_names[0] if color_names else ""
        self._set_groups_tab_row(
            row, self._unique_session_group_name(), default_color, False)
        self._groups_tab_table.scrollToBottom()
        self._groups_tab_table.editItem(self._groups_tab_table.item(row, 0))

    def _on_groups_tab_remove(self):
        row = self._groups_tab_table.currentRow()
        if row >= 0:
            self._groups_tab_table.removeRow(row)

    def _on_groups_tab_reset(self):
        """Reset session groups to the defaults from preferences."""
        defaults = self._config.get("gui", {}).get(
            "default_groups", _GUI_DEFAULTS.get("default_groups", []))
        self._session_groups = copy.deepcopy(defaults)
        self._populate_groups_tab()

    def _make_report_browser(self) -> QTextBrowser:
        """Create a consistently styled QTextBrowser for reports."""
        browser = _HelpBrowser(self._detector_help)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.Monospace)
        browser.setFont(font)
        return browser

    # ── Slots: file / analysis ────────────────────────────────────────────

    @Slot()
    def _on_open_path(self):
        start_dir = self._config.get("gui", {}).get("default_project_dir", "") or ""
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
        self._waveform.setVisible(False)
        self._play_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._detail_tabs.setTabEnabled(_TAB_FILE, False)
        self._detail_tabs.setTabEnabled(_TAB_GROUPS, False)
        self._detail_tabs.setCurrentIndex(_TAB_SUMMARY)
        self._right_stack.setCurrentIndex(_PAGE_TABS)
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

        self._analyze_action.setEnabled(True)
        self._status_bar.showMessage(
            f"Loaded {len(wav_files)} file(s) from {path}"
        )
        self.setWindowTitle("SessionPrep")

        # Auto-start analysis
        self._on_analyze()

    @Slot()
    def _on_analyze(self):
        if not self._source_dir:
            return

        self._analyze_action.setEnabled(False)
        self._current_track = None
        self._detail_tabs.setTabEnabled(_TAB_FILE, False)

        # Show progress page
        self._progress_label.setText("Analyzing…")
        self._right_stack.setCurrentIndex(_PAGE_PROGRESS)

        config = dict(default_config())
        config.update(flatten_structured_config(self._config))
        config["_source_dir"] = self._source_dir

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

    def _find_table_row(self, filename: str) -> int:
        """Return the table row index for *filename*, or -1 if not found."""
        for row in range(self._track_table.rowCount()):
            item = self._track_table.item(row, 0)
            if item and item.text() == filename:
                return row
        return -1

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
        label, color = track_analysis_label(track)
        analysis_item = _SortableItem(label, _SEVERITY_SORT.get(label, 9))
        analysis_item.setForeground(QColor(color))
        self._track_table.setItem(row, 2, analysis_item)

    @Slot(str, object)
    def _on_track_planned(self, filename: str, track):
        """Update classification and gain columns after processors complete."""
        row = self._find_table_row(filename)
        if row < 0:
            return

        # Re-evaluate severity now that processor results inform is_relevant()
        dets = self._session.detectors if self._session else None
        label, color = track_analysis_label(track, dets)
        analysis_item = _SortableItem(label, _SEVERITY_SORT.get(label, 9))
        analysis_item.setForeground(QColor(color))
        self._track_table.setItem(row, 2, analysis_item)

        # Remove previous cell widgets
        self._track_table.removeCellWidget(row, 3)
        self._track_table.removeCellWidget(row, 4)
        self._track_table.removeCellWidget(row, 5)

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
            combo.textActivated.connect(self._on_classification_changed)
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
            spin.valueChanged.connect(self._on_gain_changed)
            self._track_table.setCellWidget(row, 4, spin)

            # RMS Anchor combo (column 5)
            self._create_anchor_combo(row, track)

    @Slot(object, object)
    def _on_analyze_done(self, session, summary):
        self._session = session
        self._summary = summary
        self._analyze_action.setEnabled(True)
        self._worker = None

        # Prefill session groups from config defaults
        defaults = self._config.get("gui", {}).get(
            "default_groups", _GUI_DEFAULTS.get("default_groups", []))
        self._session_groups = copy.deepcopy(defaults)
        self._populate_groups_tab()

        self._populate_table(session)
        self._render_summary()

        # Switch to tabs — summary tab
        self._right_stack.setCurrentIndex(_PAGE_TABS)
        self._detail_tabs.setCurrentIndex(_TAB_SUMMARY)
        self._detail_tabs.setTabEnabled(_TAB_GROUPS, True)

        # Enable Session Setup phase now that analysis is available
        self._phase_tabs.setTabEnabled(_PHASE_SETUP, True)
        self._populate_setup_table()

        ok_count = sum(1 for t in session.tracks if t.status == "OK")
        self._status_bar.showMessage(
            f"Analysis complete: {ok_count}/{len(session.tracks)} tracks OK"
        )

    @Slot(str)
    def _on_analyze_error(self, message: str):
        self._analyze_action.setEnabled(True)
        self._worker = None

        self._right_stack.setCurrentIndex(_PAGE_TABS)
        self._detail_tabs.setCurrentIndex(_TAB_SUMMARY)
        self._summary_view.setHtml(self._wrap_html(
            f'<div style="color:{COLORS["problems"]}; font-weight:bold;">'
            f'Analysis Error</div>'
            f'<div style="margin-top:8px;">{esc(message)}</div>'
        ))
        self._status_bar.showMessage(f"Error: {message}")

    # ── Slots: track selection ────────────────────────────────────────────

    @Slot(int, int)
    def _on_row_clicked(self, row, _column):
        self._select_row(row)

    @Slot(int, int, int, int)
    def _on_current_cell_changed(self, row, _col, _prev_row, _prev_col):
        self._select_row(row)

    def _select_row(self, row: int):
        if not self._session or row < 0:
            return
        fname_item = self._track_table.item(row, 0)
        if not fname_item:
            return
        fname = fname_item.text()
        track = next(
            (t for t in self._session.tracks if t.filename == fname), None
        )
        if not track:
            return
        self._show_track_detail(track)

    # ── Report rendering ──────────────────────────────────────────────────

    @property
    def _show_clean(self) -> bool:
        return self._config.get("gui", {}).get("show_clean_detectors", True)

    @property
    def _verbose(self) -> bool:
        return self._config.get("gui", {}).get("report_verbosity", "normal") == "verbose"

    def _render_summary(self):
        """Render the diagnostic summary into the Summary tab."""
        if not self._summary or not self._session:
            return
        html = render_summary_html(
            self._summary, show_hints=False, show_faders=False,
            show_clean=self._show_clean,
        )
        self._summary_view.setHtml(self._wrap_html(html))

    def _show_track_detail(self, track):
        """Populate the File tab with per-track detail + waveform.

        The HTML report is rendered and displayed immediately so the UI
        feels responsive.  Waveform loading (dtype conversion, peak
        finding, RMS setup) is deferred to the next event-loop iteration
        via ``QTimer.singleShot`` so the tab switch paints first.
        """
        self._on_stop()
        self._current_track = track

        # Show HTML report immediately
        html = render_track_detail_html(track, self._session,
                                        show_clean=self._show_clean,
                                        verbose=self._verbose)
        self._file_report.setHtml(self._wrap_html(html))

        # Enable and switch to File tab before heavy work
        self._detail_tabs.setTabEnabled(_TAB_FILE, True)
        self._detail_tabs.setCurrentIndex(_TAB_FILE)

        # Defer waveform loading so the UI repaints first
        QTimer.singleShot(0, lambda: self._load_waveform(track))

    def _load_waveform(self, track):
        """Start background waveform loading for *track*."""
        # Guard: user may have clicked a different track while we were queued
        if self._current_track is not track:
            return

        # Cancel any in-flight worker
        if self._wf_worker is not None:
            self._wf_worker.finished.disconnect()
            self._wf_worker = None

        has_audio = track.audio_data is not None and track.audio_data.size > 0
        if has_audio:
            self._waveform.set_loading(True)
            self._waveform.setVisible(True)
            self._play_btn.setEnabled(False)
            self._update_time_label(0)

            flat_cfg = flatten_structured_config(self._config)
            win_ms = flat_cfg.get("window", 400)
            ws = get_window_samples(track, win_ms)

            self._wf_worker = WaveformLoadWorker(
                track.audio_data, track.samplerate, ws,
                spec_n_fft=self._waveform._spec_n_fft,
                spec_window=self._waveform._spec_window,
                parent=self)
            self._wf_worker.finished.connect(
                lambda result, t=track: self._on_waveform_loaded(result, t))
            self._wf_worker.start()
        else:
            self._waveform.set_audio(None, 44100)
            self._update_overlay_menu([])
            self._waveform.setVisible(False)
            self._play_btn.setEnabled(False)
            self._update_time_label(0)

    @Slot(object, object)
    def _on_waveform_loaded(self, result: dict, track):
        """Receive pre-computed waveform data from the background worker."""
        self._wf_worker = None

        # Discard if user switched to a different track
        if self._current_track is not track:
            return

        self._waveform.set_precomputed(result)
        cmap = self._config.get("gui", {}).get("spectrogram_colormap", "magma")
        self._waveform.set_colormap(cmap)
        # Sync colormap dropdown with preference
        for act in self._cmap_group.actions():
            if act.data() == cmap:
                act.setChecked(True)
                break

        all_issues = []
        for det_result in track.detector_results.values():
            all_issues.extend(getattr(det_result, "issues", []))
        self._waveform.set_issues(all_issues)
        self._update_overlay_menu(all_issues)
        self._play_btn.setEnabled(True)
        self._update_time_label(0)

    # ── Overlay dropdown ────────────────────────────────────────────────

    def _update_overlay_menu(self, issues: list):
        """Rebuild the overlay dropdown menu based on current track issues."""
        self._overlay_menu.clear()
        self._waveform.set_enabled_overlays(set())

        if not issues:
            self._overlay_btn.setText("Detector Overlays")
            return

        # Build detector instance map from session
        det_map: dict[str, object] = {}
        det_names: dict[str, str] = {}
        if self._session and hasattr(self._session, "detectors"):
            for d in self._session.detectors:
                det_map[d.id] = d
                det_names[d.id] = d.name

        # Filter out issues from detectors that suppress themselves or are skipped
        track = self._current_track
        filtered_issues = []
        for issue in issues:
            det = det_map.get(issue.label)
            if det and track:
                result = track.detector_results.get(issue.label)
                if result:
                    if hasattr(det, 'effective_severity') and det.effective_severity(result) is None:
                        continue
                    if not det.is_relevant(result, track):
                        continue
            filtered_issues.append(issue)

        if not filtered_issues:
            self._overlay_btn.setText("Detector Overlays")
            return

        # Build {label: count} from filtered issue list
        label_counts: dict[str, int] = {}
        for issue in filtered_issues:
            label_counts[issue.label] = label_counts.get(issue.label, 0) + 1

        # Add a checkable action per detector that has issues
        for label in sorted(label_counts, key=lambda lb: det_names.get(lb, lb).lower()):
            name = det_names.get(label, label)
            count = label_counts[label]
            action = self._overlay_menu.addAction(f"{name} ({count})")
            action.setCheckable(True)
            action.setChecked(False)
            action.setData(label)
            action.toggled.connect(self._on_overlay_toggled)

        self._overlay_btn.setText("Detector Overlays")

    @Slot()
    def _on_overlay_toggled(self):
        """Collect checked overlay labels and update the waveform."""
        checked = set()
        for action in self._overlay_menu.actions():
            if action.isChecked():
                checked.add(action.data())
        self._waveform.set_enabled_overlays(checked)
        n = len(checked)
        self._overlay_btn.setText(f"Detector Overlays ({n})" if n else "Detector Overlays")

    @Slot(QAction)
    def _on_display_mode_changed(self, action):
        """Switch waveform widget display mode and toggle toolbar controls."""
        is_waveform = action == self._wf_action
        mode = "waveform" if is_waveform else "spectrogram"
        self._display_mode_btn.setText(action.text())
        self._waveform.set_display_mode(mode)

        # Hide waveform-only toolbar controls in spectrogram mode
        self._wf_settings_btn.setVisible(is_waveform)
        self._markers_toggle.setVisible(is_waveform)
        self._rms_lr_toggle.setVisible(is_waveform)
        self._rms_avg_toggle.setVisible(is_waveform)
        # Show spectrogram-only controls
        self._spec_settings_btn.setVisible(not is_waveform)

    @Slot(bool)
    def _on_wf_aa_changed(self, checked: bool):
        self._waveform.set_wf_antialias(checked)

    @Slot(QAction)
    def _on_wf_line_width_changed(self, action):
        self._waveform.set_wf_line_width(int(action.data()))

    @Slot(QAction)
    def _on_spec_fft_changed(self, action):
        self._waveform.set_spec_fft(int(action.data()))

    @Slot(QAction)
    def _on_spec_window_changed(self, action):
        self._waveform.set_spec_window(action.data())

    @Slot(QAction)
    def _on_spec_cmap_changed(self, action):
        self._waveform.set_colormap(action.data())

    @Slot(QAction)
    def _on_spec_floor_changed(self, action):
        self._waveform.set_spec_db_floor(float(action.data()))

    @Slot(QAction)
    def _on_spec_ceil_changed(self, action):
        self._waveform.set_spec_db_ceil(float(action.data()))

    def _populate_table(self, session):
        """Update the track table with analysis results."""
        self._track_table.setSortingEnabled(False)
        track_map = {t.filename: t for t in session.tracks}
        for row in range(self._track_table.rowCount()):
            # Remove any previous cell widgets before repopulating
            self._track_table.removeCellWidget(row, 3)
            self._track_table.removeCellWidget(row, 4)
            self._track_table.removeCellWidget(row, 5)

            fname_item = self._track_table.item(row, 0)
            if not fname_item:
                continue
            track = track_map.get(fname_item.text())
            if not track:
                continue

            # Column 1: channel count
            ch_item = _SortableItem(str(track.channels), track.channels)
            ch_item.setForeground(QColor(COLORS["dim"]))
            self._track_table.setItem(row, 1, ch_item)

            # Column 2: worst severity (with is_relevant filtering)
            dets = session.detectors if hasattr(session, 'detectors') else None
            label, color = track_analysis_label(track, dets)
            analysis_item = _SortableItem(label, _SEVERITY_SORT.get(label, 9))
            analysis_item.setForeground(QColor(color))
            self._track_table.setItem(row, 2, analysis_item)

            # Column 3: classification (combo or static)
            # Column 4: gain (spin box or static)
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
                # Determine effective classification
                cls_text = pr.classification or "Unknown"
                if "Transient" in cls_text:
                    base_cls = "Transient"
                elif cls_text == "Skip":
                    base_cls = "Skip"
                elif "Sustained" in cls_text:
                    base_cls = "Sustained"
                else:
                    base_cls = "Sustained"

                # Hidden sort item (widget overlays it)
                sort_item = _SortableItem(base_cls, base_cls.lower())
                self._track_table.setItem(row, 3, sort_item)

                # Classification combo widget
                combo = BatchComboBox()
                combo.addItems(["Transient", "Sustained", "Skip"])
                combo.blockSignals(True)
                combo.setCurrentText(base_cls)
                combo.blockSignals(False)
                combo.setProperty("track_filename", track.filename)
                self._style_classification_combo(combo, base_cls)
                combo.textActivated.connect(self._on_classification_changed)
                self._track_table.setCellWidget(row, 3, combo)

                # Gain spin box
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
                spin.valueChanged.connect(self._on_gain_changed)
                self._track_table.setCellWidget(row, 4, spin)

                # RMS Anchor combo (column 5)
                self._create_anchor_combo(row, track)
            else:
                cls_item = _SortableItem("", "zzz")
                self._track_table.setItem(row, 3, cls_item)
                gain_item = _SortableItem("", 0.0)
                self._track_table.setItem(row, 4, gain_item)
        self._track_table.setSortingEnabled(True)

        # Auto-fit columns 2–5 to content, File column stays Stretch, Ch stays Fixed
        header = self._track_table.horizontalHeader()
        for col in (2, 3, 4, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self._track_table.resizeColumnsToContents()
        for col in (2, 3, 4, 5):
            header.setSectionResizeMode(col, QHeaderView.Interactive)

    def _populate_setup_table(self):
        """Refresh the Session Setup track table from the current session."""
        if not self._session:
            return
        self._setup_table.setSortingEnabled(False)
        self._setup_table.setRowCount(0)

        ok_tracks = [t for t in self._session.tracks if t.status == "OK"]
        self._setup_table.setRowCount(len(ok_tracks))

        for row, track in enumerate(ok_tracks):
            pr = (
                next(iter(track.processor_results.values()), None)
                if track.processor_results
                else None
            )
            # Column 0: filename
            fname_item = _SortableItem(
                track.filename, protools_sort_key(track.filename))
            fname_item.setForeground(FILE_COLOR_OK)
            self._setup_table.setItem(row, 0, fname_item)

            # Column 1: channels
            ch_item = _SortableItem(str(track.channels), track.channels)
            ch_item.setForeground(QColor(COLORS["dim"]))
            self._setup_table.setItem(row, 1, ch_item)

            # Column 2: clip gain
            clip_gain = pr.gain_db if pr else 0.0
            cg_item = _SortableItem(f"{clip_gain:+.1f} dB", clip_gain)
            cg_item.setForeground(QColor(COLORS["text"]))
            self._setup_table.setItem(row, 2, cg_item)

            # Column 3: fader gain
            fader_gain = pr.data.get("fader_offset", 0.0) if pr else 0.0
            fg_item = _SortableItem(f"{fader_gain:+.1f} dB", fader_gain)
            fg_item.setForeground(QColor(COLORS["text"]))
            self._setup_table.setItem(row, 3, fg_item)

        self._setup_table.setSortingEnabled(True)

    # ── Classification override helpers ───────────────────────────────────

    def _style_classification_combo(self, combo: QComboBox, cls_text: str):
        """Apply classification-specific color to a combo box."""
        if cls_text == "Transient":
            color = FILE_COLOR_TRANSIENT.name()
        elif cls_text == "Sustained":
            color = FILE_COLOR_SUSTAINED.name()
        else:
            color = FILE_COLOR_SILENT.name()
        combo.setStyleSheet(f"QComboBox {{ color: {color}; font-weight: bold; }}")

    @Slot(str)
    def _on_classification_changed(self, text: str):
        """Handle user changing the classification dropdown."""
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

        # Batch path: async re-analysis for all selected rows
        if getattr(combo, 'batch_mode', False):
            combo.batch_mode = False
            track.classification_override = text
            def _prepare(t):
                t.classification_override = text
            self._batch_apply_combo(combo, 3, text, _prepare,
                                    run_detectors=False)
        else:
            # Skip if the value didn't actually change
            if track.classification_override == text:
                return
            track.classification_override = text
            # Single-track sync path
            self._recalculate_processor(track)
            self._style_classification_combo(combo, text)
            self._update_track_row(fname)
            self._refresh_file_tab(track)

    @Slot(float)
    def _on_gain_changed(self, value: float):
        """Handle user manually editing the gain spin box."""
        spin = self.sender()
        if not spin or not self._session:
            return
        fname = spin.property("track_filename")
        if not fname:
            return
        track = next(
            (t for t in self._session.tracks if t.filename == fname), None
        )
        if not track:
            return

        # Write gain directly to the processor result
        pr = next(iter(track.processor_results.values()), None)
        if pr:
            pr.gain_db = value

        # Update hidden sort item
        for row in range(self._track_table.rowCount()):
            item = self._track_table.item(row, 0)
            if item and item.text() == fname:
                gain_sort = self._track_table.item(row, 4)
                if gain_sort:
                    gain_sort.setText(f"{value:+.1f}")
                    gain_sort._sort_key = value
                break

        # Refresh File tab if this track is currently displayed
        if self._current_track and self._current_track.filename == fname:
            html = render_track_detail_html(track, self._session,
                                            show_clean=self._show_clean,
                                            verbose=self._verbose)
            self._file_report.setHtml(self._wrap_html(html))

    def _recalculate_processor(self, track):
        """Re-run the normalization processor for a single track."""
        if not self._session or not self._session.processors:
            return
        for proc in self._session.processors:
            result = proc.process(track)
            track.processor_results[proc.id] = result

    # ── Batch combo helper ────────────────────────────────────────────────

    def _batch_apply_combo(self, source_combo, column: int, value: str,
                           prepare_fn, run_detectors: bool = True):
        """Apply *value* to the combo in *column* for every selected row.

        1. **Sync** — set overrides via *prepare_fn(track)* and update
           combo widgets instantly.
        2. **Async** — start a ``BatchReanalyzeWorker`` that re-runs
           detectors/processors in the background, updating table rows
           as each track completes and restoring the multi-selection at
           the end.

        *prepare_fn(track)* must only mutate the data model (e.g. set an
        override field).  It must **not** run analysis.
        """
        if not self._session:
            return
        if self._batch_worker and self._batch_worker.isRunning():
            return
        if self._worker and self._worker.isRunning():
            return

        track_map = {t.filename: t for t in self._session.tracks}
        batch_keys = self._track_table.batch_selected_keys()

        # Collect tracks and update combo widgets (sync, instant)
        tracks_to_reanalyze: list = []
        self._track_table.setSortingEnabled(False)
        for fname in batch_keys:
            track = track_map.get(fname)
            if not track or track.status != "OK":
                continue
            prepare_fn(track)
            tracks_to_reanalyze.append(track)
            row = self._find_table_row(fname)
            if row >= 0:
                w = self._track_table.cellWidget(row, column)
                if isinstance(w, BatchComboBox):
                    w.blockSignals(True)
                    w.setCurrentText(value)
                    w.blockSignals(False)
        if not tracks_to_reanalyze:
            self._track_table.setSortingEnabled(True)
            return

        # Save filenames for selection restore after worker completes
        self._batch_filenames = batch_keys

        # Show progress UI
        self._progress_label.setText("Re-analyzing…")
        self._progress_bar.setRange(0, len(tracks_to_reanalyze))
        self._progress_bar.setValue(0)
        self._right_stack.setCurrentIndex(_PAGE_PROGRESS)
        self._analyze_action.setEnabled(False)

        # Start async worker
        self._batch_worker = BatchReanalyzeWorker(
            tracks_to_reanalyze,
            self._session.detectors,
            self._session.processors,
            run_detectors=run_detectors,
        )
        self._batch_worker.progress.connect(self._on_worker_progress)
        self._batch_worker.progress_value.connect(self._on_worker_progress_value)
        self._batch_worker.track_done.connect(self._on_batch_track_done)
        self._batch_worker.batch_finished.connect(self._on_batch_done)
        self._batch_worker.error.connect(self._on_batch_error)
        self._batch_worker.start()

    @Slot(str)
    def _on_batch_track_done(self, filename: str):
        """Update one table row after the worker finishes re-analyzing it."""
        self._update_track_row(filename)

    @Slot()
    def _on_batch_done(self):
        """Finalize the batch: restore selection, switch back to tabs."""
        self._batch_worker = None
        self._analyze_action.setEnabled(True)
        self._right_stack.setCurrentIndex(_PAGE_TABS)

        # Re-enable sorting (was disabled in _batch_apply_combo);
        # rows may reorder, so restore selection by key afterward.
        self._track_table.setSortingEnabled(True)
        self._track_table.restore_selection(self._batch_filenames)
        self._batch_filenames = set()

        # Refresh setup table and file tab
        self._populate_setup_table()
        if self._current_track:
            self._refresh_file_tab(self._current_track)

    @Slot(str)
    def _on_batch_error(self, message: str):
        """Handle fatal error from the batch worker."""
        self._batch_worker = None
        self._analyze_action.setEnabled(True)
        self._track_table.setSortingEnabled(True)
        self._track_table.restore_selection(self._batch_filenames)
        self._batch_filenames = set()
        self._right_stack.setCurrentIndex(_PAGE_TABS)
        self._status_bar.showMessage(f"Batch error: {message}")

    # ── RMS Anchor override helpers ──────────────────────────────────────

    _ANCHOR_LABELS = ["Default", "Max", "P99", "P95", "P90", "P85"]
    _ANCHOR_TO_OVERRIDE = {
        "Default": None, "Max": "max",
        "P99": "p99", "P95": "p95", "P90": "p90", "P85": "p85",
    }
    _OVERRIDE_TO_LABEL = {v: k for k, v in _ANCHOR_TO_OVERRIDE.items()}

    def _create_anchor_combo(self, row: int, track):
        """Create and install an RMS Anchor combo in column 5."""
        anchor_sort = _SortableItem("Default", "default")
        self._track_table.setItem(row, 5, anchor_sort)

        combo = BatchComboBox()
        combo.addItems(self._ANCHOR_LABELS)
        combo.blockSignals(True)
        current = self._OVERRIDE_TO_LABEL.get(
            track.rms_anchor_override, "Default")
        combo.setCurrentText(current)
        combo.blockSignals(False)
        combo.setProperty("track_filename", track.filename)
        combo.setStyleSheet(
            f"QComboBox {{ color: {COLORS['text']}; }}"
        )
        combo.textActivated.connect(self._on_rms_anchor_changed)
        self._track_table.setCellWidget(row, 5, combo)

    @Slot(str)
    def _on_rms_anchor_changed(self, text: str):
        """Handle user changing the RMS Anchor dropdown."""
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

        new_override = self._ANCHOR_TO_OVERRIDE.get(text)

        # Batch path: async re-analysis for all selected rows
        if getattr(combo, 'batch_mode', False):
            combo.batch_mode = False
            track.rms_anchor_override = new_override
            def _prepare(t):
                t.rms_anchor_override = new_override
            self._batch_apply_combo(combo, 5, text, _prepare,
                                    run_detectors=True)
        else:
            # Skip if the value didn't actually change (textActivated
            # fires even when the user re-selects the same item)
            if track.rms_anchor_override == new_override:
                return
            track.rms_anchor_override = new_override
            self._reanalyze_single_track(track)

    def _reanalyze_single_track(self, track):
        """Re-run all track detectors + processors for a single track (sync)."""
        if not self._session:
            return

        # Re-run track-level detectors (already sorted by dependency)
        for det in self._session.detectors:
            if isinstance(det, TrackDetector):
                try:
                    result = det.analyze(track)
                    track.detector_results[det.id] = result
                except Exception:
                    pass

        # Re-run processors
        self._recalculate_processor(track)

        # Update UI
        self._update_track_row(track.filename)
        self._refresh_file_tab(track)

    # ── Track-row UI helpers ─────────────────────────────────────────────

    def _update_track_row(self, filename: str):
        """Refresh analysis label, classification, gain, and sort items
        for the table row matching *filename*.

        Called from:
        - ``_reanalyze_single_track`` (sync single-track path)
        - ``_on_batch_track_done`` (per-track signal from async worker)
        """
        if not self._session:
            return
        track = next(
            (t for t in self._session.tracks if t.filename == filename), None
        )
        if not track:
            return
        row = self._find_table_row(filename)
        if row < 0:
            return

        # Analysis label
        dets = self._session.detectors
        label, color = track_analysis_label(track, dets)
        analysis_item = _SortableItem(label, _SEVERITY_SORT.get(label, 9))
        analysis_item.setForeground(QColor(color))
        self._track_table.setItem(row, 2, analysis_item)

        # Gain spin box + sort item + classification
        pr = next(iter(track.processor_results.values()), None)
        new_gain = pr.gain_db if pr else 0.0
        base_cls = None
        if pr:
            cls_text = pr.classification or "Unknown"
            if "Transient" in cls_text:
                base_cls = "Transient"
            elif cls_text == "Skip":
                base_cls = "Skip"
            else:
                base_cls = "Sustained"

        spin = self._track_table.cellWidget(row, 4)
        if isinstance(spin, QDoubleSpinBox):
            spin.blockSignals(True)
            spin.setValue(new_gain)
            if base_cls is not None:
                spin.setEnabled(base_cls != "Skip")
            spin.blockSignals(False)
        gain_sort = self._track_table.item(row, 4)
        if gain_sort:
            gain_sort.setText(f"{new_gain:+.1f}")
            gain_sort._sort_key = new_gain

        if base_cls is not None:
            cls_combo = self._track_table.cellWidget(row, 3)
            if isinstance(cls_combo, QComboBox):
                cls_combo.blockSignals(True)
                cls_combo.setCurrentText(base_cls)
                cls_combo.blockSignals(False)
                self._style_classification_combo(cls_combo, base_cls)
            sort_item = self._track_table.item(row, 3)
            if sort_item:
                sort_item.setText(base_cls)
                sort_item._sort_key = base_cls.lower()

        # Keep the Session Setup table in sync
        self._populate_setup_table()

    def _refresh_file_tab(self, track):
        """Refresh File tab + waveform overlays if *track* is displayed."""
        if not self._current_track or self._current_track.filename != track.filename:
            return
        html = render_track_detail_html(track, self._session,
                                        show_clean=self._show_clean,
                                        verbose=self._verbose)
        self._file_report.setHtml(self._wrap_html(html))
        all_issues = []
        for result in track.detector_results.values():
            all_issues.extend(getattr(result, "issues", []))
        self._update_overlay_menu(all_issues)

    @staticmethod
    def _wrap_html(body: str) -> str:
        """Wrap HTML content in a styled <body> tag."""
        return (
            f'<body style="background-color:{COLORS["bg"]}; color:{COLORS["text"]};'
            f' font-family:Consolas,monospace; font-size:10pt; padding:12px;">'
            f'{body}</body>'
        )

    # ── Playback ──────────────────────────────────────────────────────────

    @Slot()
    def _on_toggle_play(self):
        if self._playback.is_playing:
            self._on_stop()
        elif self._current_track is not None:
            self._on_play()

    @Slot()
    def _on_play(self):
        track = self._current_track
        if track is None or track.audio_data is None:
            return
        self._on_stop()
        start = self._waveform._cursor_sample
        self._playback.play(track.audio_data, track.samplerate, start)
        if self._playback.is_playing:
            self._play_btn.setEnabled(False)
            self._stop_btn.setEnabled(True)

    @Slot()
    def _on_stop(self):
        was_playing = self._playback.is_playing
        start_sample = self._playback.play_start_sample
        self._playback.stop()
        self._stop_btn.setEnabled(False)
        if self._current_track is not None:
            self._play_btn.setEnabled(True)
        if was_playing:
            self._waveform.set_cursor(start_sample)
            self._update_time_label(start_sample)

    @Slot(int)
    def _on_cursor_updated(self, sample_pos: int):
        self._waveform.set_cursor(sample_pos)
        self._update_time_label(sample_pos)

    @Slot()
    def _on_playback_finished(self):
        self._stop_btn.setEnabled(False)
        if self._current_track is not None:
            self._play_btn.setEnabled(True)
        self._waveform.set_cursor(0)
        self._update_time_label(0)

    @Slot(str)
    def _on_playback_error(self, message: str):
        self._status_bar.showMessage(f"Playback error: {message}")

    @Slot(int)
    def _on_waveform_seek(self, sample_index: int):
        if self._playback.is_playing:
            self._on_stop()
            self._waveform.set_cursor(sample_index)
            self._on_play()
        else:
            self._update_time_label(sample_index)

    def _update_time_label(self, sample_pos: int = 0):
        track = self._current_track
        if track is None or track.samplerate <= 0:
            self._time_label.setText("00:00 / 00:00")
            return
        sr = track.samplerate
        self._time_label.setText(
            f"{fmt_time(sample_pos / sr)} / {fmt_time(track.total_samples / sr)}"
            f"  \u2022  {sample_pos:,}"
        )

    @Slot()
    def _on_preferences(self):
        old_scale = self._config.get("gui", {}).get("scale_factor", 1.0)
        _PIPELINE_KEYS = ("analysis", "detectors", "processors", "session")
        old_pipeline = {k: self._config.get(k) for k in _PIPELINE_KEYS}

        dlg = PreferencesDialog(self._config, parent=self)
        dlg.exec()
        if dlg.saved:
            from .settings import save_config
            self._config = dlg.result_config()
            save_config(self._config)
            self._status_bar.showMessage("Preferences saved.")
            self._waveform.set_invert_scroll(
                self._config.get("gui", {}).get("invert_scroll", "default"))

            if self._source_dir:
                from sessionpreplib.config import strip_presentation_keys
                new_pipeline = {k: self._config.get(k) for k in _PIPELINE_KEYS}
                old_stripped = strip_presentation_keys(old_pipeline)
                new_stripped = strip_presentation_keys(new_pipeline)
                if new_stripped != old_stripped:
                    self._on_analyze()
                elif new_pipeline != old_pipeline:
                    # Only presentation keys changed — lightweight refresh
                    self._refresh_presentation()
                else:
                    # GUI-only change — just refresh reports and colormap
                    self._render_summary()
                    cmap = self._config.get("gui", {}).get(
                        "spectrogram_colormap", "magma")
                    self._waveform.set_colormap(cmap)
                    if self._current_track:
                        html = render_track_detail_html(
                            self._current_track, self._session,
                            show_clean=self._show_clean,
                            verbose=self._verbose)
                        self._file_report.setHtml(self._wrap_html(html))

            # Prompt restart if scale factor changed
            new_scale = self._config.get("gui", {}).get("scale_factor", 1.0)
            if new_scale != old_scale:
                QMessageBox.information(
                    self, "Restart required",
                    f"HiDPI scale factor changed from {old_scale} to {new_scale}.\n"
                    "Please restart SessionPrep for the new scaling to take effect.",
                )

    def _refresh_presentation(self):
        """Re-render all UI after presentation-only config changes (e.g. report_as).

        Reconfigures detector instances in-place, rebuilds the diagnostic
        summary, and refreshes all visible components — without re-reading
        audio or re-running analysis.
        """
        if not self._session:
            return

        # 1. Reconfigure detector instances with updated flat config
        flat = dict(default_config())
        flat.update(flatten_structured_config(self._config))
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
        cmap = self._config.get("gui", {}).get("spectrogram_colormap", "magma")
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
            label, color = track_analysis_label(track, dets)
            analysis_item = _SortableItem(label, _SEVERITY_SORT.get(label, 9))
            analysis_item.setForeground(QColor(color))
            self._track_table.setItem(row, 2, analysis_item)
        self._track_table.setSortingEnabled(True)

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
        self._playback.stop()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _app_icon() -> QIcon:
    """Load the application icon from the res/ directory."""
    res_dir = os.path.join(os.path.dirname(__file__), "res")
    icon = QIcon()
    svg = os.path.join(res_dir, "icon.svg")
    png = os.path.join(res_dir, "icon.png")
    if os.path.isfile(svg):
        icon = QIcon(svg)
    if os.path.isfile(png):
        icon.addFile(png)
    return icon


def main():
    # Apply HiDPI scale factor before QApplication is created.
    # Read directly from JSON to avoid the validate-and-overwrite path
    # in load_config() which could reset the file to defaults.
    import json as _json
    from .settings import config_path as _cfg_path
    try:
        with open(_cfg_path(), "r", encoding="utf-8") as _f:
            _raw = _json.load(_f)
        scale = _raw.get("gui", {}).get("scale_factor")
        if scale is not None and float(scale) != 1.0:
            os.environ["QT_SCALE_FACTOR"] = str(float(scale))
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(_app_icon())

    window = SessionPrepWindow()
    window.show()

    sys.exit(app.exec())
