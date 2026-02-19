"""Waveform display widget with per-channel rendering and issue overlays."""

from __future__ import annotations

import numpy as np

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from ..theme import COLORS
from .compute import WaveformLoadWorker, _mel_to_hz  # noqa: F401 (re-export)
from .overlay import draw_issue_overlays, draw_time_scale
from .renderer import WaveformRenderCtx, WaveformRenderer
from .spectrogram import SpecRenderCtx, SpectrogramRenderer


class WaveformWidget(QWidget):
    """Draws per-channel audio waveforms with issue overlays and playback cursor."""

    position_clicked = Signal(int)  # sample index

    _MARGIN_LEFT = 38
    _MARGIN_RIGHT = 38
    _MARGIN_BOTTOM = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        # Renderer objects (composition)
        self._wf_renderer = WaveformRenderer()
        self._spec_renderer = SpectrogramRenderer()
        # View / audio state
        self._channels: list[np.ndarray] = []
        self._num_channels: int = 0
        self._samplerate: int = 44100
        self._total_samples: int = 0
        self._cursor_sample: int = 0
        self._cursor_y_value: float | None = None
        self._cursor_y_channel: int = 0
        self._issues: list = []
        self._view_start: int = 0
        self._view_end: int = 0
        self._vscale: float = 1.0
        # RMS / overlay / marker toggles
        self._rms_window_samples: int = 0
        self._show_rms_lr: bool = False
        self._show_rms_avg: bool = False
        self._enabled_overlays: set[str] = set()
        self._show_markers: bool = False
        # Loading / display state
        self._loading: bool = False
        self._display_mode: str = "waveform"
        self._wf_antialias: bool = False
        self._wf_line_width: int = 1
        # Mouse crosshair
        self._mouse_x: int = -1
        self._mouse_y: int = -1
        # Scroll inversion
        self._invert_h: bool = False
        self._invert_v: bool = False
        # Scroll throttle
        self._scroll_pending: bool = False
        self._scroll_timer: QTimer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(8)
        self._scroll_timer.timeout.connect(self._flush_scroll)
        self.setMinimumHeight(80)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # ── Data management ────────────────────────────────────────────────────

    def set_audio(self, audio_data: np.ndarray | None, samplerate: int):
        """Load raw audio data.  Peak finding is deferred to first paint."""
        if audio_data is None or audio_data.size == 0:
            self._channels = []
            self._num_channels = 0
            self._total_samples = 0
        else:
            if audio_data.ndim == 1:
                self._channels = [np.ascontiguousarray(audio_data)]
            else:
                self._channels = [
                    np.ascontiguousarray(audio_data[:, ch])
                    for ch in range(audio_data.shape[1])
                ]
            self._num_channels = len(self._channels)
            self._total_samples = len(self._channels[0])
        self._samplerate = samplerate
        self._cursor_sample = 0
        self._cursor_y_value = None
        self._view_start = 0
        self._view_end = self._total_samples
        self._vscale = 1.0
        self._issues = []
        self._rms_window_samples = 0
        self._wf_renderer.set_track_data(
            self._channels,
            peak_dirty=bool(self._channels),
        )
        self._spec_renderer.reset(samplerate)
        self.update()

    def set_loading(self, loading: bool):
        """Show or hide a 'Loading waveform…' placeholder."""
        self._loading = loading
        if loading:
            self._channels = []
            self._num_channels = 0
            self._total_samples = 0
            self._wf_renderer.reset()
        self.update()

    def set_precomputed(self, result: dict):
        """Apply pre-computed waveform data from a WaveformLoadWorker."""
        self._channels = result["channels"]
        self._num_channels = len(self._channels)
        self._total_samples = result["total_samples"]
        self._samplerate = result["samplerate"]
        self._cursor_sample = 0
        self._cursor_y_value = None
        self._view_start = 0
        self._view_end = self._total_samples
        self._vscale = 1.0
        self._rms_window_samples = result["rms_window_samples"]
        self._wf_renderer.set_track_data(
            self._channels,
            peak_sample=result["peak_sample"],
            peak_channel=result["peak_channel"],
            peak_db=result["peak_db"],
            peak_amplitude=result["peak_amplitude"],
            rms_cumsums=result.get("rms_cumsums", []),
            rms_window=result["rms_window_samples"],
            rms_max_sample=result["rms_max_sample"],
            rms_max_db=result["rms_max_db"],
            rms_max_amplitude=result["rms_max_amplitude"],
        )
        self._spec_renderer.reset(result["samplerate"])
        self._spec_renderer.set_spec_data(result.get("spec_db"))
        self._loading = False
        self.update()

    def set_issues(self, issues: list):
        """Set the list of IssueLocation objects to overlay on the waveform."""
        self._issues = list(issues)
        self.update()

    def set_cursor(self, sample_index: int):
        """Update the playback cursor position, auto-paging if needed."""
        self._cursor_sample = max(0, min(sample_index, self._total_samples))
        if self._cursor_sample >= self._view_end and self._view_end < self._total_samples:
            view_len = self._view_end - self._view_start
            self._view_start = self._cursor_sample
            self._view_end = min(self._cursor_sample + view_len, self._total_samples)
            self._wf_renderer.invalidate()
        self.update()

    # ── Coordinate helpers ─────────────────────────────────────────────────

    def _draw_area(self) -> tuple[int, int]:
        """Return (x0, draw_w) for the waveform drawing area."""
        draw_w = max(1, self.width() - self._MARGIN_LEFT - self._MARGIN_RIGHT)
        return self._MARGIN_LEFT, draw_w

    def _sample_to_x(self, sample: int, w: int) -> int:
        view_len = self._view_end - self._view_start
        if view_len <= 0:
            return 0
        return int((sample - self._view_start) / view_len * w)

    def _x_to_sample(self, x: float, w: int) -> int:
        view_len = self._view_end - self._view_start
        if w <= 0 or view_len <= 0:
            return 0
        return max(0, min(self._view_start + int(x / w * view_len),
                          self._total_samples - 1))

    def _make_wf_ctx(self, x0: int, draw_w: int, draw_h: int) -> WaveformRenderCtx:
        return WaveformRenderCtx(
            x0=x0, draw_w=draw_w, draw_h=draw_h,
            margin_right=self._MARGIN_RIGHT,
            view_start=self._view_start, view_end=self._view_end,
            vscale=self._vscale,
            channels=self._channels, num_channels=self._num_channels,
            show_rms_lr=self._show_rms_lr, show_rms_avg=self._show_rms_avg,
            show_markers=self._show_markers,
            wf_antialias=self._wf_antialias, wf_line_width=self._wf_line_width,
        )

    def _make_spec_ctx(self, x0: int, draw_w: int, draw_h: int) -> SpecRenderCtx:
        return SpecRenderCtx(
            x0=x0, draw_w=draw_w, draw_h=draw_h,
            view_start=self._view_start, view_end=self._view_end,
            total_samples=self._total_samples, samplerate=self._samplerate,
        )

    # ── paintEvent ─────────────────────────────────────────────────────────

    def paintEvent(self, event):
        w = self.width()
        h = self.height()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        painter.fillRect(0, 0, w, h, QColor(COLORS["bg"]))

        if self._loading:
            painter.setPen(QPen(QColor(COLORS["dim"])))
            painter.drawText(self.rect(), Qt.AlignCenter, "Loading waveform\u2026")
            painter.end()
            return

        if not self._channels or self._total_samples == 0:
            painter.setPen(QPen(QColor(COLORS["dim"])))
            painter.drawText(self.rect(), Qt.AlignCenter, "No waveform")
            painter.end()
            return

        x0, draw_w = self._draw_area()
        draw_h = h - self._MARGIN_BOTTOM

        if self._display_mode == "spectrogram":
            self._spec_renderer.paint(painter, self._make_spec_ctx(x0, draw_w, draw_h))
        else:
            self._wf_renderer.paint(painter, self._make_wf_ctx(x0, draw_w, draw_h))

        draw_issue_overlays(
            painter, x0, draw_w, draw_h,
            self._view_start, self._view_end, self._total_samples,
            self._issues, self._enabled_overlays,
            self._display_mode, self._num_channels,
            self._spec_renderer.mel_view_min, self._spec_renderer.mel_view_max,
        )
        draw_time_scale(painter, x0, draw_w, draw_h,
                        self._view_start, self._view_end, self._samplerate)

        # Playback cursor
        if self._total_samples > 0:
            cursor_x = x0 + self._sample_to_x(self._cursor_sample, draw_w)
            if x0 <= cursor_x <= x0 + draw_w:
                painter.setPen(QPen(QColor("#ffffff"), 1))
                painter.drawLine(cursor_x, 0, cursor_x, int(draw_h))

                if self._cursor_y_value is not None:
                    cursor_y = -1
                    cursor_label = ""
                    if self._display_mode == "spectrogram":
                        mel_min = self._spec_renderer.mel_view_min
                        mel_max = self._spec_renderer.mel_view_max
                        mel_range = mel_max - mel_min
                        if mel_range > 0 and draw_h > 0:
                            frac = (self._cursor_y_value - mel_min) / mel_range
                            cursor_y = int(draw_h * (1.0 - frac))
                            freq = _mel_to_hz(self._cursor_y_value)
                            cursor_label = (f"{freq / 1000:.1f} kHz" if freq >= 1000
                                            else f"{freq:.0f} Hz")
                    else:
                        nch = self._num_channels
                        if nch > 0 and draw_h > 0:
                            lane_h = draw_h / nch
                            ch = min(self._cursor_y_channel, nch - 1)
                            mid_y = ch * lane_h + lane_h / 2.0
                            scale = (lane_h / 2.0) * 0.85 * self._vscale
                            cursor_y = int(mid_y - self._cursor_y_value * scale)
                            amp = abs(self._cursor_y_value)
                            cursor_label = (f"{20.0 * np.log10(amp):.1f} dBFS"
                                            if amp > 0 else "-\u221e dBFS")
                    if 0 <= cursor_y <= int(draw_h):
                        painter.setPen(QPen(QColor(255, 255, 255, 80), 1, Qt.DotLine))
                        painter.drawLine(x0, cursor_y, x0 + draw_w, cursor_y)
                        if cursor_label:
                            painter.setFont(QFont("Consolas", 7))
                            painter.setPen(QColor(255, 255, 255, 180))
                            cfm = painter.fontMetrics()
                            clw = cfm.horizontalAdvance(cursor_label)
                            lx = cursor_x + 6
                            ly = cursor_y - 4
                            if lx + clw > x0 + draw_w:
                                lx = cursor_x - 6 - clw
                            if ly - cfm.ascent() < 0:
                                ly = cursor_y + cfm.ascent() + 4
                            painter.drawText(int(lx), int(ly), cursor_label)

        # Crosshair mouse guide
        if self._mouse_y >= 0:
            mx = self._mouse_x
            my = self._mouse_y
            guide_color = QColor(200, 200, 200, 60)
            painter.setPen(QPen(guide_color, 1, Qt.DashLine))
            painter.drawLine(0, my, w, my)
            if mx >= 0:
                painter.drawLine(mx, 0, mx, int(draw_h))
                sample = self._x_to_sample(mx - x0, draw_w)
                if self._samplerate > 0:
                    secs = sample / self._samplerate
                    m = int(secs) // 60
                    s = secs - m * 60
                    time_label = f"{m}:{s:05.2f} ({sample:,})"
                    painter.setFont(QFont("Consolas", 7))
                    painter.setPen(QColor(200, 200, 200, 180))
                    tfm = painter.fontMetrics()
                    ttw = tfm.horizontalAdvance(time_label)
                    lx = max(x0, min(mx - ttw // 2, x0 + draw_w - ttw))
                    painter.drawText(int(lx), tfm.ascent() + 2, time_label)

            if self._display_mode == "spectrogram":
                self._spec_renderer.draw_freq_guide(
                    painter, self._make_spec_ctx(x0, draw_w, draw_h), my)
            else:
                nch = self._num_channels
                if nch > 0:
                    lane_h = draw_h / nch
                    self._wf_renderer.draw_db_guide(
                        painter, self._make_wf_ctx(x0, draw_w, draw_h),
                        nch, lane_h, my)

        painter.end()

    # ── Qt event handlers ──────────────────────────────────────────────────

    def resizeEvent(self, event):
        self._wf_renderer.invalidate()
        self._spec_renderer.invalidate()
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        self.setFocus()
        if self._total_samples > 0 and event.button() == Qt.LeftButton:
            x0, draw_w = self._draw_area()
            h = self.height()
            draw_h = h - self._MARGIN_BOTTOM
            my = event.position().y()
            sample = self._x_to_sample(event.position().x() - x0, draw_w)
            self._cursor_sample = sample
            if self._display_mode == "spectrogram":
                mel_min = self._spec_renderer.mel_view_min
                mel_max = self._spec_renderer.mel_view_max
                mel_range = mel_max - mel_min
                if draw_h > 0 and mel_range > 0:
                    frac = max(0.0, min(1.0 - my / draw_h, 1.0))
                    self._cursor_y_value = mel_min + frac * mel_range
                else:
                    self._cursor_y_value = None
            else:
                nch = self._num_channels
                if nch > 0 and draw_h > 0:
                    lane_h = draw_h / nch
                    ch = int(my / lane_h) if lane_h > 0 else 0
                    ch = max(0, min(ch, nch - 1))
                    mid_y = ch * lane_h + lane_h / 2.0
                    scale = (lane_h / 2.0) * 0.85 * self._vscale
                    if scale > 0:
                        self._cursor_y_value = (mid_y - my) / scale
                        self._cursor_y_channel = ch
                    else:
                        self._cursor_y_value = None
                else:
                    self._cursor_y_value = None
            self.update()
            self.position_clicked.emit(sample)

    def mouseMoveEvent(self, event):
        """Show tooltip when hovering over an issue region or marker."""
        self._mouse_x = int(event.position().x())
        self._mouse_y = int(event.position().y())
        self.update()
        if self._total_samples <= 0:
            QToolTip.hideText()
            return
        x0, draw_w = self._draw_area()
        h = self.height()
        draw_h = h - self._MARGIN_BOTTOM
        mx = event.position().x()
        my = event.position().y()
        nch = self._num_channels
        lane_h = draw_h / nch if nch > 0 else draw_h
        sample = self._x_to_sample(mx - x0, draw_w)
        mouse_ch = max(0, min(int(my / lane_h) if lane_h > 0 else 0, nch - 1))
        view_len = self._view_end - self._view_start
        tolerance = int(view_len / max(draw_w, 1) * 5)
        tips: list[str] = []
        if self._display_mode == "waveform":
            _MARKER_PX_TOL = 6
            if self._show_markers and self._wf_renderer.peak_sample >= 0:
                peak_px = x0 + self._sample_to_x(self._wf_renderer.peak_sample, draw_w)
                if abs(mx - peak_px) <= _MARKER_PX_TOL:
                    tips.append(f"Peak: {self._wf_renderer.peak_db:.1f} dBFS")
            if self._show_markers and self._wf_renderer.rms_max_sample >= 0:
                rms_px = x0 + self._sample_to_x(self._wf_renderer.rms_max_sample, draw_w)
                if abs(mx - rms_px) <= _MARKER_PX_TOL:
                    tips.append(f"Max RMS: {self._wf_renderer.rms_max_db:.1f} dBFS")
        for issue in self._issues:
            if issue.label not in self._enabled_overlays:
                continue
            s_start = issue.sample_start
            s_end = issue.sample_end if issue.sample_end is not None else s_start
            if sample < s_start - tolerance or sample > s_end + tolerance:
                continue
            if self._display_mode == "waveform":
                if issue.channel is not None and issue.channel != mouse_ch:
                    continue
            tips.append(issue.description)
        if tips:
            QToolTip.showText(event.globalPosition().toPoint(), "\n".join(tips), self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, event):
        self._mouse_x = -1
        self._mouse_y = -1
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event):
        if self._total_samples <= 0:
            event.ignore()
            return
        mods = event.modifiers()
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            event.ignore()
            return
        ctrl = bool(mods & Qt.ControlModifier)
        shift = bool(mods & Qt.ShiftModifier)
        alt = bool(mods & Qt.AltModifier)
        if ctrl and shift:
            if self._display_mode == "spectrogram":
                draw_h = self.height() - self._MARGIN_BOTTOM
                my = event.position().y()
                mel_range = self._spec_renderer.mel_view_max - self._spec_renderer.mel_view_min
                anchor_mel = None
                if draw_h > 0 and mel_range > 0:
                    frac = max(0.0, min(1.0 - my / draw_h, 1.0))
                    anchor_mel = self._spec_renderer.mel_view_min + frac * mel_range
                self._spec_renderer.freq_zoom(2 / 3 if delta > 0 else 3 / 2,
                                              anchor_mel, self._samplerate)
            else:
                self._vscale = (min(self._vscale * 1.25, 20.0) if delta > 0
                                else max(self._vscale / 1.25, 0.1))
            self.update()
            event.accept()
        elif ctrl:
            x0, draw_w = self._draw_area()
            mx = event.position().x()
            frac = max(0.0, min((mx - x0) / max(draw_w, 1), 1.0))
            anchor_sample = max(self._view_start,
                                min(self._x_to_sample(mx - x0, draw_w), self._view_end))
            view_len = self._view_end - self._view_start
            new_len = (max(view_len * 2 // 3, 100) if delta > 0
                       else min(view_len * 3 // 2, self._total_samples))
            if new_len == view_len:
                event.accept()
                return
            new_start = int(anchor_sample - frac * new_len)
            new_end = new_start + new_len
            if new_start < 0:
                new_start = 0
                new_end = min(new_len, self._total_samples)
            if new_end > self._total_samples:
                new_end = self._total_samples
                new_start = max(0, new_end - new_len)
            self._view_start = new_start
            self._view_end = new_end
            self._wf_renderer.invalidate()
            self.update()
            event.accept()
        elif shift and alt:
            if self._display_mode == "spectrogram":
                mel_range = self._spec_renderer.mel_view_max - self._spec_renderer.mel_view_min
                scroll = mel_range / 8
                if delta < 0:
                    scroll = -scroll
                if self._invert_v:
                    scroll = -scroll
                self._spec_renderer.scroll_freq(scroll, self._samplerate)
                self.update()
            event.accept()
        elif shift:
            view_len = self._view_end - self._view_start
            scroll_amount = max(1, view_len // 8)
            if delta < 0:
                scroll_amount = -scroll_amount
            if self._invert_h:
                scroll_amount = -scroll_amount
            new_start = self._view_start + scroll_amount
            new_end = self._view_end + scroll_amount
            if new_start < 0:
                new_start = 0
                new_end = min(view_len, self._total_samples)
            if new_end > self._total_samples:
                new_end = self._total_samples
                new_start = max(0, new_end - view_len)
            self._view_start = new_start
            self._view_end = new_end
            self._wf_renderer.invalidate_rms_only()
            if not self._scroll_pending:
                self._scroll_pending = True
                self._scroll_timer.start()
            event.accept()
        else:
            event.ignore()

    def _flush_scroll(self):
        self._scroll_pending = False
        self.update()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_R:
            self._zoom_at_guide(zoom_in=True)
        elif key == Qt.Key_T:
            self._zoom_at_guide(zoom_in=False)
        else:
            super().keyPressEvent(event)

    def _zoom_at_guide(self, zoom_in: bool):
        if self._total_samples <= 0:
            return
        view_len = self._view_end - self._view_start
        x0, draw_w = self._draw_area()
        if self._mouse_x >= 0:
            frac = max(0.0, min((self._mouse_x - x0) / max(draw_w, 1), 1.0))
            anchor = max(self._view_start,
                         min(self._x_to_sample(self._mouse_x - x0, draw_w),
                             self._view_end))
        else:
            anchor = max(self._view_start, min(self._cursor_sample, self._view_end))
            frac = (anchor - self._view_start) / max(view_len, 1)
        new_len = (max(view_len * 2 // 3, 100) if zoom_in
                   else min(view_len * 3 // 2, self._total_samples))
        if new_len == view_len:
            return
        new_start = int(anchor - frac * new_len)
        new_end = new_start + new_len
        if new_start < 0:
            new_start = 0
            new_end = min(new_len, self._total_samples)
        if new_end > self._total_samples:
            new_end = self._total_samples
            new_start = max(0, new_end - new_len)
        self._view_start = new_start
        self._view_end = new_end
        self._wf_renderer.invalidate()
        self.update()

    # ── Zoom / vertical-scale public API ───────────────────────────────────

    def zoom_fit(self):
        """Reset horizontal zoom and vertical scale to show the entire file."""
        self._view_start = 0
        self._view_end = self._total_samples
        self._vscale = 1.0
        self._spec_renderer.reset_freq_view(self._samplerate)
        self._wf_renderer.invalidate()
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
        self._wf_renderer.invalidate()
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
        self._wf_renderer.invalidate()
        self.update()

    def scale_up(self):
        """Increase vertical amplitude scale / zoom freq in (spectrogram)."""
        if self._display_mode == "spectrogram":
            self._spec_renderer.freq_zoom(
                2 / 3, self._cursor_y_value, self._samplerate)
        else:
            self._vscale = min(self._vscale * 1.5, 20.0)
        self.update()

    def scale_down(self):
        """Decrease vertical amplitude scale / zoom freq out (spectrogram)."""
        if self._display_mode == "spectrogram":
            self._spec_renderer.freq_zoom(
                3 / 2, self._cursor_y_value, self._samplerate)
        else:
            self._vscale = max(self._vscale / 1.5, 0.1)
        self.update()

    # ── Public setters ─────────────────────────────────────────────────────

    def set_rms_data(self, window_samples: int):
        """Set the RMS window size."""
        self._rms_window_samples = max(window_samples, 0)
        self._wf_renderer.set_rms_window(window_samples)
        self.update()

    def toggle_markers(self, on: bool):
        self._show_markers = on
        self.update()

    def toggle_rms_lr(self, on: bool):
        self._show_rms_lr = on
        self.update()

    def toggle_rms_avg(self, on: bool):
        self._show_rms_avg = on
        self.update()

    def set_enabled_overlays(self, labels: set[str]):
        self._enabled_overlays = set(labels)
        self.update()

    def set_display_mode(self, mode: str):
        if mode not in ("waveform", "spectrogram"):
            return
        self._display_mode = mode
        self._spec_renderer.invalidate()
        self.update()

    def set_invert_scroll(self, mode: str):
        self._invert_h = mode in ("horizontal", "both")
        self._invert_v = mode in ("vertical", "both")

    def set_wf_antialias(self, enabled: bool):
        self._wf_antialias = enabled
        self.update()

    def set_wf_line_width(self, width: int):
        self._wf_line_width = max(1, min(width, 3))
        self.update()

    def set_colormap(self, name: str):
        self._spec_renderer.set_colormap(name)
        self.update()

    def set_spec_fft(self, n_fft: int):
        if n_fft == self._spec_renderer.spec_n_fft:
            return
        self._spec_renderer.set_n_fft(n_fft)
        self._recompute_spectrogram()

    def set_spec_window(self, window: str):
        if window == self._spec_renderer.spec_window:
            return
        self._spec_renderer.set_window(window)
        self._recompute_spectrogram()

    def set_spec_db_floor(self, val: float):
        self._spec_renderer.set_db_floor(val)
        self.update()

    def set_spec_db_ceil(self, val: float):
        self._spec_renderer.set_db_ceil(val)
        self.update()

    @property
    def spec_n_fft(self) -> int:
        return self._spec_renderer.spec_n_fft

    @property
    def spec_window(self) -> str:
        return self._spec_renderer.spec_window

    def _recompute_spectrogram(self):
        if not self._channels:
            return
        self._spec_renderer.recompute(
            self._channels, self._samplerate,
            on_done=self.update, parent=self,
        )
        self.update()
