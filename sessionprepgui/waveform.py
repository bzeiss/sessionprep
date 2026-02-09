"""Waveform display widget with per-channel rendering and issue overlays."""

from __future__ import annotations

import numpy as np

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter,
                          QPainterPath, QPen)
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
    _MARGIN_LEFT = 30
    _MARGIN_RIGHT = 30

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
        # RMS overlay
        self._rms_window_samples: int = 0
        self._show_rms: bool = False
        self._rms_envelope: list[list[float]] = []
        self._rms_combined: list[float] = []
        self._rms_cache_key: tuple[int, int, int] = (0, 0, 0)
        # Markers
        self._peak_sample: int = -1
        self._peak_channel: int = -1
        self._peak_db: float = float('-inf')
        self._peak_amplitude: float = 0.0  # signed amplitude on the peak channel
        self._rms_max_sample: int = -1
        self._rms_max_db: float = float('-inf')
        self._rms_max_amplitude: float = 0.0  # linear RMS at max window
        # Mouse guide
        self._mouse_y: int = -1  # -1 = not hovering
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
        # Find peak sample position and amplitude
        if self._channels:
            if self._num_channels == 1:
                self._peak_sample = int(np.argmax(np.abs(self._channels[0])))
                self._peak_channel = 0
            else:
                abs_cols = np.column_stack([np.abs(ch) for ch in self._channels])
                max_per_sample = np.max(abs_cols, axis=1)
                self._peak_sample = int(np.argmax(max_per_sample))
                self._peak_channel = int(
                    np.argmax(abs_cols[self._peak_sample])
                )
            peak_lin = abs(float(self._channels[self._peak_channel][self._peak_sample]))
            self._peak_db = 20.0 * np.log10(peak_lin) if peak_lin > 0 else float('-inf')
            self._peak_amplitude = float(
                self._channels[self._peak_channel][self._peak_sample]
            )
        else:
            self._peak_sample = -1
            self._peak_channel = -1
            self._peak_db = float('-inf')
            self._peak_amplitude = 0.0
        self._rms_max_sample = -1
        self._rms_max_db = float('-inf')
        self._samplerate = samplerate
        self._cursor_sample = 0
        self._view_start = 0
        self._view_end = self._total_samples
        self._vscale = 1.0
        self._peaks_cache = []
        self._cached_view = (0, 0, 0)
        self._issues = []
        self._rms_window_samples = 0
        self._rms_envelope = []
        self._rms_combined = []
        self._rms_cache_key = (0, 0, 0)
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

    def _draw_area(self) -> tuple[int, int]:
        """Return (x0, draw_w) for the waveform drawing area."""
        w = self.width()
        draw_w = max(1, w - self._MARGIN_LEFT - self._MARGIN_RIGHT)
        return self._MARGIN_LEFT, draw_w

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

        x0, draw_w = self._draw_area()
        self._build_peaks(draw_w)
        if self._show_rms:
            self._build_rms_envelope(draw_w)

        nch = self._num_channels
        lane_h = h / nch

        # --- dB scale and grid lines ---
        self._draw_db_scale(painter, x0, draw_w, h, nch, lane_h)

        # --- Draw issue overlays (behind waveform) ---
        for issue in self._issues:
            sev_val = issue.severity.value if hasattr(issue.severity, "value") else str(issue.severity)
            fill = self._SEVERITY_OVERLAY.get(sev_val, QColor(255, 255, 255, 30))
            border = self._SEVERITY_BORDER.get(sev_val, QColor(255, 255, 255, 60))

            ix1 = x0 + self._sample_to_x(issue.sample_start, draw_w)
            ix2 = (x0 + self._sample_to_x(issue.sample_end, draw_w)
                   if issue.sample_end is not None else ix1)
            rx = ix1
            rw = max(ix2 - ix1, 2)  # min 2px wide so point issues are visible

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

            # Clip painting to this channel's lane within drawing area
            lane_top = int(y_off)
            lane_bot = int(y_off + lane_h)
            painter.setClipRect(x0, lane_top, draw_w, lane_bot - lane_top)

            color = QColor(self._CHANNEL_COLORS[ch % len(self._CHANNEL_COLORS)])
            peaks = self._peaks_cache[ch]

            # Build closed envelope path: top edge L→R, bottom edge R→L
            top_path = QPainterPath()
            bot_path = QPainterPath()
            top_path.moveTo(x0, mid_y - peaks[0][1] * scale)
            bot_path.moveTo(x0, mid_y - peaks[0][0] * scale)
            for x in range(1, len(peaks)):
                lo, hi = peaks[x]
                top_path.lineTo(x0 + x, mid_y - hi * scale)
                bot_path.lineTo(x0 + x, mid_y - lo * scale)

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
            painter.drawLine(x0, int(mid_y), x0 + draw_w, int(mid_y))

            # Remove clip before drawing separator
            painter.setClipping(False)

            # Channel separator (except after last channel)
            if ch < nch - 1:
                sep_y = int(y_off + lane_h)
                painter.setPen(QPen(QColor("#555555"), 1))
                painter.drawLine(0, sep_y, w, sep_y)

        # --- RMS overlay (on top of waveform, below cursor) ---
        if self._show_rms and self._rms_envelope:
            ch_pen = QPen(QColor(255, 220, 60, 200), 1.0)     # yellow – per-channel
            comb_pen = QPen(QColor(255, 100, 40, 220), 1.5)   # orange – combined
            for ch in range(nch):
                if ch >= len(self._rms_envelope):
                    break
                y_off = ch * lane_h
                mid_y = y_off + lane_h / 2.0
                scale = (lane_h / 2.0) * 0.85 * self._vscale
                lane_top = int(y_off)
                painter.setClipRect(x0, lane_top, draw_w, int(lane_h))
                painter.setBrush(Qt.NoBrush)

                # Per-channel RMS
                ch_env = self._rms_envelope[ch]
                ch_path = QPainterPath()
                ch_path.moveTo(x0, mid_y - ch_env[0] * scale)
                for x in range(1, len(ch_env)):
                    ch_path.lineTo(x0 + x, mid_y - ch_env[x] * scale)
                painter.setPen(ch_pen)
                painter.drawPath(ch_path)

                # Combined RMS
                if self._rms_combined:
                    comb_path = QPainterPath()
                    comb_path.moveTo(x0, mid_y - self._rms_combined[0] * scale)
                    for x in range(1, len(self._rms_combined)):
                        comb_path.lineTo(x0 + x, mid_y - self._rms_combined[x] * scale)
                    painter.setPen(comb_pen)
                    painter.drawPath(comb_path)

                painter.setClipping(False)

        # --- Peak and RMS max markers ---
        self._draw_markers(painter, x0, draw_w, h, nch, lane_h)

        # Playback cursor (spans all channels)
        if self._total_samples > 0:
            cursor_x = x0 + self._sample_to_x(self._cursor_sample, draw_w)
            if x0 <= cursor_x <= x0 + draw_w:
                painter.setPen(QPen(QColor("#ffffff"), 1))
                painter.drawLine(cursor_x, 0, cursor_x, h)

        # --- Horizontal mouse guide with dBFS readout ---
        if self._mouse_y >= 0 and nch > 0:
            my = self._mouse_y
            mouse_ch = int(my / lane_h) if lane_h > 0 else 0
            mouse_ch = max(0, min(mouse_ch, nch - 1))
            ch_y_off = mouse_ch * lane_h
            ch_mid_y = ch_y_off + lane_h / 2.0
            ch_scale = (lane_h / 2.0) * 0.85 * self._vscale

            # Draw guide line across full width
            guide_color = QColor(200, 200, 200, 60)
            painter.setPen(QPen(guide_color, 1, Qt.DashLine))
            painter.drawLine(0, my, w, my)

            # Compute dBFS from mouse y position
            if ch_scale > 0:
                amp = abs(ch_mid_y - my) / ch_scale
                if amp > 0:
                    db_val = 20.0 * np.log10(amp)
                    db_label = f"{db_val:.1f}"
                else:
                    db_label = "-\u221e"
                # Draw label at top of left scale margin
                painter.setFont(QFont("Consolas", 7))
                label_color = QColor(180, 180, 180, 120)
                painter.setPen(label_color)
                fm = painter.fontMetrics()
                tw = fm.horizontalAdvance(db_label)
                painter.drawText(x0 - 5 - tw, int(ch_y_off) + fm.ascent() + 1,
                                 db_label)
                # Also on the right
                painter.drawText(x0 + draw_w + 5, int(ch_y_off) + fm.ascent() + 1,
                                 db_label)

        painter.end()

    def _draw_db_scale(self, painter, x0, draw_w, h, nch, lane_h):
        """Draw dB measurement scale on left/right margins and grid lines."""
        _DB_TICKS = [0, -3, -6, -12, -18, -24, -36, -48, -60]
        _MIN_TICK_SPACING = 18  # minimum pixels between ticks

        scale_font = QFont("Consolas", 7)
        painter.setFont(scale_font)
        fm = painter.fontMetrics()
        text_h = fm.height()

        grid_color = QColor(COLORS["accent"])
        grid_color.setAlpha(35)
        grid_pen = QPen(grid_color, 1, Qt.DotLine)

        label_color = QColor(COLORS["dim"])
        tick_pen = QPen(label_color, 1)

        for ch in range(nch):
            y_off = ch * lane_h
            mid_y = y_off + lane_h / 2.0
            scale = (lane_h / 2.0) * 0.85 * self._vscale

            lane_top = int(y_off)
            lane_bot = int(y_off + lane_h)
            painter.setClipRect(0, lane_top,
                                x0 + draw_w + self._MARGIN_RIGHT,
                                lane_bot - lane_top)

            visible_ticks: list[tuple[int, float, float]] = []
            used_ys: list[float] = []  # all placed label y-positions
            for db_val in _DB_TICKS:
                amp = 10.0 ** (db_val / 20.0)
                pixel_offset = amp * scale
                if pixel_offset >= lane_h / 2.0:
                    continue  # outside visible lane
                y_top = mid_y - pixel_offset
                y_bot = mid_y + pixel_offset
                # Skip if labels would be too close to lane edges
                if y_top < lane_top + text_h or y_bot > lane_bot - text_h:
                    continue
                # Check that both y_top and y_bot are far enough from
                # every previously placed label position
                too_close = False
                for uy in used_ys:
                    if abs(uy - y_top) < _MIN_TICK_SPACING:
                        too_close = True
                        break
                    if db_val != 0 and abs(uy - y_bot) < _MIN_TICK_SPACING:
                        too_close = True
                        break
                if too_close:
                    continue
                visible_ticks.append((db_val, y_top, y_bot))
                used_ys.append(y_top)
                if db_val != 0:
                    used_ys.append(y_bot)

            for db_val, y_top, y_bot in visible_ticks:
                label = str(db_val)

                # Horizontal grid lines across waveform area
                painter.setPen(grid_pen)
                painter.drawLine(x0, int(y_top), x0 + draw_w, int(y_top))
                if db_val != 0:
                    painter.drawLine(x0, int(y_bot), x0 + draw_w, int(y_bot))

                # Left margin labels (right-aligned)
                painter.setPen(tick_pen)
                text_w = fm.horizontalAdvance(label)
                lx = x0 - 5 - text_w
                painter.drawText(int(lx), int(y_top + text_h / 3), label)
                if db_val != 0:
                    painter.drawText(int(lx), int(y_bot + text_h / 3), label)

                # Right margin labels (left-aligned)
                rx = x0 + draw_w + 5
                painter.drawText(int(rx), int(y_top + text_h / 3), label)
                if db_val != 0:
                    painter.drawText(int(rx), int(y_bot + text_h / 3), label)

                # Small tick marks at edges of waveform area
                painter.drawLine(x0 - 3, int(y_top), x0, int(y_top))
                painter.drawLine(x0 + draw_w, int(y_top),
                                 x0 + draw_w + 3, int(y_top))
                if db_val != 0:
                    painter.drawLine(x0 - 3, int(y_bot), x0, int(y_bot))
                    painter.drawLine(x0 + draw_w, int(y_bot),
                                     x0 + draw_w + 3, int(y_bot))

                # Thin connecting lines spanning full width (behind waveform)
                conn_color = QColor(45, 45, 45)
                painter.setPen(QPen(conn_color, 1))
                painter.drawLine(0, int(y_top),
                                 x0 + draw_w + self._MARGIN_RIGHT, int(y_top))
                if db_val != 0:
                    painter.drawLine(0, int(y_bot),
                                     x0 + draw_w + self._MARGIN_RIGHT, int(y_bot))

            painter.setClipping(False)

    def _draw_markers(self, painter, x0, draw_w, h, nch, lane_h):
        """Draw peak and max RMS marker vertical lines."""
        marker_font = QFont("Consolas", 7, QFont.Bold)
        _CROSS_HALF = 6  # half-width of horizontal crosshair

        # Peak marker (magenta, solid)
        if self._peak_sample >= 0:
            px = x0 + self._sample_to_x(self._peak_sample, draw_w)
            if x0 <= px <= x0 + draw_w:
                peak_color = QColor(255, 80, 180, 200)
                painter.setPen(QPen(peak_color, 1))
                painter.drawLine(px, 0, px, h)
                painter.setFont(marker_font)
                painter.setPen(peak_color)
                painter.drawText(px + 3, 12, "P")

                # Horizontal crosshair at peak amplitude on the peak channel
                if 0 <= self._peak_channel < nch:
                    painter.setPen(QPen(peak_color, 1))
                    ch = self._peak_channel
                    amp = self._peak_amplitude
                    y_off = ch * lane_h
                    mid_y = y_off + lane_h / 2.0
                    scale = (lane_h / 2.0) * 0.85 * self._vscale
                    cy = int(mid_y - amp * scale)
                    painter.drawLine(px - _CROSS_HALF, cy,
                                     px + _CROSS_HALF, cy)

        # Max RMS marker (cyan, solid)
        if self._rms_max_sample >= 0:
            rx = x0 + self._sample_to_x(self._rms_max_sample, draw_w)
            if x0 <= rx <= x0 + draw_w:
                rms_color = QColor(100, 220, 255, 200)
                painter.setPen(QPen(rms_color, 1))
                painter.drawLine(rx, 0, rx, h)
                painter.setFont(marker_font)
                painter.setPen(rms_color)
                painter.drawText(rx + 3, 24, "R")

                # Horizontal crosshair at RMS amplitude (positive side only)
                amp = self._rms_max_amplitude
                if amp > 0:
                    painter.setPen(QPen(rms_color, 1))
                    for ch in range(nch):
                        y_off = ch * lane_h
                        mid_y = y_off + lane_h / 2.0
                        scale = (lane_h / 2.0) * 0.85 * self._vscale
                        cy = int(mid_y - amp * scale)
                        painter.drawLine(rx - _CROSS_HALF, cy,
                                         rx + _CROSS_HALF, cy)

    def resizeEvent(self, event):
        self._peaks_cache = []
        self._cached_view = (0, 0, 0)
        self._rms_envelope = []
        self._rms_combined = []
        self._rms_cache_key = (0, 0, 0)
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        if self._total_samples > 0 and event.button() == Qt.LeftButton:
            x0, draw_w = self._draw_area()
            sample = self._x_to_sample(event.position().x() - x0, draw_w)
            self._cursor_sample = sample
            self.update()
            self.position_clicked.emit(sample)

    def mouseMoveEvent(self, event):
        """Show tooltip when hovering over an issue region or marker."""
        self._mouse_y = int(event.position().y())
        self.update()  # repaint for horizontal guide

        if self._total_samples <= 0:
            QToolTip.hideText()
            return

        x0, draw_w = self._draw_area()
        h = self.height()
        mx = event.position().x()
        my = event.position().y()
        nch = self._num_channels
        lane_h = h / nch if nch > 0 else h

        # Convert mouse x to sample position
        sample = self._x_to_sample(mx - x0, draw_w)
        # Determine which channel lane the mouse is in
        mouse_ch = int(my / lane_h) if lane_h > 0 else 0
        mouse_ch = max(0, min(mouse_ch, nch - 1))

        # Hit tolerance: at least 5 pixels worth of samples on each side
        view_len = self._view_end - self._view_start
        samples_per_px = view_len / max(draw_w, 1)
        tolerance = int(samples_per_px * 5)

        tips: list[str] = []

        # Marker tooltips (check pixel proximity)
        _MARKER_PX_TOL = 6
        if self._peak_sample >= 0:
            peak_px = x0 + self._sample_to_x(self._peak_sample, draw_w)
            if abs(mx - peak_px) <= _MARKER_PX_TOL:
                tips.append(f"Peak: {self._peak_db:.1f} dBFS")
        if self._rms_max_sample >= 0:
            rms_px = x0 + self._sample_to_x(self._rms_max_sample, draw_w)
            if abs(mx - rms_px) <= _MARKER_PX_TOL:
                tips.append(f"Max RMS: {self._rms_max_db:.1f} dBFS")

        # Issue tooltips
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

    def leaveEvent(self, event):
        self._mouse_y = -1
        self.update()
        super().leaveEvent(event)

    # ── Zoom / vertical-scale public API ──────────────────────────────────

    def _invalidate_peaks(self):
        self._peaks_cache = []
        self._cached_view = (0, 0, 0)
        self._rms_envelope = []
        self._rms_combined = []
        self._rms_cache_key = (0, 0, 0)

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

    # ── RMS overlay ───────────────────────────────────────────────────────

    def set_rms_data(self, window_samples: int):
        """Set the RMS window size.  Per-channel envelopes are computed
        on demand from the already-loaded channel data."""
        self._rms_window_samples = max(window_samples, 0)
        self._rms_envelope = []
        self._rms_cache_key = (0, 0, 0)
        self._compute_rms_max_sample()
        self.update()

    def toggle_rms(self, on: bool):
        """Enable or disable the RMS overlay."""
        self._show_rms = on
        self.update()

    def _compute_rms_max_sample(self):
        """Find the sample position of the maximum momentary RMS window."""
        win = self._rms_window_samples
        if not self._channels or win <= 0:
            self._rms_max_sample = -1
            return
        ch_wms: list[np.ndarray] = []
        for ch_data in self._channels:
            n = len(ch_data)
            if n <= win:
                ch_wms.append(np.zeros(1, dtype=np.float64))
                continue
            sq = ch_data.astype(np.float64) ** 2
            cs = np.empty(n + 1, dtype=np.float64)
            cs[0] = 0.0
            np.cumsum(sq, out=cs[1:])
            ch_wms.append((cs[win:] - cs[:n - win + 1]) / win)
        min_len = min(len(wm) for wm in ch_wms)
        if min_len == 0:
            self._rms_max_sample = -1
            return
        combined = np.mean(
            np.column_stack([wm[:min_len] for wm in ch_wms]), axis=1
        )
        max_idx = int(np.argmax(combined))
        self._rms_max_sample = max_idx + win // 2
        max_rms_lin = float(np.sqrt(combined[max_idx]))
        self._rms_max_db = 20.0 * np.log10(max_rms_lin) if max_rms_lin > 0 else float('-inf')
        self._rms_max_amplitude = max_rms_lin

    def _build_rms_envelope(self, width: int):
        """Compute per-channel AND combined RMS envelopes for *width* pixels.

        Uses a sliding-window cumsum over each channel independently,
        then picks the max-RMS within each pixel's sample range.

        Results:
            ``_rms_envelope``  – list (per channel) of list[float]
            ``_rms_combined``  – list[float] (avg across channels)
        """
        win = self._rms_window_samples
        if not self._channels or width <= 0 or win <= 0:
            self._rms_envelope = []
            self._rms_combined = []
            return
        cache_key = (width, self._view_start, self._view_end)
        if self._rms_cache_key == cache_key and self._rms_envelope:
            return

        vs, ve = self._view_start, self._view_end
        view_len = ve - vs
        if view_len <= 0:
            self._rms_envelope = []
            self._rms_combined = []
            return

        half_win = win // 2
        # Per-channel window-means arrays (full resolution)
        ch_wms: list[np.ndarray] = []
        for ch_data in self._channels:
            n = len(ch_data)
            if n <= win:
                ch_wms.append(np.zeros(1, dtype=np.float64))
                continue
            sq = ch_data.astype(np.float64) ** 2
            cs = np.empty(n + 1, dtype=np.float64)
            cs[0] = 0.0
            np.cumsum(sq, out=cs[1:])
            ch_wms.append((cs[win:] - cs[: n - win + 1]) / win)

        # Combined (average across channels)
        min_len = min(len(wm) for wm in ch_wms)
        combined_wm = np.mean(
            np.column_stack([wm[:min_len] for wm in ch_wms]), axis=1
        )

        def _downsample(wm: np.ndarray) -> list[float]:
            n_means = len(wm)
            env: list[float] = []
            for i in range(width):
                s_start = vs + i * view_len // width
                s_end = vs + (i + 1) * view_len // width
                idx_start = max(0, s_start - half_win)
                idx_end = min(n_means, max(s_end - half_win, idx_start + 1))
                if idx_start >= idx_end:
                    env.append(0.0)
                else:
                    env.append(float(np.sqrt(np.max(wm[idx_start:idx_end]))))
            return env

        self._rms_envelope = [_downsample(wm) for wm in ch_wms]
        self._rms_combined = _downsample(combined_wm)
        self._rms_cache_key = cache_key
