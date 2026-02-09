"""Main application window for SessionPrep GUI."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, Slot, QSize, QEvent
from PySide6.QtGui import QAction, QFont, QColor, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
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
from sessionpreplib.detectors import detector_help_map
from sessionpreplib.utils import protools_sort_key

from .settings import load_config, config_path
from .theme import (
    COLORS,
    FILE_COLOR_OK,
    FILE_COLOR_ERROR,
    FILE_COLOR_SILENT,
    FILE_COLOR_TRANSIENT,
    FILE_COLOR_SUSTAINED,
    apply_dark_theme,
)
from .helpers import track_analysis_label, esc, fmt_time
from .preferences import PreferencesDialog
from .report import render_summary_html, render_track_detail_html
from .worker import AnalyzeWorker
from .waveform import WaveformWidget
from .playback import PlaybackController

_TAB_SUMMARY = 0
_TAB_FILE = 1

_PAGE_PROGRESS = 0
_PAGE_TABS = 1

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
        self._current_track = None
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
        self._init_toolbar()

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(self._build_left_panel())
        main_splitter.addWidget(self._build_right_panel())
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 3)
        self.setCentralWidget(main_splitter)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Open a directory containing .wav files to begin.")

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

    def _init_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        toolbar.setIconSize(QSize(16, 16))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._open_action = QAction("Open", self)
        self._open_action.triggered.connect(self._on_open_path)
        toolbar.addAction(self._open_action)

        toolbar.addSeparator()

        self._analyze_action = QAction("Analyze", self)
        self._analyze_action.setEnabled(False)
        self._analyze_action.triggered.connect(self._on_analyze)
        toolbar.addAction(self._analyze_action)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Track table
        self._track_table = QTableWidget()
        self._track_table.setColumnCount(3)
        self._track_table.setHorizontalHeaderLabels(
            ["File", "Analysis", "Classification"]
        )
        self._track_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._track_table.setSelectionMode(QTableWidget.SingleSelection)
        self._track_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._track_table.verticalHeader().setVisible(False)
        self._track_table.setMinimumWidth(300)
        self._track_table.setShowGrid(True)
        self._track_table.setAlternatingRowColors(True)
        self._track_table.setSortingEnabled(True)

        header = self._track_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.resizeSection(1, 150)
        header.resizeSection(2, 120)

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

        # Summary tab — single QTextBrowser
        self._summary_view = self._make_report_browser()
        self._detail_tabs.addTab(self._summary_view, "Summary")

        # File tab — vertical splitter (report + waveform)
        file_splitter = QSplitter(Qt.Vertical)

        self._file_report = self._make_report_browser()
        file_splitter.addWidget(self._file_report)

        self._waveform = WaveformWidget()
        self._waveform.position_clicked.connect(self._on_waveform_seek)

        # Waveform toolbar + widget container
        wf_container = QWidget()
        wf_layout = QVBoxLayout(wf_container)
        wf_layout.setContentsMargins(0, 0, 0, 0)
        wf_layout.setSpacing(0)

        wf_toolbar = QHBoxLayout()
        wf_toolbar.setContentsMargins(4, 2, 4, 2)

        self._rms_toggle = QToolButton()
        self._rms_toggle.setText("RMS")
        self._rms_toggle.setToolTip("Toggle RMS overlay")
        self._rms_toggle.setCheckable(True)
        self._rms_toggle.setAutoRaise(True)
        self._rms_toggle.setStyleSheet(
            "QToolButton:checked { background-color: #2a6db5; color: #ffffff; }")
        self._rms_toggle.toggled.connect(self._waveform.toggle_rms)
        wf_toolbar.addWidget(self._rms_toggle)

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

        self._right_stack.addWidget(self._detail_tabs)  # index 1

        # Start on the tabs page (summary empty until first analysis)
        self._right_stack.setCurrentIndex(_PAGE_TABS)

        return self._right_stack

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
        path = QFileDialog.getExistingDirectory(
            self, "Select Session Directory", "",
            QFileDialog.ShowDirsOnly,
        )
        if not path:
            return

        self._on_stop()
        self._source_dir = path
        self._session = None
        self._summary = None
        self._current_track = None

        # Reset UI
        self._track_table.setRowCount(0)
        self._summary_view.clear()
        self._file_report.clear()
        self._waveform.setVisible(False)
        self._play_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._detail_tabs.setTabEnabled(_TAB_FILE, False)
        self._detail_tabs.setCurrentIndex(_TAB_SUMMARY)
        self._right_stack.setCurrentIndex(_PAGE_TABS)

        wav_files = sorted(
            f for f in os.listdir(path) if f.lower().endswith(".wav")
        )

        if not wav_files:
            self._status_bar.showMessage(f"No .wav files found in {path}")
            self._analyze_action.setEnabled(False)
            return

        self._track_table.setSortingEnabled(False)
        self._track_table.setRowCount(len(wav_files))
        for row, fname in enumerate(wav_files):
            item = _SortableItem(fname, protools_sort_key(fname))
            item.setForeground(FILE_COLOR_OK)
            self._track_table.setItem(row, 0, item)
            for col in range(1, 3):
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

        self._worker = AnalyzeWorker(self._source_dir, config)
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.finished.connect(self._on_analyze_done)
        self._worker.error.connect(self._on_analyze_error)
        self._worker.start()

    @Slot(str)
    def _on_worker_progress(self, message: str):
        self._progress_label.setText(message)
        self._status_bar.showMessage(message)

    @Slot(object, object)
    def _on_analyze_done(self, session, summary):
        self._session = session
        self._summary = summary
        self._analyze_action.setEnabled(True)
        self._worker = None

        self._populate_table(session)
        self._render_summary()

        # Switch to tabs — summary tab
        self._right_stack.setCurrentIndex(_PAGE_TABS)
        self._detail_tabs.setCurrentIndex(_TAB_SUMMARY)

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

    def _render_summary(self):
        """Render the diagnostic summary into the Summary tab."""
        if not self._summary or not self._session:
            return
        html = render_summary_html(
            self._summary, show_hints=False, show_faders=False
        )
        self._summary_view.setHtml(self._wrap_html(html))

    def _show_track_detail(self, track):
        """Populate the File tab with per-track detail + waveform."""
        self._on_stop()
        self._current_track = track

        # Waveform
        has_audio = track.audio_data is not None and track.audio_data.size > 0
        if has_audio:
            self._waveform.set_audio(track.audio_data, track.samplerate)
            all_issues = []
            for result in track.detector_results.values():
                all_issues.extend(getattr(result, "issues", []))
            self._waveform.set_issues(all_issues)
            # RMS overlay: pass window size so per-channel RMS is computed on demand
            flat_cfg = flatten_structured_config(self._config)
            win_ms = flat_cfg.get("window", 400)
            ws = get_window_samples(track, win_ms)
            self._waveform.set_rms_data(ws)
            self._waveform.setVisible(True)
            self._play_btn.setEnabled(True)
            self._update_time_label(0)
        else:
            self._waveform.set_audio(None, 44100)
            self._waveform.set_rms_data(0)
            self._waveform.setVisible(False)
            self._play_btn.setEnabled(False)
            self._update_time_label(0)

        # Detail HTML
        html = render_track_detail_html(track)
        self._file_report.setHtml(self._wrap_html(html))

        # Enable and switch to File tab
        self._detail_tabs.setTabEnabled(_TAB_FILE, True)
        self._detail_tabs.setCurrentIndex(_TAB_FILE)

    def _populate_table(self, session):
        """Update the track table with analysis results."""
        self._track_table.setSortingEnabled(False)
        track_map = {t.filename: t for t in session.tracks}
        for row in range(self._track_table.rowCount()):
            # Remove any previous cell widget before repopulating
            self._track_table.removeCellWidget(row, 2)

            fname_item = self._track_table.item(row, 0)
            if not fname_item:
                continue
            track = track_map.get(fname_item.text())
            if not track:
                continue

            # Column 1: worst severity
            label, color = track_analysis_label(track)
            analysis_item = _SortableItem(label, _SEVERITY_SORT.get(label, 9))
            analysis_item.setForeground(QColor(color))
            self._track_table.setItem(row, 1, analysis_item)

            # Column 2: classification (combo or static)
            pr = (
                next(iter(track.processor_results.values()), None)
                if track.processor_results
                else None
            )
            if track.status != "OK":
                cls_item = _SortableItem("Error", "error")
                cls_item.setForeground(FILE_COLOR_ERROR)
                self._track_table.setItem(row, 2, cls_item)
            elif pr and pr.classification == "Silent":
                cls_item = _SortableItem("Silent", "silent")
                cls_item.setForeground(FILE_COLOR_SILENT)
                self._track_table.setItem(row, 2, cls_item)
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
                self._track_table.setItem(row, 2, sort_item)

                # Combo widget
                combo = QComboBox()
                combo.addItems(["Transient", "Sustained", "Skip"])
                combo.blockSignals(True)
                combo.setCurrentText(base_cls)
                combo.blockSignals(False)
                combo.setProperty("track_filename", track.filename)
                self._style_classification_combo(combo, base_cls)
                combo.currentTextChanged.connect(self._on_classification_changed)
                self._track_table.setCellWidget(row, 2, combo)
            else:
                cls_item = _SortableItem("", "zzz")
                self._track_table.setItem(row, 2, cls_item)
        self._track_table.setSortingEnabled(True)

        # Auto-fit columns 1 & 2 to content, File column stays Stretch
        header = self._track_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._track_table.resizeColumnsToContents()
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Interactive)

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

        # Set override and recalculate processor result
        track.classification_override = text
        self._recalculate_processor(track)

        # Update combo color
        self._style_classification_combo(combo, text)

        # Update hidden sort item
        for row in range(self._track_table.rowCount()):
            item = self._track_table.item(row, 0)
            if item and item.text() == fname:
                sort_item = self._track_table.item(row, 2)
                if sort_item:
                    sort_item.setText(text)
                    sort_item._sort_key = text.lower()
                break

        # Refresh File tab if this track is currently displayed
        if self._current_track and self._current_track.filename == fname:
            html = render_track_detail_html(track)
            self._file_report.setHtml(self._wrap_html(html))

    def _recalculate_processor(self, track):
        """Re-run the normalization processor for a single track."""
        from sessionpreplib.processors.bimodal_normalize import (
            BimodalNormalizeProcessor,
        )
        proc = BimodalNormalizeProcessor()
        flat_cfg = flatten_structured_config(self._config)
        proc.configure(flat_cfg)
        result = proc.process(track)
        track.processor_results[proc.id] = result

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
        dlg = PreferencesDialog(self._config, parent=self)
        dlg.exec()
        if dlg.saved:
            from .settings import save_config
            self._config = dlg.result_config()
            save_config(self._config)
            self._status_bar.showMessage("Preferences saved.")
            # Re-analyze if a session is loaded
            if self._source_dir:
                self._on_analyze()
            # Prompt restart if scale factor changed
            new_scale = self._config.get("gui", {}).get("scale_factor", 1.0)
            if new_scale != old_scale:
                QMessageBox.information(
                    self, "Restart required",
                    f"HiDPI scale factor changed from {old_scale} to {new_scale}.\n"
                    "Please restart SessionPrep for the new scaling to take effect.",
                )

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
