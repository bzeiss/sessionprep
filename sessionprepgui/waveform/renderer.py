"""Waveform renderer: peaks, RMS envelope, dB scale, and markers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter,
                           QPen, QPolygonF)

from ..theme import COLORS

_CHANNEL_COLORS = [
    "#44aa44", "#44aaaa", "#aa44aa", "#aaaa44",
    "#4488cc", "#cc8844", "#88cc44", "#cc4488",
]


@dataclass
class WaveformRenderCtx:
    """All data WaveformRenderer needs — immutable snapshot from WaveformWidget."""
    x0: int
    draw_w: int
    draw_h: int
    margin_right: int
    view_start: int
    view_end: int
    vscale: float
    channels: list
    num_channels: int
    show_rms_lr: bool
    show_rms_avg: bool
    show_markers: bool
    wf_antialias: bool
    wf_line_width: int


class WaveformRenderer:
    """Draws per-channel audio waveforms with RMS overlays and peak markers."""

    def __init__(self):
        self._peaks_cache: list[tuple[np.ndarray, np.ndarray]] = []
        self._cached_view: tuple[int, int, int] = (0, 0, 0)
        self._rms_envelope: list[np.ndarray] = []
        self._rms_combined: np.ndarray | list = []
        self._rms_cache_key: tuple[int, int, int] = (0, 0, 0)
        self._rms_cumsums: list[np.ndarray] = []
        self._rms_window_samples: int = 0
        self._channels: list[np.ndarray] = []
        self._peak_sample: int = -1
        self._peak_channel: int = -1
        self._peak_db: float = float('-inf')
        self._peak_amplitude: float = 0.0
        self._peak_dirty: bool = False
        self._rms_max_sample: int = -1
        self._rms_max_db: float = float('-inf')
        self._rms_max_amplitude: float = 0.0
        self._rms_max_dirty: bool = False

    def reset(self):
        """Clear all state on track unload / set_loading."""
        self._peaks_cache = []
        self._cached_view = (0, 0, 0)
        self._rms_envelope = []
        self._rms_combined = []
        self._rms_cache_key = (0, 0, 0)
        self._rms_cumsums = []
        self._rms_window_samples = 0
        self._channels = []
        self._peak_sample = -1
        self._peak_channel = -1
        self._peak_db = float('-inf')
        self._peak_amplitude = 0.0
        self._peak_dirty = False
        self._rms_max_sample = -1
        self._rms_max_db = float('-inf')
        self._rms_max_amplitude = 0.0
        self._rms_max_dirty = False

    def set_track_data(self, channels: list, *,
                       peak_sample: int = -1, peak_channel: int = -1,
                       peak_db: float = float('-inf'),
                       peak_amplitude: float = 0.0,
                       peak_dirty: bool = False,
                       rms_cumsums: list | None = None,
                       rms_window: int = 0,
                       rms_max_sample: int = -1,
                       rms_max_db: float = float('-inf'),
                       rms_max_amplitude: float = 0.0,
                       rms_max_dirty: bool = False):
        """Set per-track data.  Resets caches.  Called from set_audio / set_precomputed."""
        self._channels = channels
        self._peak_sample = peak_sample
        self._peak_channel = peak_channel
        self._peak_db = peak_db
        self._peak_amplitude = peak_amplitude
        self._peak_dirty = peak_dirty
        self._rms_cumsums = rms_cumsums or []
        self._rms_window_samples = rms_window
        self._rms_max_sample = rms_max_sample
        self._rms_max_db = rms_max_db
        self._rms_max_amplitude = rms_max_amplitude
        self._rms_max_dirty = rms_max_dirty
        self._peaks_cache = []
        self._cached_view = (0, 0, 0)
        self._rms_envelope = []
        self._rms_combined = []
        self._rms_cache_key = (0, 0, 0)

    def set_rms_window(self, window_samples: int):
        """Update RMS window size and reset RMS caches + dirty flags."""
        self._rms_window_samples = max(window_samples, 0)
        self._rms_envelope = []
        self._rms_combined = []
        self._rms_cache_key = (0, 0, 0)
        self._rms_max_sample = -1
        self._rms_max_db = float('-inf')
        self._rms_max_amplitude = 0.0
        self._rms_max_dirty = bool(self._channels and window_samples > 0)

    def invalidate(self):
        """Invalidate peak and RMS caches (zoom change, resize, large scroll)."""
        self._peaks_cache = []
        self._cached_view = (0, 0, 0)
        self._rms_envelope = []
        self._rms_combined = []
        self._rms_cache_key = (0, 0, 0)

    def invalidate_rms_only(self):
        """Invalidate RMS envelope cache but keep peaks for incremental updates."""
        self._rms_envelope = []
        self._rms_combined = []
        self._rms_cache_key = (0, 0, 0)

    def paint(self, painter: QPainter, ctx: WaveformRenderCtx):
        """Full waveform draw pass: envelope + dB scale + RMS + markers."""
        painter.setRenderHint(QPainter.Antialiasing, ctx.wf_antialias)
        self._build_peaks(ctx)
        if ctx.show_rms_lr or ctx.show_rms_avg:
            self._build_rms_envelope(ctx)
        nch = ctx.num_channels
        lane_h = ctx.draw_h / nch
        self._draw_db_scale(painter, ctx, nch, lane_h)
        self._draw_waveform_channels(painter, ctx, nch, lane_h)
        if ctx.show_rms_lr or ctx.show_rms_avg:
            self._draw_rms_overlay(painter, ctx, nch, lane_h)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if ctx.show_markers:
            self._draw_markers(painter, ctx, nch, lane_h)

    def draw_db_guide(self, painter: QPainter, ctx: WaveformRenderCtx,
                      nch: int, lane_h: float, my: float):
        """Draw dBFS readout labels at mouse y position (called from paintEvent)."""
        mouse_ch = int(my / lane_h) if lane_h > 0 else 0
        mouse_ch = max(0, min(mouse_ch, nch - 1))
        ch_y_off = mouse_ch * lane_h
        ch_mid_y = ch_y_off + lane_h / 2.0
        ch_scale = (lane_h / 2.0) * 0.85 * ctx.vscale
        if ch_scale > 0:
            amp = abs(ch_mid_y - my) / ch_scale
            db_label = f"{20.0 * np.log10(amp):.1f}" if amp > 0 else "-\u221e"
            painter.setFont(QFont("Consolas", 7))
            label_color = QColor(180, 180, 180, 120)
            painter.setPen(label_color)
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(db_label)
            painter.drawText(ctx.x0 - 5 - tw, int(ch_y_off) + fm.ascent() + 1, db_label)
            painter.drawText(ctx.x0 + ctx.draw_w + 5, int(ch_y_off) + fm.ascent() + 1, db_label)

    @property
    def peak_sample(self) -> int:
        return self._peak_sample

    @property
    def peak_db(self) -> float:
        return self._peak_db

    @property
    def rms_max_sample(self) -> int:
        return self._rms_max_sample

    @property
    def rms_max_db(self) -> float:
        return self._rms_max_db

    @property
    def rms_max_amplitude(self) -> float:
        return self._rms_max_amplitude

    # ── Internal helpers ───────────────────────────────────────────────────

    def _sample_to_x(self, sample: int, ctx: WaveformRenderCtx) -> int:
        view_len = ctx.view_end - ctx.view_start
        if view_len <= 0:
            return 0
        return int((sample - ctx.view_start) / view_len * ctx.draw_w)

    @staticmethod
    def _peaks_for_view(ch_data, vs, ve, width):
        """Compute (mins, maxs) arrays for one channel over samples [vs:ve]."""
        view_data = ch_data[vs:ve]
        n = len(view_data)
        if n == 0:
            return np.zeros(width, dtype=np.float64), np.zeros(width, dtype=np.float64)
        if n >= width:
            starts = np.arange(width, dtype=np.int64) * n // width
            maxs = np.maximum.reduceat(view_data, starts).astype(np.float64)
            mins = np.minimum.reduceat(view_data, starts).astype(np.float64)
        else:
            mins = np.zeros(width, dtype=np.float64)
            maxs = np.zeros(width, dtype=np.float64)
            starts = np.arange(width) * n // width
            ends = np.minimum((np.arange(width) + 1) * n // width, n)
            valid = ends > starts
            if valid.any():
                single = valid & ((ends - starts) == 1)
                if single.any():
                    mins[single] = view_data[starts[single]]
                    maxs[single] = view_data[starts[single]]
                multi = valid & ((ends - starts) > 1)
                for i in np.nonzero(multi)[0]:
                    chunk = view_data[starts[i]:ends[i]]
                    mins[i] = chunk.min()
                    maxs[i] = chunk.max()
        return mins, maxs

    def _build_peaks(self, ctx: WaveformRenderCtx):
        """Downsample audio to peak envelope, with incremental scroll updates."""
        channels = ctx.channels
        width = ctx.draw_w
        if not channels or width <= 0:
            self._peaks_cache = []
            return
        cache_key = (width, ctx.view_start, ctx.view_end)
        if self._cached_view == cache_key and self._peaks_cache:
            return
        vs, ve = ctx.view_start, ctx.view_end
        view_len = ve - vs
        if view_len <= 0:
            self._peaks_cache = []
            return
        old_w, old_vs, old_ve = self._cached_view
        can_inc = (
            self._peaks_cache
            and old_w == width
            and (old_ve - old_vs) == view_len
            and vs != old_vs
            and len(self._peaks_cache) == len(channels)
        )
        if can_inc:
            shift_samples = vs - old_vs
            shift_bins = int(round(shift_samples * width / view_len))
            if 0 < abs(shift_bins) < width:
                new_cache = []
                for ch_idx, ch_data in enumerate(channels):
                    old_mins, old_maxs = self._peaks_cache[ch_idx]
                    mins = np.empty(width, dtype=np.float64)
                    maxs = np.empty(width, dtype=np.float64)
                    if shift_bins > 0:
                        keep = width - shift_bins
                        mins[:keep] = old_mins[shift_bins:]
                        maxs[:keep] = old_maxs[shift_bins:]
                        new_vs = vs + keep * view_len // width
                        nm, nx = self._peaks_for_view(ch_data, new_vs, ve, width - keep)
                        mins[keep:] = nm
                        maxs[keep:] = nx
                    else:
                        sb = -shift_bins
                        keep = width - sb
                        mins[sb:] = old_mins[:keep]
                        maxs[sb:] = old_maxs[:keep]
                        new_ve = vs + sb * view_len // width
                        nm, nx = self._peaks_for_view(ch_data, vs, new_ve, sb)
                        mins[:sb] = nm
                        maxs[:sb] = nx
                    new_cache.append((mins, maxs))
                self._peaks_cache = new_cache
                self._cached_view = cache_key
                return
        self._peaks_cache = []
        for ch_data in channels:
            mins, maxs = self._peaks_for_view(ch_data, vs, ve, width)
            self._peaks_cache.append((mins, maxs))
        self._cached_view = cache_key

    def _draw_waveform_channels(self, painter: QPainter,
                                ctx: WaveformRenderCtx,
                                nch: int, lane_h: float):
        """Draw filled waveform envelopes and centre lines for all channels."""
        widget_w = ctx.x0 + ctx.draw_w + ctx.margin_right
        view_len = ctx.view_end - ctx.view_start
        spp = view_len / max(ctx.draw_w, 1)

        for ch in range(nch):
            y_off = ch * lane_h
            mid_y = y_off + lane_h / 2.0
            scale = (lane_h / 2.0) * 0.85 * ctx.vscale

            lane_top = int(y_off)
            lane_bot = int(y_off + lane_h)
            painter.setClipRect(ctx.x0, lane_top, ctx.draw_w, lane_bot - lane_top)

            color = QColor(_CHANNEL_COLORS[ch % len(_CHANNEL_COLORS)])
            mins, maxs = self._peaks_cache[ch]

            n_pts = len(mins)
            xs = np.arange(n_pts, dtype=np.float64) + ctx.x0
            ys_top = mid_y - maxs * scale
            ys_bot = mid_y - mins * scale

            env_x = np.concatenate([xs, xs[::-1]])
            env_y = np.concatenate([ys_top, ys_bot[::-1]])
            env_poly = QPolygonF([QPointF(env_x[i], env_y[i])
                                  for i in range(len(env_x))])

            grad = QLinearGradient(0, y_off, 0, y_off + lane_h)
            color_edge = QColor(color)
            color_edge.setAlpha(30)
            color_mid = QColor(color)
            color_mid.setAlpha(140)
            grad.setColorAt(0.0, color_edge)
            grad.setColorAt(0.5, color_mid)
            grad.setColorAt(1.0, color_edge)

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(COLORS["bg"]))
            painter.drawPolygon(env_poly)
            painter.setBrush(grad)
            painter.drawPolygon(env_poly)

            outline_alpha = max(100, min(200, int(200 - spp * 0.1)))
            outline = QColor(color)
            outline.setAlpha(outline_alpha)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(outline, ctx.wf_line_width))
            top_poly = QPolygonF([QPointF(xs[i], ys_top[i]) for i in range(n_pts)])
            bot_poly = QPolygonF([QPointF(xs[i], ys_bot[i]) for i in range(n_pts)])
            painter.drawPolyline(top_poly)
            painter.drawPolyline(bot_poly)

            center_color = QColor(160, 100, 220, 160)
            painter.setPen(QPen(center_color, 2, Qt.DotLine))
            painter.drawLine(ctx.x0, int(mid_y), ctx.x0 + ctx.draw_w, int(mid_y))

            painter.setClipping(False)

            if ch < nch - 1:
                sep_y = int(y_off + lane_h)
                painter.setPen(QPen(QColor("#555555"), 1))
                painter.drawLine(0, sep_y, widget_w, sep_y)

    def _draw_rms_overlay(self, painter: QPainter, ctx: WaveformRenderCtx,
                          nch: int, lane_h: float):
        """Draw per-channel and combined RMS envelope lines."""
        if not self._rms_envelope:
            return
        lw = ctx.wf_line_width
        ch_pen = QPen(QColor(255, 220, 60, 200), float(lw))
        comb_pen = QPen(QColor(255, 100, 40, 220), float(lw) * 1.5)
        for ch in range(nch):
            if ch >= len(self._rms_envelope):
                break
            y_off = ch * lane_h
            mid_y = y_off + lane_h / 2.0
            scale = (lane_h / 2.0) * 0.85 * ctx.vscale
            painter.setClipRect(ctx.x0, int(y_off), ctx.draw_w, int(lane_h))
            painter.setBrush(Qt.NoBrush)
            if ctx.show_rms_lr:
                ch_env = self._rms_envelope[ch]
                n_rms = len(ch_env)
                rxs = np.arange(n_rms, dtype=np.float64) + ctx.x0
                rys = mid_y - ch_env * scale
                painter.setPen(ch_pen)
                painter.drawPolyline(QPolygonF(
                    [QPointF(rxs[i], rys[i]) for i in range(n_rms)]))
            if ctx.show_rms_avg and len(self._rms_combined) > 0:
                n_comb = len(self._rms_combined)
                cxs = np.arange(n_comb, dtype=np.float64) + ctx.x0
                cys = mid_y - self._rms_combined * scale
                painter.setPen(comb_pen)
                painter.drawPolyline(QPolygonF(
                    [QPointF(cxs[i], cys[i]) for i in range(n_comb)]))
            painter.setClipping(False)

    def _draw_db_scale(self, painter: QPainter, ctx: WaveformRenderCtx,
                       nch: int, lane_h: float):
        """Draw dB measurement scale on left/right margins and grid lines."""
        _DB_TICKS = [0, -3, -6, -12, -18, -24, -36, -48, -60]
        _MIN_TICK_SPACING = 18
        scale_font = QFont("Consolas", 7)
        painter.setFont(scale_font)
        fm = painter.fontMetrics()
        text_h = fm.height()
        grid_color = QColor(COLORS["accent"])
        grid_color.setAlpha(35)
        grid_pen = QPen(grid_color, 1, Qt.DotLine)
        label_color = QColor(COLORS["dim"])
        tick_pen = QPen(label_color, 1)
        full_right = ctx.x0 + ctx.draw_w + ctx.margin_right
        for ch in range(nch):
            y_off = ch * lane_h
            mid_y = y_off + lane_h / 2.0
            scale = (lane_h / 2.0) * 0.85 * ctx.vscale
            lane_top = int(y_off)
            lane_bot = int(y_off + lane_h)
            painter.setClipRect(0, lane_top, full_right, lane_bot - lane_top)
            visible_ticks: list[tuple[int, float, float]] = []
            used_ys: list[float] = []
            for db_val in _DB_TICKS:
                amp = 10.0 ** (db_val / 20.0)
                pixel_offset = amp * scale
                if pixel_offset >= lane_h / 2.0:
                    continue
                y_top = mid_y - pixel_offset
                y_bot = mid_y + pixel_offset
                if y_top < lane_top + text_h or y_bot > lane_bot - text_h:
                    continue
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
                painter.setPen(grid_pen)
                painter.drawLine(ctx.x0, int(y_top), ctx.x0 + ctx.draw_w, int(y_top))
                if db_val != 0:
                    painter.drawLine(ctx.x0, int(y_bot), ctx.x0 + ctx.draw_w, int(y_bot))
                painter.setPen(tick_pen)
                text_w = fm.horizontalAdvance(label)
                lx = ctx.x0 - 5 - text_w
                painter.drawText(int(lx), int(y_top + text_h / 3), label)
                if db_val != 0:
                    painter.drawText(int(lx), int(y_bot + text_h / 3), label)
                rx = ctx.x0 + ctx.draw_w + 5
                painter.drawText(int(rx), int(y_top + text_h / 3), label)
                if db_val != 0:
                    painter.drawText(int(rx), int(y_bot + text_h / 3), label)
                painter.drawLine(ctx.x0 - 3, int(y_top), ctx.x0, int(y_top))
                painter.drawLine(ctx.x0 + ctx.draw_w, int(y_top), ctx.x0 + ctx.draw_w + 3, int(y_top))
                if db_val != 0:
                    painter.drawLine(ctx.x0 - 3, int(y_bot), ctx.x0, int(y_bot))
                    painter.drawLine(ctx.x0 + ctx.draw_w, int(y_bot), ctx.x0 + ctx.draw_w + 3, int(y_bot))
                conn_color = QColor(45, 45, 45)
                painter.setPen(QPen(conn_color, 1))
                painter.drawLine(0, int(y_top), full_right, int(y_top))
                if db_val != 0:
                    painter.drawLine(0, int(y_bot), full_right, int(y_bot))
            painter.setClipping(False)

    def _ensure_peak_computed(self):
        if not self._peak_dirty:
            return
        self._peak_dirty = False
        if not self._channels:
            return
        if len(self._channels) == 1:
            self._peak_sample = int(np.argmax(np.abs(self._channels[0])))
            self._peak_channel = 0
        else:
            abs_cols = np.column_stack([np.abs(ch) for ch in self._channels])
            max_per_sample = np.max(abs_cols, axis=1)
            self._peak_sample = int(np.argmax(max_per_sample))
            self._peak_channel = int(np.argmax(abs_cols[self._peak_sample]))
        peak_lin = abs(float(self._channels[self._peak_channel][self._peak_sample]))
        self._peak_db = 20.0 * np.log10(peak_lin) if peak_lin > 0 else float('-inf')
        self._peak_amplitude = float(self._channels[self._peak_channel][self._peak_sample])

    def _ensure_rms_max_computed(self):
        if not self._rms_max_dirty:
            return
        self._rms_max_dirty = False
        self._compute_rms_max_sample()

    def _compute_rms_max_sample(self):
        """Find the sample position of the maximum momentary RMS window."""
        win = self._rms_window_samples
        if not self._channels or win <= 0:
            self._rms_max_sample = -1
            return
        ch_wms: list[np.ndarray] = []
        have_cumsums = len(self._rms_cumsums) == len(self._channels)
        for ch_idx, ch_data in enumerate(self._channels):
            n = len(ch_data)
            if n <= win:
                ch_wms.append(np.zeros(1, dtype=np.float64))
                continue
            if have_cumsums:
                cs = self._rms_cumsums[ch_idx]
            else:
                sq = ch_data.astype(np.float64) ** 2
                cs = np.empty(n + 1, dtype=np.float64)
                cs[0] = 0.0
                np.cumsum(sq, out=cs[1:])
            ch_wms.append((cs[win:] - cs[:n - win + 1]) / win)
        min_len = min(len(wm) for wm in ch_wms)
        if min_len == 0:
            self._rms_max_sample = -1
            return
        combined = np.mean(np.column_stack([wm[:min_len] for wm in ch_wms]), axis=1)
        max_idx = int(np.argmax(combined))
        self._rms_max_sample = max_idx + win // 2
        max_rms_lin = float(np.sqrt(combined[max_idx]))
        self._rms_max_db = 20.0 * np.log10(max_rms_lin) if max_rms_lin > 0 else float('-inf')
        self._rms_max_amplitude = max_rms_lin

    def _draw_markers(self, painter: QPainter, ctx: WaveformRenderCtx,
                      nch: int, lane_h: float):
        """Draw peak and max RMS marker vertical lines."""
        self._ensure_peak_computed()
        self._ensure_rms_max_computed()
        marker_font = QFont("Consolas", 7, QFont.Bold)
        _CROSS_HALF = 6
        if self._peak_sample >= 0:
            px = ctx.x0 + self._sample_to_x(self._peak_sample, ctx)
            if ctx.x0 <= px <= ctx.x0 + ctx.draw_w:
                peak_color = QColor(180, 50, 220, 250)
                painter.setPen(QPen(peak_color, 1))
                painter.drawLine(px, 0, px, ctx.draw_h)
                painter.setFont(marker_font)
                painter.setPen(peak_color)
                painter.drawText(px + 3, 12, "P")
                if 0 <= self._peak_channel < nch:
                    ch = self._peak_channel
                    amp = self._peak_amplitude
                    y_off = ch * lane_h
                    mid_y = y_off + lane_h / 2.0
                    scale = (lane_h / 2.0) * 0.85 * ctx.vscale
                    cy = int(mid_y - amp * scale)
                    painter.setPen(QPen(peak_color, 1))
                    painter.drawLine(px - _CROSS_HALF, cy, px + _CROSS_HALF, cy)
        if self._rms_max_sample >= 0:
            rx = ctx.x0 + self._sample_to_x(self._rms_max_sample, ctx)
            if ctx.x0 <= rx <= ctx.x0 + ctx.draw_w:
                rms_color = QColor(40, 160, 220, 250)
                painter.setPen(QPen(rms_color, 1))
                painter.drawLine(rx, 0, rx, ctx.draw_h)
                painter.setFont(marker_font)
                painter.setPen(rms_color)
                painter.drawText(rx + 3, 24, "R")
                amp = self._rms_max_amplitude
                if amp > 0:
                    painter.setPen(QPen(rms_color, 1))
                    for ch in range(nch):
                        y_off = ch * lane_h
                        mid_y = y_off + lane_h / 2.0
                        scale = (lane_h / 2.0) * 0.85 * ctx.vscale
                        cy = int(mid_y - amp * scale)
                        painter.drawLine(rx - _CROSS_HALF, cy, rx + _CROSS_HALF, cy)

    def _build_rms_envelope(self, ctx: WaveformRenderCtx):
        """Compute per-channel AND combined RMS envelopes for ctx.draw_w pixels."""
        win = self._rms_window_samples
        width = ctx.draw_w
        if not ctx.channels or width <= 0 or win <= 0:
            self._rms_envelope = []
            self._rms_combined = []
            return
        cache_key = (width, ctx.view_start, ctx.view_end)
        if self._rms_cache_key == cache_key and self._rms_envelope:
            return
        vs, ve = ctx.view_start, ctx.view_end
        view_len = ve - vs
        if view_len <= 0:
            self._rms_envelope = []
            self._rms_combined = []
            return
        half_win = win // 2
        have_cumsums = len(self._rms_cumsums) == len(ctx.channels)
        ch_wms: list[np.ndarray] = []
        wm_offset = 0
        for ch_idx, ch_data in enumerate(ctx.channels):
            n = len(ch_data)
            n_wm_total = n - win + 1
            if n <= win:
                ch_wms.append(np.zeros(1, dtype=np.float64))
                continue
            if have_cumsums:
                cs = self._rms_cumsums[ch_idx]
            else:
                sq = ch_data.astype(np.float64) ** 2
                cs = np.empty(n + 1, dtype=np.float64)
                cs[0] = 0.0
                np.cumsum(sq, out=cs[1:])
            wm_lo = max(0, vs - half_win - win)
            wm_hi = min(n_wm_total, ve + half_win + win)
            wm_offset = wm_lo
            ch_wms.append((cs[wm_lo + win: wm_hi + win] - cs[wm_lo: wm_hi]) / win)
        min_len = min(len(wm) for wm in ch_wms)
        if min_len > 1 and len(ch_wms) > 1:
            combined_wm = np.mean(
                np.column_stack([wm[:min_len] for wm in ch_wms]), axis=1)
        elif ch_wms:
            combined_wm = ch_wms[0][:min_len].copy()
        else:
            combined_wm = np.zeros(1, dtype=np.float64)

        def _downsample(wm: np.ndarray, offset: int) -> np.ndarray:
            n_wm = len(wm)
            if n_wm == 0:
                return np.zeros(width)
            pixel_edges = np.arange(width + 1)
            s_edges = vs + pixel_edges * view_len // width
            global_wm = np.clip(s_edges - half_win, 0, offset + n_wm)
            local_wm = np.clip(global_wm - offset, 0, n_wm)
            first = int(local_wm[0])
            last = max(int(local_wm[-1]), first + 1)
            last = min(last, n_wm)
            wm_slice = wm[first:last]
            n_slice = len(wm_slice)
            if n_slice >= width:
                spb = n_slice // width
                n_use = spb * width
                reshaped = wm_slice[:n_use].reshape(width, spb)
                result = np.sqrt(np.maximum(reshaped.max(axis=1), 0.0))
                if n_use < n_slice:
                    tail_max = float(wm_slice[n_use:].max())
                    result[-1] = np.sqrt(max(float(result[-1]) ** 2, tail_max))
                return result
            else:
                local = np.clip(local_wm[:-1] - first, 0,
                                max(n_slice - 1, 0)).astype(np.intp)
                return np.sqrt(np.maximum(wm_slice[local], 0.0))

        self._rms_envelope = [_downsample(wm, wm_offset) for wm in ch_wms]
        self._rms_combined = _downsample(combined_wm, wm_offset)
        self._rms_cache_key = cache_key
