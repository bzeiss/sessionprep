"""Waveform display widget with per-channel rendering and issue overlays."""

from __future__ import annotations

import numpy as np

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (QColor, QLinearGradient, QPainter, QPainterPath,
                          QPen)
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
        self._cached_view: tuple[int, int, int] = (0, 0, 0)  # (width, view_start, view_end)
        self._issues: list = []  # list of IssueLocation objects
        self._view_start: int = 0
        self._view_end: int = 0
        self._vscale: float = 1.0
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
        self._view_start = 0
        self._view_end = self._total_samples
        self._vscale = 1.0
        self._peaks_cache = []
        self._cached_view = (0, 0, 0)
        self._issues = []
        self.update()

    def set_issues(self, issues: list):
        """Set the list of IssueLocation objects to overlay on the waveform."""
        self._issues = list(issues)
        self.update()

    def set_cursor(self, sample_index: int):
        """Update the playback cursor position.

        If the cursor moves past the right edge of the current view,
        the view pages forward so the cursor appears at the left edge.
        """
        self._cursor_sample = max(0, min(sample_index, self._total_samples))
        # Auto-page when cursor exceeds the visible range
        if self._cursor_sample >= self._view_end and self._view_end < self._total_samples:
            view_len = self._view_end - self._view_start
            self._view_start = self._cursor_sample
            self._view_end = min(self._cursor_sample + view_len, self._total_samples)
            self._invalidate_peaks()
        self.update()

    def _build_peaks(self, width: int):
        """Downsample audio to peak envelope for the given pixel width, per channel."""
        if not self._channels or width <= 0:
            self._peaks_cache = []
            return
        cache_key = (width, self._view_start, self._view_end)
        if self._cached_view == cache_key and self._peaks_cache:
            return

        vs, ve = self._view_start, self._view_end
        view_len = ve - vs
        if view_len <= 0:
            self._peaks_cache = []
            return

        self._peaks_cache = []
        for ch_data in self._channels:
            view_data = ch_data[vs:ve]
            n = len(view_data)
            peaks: list[tuple[float, float]] = []
            for i in range(width):
                start = i * n // width
                end = min((i + 1) * n // width, n)
                if start >= end:
                    peaks.append((0.0, 0.0))
                else:
                    chunk = view_data[start:end]
                    peaks.append((float(chunk.min()), float(chunk.max())))
            self._peaks_cache.append(peaks)
        self._cached_view = cache_key

    def _sample_to_x(self, sample: int, w: int) -> int:
        view_len = self._view_end - self._view_start
        if view_len <= 0:
            return 0
        return int((sample - self._view_start) / view_len * w)

    def _x_to_sample(self, x: float, w: int) -> int:
        """Convert a pixel x coordinate to a sample index within the view."""
        view_len = self._view_end - self._view_start
        if w <= 0 or view_len <= 0:
            return 0
        sample = self._view_start + int(x / w * view_len)
        return max(0, min(sample, self._total_samples - 1))

    def paintEvent(self, event):
        w = self.width()
        h = self.height()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

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
            scale = (lane_h / 2.0) * 0.85 * self._vscale

            # Clip painting to this channel's lane
            lane_top = int(y_off)
            lane_bot = int(y_off + lane_h)
            painter.setClipRect(0, lane_top, w, lane_bot - lane_top)

            color = QColor(self._CHANNEL_COLORS[ch % len(self._CHANNEL_COLORS)])
            peaks = self._peaks_cache[ch]

            # Build closed envelope path: top edge L→R, bottom edge R→L
            top_path = QPainterPath()
            bot_path = QPainterPath()
            top_path.moveTo(0, mid_y - peaks[0][1] * scale)
            bot_path.moveTo(0, mid_y - peaks[0][0] * scale)
            for x in range(1, len(peaks)):
                lo, hi = peaks[x]
                top_path.lineTo(x, mid_y - hi * scale)
                bot_path.lineTo(x, mid_y - lo * scale)

            # Combine into a single closed shape
            envelope = QPainterPath(top_path)
            rev = bot_path.toReversed()
            envelope.lineTo(rev.elementAt(0).x, rev.elementAt(0).y)
            envelope.connectPath(rev)
            envelope.closeSubpath()

            # Gradient fill: opaque at center line, transparent at peaks
            grad = QLinearGradient(0, y_off, 0, y_off + lane_h)
            color_edge = QColor(color)
            color_edge.setAlpha(30)
            color_mid = QColor(color)
            color_mid.setAlpha(140)
            grad.setColorAt(0.0, color_edge)
            grad.setColorAt(0.5, color_mid)
            grad.setColorAt(1.0, color_edge)

            painter.setPen(Qt.NoPen)
            painter.setBrush(grad)
            painter.drawPath(envelope)

            # Thin outline on top and bottom edges for definition
            outline = QColor(color)
            outline.setAlpha(200)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(outline, 1))
            painter.drawPath(top_path)
            painter.drawPath(bot_path)

            # Center line
            center_color = QColor(COLORS["accent"])
            center_color.setAlpha(80)
            painter.setPen(QPen(center_color, 1, Qt.DotLine))
            painter.drawLine(0, int(mid_y), w, int(mid_y))

            # Remove clip before drawing separator
            painter.setClipping(False)

            # Channel separator (except after last channel)
            if ch < nch - 1:
                sep_y = int(y_off + lane_h)
                painter.setPen(QPen(QColor("#555555"), 1))
                painter.drawLine(0, sep_y, w, sep_y)

        # Playback cursor (spans all channels)
        if self._total_samples > 0:
            cursor_x = self._sample_to_x(self._cursor_sample, w)
            if 0 <= cursor_x <= w:
                painter.setPen(QPen(QColor("#ffffff"), 1))
                painter.drawLine(cursor_x, 0, cursor_x, h)

        painter.end()

    def resizeEvent(self, event):
        self._peaks_cache = []
        self._cached_view = (0, 0, 0)
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        if self._total_samples > 0 and event.button() == Qt.LeftButton:
            sample = self._x_to_sample(event.position().x(), self.width())
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
        sample = self._x_to_sample(mx, w)
        # Determine which channel lane the mouse is in
        mouse_ch = int(my / lane_h) if lane_h > 0 else 0
        mouse_ch = max(0, min(mouse_ch, nch - 1))

        # Hit tolerance: at least 5 pixels worth of samples on each side
        view_len = self._view_end - self._view_start
        samples_per_px = view_len / max(w, 1)
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

    # ── Zoom / vertical-scale public API ──────────────────────────────────

    def _invalidate_peaks(self):
        self._peaks_cache = []
        self._cached_view = (0, 0, 0)

    def zoom_fit(self):
        """Reset horizontal zoom and vertical scale to show the entire file."""
        self._view_start = 0
        self._view_end = self._total_samples
        self._vscale = 1.0
        self._invalidate_peaks()
        self.update()

    def zoom_in(self):
        """Zoom in 2× centered on the cursor."""
        view_len = self._view_end - self._view_start
        if view_len <= 100:
            return
        center = max(self._view_start, min(self._cursor_sample, self._view_end))
        new_len = max(view_len // 2, 100)
        new_start = center - new_len // 2
        new_end = new_start + new_len
        if new_start < 0:
            new_start = 0
            new_end = new_len
        if new_end > self._total_samples:
            new_end = self._total_samples
            new_start = max(0, new_end - new_len)
        self._view_start = new_start
        self._view_end = new_end
        self._invalidate_peaks()
        self.update()

    def zoom_out(self):
        """Zoom out 2× centered on the cursor."""
        view_len = self._view_end - self._view_start
        if view_len >= self._total_samples:
            return
        center = max(self._view_start, min(self._cursor_sample, self._view_end))
        new_len = min(view_len * 2, self._total_samples)
        new_start = center - new_len // 2
        new_end = new_start + new_len
        if new_start < 0:
            new_start = 0
            new_end = min(new_len, self._total_samples)
        if new_end > self._total_samples:
            new_end = self._total_samples
            new_start = max(0, new_end - new_len)
        self._view_start = new_start
        self._view_end = new_end
        self._invalidate_peaks()
        self.update()

    def scale_up(self):
        """Increase vertical amplitude scale."""
        self._vscale = min(self._vscale * 1.5, 20.0)
        self.update()

    def scale_down(self):
        """Decrease vertical amplitude scale."""
        self._vscale = max(self._vscale / 1.5, 0.1)
        self.update()
