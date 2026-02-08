"""Waveform display widget with per-channel rendering and issue overlays."""

from __future__ import annotations

import numpy as np

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from .theme import COLORS


class WaveformWidget(QWidget):
    """Draws per-channel audio waveforms with issue overlays and playback cursor."""

    position_clicked = Signal(int)  # sample index

    _CHANNEL_COLORS = [
        "#44aa44", "#44aaaa", "#aa44aa", "#aaaa44",
        "#4488cc", "#cc8844", "#88cc44", "#cc4488",
    ]
    _SEVERITY_OVERLAY = {
        "problem": QColor(255, 68, 68, 55),
        "attention": QColor(255, 170, 0, 45),
        "information": QColor(68, 153, 255, 40),
        "info": QColor(68, 153, 255, 40),
    }
    _SEVERITY_BORDER = {
        "problem": QColor(255, 68, 68, 140),
        "attention": QColor(255, 170, 0, 120),
        "information": QColor(68, 153, 255, 100),
        "info": QColor(68, 153, 255, 100),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._channels: list[np.ndarray] = []  # one 1-D array per channel
        self._num_channels: int = 0
        self._samplerate: int = 44100
        self._total_samples: int = 0
        self._cursor_sample: int = 0
        self._peaks_cache: list[list[tuple[float, float]]] = []  # per channel
        self._cached_width: int = 0
        self._issues: list = []  # list of IssueLocation objects
        self.setMinimumHeight(80)
        self.setMouseTracking(True)

    def set_audio(self, audio_data: np.ndarray | None, samplerate: int):
        """Load audio data (numpy array, shape (samples,) or (samples, channels))."""
        if audio_data is None or audio_data.size == 0:
            self._channels = []
            self._num_channels = 0
            self._total_samples = 0
        else:
            if audio_data.ndim == 1:
                self._channels = [audio_data.astype(np.float32)]
            else:
                self._channels = [
                    audio_data[:, ch].astype(np.float32)
                    for ch in range(audio_data.shape[1])
                ]
            self._num_channels = len(self._channels)
            self._total_samples = len(self._channels[0])
        self._samplerate = samplerate
        self._cursor_sample = 0
        self._peaks_cache = []
        self._cached_width = 0
        self._issues = []
        self.update()

    def set_issues(self, issues: list):
        """Set the list of IssueLocation objects to overlay on the waveform."""
        self._issues = list(issues)
        self.update()

    def set_cursor(self, sample_index: int):
        """Update the playback cursor position."""
        self._cursor_sample = max(0, min(sample_index, self._total_samples))
        self.update()

    def _build_peaks(self, width: int):
        """Downsample audio to peak envelope for the given pixel width, per channel."""
        if not self._channels or width <= 0:
            self._peaks_cache = []
            return
        if self._cached_width == width and self._peaks_cache:
            return

        self._peaks_cache = []
        for ch_data in self._channels:
            n = len(ch_data)
            peaks: list[tuple[float, float]] = []
            for i in range(width):
                start = i * n // width
                end = min((i + 1) * n // width, n)
                if start >= end:
                    peaks.append((0.0, 0.0))
                else:
                    chunk = ch_data[start:end]
                    peaks.append((float(chunk.min()), float(chunk.max())))
            self._peaks_cache.append(peaks)
        self._cached_width = width

    def _sample_to_x(self, sample: int, w: int) -> int:
        if self._total_samples <= 0:
            return 0
        return int(sample / self._total_samples * w)

    def paintEvent(self, event):
        w = self.width()
        h = self.height()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        # Background
        painter.fillRect(0, 0, w, h, QColor(COLORS["bg"]))

        if not self._channels or self._total_samples == 0:
            painter.setPen(QPen(QColor(COLORS["dim"])))
            painter.drawText(self.rect(), Qt.AlignCenter, "No waveform")
            painter.end()
            return

        self._build_peaks(w)

        nch = self._num_channels
        lane_h = h / nch

        # --- Draw issue overlays (behind waveform) ---
        for issue in self._issues:
            sev_val = issue.severity.value if hasattr(issue.severity, "value") else str(issue.severity)
            fill = self._SEVERITY_OVERLAY.get(sev_val, QColor(255, 255, 255, 30))
            border = self._SEVERITY_BORDER.get(sev_val, QColor(255, 255, 255, 60))

            x1 = self._sample_to_x(issue.sample_start, w)
            x2 = self._sample_to_x(issue.sample_end, w) if issue.sample_end is not None else x1
            rx = x1
            rw = max(x2 - x1, 2)  # min 2px wide so point issues are visible

            if issue.channel is None:
                # Spans all channels
                ry = 0
                rh = h
            else:
                ch = issue.channel
                if ch < nch:
                    ry = int(ch * lane_h)
                    rh = int(lane_h)
                else:
                    continue  # channel index out of range

            painter.fillRect(rx, ry, rw, rh, fill)
            painter.setPen(QPen(border, 1))
            painter.drawRect(rx, ry, rw, rh)

        # --- Draw waveforms ---
        for ch in range(nch):
            y_off = ch * lane_h
            mid_y = y_off + lane_h / 2.0
            scale = (lane_h / 2.0) * 0.85

            color = self._CHANNEL_COLORS[ch % len(self._CHANNEL_COLORS)]
            painter.setPen(QPen(QColor(color), 1))
            for x, (lo, hi) in enumerate(self._peaks_cache[ch]):
                y_top = int(mid_y - hi * scale)
                y_bot = int(mid_y - lo * scale)
                if y_top == y_bot:
                    y_bot += 1
                painter.drawLine(x, y_top, x, y_bot)

            # Center line
            painter.setPen(QPen(QColor(COLORS["accent"]), 1))
            painter.drawLine(0, int(mid_y), w, int(mid_y))

            # Channel separator (except after last channel)
            if ch < nch - 1:
                sep_y = int(y_off + lane_h)
                painter.setPen(QPen(QColor("#555555"), 1))
                painter.drawLine(0, sep_y, w, sep_y)

        # Playback cursor (spans all channels)
        if self._total_samples > 0:
            cursor_x = int(self._cursor_sample / self._total_samples * w)
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.drawLine(cursor_x, 0, cursor_x, h)

        painter.end()

    def resizeEvent(self, event):
        self._peaks_cache = []
        self._cached_width = 0
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        if self._total_samples > 0 and event.button() == Qt.LeftButton:
            frac = event.position().x() / max(self.width(), 1)
            sample = int(frac * self._total_samples)
            sample = max(0, min(sample, self._total_samples - 1))
            self._cursor_sample = sample
            self.update()
            self.position_clicked.emit(sample)

    def mouseMoveEvent(self, event):
        """Show tooltip when hovering over an issue region."""
        if not self._issues or self._total_samples <= 0:
            QToolTip.hideText()
            return

        w = self.width()
        h = self.height()
        mx = event.position().x()
        my = event.position().y()
        nch = self._num_channels
        lane_h = h / nch if nch > 0 else h

        # Convert mouse x to sample position
        sample = int(mx / max(w, 1) * self._total_samples)
        # Determine which channel lane the mouse is in
        mouse_ch = int(my / lane_h) if lane_h > 0 else 0
        mouse_ch = max(0, min(mouse_ch, nch - 1))

        # Hit tolerance: at least 5 pixels worth of samples on each side
        samples_per_px = self._total_samples / max(w, 1)
        tolerance = int(samples_per_px * 5)

        tips = []
        for issue in self._issues:
            s_start = issue.sample_start
            s_end = issue.sample_end if issue.sample_end is not None else s_start
            # Expand narrow regions by tolerance for easier hit-testing
            hit_start = s_start - tolerance
            hit_end = s_end + tolerance
            if sample < hit_start or sample > hit_end:
                continue
            # Check channel match: None = all channels, or specific channel
            if issue.channel is not None and issue.channel != mouse_ch:
                continue
            tips.append(issue.description)

        if tips:
            QToolTip.showText(event.globalPosition().toPoint(), "\n".join(tips), self)
        else:
            QToolTip.hideText()
