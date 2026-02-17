"""Main application window for SessionPrep GUI."""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import time

from PySide6.QtCore import Qt, Signal, Slot, QSize, QTimer, QUrl, QMimeData, QPoint
from PySide6.QtGui import (
    QAction, QActionGroup, QDrag, QFont, QColor, QIcon, QKeySequence,
    QPainter, QPen, QPixmap, QShortcut,
)
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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QStatusBar,
    QWidget,
)

from sessionpreplib.audio import get_window_samples
from sessionpreplib.config import (
    ANALYSIS_PARAMS, PRESENTATION_PARAMS, default_config,
    flatten_structured_config,
)
from sessionpreplib.daw_processors import (
    default_daw_processors,
    create_runtime_daw_processors,
)
from sessionpreplib.detector import TrackDetector
from sessionpreplib.detectors import default_detectors, detector_help_map
from sessionpreplib.processors import default_processors
from sessionpreplib.utils import protools_sort_key

from .settings import (
    load_config, config_path, save_config,
    resolve_config_preset, build_defaults,
)
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
from .log import dbg
from .param_widgets import _build_param_page, _read_widget, _set_widget_value
from .preferences import PreferencesDialog, _argb_to_qcolor
from .report import render_summary_html, render_track_detail_html
from .widgets import BatchEditTableWidget, BatchComboBox, ProgressPanel
from .worker import (
    AnalyzeWorker, BatchReanalyzeWorker, DawCheckWorker, DawFetchWorker,
    DawTransferWorker, PrepareWorker,
)
from .waveform import WaveformWidget, WaveformLoadWorker
from sessionpreplib.audio import AUDIO_EXTENSIONS
from .playback import PlaybackController

_TAB_SUMMARY = 0
_TAB_FILE = 1
_TAB_GROUPS = 2
_TAB_SESSION = 3

_PAGE_PROGRESS = 0
_PAGE_TABS = 1

_PHASE_ANALYSIS = 0
_PHASE_SETUP = 1

_SETUP_RIGHT_PLACEHOLDER = 0
_SETUP_RIGHT_TREE = 1

_SEVERITY_SORT = {"PROBLEMS": 0, "Error": 0, "ATTENTION": 1, "OK": 2, "": 3}


def _make_analysis_cell(html: str, sort_key: int) -> tuple[QLabel, '_SortableItem']:
    """Create a QLabel + hidden sort item for the Analysis column."""
    lbl = QLabel(html)
    lbl.setStyleSheet(
        "QLabel { background: transparent; font-size: 8pt;"
        " font-family: Consolas, monospace; padding: 0 4px; }")
    lbl.setTextFormat(Qt.RichText)
    item = _SortableItem("", sort_key)
    return lbl, item


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


_MIME_TRACKS = "application/x-sessionprep-tracks"


class _SetupDragTable(BatchEditTableWidget):
    """BatchEditTableWidget that produces custom MIME for internal drag."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDefaultDropAction(Qt.CopyAction)

    def mimeTypes(self):
        return [_MIME_TRACKS]

    def mimeData(self, items):
        filenames: set[str] = set()
        for item in items:
            if item.column() == 1 and item.text():  # col 1 = File
                filenames.add(item.text())
        if not filenames:
            return super().mimeData(items)
        mime = QMimeData()
        mime.setData(_MIME_TRACKS, json.dumps(sorted(filenames)).encode())
        return mime

    def supportedDragActions(self):
        return Qt.CopyAction

    def startDrag(self, supportedActions):
        items = self.selectedItems()
        mime = self.mimeData(items)
        if mime is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime)
        # Build a compact, semi-transparent label listing dragged filenames
        filenames = sorted({
            it.text() for it in items if it.column() == 1 and it.text()})
        if not filenames:
            return
        label = "\n".join(filenames[:8])
        if len(filenames) > 8:
            label += f"\n… +{len(filenames) - 8} more"
        fm = self.fontMetrics()
        lines = label.split("\n")
        line_h = fm.height() + 2
        w = max(fm.horizontalAdvance(ln) for ln in lines) + 12
        h = line_h * len(lines) + 6
        pix = QPixmap(w, h)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setOpacity(0.75)
        painter.fillRect(pix.rect(), QColor(COLORS["accent"]))
        painter.setOpacity(1.0)
        painter.setPen(QColor(COLORS["text"]))
        painter.setFont(self.font())
        y = 3 + fm.ascent()
        for ln in lines:
            painter.drawText(6, y, ln)
            y += line_h
        painter.end()
        drag.setPixmap(pix)
        drag.setHotSpot(QPoint(0, 0))
        drag.exec(Qt.CopyAction)


class _FolderDropTree(QTreeWidget):
    """QTreeWidget that accepts track drops onto folder items.

    Supports external drops from the setup table and internal
    drag-and-drop to reorder tracks within / across folders.
    """

    # (filenames, folder_id, insert_index)  -1 = append
    tracks_dropped = Signal(list, str, int)
    tracks_unassigned = Signal(list)  # [filenames]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QTreeWidget.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(True)

    # -- MIME production (for internal drag of track items) -----------------

    def mimeTypes(self):
        return [_MIME_TRACKS]

    def mimeData(self, items):
        filenames = [
            it.data(0, Qt.UserRole) for it in items
            if it.data(0, Qt.UserRole + 1) == "track"
        ]
        if not filenames:
            return None  # block drag of non-track items (folders)
        mime = QMimeData()
        mime.setData(_MIME_TRACKS, json.dumps(filenames).encode())
        return mime

    def supportedDropActions(self):
        return Qt.CopyAction | Qt.MoveAction

    # -- Drop handling -----------------------------------------------------

    def _is_valid_mime(self, mimeData) -> bool:
        """Check that the MIME payload is our JSON, not Qt internal data."""
        if not mimeData.hasFormat(_MIME_TRACKS):
            return False
        try:
            bytes(mimeData.data(_MIME_TRACKS)).decode("utf-8")
            return True
        except (UnicodeDecodeError, ValueError):
            return False

    def _resolve_drop(self, pos):
        """Return (folder_id, insert_index) for a drop at *pos*.

        Uses the item geometry to decide above / on / below placement.
        Returns (None, -1) if the drop target is invalid.
        """
        item = self.itemAt(pos)
        if not item:
            return None, -1
        kind = item.data(0, Qt.UserRole + 1)
        if kind == "folder":
            return item.data(0, Qt.UserRole), -1
        if kind == "track":
            parent = item.parent()
            if not parent or parent.data(0, Qt.UserRole + 1) != "folder":
                return None, -1
            folder_id = parent.data(0, Qt.UserRole)
            idx = parent.indexOfChild(item)
            rect = self.visualItemRect(item)
            mid = rect.top() + rect.height() // 2
            if pos.y() > mid:
                idx += 1  # drop below → insert after
            return folder_id, idx
        return None, -1

    def dragEnterEvent(self, event):
        if self._is_valid_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if not self._is_valid_mime(event.mimeData()):
            event.ignore()
            return
        folder_id, _ = self._resolve_drop(event.position().toPoint())
        if folder_id is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not self._is_valid_mime(event.mimeData()):
            event.ignore()
            return
        pos = event.position().toPoint()
        folder_id, idx = self._resolve_drop(pos)
        if folder_id is None:
            event.ignore()
            return
        data = bytes(event.mimeData().data(_MIME_TRACKS)).decode("utf-8")
        filenames = json.loads(data)
        self.tracks_dropped.emit(filenames, folder_id, idx)
        event.acceptProposedAction()

    # -- Delete to unassign ------------------------------------------------

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            filenames = []
            for item in self.selectedItems():
                if item.data(0, Qt.UserRole + 1) == "track":
                    filenames.append(item.data(0, Qt.UserRole))
            if filenames:
                self.tracks_unassigned.emit(filenames)
            return
        super().keyPressEvent(event)


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
        self._worker = None
        self._batch_worker: BatchReanalyzeWorker | None = None
        self._batch_filenames: set[str] = set()
        self._wf_worker: WaveformLoadWorker | None = None
        self._current_track = None
        self._session_groups: list[dict] = []
        self._prev_group_assignments: dict[str, str | None] = {}
        self._active_session_preset: str = "Default"
        self._session_config: dict[str, Any] | None = None
        self._session_widgets: dict[str, list[tuple[str, QWidget]]] = {}

        t0 = time.perf_counter()
        self._detector_help = detector_help_map()
        dbg(f"detector_help_map: {(time.perf_counter() - t0) * 1000:.1f} ms")

        self._daw_check_worker: DawCheckWorker | None = None
        self._pending_after_check = None
        self._daw_fetch_worker: DawFetchWorker | None = None
        self._daw_transfer_worker: DawTransferWorker | None = None
        self._prepare_worker: PrepareWorker | None = None

        # Load persistent GUI configuration (four-section structure)
        t0 = time.perf_counter()
        self._config = load_config()
        dbg(f"load_config: {(time.perf_counter() - t0) * 1000:.1f} ms")
        self._active_config_preset_name: str = self._config.get(
            "app", {}).get("active_config_preset", "Default")

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

        # Tab 0 — Analysis
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
        self._phase_tabs.addTab(analysis_page, "Analysis && Preparation")

        # Tab 1 — Session Setup (placeholder)
        self._phase_tabs.addTab(self._build_setup_page(), "Session Setup")
        self._phase_tabs.setTabEnabled(_PHASE_SETUP, False)
        self._phase_tabs.currentChanged.connect(self._on_phase_tab_changed)

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
        self._setup_table.setColumnCount(6)
        self._setup_table.setHorizontalHeaderLabels(
            ["", "File", "Ch", "Clip Gain", "Fader Gain", "Group"]
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
        sh.setSectionResizeMode(0, QHeaderView.Fixed)
        sh.resizeSection(0, 24)
        sh.setSectionResizeMode(1, QHeaderView.Stretch)
        sh.setSectionResizeMode(2, QHeaderView.Fixed)
        sh.setSectionResizeMode(3, QHeaderView.Interactive)
        sh.setSectionResizeMode(4, QHeaderView.Interactive)
        sh.setSectionResizeMode(5, QHeaderView.Interactive)
        sh.resizeSection(2, 30)
        sh.resizeSection(3, 90)
        sh.resizeSection(4, 90)
        sh.resizeSection(5, 110)

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
            self._daw_check_label.setText(message)
            self._daw_check_label.setStyleSheet(f"color: {COLORS['problems']};")
            self._pending_after_check = None
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
        dp_name = self._active_daw_processor.name if self._active_daw_processor else "DAW"
        self._status_bar.showMessage(f"Transferring to {dp_name}\u2026")
        self._transfer_progress.start("Preparing\u2026")
        # Inject GUI config (groups + colors) into session.config so
        # transfer() can resolve group → color ARGB
        self._session.config.setdefault("gui", {})["groups"] = list(
            self._session_groups)
        colors = self._config.get("colors", PT_DEFAULT_COLORS)
        self._session.config["gui"]["colors"] = colors
        # Inject source dir and output folder for file-based processors
        self._session.config["_source_dir"] = self._source_dir
        self._session.config["_output_folder"] = self._config.get(
            "app", {}).get("output_folder", "processed")
        self._daw_transfer_worker = DawTransferWorker(
            self._active_daw_processor, self._session)
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
        if self._session:
            track_map = {t.filename: t for t in self._session.tracks}

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
                track_item.setText(0, fname)
                track_item.setData(0, Qt.UserRole, fname)
                track_item.setData(0, Qt.UserRole + 1, "track")
                track_item.setFlags(
                    (track_item.flags() | Qt.ItemIsDragEnabled)
                    & ~Qt.ItemIsDropEnabled)
                # Row background from group color (matches table tint)
                tc = track_map.get(fname)
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

        # Collect assignments: folder_id → [filenames]
        batch: dict[str, list[str]] = {}
        no_group = 0
        no_target = 0
        no_folder = 0
        already_assigned = 0
        for track in self._session.tracks:
            # Skip already-assigned tracks
            if track.filename in assignments:
                already_assigned += 1
                continue
            # Skip tracks without a group or without a DAW target
            if not track.group:
                no_group += 1
                continue
            target_key = group_target.get(track.group)
            if not target_key:
                no_target += 1
                continue
            folder_id = folder_by_name.get(target_key)
            if not folder_id:
                no_folder += 1
                continue
            batch.setdefault(folder_id, []).append(track.filename)

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

        self._mono_btn = QPushButton("M")
        self._mono_btn.setCheckable(True)
        self._mono_btn.setToolTip("Play as mono (L+R)/2")
        self._mono_btn.setFixedWidth(36)
        self._mono_btn.setStyleSheet(
            "QPushButton { font-weight: bold; }"
            "QPushButton:checked { background-color: #cc8800; color: #000; }"
        )
        controls.addWidget(self._mono_btn)

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
        self._detail_tabs.currentChanged.connect(self._on_detail_tab_changed)

        # Summary tab — single QTextBrowser
        self._summary_view = self._make_report_browser()
        self._detail_tabs.addTab(self._summary_view, "Summary")

        # File tab — vertical splitter (report + waveform)
        self._file_splitter = QSplitter(Qt.Vertical)

        self._file_report = self._make_report_browser()
        self._file_splitter.addWidget(self._file_report)

        self._waveform = WaveformWidget()
        self._waveform.position_clicked.connect(self._on_waveform_seek)
        self._waveform.set_invert_scroll(
            self._config.get("app", {}).get("invert_scroll", "default"))


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
        self._markers_toggle.setChecked(False)
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

        self._file_splitter.addWidget(wf_container)

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

    # ── Config tab (per-session overrides) ────────────────────────────────

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
        preset = self._active_preset()

        # Analysis
        item = QTreeWidgetItem(self._session_tree, ["Analysis"])
        values = preset.get("analysis", {})
        pg, wdg = _build_param_page(ANALYSIS_PARAMS, values)
        self._session_widgets["analysis"] = wdg
        idx = self._session_stack.addWidget(pg)
        self._session_page_index[id(item)] = idx

        # Detectors (parent shows presentation params)
        det_parent = QTreeWidgetItem(self._session_tree, ["Detectors"])
        pres_values = preset.get("presentation", {})
        pg, wdg = _build_param_page(PRESENTATION_PARAMS, pres_values)
        self._session_widgets["_presentation"] = wdg
        idx = self._session_stack.addWidget(pg)
        self._session_page_index[id(det_parent)] = idx

        det_sections = preset.get("detectors", {})
        for det in default_detectors():
            params = det.config_params()
            if not params:
                continue
            child = QTreeWidgetItem(det_parent, [det.name])
            vals = det_sections.get(det.id, {})
            pg, wdg = _build_param_page(params, vals)
            self._session_widgets[f"detectors.{det.id}"] = wdg
            idx = self._session_stack.addWidget(pg)
            self._session_page_index[id(child)] = idx

        # Processors
        proc_parent = QTreeWidgetItem(self._session_tree, ["Processors"])
        placeholder = QWidget()
        pl = QVBoxLayout(placeholder)
        pl.setContentsMargins(12, 12, 12, 12)
        pl.addWidget(QLabel("Select a processor from the tree to configure."))
        pl.addStretch()
        idx = self._session_stack.addWidget(placeholder)
        self._session_page_index[id(proc_parent)] = idx

        proc_sections = preset.get("processors", {})
        for proc in default_processors():
            params = proc.config_params()
            if not params:
                continue
            child = QTreeWidgetItem(proc_parent, [proc.name])
            vals = proc_sections.get(proc.id, {})
            pg, wdg = _build_param_page(params, vals)
            self._session_widgets[f"processors.{proc.id}"] = wdg
            idx = self._session_stack.addWidget(pg)
            self._session_page_index[id(child)] = idx

            # Connect processor enabled toggle to live-update Processing column
            enabled_key = f"{proc.id}_enabled"
            for key, widget in wdg:
                if key == enabled_key and isinstance(widget, QCheckBox):
                    widget.toggled.connect(self._on_processor_enabled_changed)
                    break

        # DAW Processors
        daw_parent = QTreeWidgetItem(self._session_tree, ["DAW Processors"])
        placeholder2 = QWidget()
        pl2 = QVBoxLayout(placeholder2)
        pl2.setContentsMargins(12, 12, 12, 12)
        pl2.addWidget(QLabel(
            "Select a DAW processor from the tree to configure."))
        pl2.addStretch()
        idx = self._session_stack.addWidget(placeholder2)
        self._session_page_index[id(daw_parent)] = idx

        dp_sections = preset.get("daw_processors", {})
        for dp in default_daw_processors():
            params = dp.config_params()
            if not params:
                continue
            child = QTreeWidgetItem(daw_parent, [dp.name])
            vals = dp_sections.get(dp.id, {})
            pg, wdg = _build_param_page(params, vals)
            self._session_widgets[f"daw_processors.{dp.id}"] = wdg
            idx = self._session_stack.addWidget(pg)
            self._session_page_index[id(child)] = idx

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
        # Analysis
        analysis = preset.get("analysis", {})
        for key, widget in self._session_widgets.get("analysis", []):
            if key in analysis:
                _set_widget_value(widget, analysis[key])

        # Presentation
        pres = preset.get("presentation", {})
        for key, widget in self._session_widgets.get("_presentation", []):
            if key in pres:
                _set_widget_value(widget, pres[key])

        # Detectors
        det_sections = preset.get("detectors", {})
        for det in default_detectors():
            wkey = f"detectors.{det.id}"
            if wkey not in self._session_widgets:
                continue
            vals = det_sections.get(det.id, {})
            for key, widget in self._session_widgets[wkey]:
                if key in vals:
                    _set_widget_value(widget, vals[key])

        # Processors
        proc_sections = preset.get("processors", {})
        for proc in default_processors():
            wkey = f"processors.{proc.id}"
            if wkey not in self._session_widgets:
                continue
            vals = proc_sections.get(proc.id, {})
            for key, widget in self._session_widgets[wkey]:
                if key in vals:
                    _set_widget_value(widget, vals[key])

        # DAW Processors
        dp_sections = preset.get("daw_processors", {})
        for dp in default_daw_processors():
            wkey = f"daw_processors.{dp.id}"
            if wkey not in self._session_widgets:
                continue
            vals = dp_sections.get(dp.id, {})
            for key, widget in self._session_widgets[wkey]:
                if key in vals:
                    _set_widget_value(widget, vals[key])

    def _read_session_config(self) -> dict[str, Any]:
        """Read current session widget values into a structured config dict."""
        cfg: dict[str, Any] = {}

        # Analysis
        analysis: dict[str, Any] = {}
        for key, widget in self._session_widgets.get("analysis", []):
            analysis[key] = _read_widget(widget)
        cfg["analysis"] = analysis

        # Presentation
        presentation: dict[str, Any] = {}
        for key, widget in self._session_widgets.get("_presentation", []):
            presentation[key] = _read_widget(widget)
        cfg["presentation"] = presentation

        # Detectors
        detectors: dict[str, dict] = {}
        for det in default_detectors():
            wkey = f"detectors.{det.id}"
            if wkey not in self._session_widgets:
                continue
            section: dict[str, Any] = {}
            for key, widget in self._session_widgets[wkey]:
                section[key] = _read_widget(widget)
            detectors[det.id] = section
        cfg["detectors"] = detectors

        # Processors
        processors: dict[str, dict] = {}
        for proc in default_processors():
            wkey = f"processors.{proc.id}"
            if wkey not in self._session_widgets:
                continue
            section = {}
            for key, widget in self._session_widgets[wkey]:
                section[key] = _read_widget(widget)
            processors[proc.id] = section
        cfg["processors"] = processors

        # DAW Processors
        daw_procs: dict[str, dict] = {}
        global_dp = self._active_preset().get("daw_processors", {})
        for dp in default_daw_processors():
            wkey = f"daw_processors.{dp.id}"
            if wkey not in self._session_widgets:
                continue
            section = {}
            for key, widget in self._session_widgets[wkey]:
                section[key] = _read_widget(widget)
            # Carry forward non-widget keys (e.g. dawproject_templates)
            for gk, gv in global_dp.get(dp.id, {}).items():
                if gk not in section:
                    section[gk] = gv
            daw_procs[dp.id] = section
        cfg["daw_processors"] = daw_procs

        return cfg

    def _on_session_config_reset(self):
        """Reset session config to the global config preset defaults."""
        preset = self._active_preset()
        self._session_config = copy.deepcopy(preset)
        self._load_session_widgets(self._session_config)
        self._status_bar.showMessage("Session config reset to preset defaults.")

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

    def _make_report_browser(self) -> QTextBrowser:
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
        self._waveform.setVisible(False)
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
        if self._session_config is not None:
            cfg = self._read_session_config()
            return cfg.get("presentation", {}).get(
                "show_clean_detectors", False)
        preset = self._active_preset()
        return preset.get("presentation", {}).get("show_clean_detectors", False)

    @property
    def _verbose(self) -> bool:
        return self._config.get("app", {}).get("report_verbosity", "normal") == "verbose"

    def _render_summary(self):
        """Render the diagnostic summary into the Summary tab."""
        if not self._summary or not self._session:
            return
        html = render_summary_html(
            self._summary, show_faders=False,
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
            self._wf_worker.cancel()
            self._wf_worker.finished.disconnect()
            self._wf_worker = None

        has_audio = track.audio_data is not None and track.audio_data.size > 0
        if has_audio:
            self._waveform.set_loading(True)
            if self._detail_tabs.currentIndex() == _TAB_FILE:
                self._waveform.setVisible(True)
            self._play_btn.setEnabled(False)
            self._update_time_label(0)

            flat_cfg = self._flat_config()
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
            if self._detail_tabs.currentIndex() == _TAB_FILE:
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
        cmap = self._config.get("app", {}).get("spectrogram_colormap", "magma")
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

    # ── Processing column (col 7) ──────────────────────────────────────

    def _create_processing_button(self, row: int, track) -> None:
        """Create a multiselect tool button for the Processing column."""
        if track.status != "OK":
            item = _SortableItem("", "zzz")
            self._track_table.setItem(row, 7, item)
            return

        processors = self._session.processors if self._session else []

        btn = QToolButton()
        btn.setProperty("track_filename", track.filename)

        if processors:
            btn.setPopupMode(QToolButton.InstantPopup)
            menu = QMenu(btn)
            for proc in processors:
                action = menu.addAction(proc.name)
                action.setCheckable(True)
                checked = proc.id not in track.processor_skip
                action.setChecked(checked)
                action.setData(proc.id)
                action.toggled.connect(self._on_processing_toggled)
            btn.setMenu(menu)
        else:
            btn.setEnabled(False)

        self._update_processing_button_label(btn, track, processors)

        # Hidden sort item
        sort_item = _SortableItem("", len(track.processor_skip))
        self._track_table.setItem(row, 7, sort_item)
        self._track_table.setCellWidget(row, 7, btn)

    def _update_processing_button_label(self, btn, track, processors):
        """Set the button label based on current processor_skip state."""
        if not processors:
            btn.setText("None")
            btn.setToolTip("No audio processors enabled")
            return
        active = [p.name for p in processors if p.id not in track.processor_skip]
        if len(active) == len(processors):
            btn.setText("Default")
            btn.setToolTip("Using all enabled processors: " + ", ".join(p.name for p in processors))
        elif not active:
            btn.setText("None")
            btn.setToolTip("All processors skipped for this track")
        else:
            btn.setText(", ".join(active))
            btn.setToolTip("Active processors: " + ", ".join(active))

    @Slot(bool)
    def _on_processing_toggled(self, checked: bool):
        """Handle user toggling a processor in the Processing column menu."""
        action = self.sender()
        if not action:
            return
        menu = action.parent()
        if not menu:
            return
        btn = menu.parent()
        if not btn:
            return
        fname = btn.property("track_filename")
        if not fname or not self._session:
            return
        track = next(
            (t for t in self._session.tracks if t.filename == fname), None
        )
        if not track:
            return

        proc_id = action.data()
        if checked:
            track.processor_skip.discard(proc_id)
        else:
            track.processor_skip.add(proc_id)

        processors = self._session.processors if self._session else []
        self._update_processing_button_label(btn, track, processors)
        self._mark_prepare_stale()

    def _populate_table(self, session):
        """Update the track table with analysis results."""
        self._track_table.setSortingEnabled(False)
        track_map = {t.filename: t for t in session.tracks}
        for row in range(self._track_table.rowCount()):
            # Remove any previous cell widgets before repopulating
            self._track_table.removeCellWidget(row, 3)
            self._track_table.removeCellWidget(row, 4)
            self._track_table.removeCellWidget(row, 5)
            self._track_table.removeCellWidget(row, 6)
            self._track_table.removeCellWidget(row, 7)

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

            # Column 2: severity counts
            dets = session.detectors if hasattr(session, 'detectors') else None
            _plain, html, _color, sort_key = track_analysis_label(track, dets)
            lbl, item = _make_analysis_cell(html, sort_key)
            self._track_table.setItem(row, 2, item)
            self._track_table.setCellWidget(row, 2, lbl)

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
            elif track.status == "OK":
                # OK track but no processor results (all processors disabled)
                cls_item = _SortableItem("", "zzz")
                self._track_table.setItem(row, 3, cls_item)
                gain_item = _SortableItem("", 0.0)
                self._track_table.setItem(row, 4, gain_item)
            else:
                cls_item = _SortableItem("", "zzz")
                self._track_table.setItem(row, 3, cls_item)
                gain_item = _SortableItem("", 0.0)
                self._track_table.setItem(row, 4, gain_item)

            # Group combo, processing button, and row color for all OK tracks
            if track.status == "OK":
                # Group combo (column 6)
                self._create_group_combo(row, track)

                # Processing multiselect (column 7)
                self._create_processing_button(row, track)

                # Row background from group color
                self._apply_row_group_color(row, track.group)
        self._track_table.setSortingEnabled(True)

        # Auto-fit columns 2–7 to content, File column stays Stretch, Ch stays Fixed
        header = self._track_table.horizontalHeader()
        for col in (2, 3, 4, 5, 6, 7):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self._track_table.resizeColumnsToContents()
        for col in (2, 3, 4, 5, 6, 7):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        self._auto_fit_group_column()
        self._auto_fit_track_table()

    def _populate_setup_table(self):
        """Refresh the Session Setup track table from the current session."""
        if not self._session:
            return
        self._setup_table.setSortingEnabled(False)
        self._setup_table.setRowCount(0)

        ok_tracks = [t for t in self._session.tracks if t.status == "OK"]
        self._setup_table.setRowCount(len(ok_tracks))
        gcm = self._group_color_map()
        gcm_rank = self._group_rank_map()
        glm = self._gain_linked_map()

        # Determine which tracks are assigned to a DAW folder
        assignments = {}
        if self._session.daw_state and self._active_daw_processor:
            dp_state = self._session.daw_state.get(
                self._active_daw_processor.id, {})
            assignments = dp_state.get("assignments", {})

        for row, track in enumerate(ok_tracks):
            pr = (
                next(iter(track.processor_results.values()), None)
                if track.processor_results
                else None
            )
            # Column 0: checkmark (assigned to folder?)
            assigned = track.filename in assignments
            chk_item = _SortableItem("✓" if assigned else "", int(not assigned))
            if assigned:
                chk_item.setForeground(QColor(COLORS["clean"]))
            self._setup_table.setItem(row, 0, chk_item)

            # Column 1: filename
            fname_item = _SortableItem(
                track.filename, protools_sort_key(track.filename))
            fname_item.setForeground(FILE_COLOR_OK)
            self._setup_table.setItem(row, 1, fname_item)

            # Column 2: channels
            ch_item = _SortableItem(str(track.channels), track.channels)
            ch_item.setForeground(QColor(COLORS["dim"]))
            self._setup_table.setItem(row, 2, ch_item)

            # Column 3: clip gain
            clip_gain = pr.gain_db if pr else 0.0
            cg_item = _SortableItem(f"{clip_gain:+.1f} dB", clip_gain)
            cg_item.setForeground(QColor(COLORS["text"]))
            self._setup_table.setItem(row, 3, cg_item)

            # Column 4: fader gain
            fader_gain = pr.data.get("fader_offset", 0.0) if pr else 0.0
            fg_item = _SortableItem(f"{fader_gain:+.1f} dB", fader_gain)
            fg_item.setForeground(QColor(COLORS["text"]))
            self._setup_table.setItem(row, 4, fg_item)

            # Column 5: group (read-only, with link indicator)
            grp_label = self._group_display_name(track.group, glm) if track.group else ""
            grp_rank = gcm_rank.get(track.group, len(gcm_rank)) if track.group else len(gcm_rank)
            grp_item = _SortableItem(grp_label, grp_rank)
            grp_item.setForeground(QColor(COLORS["text"]))
            self._setup_table.setItem(row, 5, grp_item)

            # Row background from group color
            self._apply_row_group_color(row, track.group, gcm,
                                        table=self._setup_table)

        self._setup_table.setSortingEnabled(True)

        # Auto-fit columns to content
        sh = self._setup_table.horizontalHeader()
        for col in range(self._setup_table.columnCount()):
            sh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self._setup_table.resizeColumnsToContents()
        sh.setSectionResizeMode(0, QHeaderView.Fixed)
        sh.resizeSection(0, 24)
        sh.setSectionResizeMode(1, QHeaderView.Stretch)
        sh.setSectionResizeMode(2, QHeaderView.Fixed)
        for col in range(3, self._setup_table.columnCount()):
            sh.setSectionResizeMode(col, QHeaderView.Interactive)

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
        self._mark_prepare_stale()

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
        self._mark_prepare_stale()

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
            result.data["original_gain_db"] = result.gain_db
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
        self._mark_prepare_stale()

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
        combo.textActivated.connect(self._on_group_changed)
        self._track_table.setCellWidget(row, 6, combo)

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

    def _auto_fit_track_table(self):
        """Shrink the left panel to fit the track table columns, giving
        more space to the right detail panel.

        Temporarily switches the File column from Stretch to
        ResizeToContents so we can measure its true content width,
        then adjusts the splitter and restores Stretch mode.
        """
        header = self._track_table.horizontalHeader()

        # Temporarily fit File column to content so we get a true width
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._track_table.resizeColumnToContents(0)
        total_w = sum(header.sectionSize(c) for c in range(header.count()))
        # Restore File column to Stretch
        header.setSectionResizeMode(0, QHeaderView.Stretch)

        # vertical-header (hidden=0) + scrollbar (~20) + frame borders (~4)
        vhw = self._track_table.verticalHeader().width() if self._track_table.verticalHeader().isVisible() else 0
        padding = vhw + 20 + 4
        needed = total_w + padding

        splitter_total = self._main_splitter.width()
        if splitter_total > 0:
            right_w = max(splitter_total - needed, 300)
            left_w = splitter_total - right_w
            self._main_splitter.setSizes([left_w, right_w])

    def _auto_fit_group_column(self):
        """Resize the Group column (6) to fit the widest current combo text."""
        max_w = 0
        for row in range(self._track_table.rowCount()):
            w = self._track_table.cellWidget(row, 6)
            if isinstance(w, BatchComboBox):
                fm = w.fontMetrics()
                tw = fm.horizontalAdvance(w.currentText())
                max_w = max(max_w, tw)
        if max_w > 0:
            # icon (16) + icon gap (4) + text + dropdown arrow (~24) + margins (16)
            needed = 16 + 4 + max_w + 24 + 16
            header = self._track_table.horizontalHeader()
            header.resizeSection(6, max(needed, 100))

    @Slot(str)
    def _on_group_changed(self, text: str):
        """Handle user changing the Group dropdown."""
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
        if getattr(combo, 'batch_mode', False):
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

        # Re-apply group levels for any gain-linked groups this track belongs to
        self._apply_linked_group_levels()

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
        _plain, html, _color, sort_key = track_analysis_label(track, dets)
        lbl, item = _make_analysis_cell(html, sort_key)
        self._track_table.setItem(row, 2, item)
        self._track_table.setCellWidget(row, 2, lbl)

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

        # Re-apply row group color (new items lose their background)
        self._apply_row_group_color(row, track.group)

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
        self._playback.play(track.audio_data, track.samplerate, start,
                            mono=self._mono_btn.isChecked())
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
    t_main = time.perf_counter()

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

    dbg(f"main() startup total: {(time.perf_counter() - t_main) * 1000:.1f} ms")

    sys.exit(app.exec())
