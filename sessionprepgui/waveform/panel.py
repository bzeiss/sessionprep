"""Reusable waveform panel: toolbar + WaveformWidget + transport bar."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .widget import WaveformWidget


class WaveformPanel(QWidget):
    """Composite widget: waveform toolbar + WaveformWidget + transport bar.

    Parameters
    ----------
    analysis_mode : bool
        When True, analysis-specific toolbar controls (Detector Overlays,
        Peak/RMS Max, RMS L/R, RMS AVG) are visible. When False they are
        hidden — suitable for Phase 1 or other contexts without analysis data.
    """

    # Signals forwarded from inner widgets / transport
    play_clicked = Signal()
    stop_clicked = Signal()
    position_clicked = Signal(int)

    def __init__(self, analysis_mode: bool = True, parent=None):
        super().__init__(parent)
        self._analysis_mode = analysis_mode

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── WaveformWidget (created first — toolbar references it) ───────
        self.waveform = WaveformWidget()
        self.waveform.position_clicked.connect(self.position_clicked)

        # ── Toolbar ──────────────────────────────────────────────────────
        layout.addWidget(self._build_toolbar())
        layout.addWidget(self.waveform, 1)

        # ── Transport bar ────────────────────────────────────────────────
        layout.addWidget(self._build_transport())

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> QWidget:
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(4, 2, 4, 2)

        toggle_style = (
            "QToolButton:checked { background-color: #2a6db5;"
            " color: #ffffff; }")

        dropdown_style = (
            "QToolButton { padding-right: 30px; }"
            "QToolButton::menu-indicator { subcontrol-position: right center;"
            " subcontrol-origin: padding; right: 5px; }")

        # Display mode dropdown
        self.display_mode_btn = QToolButton()
        self.display_mode_btn.setText("Waveform")
        self.display_mode_btn.setToolTip(
            "Switch between Waveform and Spectrogram display")
        self.display_mode_btn.setPopupMode(QToolButton.InstantPopup)
        self.display_mode_btn.setAutoRaise(True)
        self.display_mode_btn.setStyleSheet(dropdown_style)
        display_menu = QMenu(self.display_mode_btn)
        self.wf_action = display_menu.addAction("Waveform")
        self.spec_action = display_menu.addAction("Spectrogram")
        self.wf_action.setCheckable(True)
        self.wf_action.setChecked(True)
        self.spec_action.setCheckable(True)
        self._display_group = QActionGroup(self)
        self._display_group.addAction(self.wf_action)
        self._display_group.addAction(self.spec_action)
        self._display_group.triggered.connect(self._on_display_mode_changed)
        self.display_mode_btn.setMenu(display_menu)
        toolbar.addWidget(self.display_mode_btn)

        toolbar.addSpacing(8)

        # Spectrogram settings dropdown
        self.spec_settings_btn = QToolButton()
        self.spec_settings_btn.setText("Display")
        self.spec_settings_btn.setToolTip(
            "Configure spectrogram display parameters")
        self.spec_settings_btn.setPopupMode(QToolButton.InstantPopup)
        self.spec_settings_btn.setAutoRaise(True)
        self.spec_settings_btn.setStyleSheet(dropdown_style)
        spec_menu = QMenu(self.spec_settings_btn)

        # -- FFT Size submenu --
        fft_menu = spec_menu.addMenu("FFT Size")
        self.fft_group = QActionGroup(self)
        for sz in (512, 1024, 2048, 4096, 8192):
            act = fft_menu.addAction(str(sz))
            act.setCheckable(True)
            act.setData(sz)
            if sz == 2048:
                act.setChecked(True)
            self.fft_group.addAction(act)

        # -- Window submenu --
        win_menu = spec_menu.addMenu("Window")
        self.win_group = QActionGroup(self)
        _WINDOW_MAP = [("Hann", "hann"), ("Hamming", "hamming"),
                       ("Blackman-Harris", "blackmanharris")]
        for label, key in _WINDOW_MAP:
            act = win_menu.addAction(label)
            act.setCheckable(True)
            act.setData(key)
            if key == "hann":
                act.setChecked(True)
            self.win_group.addAction(act)

        # -- Color Theme submenu --
        cmap_menu = spec_menu.addMenu("Color Theme")
        self.cmap_group = QActionGroup(self)
        for name in ("Magma", "Viridis", "Grayscale"):
            act = cmap_menu.addAction(name)
            act.setCheckable(True)
            act.setData(name.lower())
            if name == "Magma":
                act.setChecked(True)
            self.cmap_group.addAction(act)

        # -- dB Floor submenu --
        floor_menu = spec_menu.addMenu("dB Floor")
        self.floor_group = QActionGroup(self)
        for val in (-120, -100, -80, -60, -50, -40, -30, -20):
            act = floor_menu.addAction(f"{val} dB")
            act.setCheckable(True)
            act.setData(val)
            if val == -80:
                act.setChecked(True)
            self.floor_group.addAction(act)

        # -- dB Ceiling submenu --
        ceil_menu = spec_menu.addMenu("dB Ceiling")
        self.ceil_group = QActionGroup(self)
        for val in (-30, -20, -10, -5, 0):
            act = ceil_menu.addAction(f"{val} dB")
            act.setCheckable(True)
            act.setData(val)
            if val == 0:
                act.setChecked(True)
            self.ceil_group.addAction(act)

        self.spec_settings_btn.setMenu(spec_menu)
        self.spec_settings_btn.setVisible(False)
        toolbar.addWidget(self.spec_settings_btn)

        # Waveform settings dropdown
        self.wf_settings_btn = QToolButton()
        self.wf_settings_btn.setText("Display")
        self.wf_settings_btn.setToolTip(
            "Configure waveform display parameters")
        self.wf_settings_btn.setPopupMode(QToolButton.InstantPopup)
        self.wf_settings_btn.setAutoRaise(True)
        self.wf_settings_btn.setStyleSheet(dropdown_style)
        wf_menu = QMenu(self.wf_settings_btn)

        # -- Anti-Aliased Lines toggle --
        self.wf_aa_action = wf_menu.addAction("Anti-Aliased Lines")
        self.wf_aa_action.setCheckable(True)
        self.wf_aa_action.setChecked(False)
        self.wf_aa_action.toggled.connect(self.waveform.set_wf_antialias)

        # -- Line Thickness submenu --
        thick_menu = wf_menu.addMenu("Line Thickness")
        self.wf_thick_group = QActionGroup(self)
        for label, val in [("Thin (1px)", 1), ("Normal (2px)", 2)]:
            act = thick_menu.addAction(label)
            act.setCheckable(True)
            act.setData(val)
            if val == 1:
                act.setChecked(True)
            self.wf_thick_group.addAction(act)
        self.wf_thick_group.triggered.connect(
            lambda a: self.waveform.set_wf_line_width(int(a.data())))

        self.wf_settings_btn.setMenu(wf_menu)
        toolbar.addWidget(self.wf_settings_btn)

        toolbar.addSpacing(8)

        # Overlay dropdown (analysis-only)
        self.overlay_btn = QToolButton()
        self.overlay_btn.setText("Detector Overlays")
        self.overlay_btn.setToolTip(
            "Select detector overlays to display on the waveform")
        self.overlay_btn.setPopupMode(QToolButton.InstantPopup)
        self.overlay_btn.setAutoRaise(True)
        self.overlay_btn.setStyleSheet(dropdown_style)
        self.overlay_menu = QMenu(self.overlay_btn)
        self.overlay_btn.setMenu(self.overlay_menu)
        toolbar.addWidget(self.overlay_btn)

        toolbar.addSpacing(8)

        # Markers toggle (analysis-only)
        self.markers_toggle = QToolButton()
        self.markers_toggle.setText("Peak / RMS Max")
        self.markers_toggle.setToolTip(
            "Toggle peak and maximum RMS markers on the waveform")
        self.markers_toggle.setCheckable(True)
        self.markers_toggle.setChecked(False)
        self.markers_toggle.setAutoRaise(True)
        self.markers_toggle.setStyleSheet(toggle_style)
        self.markers_toggle.toggled.connect(self.waveform.toggle_markers)
        toolbar.addWidget(self.markers_toggle)

        toolbar.addSpacing(8)

        # RMS L/R toggle (analysis-only)
        self.rms_lr_toggle = QToolButton()
        self.rms_lr_toggle.setText("RMS L/R")
        self.rms_lr_toggle.setToolTip(
            "Toggle per-channel RMS envelope overlay")
        self.rms_lr_toggle.setCheckable(True)
        self.rms_lr_toggle.setAutoRaise(True)
        self.rms_lr_toggle.setStyleSheet(toggle_style)
        self.rms_lr_toggle.toggled.connect(self.waveform.toggle_rms_lr)
        toolbar.addWidget(self.rms_lr_toggle)

        toolbar.addSpacing(4)

        # RMS AVG toggle (analysis-only)
        self.rms_avg_toggle = QToolButton()
        self.rms_avg_toggle.setText("RMS AVG")
        self.rms_avg_toggle.setToolTip(
            "Toggle combined (average) RMS envelope overlay")
        self.rms_avg_toggle.setCheckable(True)
        self.rms_avg_toggle.setAutoRaise(True)
        self.rms_avg_toggle.setStyleSheet(toggle_style)
        self.rms_avg_toggle.toggled.connect(self.waveform.toggle_rms_avg)
        toolbar.addWidget(self.rms_avg_toggle)

        # Hide analysis-only controls when not in analysis mode
        if not self._analysis_mode:
            self.overlay_btn.setVisible(False)
            self.markers_toggle.setVisible(False)
            self.rms_lr_toggle.setVisible(False)
            self.rms_avg_toggle.setVisible(False)

        toolbar.addStretch()

        # Zoom / scale buttons
        style = self.style()

        def _tb(text: str, tooltip: str, icon=None):
            btn = QToolButton()
            if icon is not None:
                btn.setIcon(style.standardIcon(icon))
            else:
                btn.setText(text)
            btn.setToolTip(tooltip)
            btn.setAutoRaise(True)
            toolbar.addWidget(btn)
            return btn

        _tb("Fit", "Zoom to fit entire file", QStyle.SP_BrowserReload
             ).clicked.connect(self.waveform.zoom_fit)
        _tb("+", "Zoom in at cursor"
             ).clicked.connect(self.waveform.zoom_in)
        _tb("\u2212", "Zoom out at cursor"
             ).clicked.connect(self.waveform.zoom_out)
        _tb("", "Scale up (vertical)", QStyle.SP_ArrowUp
             ).clicked.connect(self.waveform.scale_up)
        _tb("", "Scale down (vertical)", QStyle.SP_ArrowDown
             ).clicked.connect(self.waveform.scale_down)

        toolbar_widget = QWidget()
        toolbar_widget.setLayout(toolbar)
        toolbar_widget.setFixedHeight(28)
        toolbar_widget.setStyleSheet(
            "background-color: #2d2d2d; border-bottom: 1px solid #555;")
        return toolbar_widget

    # ------------------------------------------------------------------
    # Transport bar
    # ------------------------------------------------------------------

    def _build_transport(self) -> QWidget:
        transport = QWidget()
        transport.setObjectName("wfTransport")
        transport.setFixedHeight(32)
        transport.setStyleSheet(
            "#wfTransport { background-color: #2d2d2d;"
            " border-top: 1px solid #555; }")
        layout = QHBoxLayout(transport)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        _BTN_H = 24

        self.play_btn = QPushButton("\u25B6 Play")
        self.play_btn.setFixedHeight(_BTN_H)
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self.play_clicked)
        layout.addWidget(self.play_btn)

        self.stop_btn = QPushButton("\u25A0 Stop")
        self.stop_btn.setFixedHeight(_BTN_H)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_clicked)
        layout.addWidget(self.stop_btn)

        # Play-mode combo (matches QComboBox style used in analysis toolbar)
        self._play_mode_combo = QComboBox()
        self._play_mode_combo.setFixedHeight(_BTN_H)
        self._play_mode_combo.setToolTip("Select playback channel routing")
        self._play_mode_combo.setMinimumWidth(160)
        self._play_mode_combo.addItem("As-is", ("as_is", None))
        self._play_mode_combo.addItem("Mono", ("mono", None))
        self._play_mode_combo.currentIndexChanged.connect(
            self._on_play_mode_changed)
        layout.addWidget(self._play_mode_combo)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet(
            "color: #888888; font-family: Consolas, monospace;"
            " font-size: 9pt; padding: 0 8px;")
        layout.addWidget(self.time_label)
        layout.addStretch()

        return transport

    # ------------------------------------------------------------------
    # Play-mode helpers
    # ------------------------------------------------------------------

    def _on_play_mode_changed(self, index: int):
        pass  # nothing extra needed; play_mode reads current data directly

    @property
    def play_mode(self) -> tuple[str, int | None]:
        """Return the current play mode as (mode_str, channel_or_None)."""
        data = self._play_mode_combo.currentData()
        if data is None:
            return ("as_is", None)
        return data

    # Microsoft WAV channel order labels for common layouts
    _CHANNEL_LABELS: dict[int, list[str]] = {
        1: ["M"],
        2: ["L", "R"],
        4: ["L", "R", "BL", "BR"],
        6: ["L", "R", "C", "LFE", "BL", "BR"],
        8: ["L", "R", "C", "LFE", "BL", "BR", "SL", "SR"],
    }

    @staticmethod
    def _channel_name(n_channels: int, ch: int) -> str:
        """Return a display label for channel *ch* in an *n_channels* file."""
        labels = WaveformPanel._CHANNEL_LABELS.get(n_channels)
        if labels and ch < len(labels):
            return f"Ch {ch} ({labels[ch]})"
        return f"Ch {ch}"

    def update_play_mode_channels(self, n_channels: int,
                                   labels: list[str] | None = None):
        """Rebuild per-channel entries in the play-mode combo.

        Parameters
        ----------
        n_channels : int
            Total number of display channels.
        labels : list[str] or None
            Custom per-channel labels (e.g. for multi-track stacked display).
            If *None*, uses standard Microsoft WAV channel labels.
        """
        # Remember current selection
        current = self.play_mode

        self._play_mode_combo.blockSignals(True)

        # Keep first two items (As-is, Mono), remove the rest
        while self._play_mode_combo.count() > 2:
            self._play_mode_combo.removeItem(self._play_mode_combo.count() - 1)

        if n_channels > 1:
            # QComboBox has no real separator — use a disabled item as divider
            self._play_mode_combo.addItem("─────")
            sep_idx = self._play_mode_combo.count() - 1
            model = self._play_mode_combo.model()
            model.item(sep_idx).setEnabled(False)

            for ch in range(n_channels):
                if labels and ch < len(labels):
                    name = f"Ch {ch} ({labels[ch]})"
                else:
                    name = self._channel_name(n_channels, ch)
                self._play_mode_combo.addItem(
                    f"{name} / As-is", ("channel_as_is", ch))
                self._play_mode_combo.addItem(
                    f"{name} / Mono", ("channel_mono", ch))

        # Try to restore previous selection
        restored = False
        for i in range(self._play_mode_combo.count()):
            if self._play_mode_combo.itemData(i) == current:
                self._play_mode_combo.setCurrentIndex(i)
                restored = True
                break
        if not restored:
            self._play_mode_combo.setCurrentIndex(0)

        self._play_mode_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Display mode (internal)
    # ------------------------------------------------------------------

    def _on_display_mode_changed(self, action: QAction):
        """Switch waveform display mode and toggle toolbar controls."""
        is_waveform = action == self.wf_action
        mode = "waveform" if is_waveform else "spectrogram"
        self.display_mode_btn.setText(action.text())
        self.waveform.set_display_mode(mode)

        # Hide waveform-only toolbar controls in spectrogram mode
        self.wf_settings_btn.setVisible(is_waveform)
        if self._analysis_mode:
            self.markers_toggle.setVisible(is_waveform)
            self.rms_lr_toggle.setVisible(is_waveform)
            self.rms_avg_toggle.setVisible(is_waveform)
        # Show spectrogram-only controls
        self.spec_settings_btn.setVisible(not is_waveform)

    # ------------------------------------------------------------------
    # analysis_mode setter
    # ------------------------------------------------------------------

    def set_analysis_mode(self, enabled: bool):
        """Show or hide analysis-specific toolbar controls."""
        self._analysis_mode = enabled
        self.overlay_btn.setVisible(enabled)
        self.markers_toggle.setVisible(enabled)
        self.rms_lr_toggle.setVisible(enabled)
        self.rms_avg_toggle.setVisible(enabled)
