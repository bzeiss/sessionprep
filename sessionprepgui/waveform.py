"""Waveform display widget with per-channel rendering and issue overlays."""

from __future__ import annotations

import threading

import numpy as np

from PySide6.QtCore import QPointF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter,
                          QPen, QPolygonF)
from PySide6.QtWidgets import QToolTip, QWidget
from scipy.signal import stft as scipy_stft

from .theme import COLORS


# ---------------------------------------------------------------------------
# Spectrogram colormaps
# ---------------------------------------------------------------------------

SPECTROGRAM_COLORMAPS: dict[str, np.ndarray] = {}  # name → (256, 4) uint8 RGBA


def _register_colormap(name: str,
                       controls: list[tuple[float, tuple[int, int, int]]]):
    """Build a 256-entry RGBA LUT from control points via linear interpolation."""
    lut = np.zeros((256, 4), dtype=np.uint8)
    lut[:, 3] = 255  # fully opaque
    positions = np.array([c[0] for c in controls])
    for ch in range(3):
        values = np.array([c[1][ch] for c in controls], dtype=np.float64)
        lut[:, ch] = np.clip(
            np.interp(np.linspace(0, 1, 256), positions, values), 0, 255
        ).astype(np.uint8)
    SPECTROGRAM_COLORMAPS[name] = lut


_register_colormap("magma", [
    (0.0, (0, 0, 4)),
    (0.25, (81, 18, 124)),
    (0.5, (183, 55, 121)),
    (0.75, (254, 159, 109)),
    (1.0, (252, 253, 191)),
])

_register_colormap("viridis", [
    (0.0, (68, 1, 84)),
    (0.25, (59, 82, 139)),
    (0.5, (33, 145, 140)),
    (0.75, (94, 201, 98)),
    (1.0, (253, 231, 37)),
])

_register_colormap("grayscale", [
    (0.0, (0, 0, 0)),
    (1.0, (255, 255, 255)),
])


# ---------------------------------------------------------------------------
# Mel filterbank
# ---------------------------------------------------------------------------

def _hz_to_mel(f: float) -> float:
    return 2595.0 * np.log10(1.0 + f / 700.0)


def _mel_to_hz(m: float) -> float:
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def _mel_filterbank(sr: int, n_fft: int, n_mels: int = 128,
                    f_min: float = 20.0, f_max: float = 22050.0
                    ) -> np.ndarray:
    """Build a Mel filterbank matrix (n_mels, n_fft // 2 + 1)."""
    f_max = min(f_max, sr / 2.0)
    n_freqs = n_fft // 2 + 1
    mel_min = _hz_to_mel(f_min)
    mel_max = _hz_to_mel(f_max)
    mel_points = np.linspace(mel_min, mel_max, n_mels + 2)
    hz_points = 700.0 * (10.0 ** (mel_points / 2595.0) - 1.0)
    bin_points = np.floor((n_fft + 1) * hz_points / sr).astype(np.intp)

    fb = np.zeros((n_mels, n_freqs), dtype=np.float64)
    for i in range(n_mels):
        left, center, right = bin_points[i], bin_points[i + 1], bin_points[i + 2]
        if center == left:
            center = left + 1
        if right == center:
            right = center + 1
        for j in range(int(left), int(center)):
            if j < n_freqs:
                fb[i, j] = (j - left) / (center - left)
        for j in range(int(center), int(right)):
            if j < n_freqs:
                fb[i, j] = (right - j) / (right - center)
    return fb


# ---------------------------------------------------------------------------
# Spectrogram computation (used by background worker)
# ---------------------------------------------------------------------------

_SPEC_N_FFT = 2048
_SPEC_HOP = 512
_SPEC_N_MELS = 256
_SPEC_F_MIN = 20.0
_SPEC_F_MAX = 22050.0
_SPEC_DB_FLOOR = -80.0  # dB floor for normalization


def compute_mel_spectrogram(channels: list[np.ndarray], sr: int, *,
                            n_fft: int = _SPEC_N_FFT,
                            hop: int | None = None,
                            window: str = "hann",
                            ) -> np.ndarray | None:
    """Compute a full-file mel spectrogram from channel data.

    Returns a float32 array of shape (n_mels, n_frames) in dB, or None
    if the audio is too short.
    """
    if not channels:
        return None
    if hop is None:
        hop = n_fft // 4
    # Mix to mono
    if len(channels) == 1:
        mono = channels[0].astype(np.float64)
    else:
        mono = np.mean(
            np.column_stack([ch.astype(np.float64) for ch in channels]),
            axis=1,
        )
    if len(mono) < n_fft:
        return None
    # STFT
    _f, _t, Zxx = scipy_stft(
        mono, fs=sr, nperseg=n_fft,
        noverlap=n_fft - hop, window=window, boundary=None,
    )
    power = np.abs(Zxx) ** 2
    # Mel filterbank
    f_max = min(_SPEC_F_MAX, sr / 2.0)
    fb = _mel_filterbank(sr, n_fft, _SPEC_N_MELS, _SPEC_F_MIN, f_max)
    mel_spec = fb @ power  # (n_mels, n_frames)
    # To dB
    mel_spec = 10.0 * np.log10(np.maximum(mel_spec, 1e-10))
    return mel_spec.astype(np.float32)


class WaveformLoadWorker(QThread):
    """Background thread for heavy waveform preparation work.

    Splits channels, finds peak position, and computes RMS-max position
    so the main thread stays responsive.
    """

    finished = Signal(object)  # emits a dict with all computed results

    def __init__(self, audio_data: np.ndarray, samplerate: int,
                 rms_window_samples: int, *,
                 spec_n_fft: int = _SPEC_N_FFT,
                 spec_window: str = "hann",
                 parent=None):
        super().__init__(parent)
        self._audio_data = audio_data
        self._samplerate = samplerate
        self._rms_win = rms_window_samples
        self._spec_n_fft = spec_n_fft
        self._spec_window = spec_window
        self._cancelled = threading.Event()

    def cancel(self):
        """Request early termination of the computation."""
        self._cancelled.set()

    def run(self):
        data = self._audio_data
        sr = self._samplerate
        win = self._rms_win

        # --- Channel splitting ---
        if data.ndim == 1:
            channels = [np.ascontiguousarray(data)]
        else:
            channels = [
                np.ascontiguousarray(data[:, ch])
                for ch in range(data.shape[1])
            ]
        nch = len(channels)
        total = len(channels[0])

        if self._cancelled.is_set():
            return

        # --- Peak finding ---
        if nch == 1:
            peak_sample = int(np.argmax(np.abs(channels[0])))
            peak_channel = 0
        else:
            abs_cols = np.column_stack([np.abs(ch) for ch in channels])
            max_per_sample = np.max(abs_cols, axis=1)
            peak_sample = int(np.argmax(max_per_sample))
            peak_channel = int(np.argmax(abs_cols[peak_sample]))
        peak_lin = abs(float(channels[peak_channel][peak_sample]))
        peak_db = 20.0 * np.log10(peak_lin) if peak_lin > 0 else float('-inf')
        peak_amplitude = float(channels[peak_channel][peak_sample])

        if self._cancelled.is_set():
            return

        # --- RMS cumsum (computed once, reused for envelope drawing) ---
        rms_max_sample = -1
        rms_max_db = float('-inf')
        rms_max_amplitude = 0.0
        rms_cumsums: list[np.ndarray] = []
        if win > 0:
            ch_wms: list[np.ndarray] = []
            for ch_data in channels:
                if self._cancelled.is_set():
                    return
                n = len(ch_data)
                if n <= win:
                    rms_cumsums.append(np.zeros(2, dtype=np.float64))
                    ch_wms.append(np.zeros(1, dtype=np.float64))
                    continue
                sq = ch_data.astype(np.float64) ** 2
                cs = np.empty(n + 1, dtype=np.float64)
                cs[0] = 0.0
                np.cumsum(sq, out=cs[1:])
                rms_cumsums.append(cs)
                ch_wms.append((cs[win:] - cs[:n - win + 1]) / win)
            min_len = min(len(wm) for wm in ch_wms)
            if min_len > 0:
                combined = np.mean(
                    np.column_stack([wm[:min_len] for wm in ch_wms]), axis=1
                )
                max_idx = int(np.argmax(combined))
                rms_max_sample = max_idx + win // 2
                rms_lin = float(np.sqrt(combined[max_idx]))
                rms_max_db = 20.0 * np.log10(rms_lin) if rms_lin > 0 else float('-inf')
                rms_max_amplitude = rms_lin

        if self._cancelled.is_set():
            return

        # --- Spectrogram ---
        spec_db = compute_mel_spectrogram(
            channels, sr,
            n_fft=self._spec_n_fft, window=self._spec_window,
        )

        if self._cancelled.is_set():
            return

        self.finished.emit({
            "channels": channels,
            "samplerate": sr,
            "total_samples": total,
            "peak_sample": peak_sample,
            "peak_channel": peak_channel,
            "peak_db": peak_db,
            "peak_amplitude": peak_amplitude,
            "rms_window_samples": win,
            "rms_max_sample": rms_max_sample,
            "rms_max_db": rms_max_db,
            "rms_max_amplitude": rms_max_amplitude,
            "rms_cumsums": rms_cumsums,
            "spec_db": spec_db,
        })


class SpectrogramRecomputeWorker(QThread):
    """Lightweight background thread to recompute the mel spectrogram."""

    finished = Signal(object)  # emits np.ndarray | None

    def __init__(self, channels: list[np.ndarray], sr: int, *,
                 n_fft: int = _SPEC_N_FFT, window: str = "hann",
                 parent=None):
        super().__init__(parent)
        self._channels = channels
        self._sr = sr
        self._n_fft = n_fft
        self._window = window
        self._cancelled = threading.Event()

    def cancel(self):
        """Request early termination."""
        self._cancelled.set()

    def run(self):
        result = compute_mel_spectrogram(
            self._channels, self._sr,
            n_fft=self._n_fft, window=self._window,
        )
        if self._cancelled.is_set():
            return
        self.finished.emit(result)


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
    _MARGIN_LEFT = 38
    _MARGIN_RIGHT = 38
    _MARGIN_BOTTOM = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self._channels: list[np.ndarray] = []  # one 1-D array per channel
        self._num_channels: int = 0
        self._samplerate: int = 44100
        self._total_samples: int = 0
        self._cursor_sample: int = 0
        self._cursor_y_value: float | None = None  # amplitude (waveform) or mel (spectrogram)
        self._cursor_y_channel: int = 0             # channel lane (waveform only)
        self._peaks_cache: list[tuple[np.ndarray, np.ndarray]] = []  # (mins, maxs) per channel
        self._cached_view: tuple[int, int, int] = (0, 0, 0)  # (width, view_start, view_end)
        self._issues: list = []  # list of IssueLocation objects
        self._view_start: int = 0
        self._view_end: int = 0
        self._vscale: float = 1.0
        # RMS overlay
        self._rms_window_samples: int = 0
        self._show_rms_lr: bool = False
        self._show_rms_avg: bool = False
        self._rms_envelope: list[list[float]] = []
        self._rms_combined: list[float] = []
        self._rms_cache_key: tuple[int, int, int] = (0, 0, 0)
        # Overlay filtering
        self._enabled_overlays: set[str] = set()
        # Markers
        self._show_markers: bool = False
        self._peak_sample: int = -1
        self._peak_channel: int = -1
        self._peak_db: float = float('-inf')
        self._peak_amplitude: float = 0.0  # signed amplitude on the peak channel
        self._peak_dirty: bool = False      # True = needs recomputation
        self._rms_max_sample: int = -1
        self._rms_max_db: float = float('-inf')
        self._rms_max_amplitude: float = 0.0  # linear RMS at max window
        self._rms_max_dirty: bool = False    # True = needs recomputation
        # Loading state
        self._loading: bool = False
        # Mouse guide (crosshair)
        self._mouse_x: int = -1  # -1 = not hovering
        self._mouse_y: int = -1
        # Display mode
        self._display_mode: str = "waveform"  # "waveform" | "spectrogram"
        # Spectrogram data
        self._spec_db: np.ndarray | None = None  # (n_mels, n_frames) dB
        self._spec_image: QImage | None = None
        self._spec_cache_key: tuple = ()
        self._colormap: str = "magma"
        self._mel_view_min: float = _hz_to_mel(_SPEC_F_MIN)
        self._mel_view_max: float = _hz_to_mel(_SPEC_F_MAX)
        self._spec_n_fft: int = _SPEC_N_FFT
        self._spec_window: str = "hann"
        self._spec_db_floor: float = _SPEC_DB_FLOOR
        self._spec_db_ceil: float = 0.0
        self._spec_recompute_worker: SpectrogramRecomputeWorker | None = None
        # Cached RMS cumsums (computed once per track in background worker)
        self._rms_cumsums: list[np.ndarray] = []
        # Waveform display settings
        self._wf_antialias: bool = False
        self._wf_line_width: int = 1
        # Scroll inversion
        self._invert_h: bool = False
        self._invert_v: bool = False
        # Scroll throttle
        self._scroll_pending: bool = False
        self._scroll_timer: QTimer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(8)  # ~120 fps cap
        self._scroll_timer.timeout.connect(self._flush_scroll)
        self.setMinimumHeight(80)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def set_audio(self, audio_data: np.ndarray | None, samplerate: int):
        """Load audio data (numpy array, shape (samples,) or (samples, channels)).

        Stores contiguous per-channel arrays (no dtype conversion).
        Peak finding is deferred until markers are first painted.
        """
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
        # Mark peak as needing computation (deferred to first paint)
        self._peak_sample = -1
        self._peak_channel = -1
        self._peak_db = float('-inf')
        self._peak_amplitude = 0.0
        self._peak_dirty = bool(self._channels)
        self._rms_max_sample = -1
        self._rms_max_db = float('-inf')
        self._rms_max_dirty = False
        self._samplerate = samplerate
        self._cursor_sample = 0
        self._cursor_y_value = None
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
        self._rms_cumsums = []
        self._spec_db = None
        self._spec_image = None
        self._spec_cache_key = ()
        self._mel_view_min = _hz_to_mel(_SPEC_F_MIN)
        self._mel_view_max = _hz_to_mel(min(_SPEC_F_MAX, samplerate / 2.0))
        self.update()

    def set_loading(self, loading: bool):
        """Show or hide a 'Loading waveform…' placeholder."""
        self._loading = loading
        if loading:
            self._channels = []
            self._num_channels = 0
            self._total_samples = 0
            self._peaks_cache = []
            self._cached_view = (0, 0, 0)
        self.update()

    def set_precomputed(self, result: dict):
        """Apply pre-computed waveform data from a WaveformLoadWorker."""
        self._channels = result["channels"]
        self._num_channels = len(self._channels)
        self._total_samples = result["total_samples"]
        self._samplerate = result["samplerate"]
        self._peak_sample = result["peak_sample"]
        self._peak_channel = result["peak_channel"]
        self._peak_db = result["peak_db"]
        self._peak_amplitude = result["peak_amplitude"]
        self._peak_dirty = False
        self._rms_window_samples = result["rms_window_samples"]
        self._rms_max_sample = result["rms_max_sample"]
        self._rms_max_db = result["rms_max_db"]
        self._rms_max_amplitude = result["rms_max_amplitude"]
        self._rms_max_dirty = False
        self._cursor_sample = 0
        self._cursor_y_value = None
        self._view_start = 0
        self._view_end = self._total_samples
        self._vscale = 1.0
        self._peaks_cache = []
        self._cached_view = (0, 0, 0)
        self._rms_envelope = []
        self._rms_combined = []
        self._rms_cache_key = (0, 0, 0)
        self._rms_cumsums = result.get("rms_cumsums", [])
        self._spec_db = result.get("spec_db")
        self._spec_image = None
        self._spec_cache_key = ()
        self._mel_view_min = _hz_to_mel(_SPEC_F_MIN)
        self._mel_view_max = _hz_to_mel(min(_SPEC_F_MAX,
                                            self._samplerate / 2.0))
        self._loading = False
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

    @staticmethod
    def _peaks_for_view(ch_data, vs, ve, width):
        """Compute (mins, maxs) arrays for one channel over samples [vs:ve].

        Uses proportional bin edges (matching ``_sample_to_x`` mapping)
        via ``np.reduceat`` so marker positions align pixel-perfectly
        with the waveform envelope.
        """
        view_data = ch_data[vs:ve]
        n = len(view_data)
        if n == 0:
            return np.zeros(width, dtype=np.float64), np.zeros(width, dtype=np.float64)
        if n >= width:
            # Proportional bin edges — same mapping as _sample_to_x
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

    def _build_peaks(self, width: int):
        """Downsample audio to peak envelope for the given pixel width, per channel.

        Supports incremental updates on horizontal scroll: when the view
        shifts by a fraction, existing peak data is shifted and only the
        newly exposed bins are recomputed.
        """
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

        # Try incremental update: same width & view_len, shifted start
        old_w, old_vs, old_ve = self._cached_view
        can_inc = (
            self._peaks_cache
            and old_w == width
            and (old_ve - old_vs) == view_len
            and vs != old_vs
            and len(self._peaks_cache) == len(self._channels)
        )
        if can_inc:
            # How many bins shifted?
            shift_samples = vs - old_vs  # positive = scrolled right
            shift_bins = int(round(shift_samples * width / view_len))
            if 0 < abs(shift_bins) < width:
                new_cache = []
                for ch_idx, ch_data in enumerate(self._channels):
                    old_mins, old_maxs = self._peaks_cache[ch_idx]
                    mins = np.empty(width, dtype=np.float64)
                    maxs = np.empty(width, dtype=np.float64)
                    if shift_bins > 0:
                        # Scrolled right: keep left portion, compute right
                        keep = width - shift_bins
                        mins[:keep] = old_mins[shift_bins:]
                        maxs[:keep] = old_maxs[shift_bins:]
                        # Compute new bins [keep:width]
                        new_vs = vs + keep * view_len // width
                        new_ve = ve
                        new_w = width - keep
                        nm, nx = self._peaks_for_view(
                            ch_data, new_vs, new_ve, new_w)
                        mins[keep:] = nm
                        maxs[keep:] = nx
                    else:
                        # Scrolled left: keep right portion, compute left
                        sb = -shift_bins
                        keep = width - sb
                        mins[sb:] = old_mins[:keep]
                        maxs[sb:] = old_maxs[:keep]
                        # Compute new bins [0:sb]
                        new_vs = vs
                        new_ve = vs + sb * view_len // width
                        nm, nx = self._peaks_for_view(
                            ch_data, new_vs, new_ve, sb)
                        mins[:sb] = nm
                        maxs[:sb] = nx
                    new_cache.append((mins, maxs))
                self._peaks_cache = new_cache
                self._cached_view = cache_key
                return

        # Full recompute (zoom change, resize, or large jump)
        self._peaks_cache = []
        for ch_data in self._channels:
            mins, maxs = self._peaks_for_view(ch_data, vs, ve, width)
            self._peaks_cache.append((mins, maxs))
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

        # Dispatch to mode-specific painter
        if self._display_mode == "spectrogram":
            self._paint_spectrogram(painter, x0, draw_w, draw_h)
        else:
            self._paint_waveform(painter, x0, draw_w, draw_h)

        # --- Issue overlays (shared, drawn on top of waveform/spectrogram) ---
        self._draw_issue_overlays(painter, x0, draw_w, draw_h)

        # --- Time scale (shared) ---
        self._draw_time_scale(painter, x0, draw_w, draw_h)

        # Playback cursor — 2D crosshair (shared, spans all channels)
        if self._total_samples > 0:
            cursor_x = x0 + self._sample_to_x(self._cursor_sample, draw_w)
            if x0 <= cursor_x <= x0 + draw_w:
                painter.setPen(QPen(QColor("#ffffff"), 1))
                painter.drawLine(cursor_x, 0, cursor_x, int(draw_h))

                # Horizontal crosshair line at stored y value
                if self._cursor_y_value is not None:
                    cursor_y = -1
                    cursor_label = ""
                    if self._display_mode == "spectrogram":
                        mel_range = self._mel_view_max - self._mel_view_min
                        if mel_range > 0 and draw_h > 0:
                            frac = ((self._cursor_y_value - self._mel_view_min)
                                    / mel_range)
                            cursor_y = int(draw_h * (1.0 - frac))
                            freq = _mel_to_hz(self._cursor_y_value)
                            if freq >= 1000:
                                cursor_label = f"{freq / 1000:.1f} kHz"
                            else:
                                cursor_label = f"{freq:.0f} Hz"
                    else:
                        nch = self._num_channels
                        if nch > 0 and draw_h > 0:
                            lane_h = draw_h / nch
                            ch = min(self._cursor_y_channel, nch - 1)
                            mid_y = ch * lane_h + lane_h / 2.0
                            scale = (lane_h / 2.0) * 0.85 * self._vscale
                            cursor_y = int(mid_y - self._cursor_y_value * scale)
                            amp = abs(self._cursor_y_value)
                            if amp > 0:
                                cursor_label = f"{20.0 * np.log10(amp):.1f} dBFS"
                            else:
                                cursor_label = "-\u221e dBFS"

                    if 0 <= cursor_y <= int(draw_h):
                        h_pen = QPen(QColor(255, 255, 255, 80), 1, Qt.DotLine)
                        painter.setPen(h_pen)
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

        # --- Crosshair mouse guide (shared vertical, mode-specific readout) ---
        if self._mouse_y >= 0:
            mx = self._mouse_x
            my = self._mouse_y
            guide_color = QColor(200, 200, 200, 60)
            painter.setPen(QPen(guide_color, 1, Qt.DashLine))
            painter.drawLine(0, my, w, my)
            if mx >= 0:
                painter.drawLine(mx, 0, mx, int(draw_h))

                # Time label at top of vertical guide
                sample = self._x_to_sample(mx - x0, draw_w)
                if self._samplerate > 0:
                    secs = sample / self._samplerate
                    m = int(secs) // 60
                    s = secs - m * 60
                    time_label = f"{m}:{s:05.2f} ({sample:,})"
                    painter.setFont(QFont("Consolas", 7))
                    time_color = QColor(200, 200, 200, 180)
                    painter.setPen(time_color)
                    tfm = painter.fontMetrics()
                    ttw = tfm.horizontalAdvance(time_label)
                    lx = mx - ttw // 2
                    lx = max(x0, min(lx, x0 + draw_w - ttw))
                    painter.drawText(int(lx), tfm.ascent() + 2, time_label)

            if self._display_mode == "spectrogram":
                self._draw_freq_guide(painter, x0, draw_w, draw_h, my)
            else:
                nch = self._num_channels
                if nch > 0:
                    lane_h = draw_h / nch
                    self._draw_db_guide(painter, x0, draw_w, draw_h,
                                        nch, lane_h, my)

        painter.end()

    def _draw_db_guide(self, painter, x0, draw_w, draw_h, nch, lane_h, my):
        """Draw dBFS readout labels at mouse y position."""
        mouse_ch = int(my / lane_h) if lane_h > 0 else 0
        mouse_ch = max(0, min(mouse_ch, nch - 1))
        ch_y_off = mouse_ch * lane_h
        ch_mid_y = ch_y_off + lane_h / 2.0
        ch_scale = (lane_h / 2.0) * 0.85 * self._vscale

        if ch_scale > 0:
            amp = abs(ch_mid_y - my) / ch_scale
            if amp > 0:
                db_val = 20.0 * np.log10(amp)
                db_label = f"{db_val:.1f}"
            else:
                db_label = "-\u221e"
            painter.setFont(QFont("Consolas", 7))
            label_color = QColor(180, 180, 180, 120)
            painter.setPen(label_color)
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(db_label)
            painter.drawText(x0 - 5 - tw, int(ch_y_off) + fm.ascent() + 1,
                             db_label)
            painter.drawText(x0 + draw_w + 5, int(ch_y_off) + fm.ascent() + 1,
                             db_label)

    def _paint_waveform(self, painter, x0, draw_w, draw_h):
        """Paint the waveform display with channels, overlays, RMS, and markers."""
        # AA for waveform paths — user-configurable (default off for perf)
        painter.setRenderHint(QPainter.Antialiasing, self._wf_antialias)
        w = self.width()
        self._build_peaks(draw_w)
        if self._show_rms_lr or self._show_rms_avg:
            self._build_rms_envelope(draw_w)

        nch = self._num_channels
        lane_h = draw_h / nch

        # --- dB scale and grid lines ---
        self._draw_db_scale(painter, x0, draw_w, draw_h, nch, lane_h)

        # --- Draw waveforms ---
        for ch in range(nch):
            y_off = ch * lane_h
            mid_y = y_off + lane_h / 2.0
            scale = (lane_h / 2.0) * 0.85 * self._vscale

            lane_top = int(y_off)
            lane_bot = int(y_off + lane_h)
            painter.setClipRect(x0, lane_top, draw_w, lane_bot - lane_top)

            color = QColor(self._CHANNEL_COLORS[ch % len(self._CHANNEL_COLORS)])
            mins, maxs = self._peaks_cache[ch]

            # Build x/y arrays in numpy, then construct QPolygonF once
            n_pts = len(mins)
            xs = np.arange(n_pts, dtype=np.float64) + x0
            ys_top = mid_y - maxs * scale
            ys_bot = mid_y - mins * scale

            # Filled envelope: top L→R then bottom R→L
            env_x = np.concatenate([xs, xs[::-1]])
            env_y = np.concatenate([ys_top, ys_bot[::-1]])
            env_poly = QPolygonF([QPointF(env_x[i], env_y[i])
                                  for i in range(len(env_x))])

            grad = QLinearGradient(0, y_off, 0, y_off + lane_h)
            view_len = self._view_end - self._view_start
            spp = view_len / max(draw_w, 1)  # samples per pixel
            color_edge = QColor(color)
            color_edge.setAlpha(30)
            color_mid = QColor(color)
            color_mid.setAlpha(140)
            grad.setColorAt(0.0, color_edge)
            grad.setColorAt(0.5, color_mid)
            grad.setColorAt(1.0, color_edge)

            # Solid dark fill first to occlude grid lines behind
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(COLORS["bg"]))
            painter.drawPolygon(env_poly)
            # Gradient on top
            painter.setBrush(grad)
            painter.drawPolygon(env_poly)

            # Outline polylines — softer when zoomed out
            outline_alpha = max(100, min(200, int(200 - spp * 0.1)))
            outline = QColor(color)
            outline.setAlpha(outline_alpha)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(outline, self._wf_line_width))
            top_poly = QPolygonF([QPointF(xs[i], ys_top[i])
                                  for i in range(n_pts)])
            bot_poly = QPolygonF([QPointF(xs[i], ys_bot[i])
                                  for i in range(n_pts)])
            painter.drawPolyline(top_poly)
            painter.drawPolyline(bot_poly)

            center_color = QColor(COLORS["accent"])
            center_color.setAlpha(80)
            painter.setPen(QPen(center_color, 1, Qt.DotLine))
            painter.drawLine(x0, int(mid_y), x0 + draw_w, int(mid_y))

            painter.setClipping(False)

            if ch < nch - 1:
                sep_y = int(y_off + lane_h)
                painter.setPen(QPen(QColor("#555555"), 1))
                painter.drawLine(0, sep_y, w, sep_y)

        # --- RMS overlay ---
        if (self._show_rms_lr or self._show_rms_avg) and self._rms_envelope:
            lw = self._wf_line_width
            ch_pen = QPen(QColor(255, 220, 60, 200), float(lw))
            comb_pen = QPen(QColor(255, 100, 40, 220), float(lw) * 1.5)
            for ch in range(nch):
                if ch >= len(self._rms_envelope):
                    break
                y_off = ch * lane_h
                mid_y = y_off + lane_h / 2.0
                scale = (lane_h / 2.0) * 0.85 * self._vscale
                lane_top = int(y_off)
                painter.setClipRect(x0, lane_top, draw_w, int(lane_h))
                painter.setBrush(Qt.NoBrush)

                if self._show_rms_lr:
                    ch_env = self._rms_envelope[ch]
                    n_rms = len(ch_env)
                    rxs = np.arange(n_rms, dtype=np.float64) + x0
                    rys = mid_y - ch_env * scale
                    painter.setPen(ch_pen)
                    painter.drawPolyline(QPolygonF(
                        [QPointF(rxs[i], rys[i]) for i in range(n_rms)]))

                if self._show_rms_avg and len(self._rms_combined) > 0:
                    n_comb = len(self._rms_combined)
                    cxs = np.arange(n_comb, dtype=np.float64) + x0
                    cys = mid_y - self._rms_combined * scale
                    painter.setPen(comb_pen)
                    painter.drawPolyline(QPolygonF(
                        [QPointF(cxs[i], cys[i]) for i in range(n_comb)]))

                painter.setClipping(False)

        # Re-enable AA for markers and text
        painter.setRenderHint(QPainter.Antialiasing, True)

        # --- Peak and RMS max markers ---
        if self._show_markers:
            self._draw_markers(painter, x0, draw_w, draw_h, nch, lane_h)

    def _paint_spectrogram(self, painter, x0, draw_w, draw_h):
        """Paint the spectrogram display with mel-scale frequency axis."""
        if self._spec_db is None:
            painter.setPen(QPen(QColor(COLORS["dim"])))
            painter.drawText(x0, int(draw_h / 2),
                             "Spectrogram not available (audio too short)")
            return

        # Build or reuse cached spectrogram image
        cache_key = (self._view_start, self._view_end, draw_w,
                     int(draw_h), self._colormap,
                     self._mel_view_min, self._mel_view_max,
                     self._spec_db_floor, self._spec_db_ceil)
        if self._spec_cache_key != cache_key or self._spec_image is None:
            self._build_spec_image(draw_w, int(draw_h))
            self._spec_cache_key = cache_key

        if self._spec_image is not None:
            painter.drawImage(x0, 0, self._spec_image)

        # Frequency scale
        self._draw_freq_scale(painter, x0, draw_w, int(draw_h))

    def _build_spec_image(self, width: int, height: int):
        """Render the visible portion of the spectrogram to a cached QImage."""
        spec = self._spec_db
        if spec is None or width <= 0 or height <= 0:
            self._spec_image = None
            return

        n_mels, n_frames = spec.shape

        # Map view range to spectrogram frame indices
        frame_start = max(0, self._view_start * n_frames // self._total_samples)
        frame_end = min(n_frames, self._view_end * n_frames // self._total_samples)
        if frame_end <= frame_start:
            frame_end = min(frame_start + 1, n_frames)

        # Slice mel rows by frequency view range
        mel_full_min = _hz_to_mel(_SPEC_F_MIN)
        mel_full_max = _hz_to_mel(min(_SPEC_F_MAX, self._samplerate / 2.0))
        mel_full_range = mel_full_max - mel_full_min
        if mel_full_range <= 0:
            self._spec_image = None
            return
        row_lo = int((self._mel_view_min - mel_full_min)
                     / mel_full_range * (n_mels - 1))
        row_hi = int(np.ceil((self._mel_view_max - mel_full_min)
                             / mel_full_range * (n_mels - 1)))
        row_lo = max(0, min(row_lo, n_mels - 1))
        row_hi = max(row_lo + 1, min(row_hi + 1, n_mels))

        view_spec = spec[row_lo:row_hi, frame_start:frame_end]

        # Normalize to 0..1 (clamp to dB floor..ceiling)
        db_floor = self._spec_db_floor
        db_ceil = self._spec_db_ceil
        norm = np.clip((view_spec - db_floor) / max(db_ceil - db_floor, 1.0),
                       0.0, 1.0)

        # Flip vertically (low freq at bottom)
        norm = norm[::-1, :]

        # Apply colormap at native spectrogram resolution
        lut = SPECTROGRAM_COLORMAPS.get(self._colormap)
        if lut is None:
            lut = SPECTROGRAM_COLORMAPS.get("magma", np.zeros((256, 4), np.uint8))
        indices = (norm * 255).astype(np.uint8)
        rgba = lut[indices]  # (n_mels, view_frames, 4)

        # Build QImage at native resolution, then smooth-scale to display size
        nat_h, nat_w = rgba.shape[:2]
        rgba_c = np.ascontiguousarray(rgba)
        self._spec_image_data = rgba_c  # prevent garbage collection
        native_img = QImage(
            rgba_c.data, nat_w, nat_h, nat_w * 4,
            QImage.Format.Format_RGBA8888,
        )
        self._spec_image = native_img.scaled(
            width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation,
        )

    def _draw_freq_scale(self, painter, x0, draw_w, draw_h):
        """Draw frequency scale on left/right margins for spectrogram mode."""
        if self._spec_db is None or draw_h <= 0:
            return

        _FREQ_TICKS = [50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]
        _MIN_TICK_SPACING = 20

        mel_min = self._mel_view_min
        mel_max = self._mel_view_max
        mel_range = mel_max - mel_min
        if mel_range <= 0:
            return

        scale_font = QFont("Consolas", 7)
        painter.setFont(scale_font)
        fm = painter.fontMetrics()

        label_color = QColor(COLORS["dim"])
        tick_pen = QPen(label_color, 1)

        grid_color = QColor(COLORS["accent"])
        grid_color.setAlpha(35)
        grid_pen = QPen(grid_color, 1, Qt.DotLine)

        text_h = fm.height()
        used_ys: list[int] = []
        for freq in _FREQ_TICKS:
            mel = _hz_to_mel(freq)
            if mel < mel_min or mel > mel_max:
                continue
            frac = (mel - mel_min) / mel_range
            y = int(draw_h * (1.0 - frac))  # low freq at bottom
            if y < text_h or y > draw_h - text_h:
                continue

            too_close = any(abs(uy - y) < _MIN_TICK_SPACING for uy in used_ys)
            if too_close:
                continue
            used_ys.append(y)

            # Label
            if freq >= 1000:
                label = f"{freq // 1000}k"
            else:
                label = str(freq)

            # Grid line
            painter.setPen(grid_pen)
            painter.drawLine(x0, y, x0 + draw_w, y)

            # Left margin
            painter.setPen(tick_pen)
            tw = fm.horizontalAdvance(label)
            painter.drawText(x0 - 5 - tw, y + fm.ascent() // 2, label)

            # Right margin
            painter.drawText(x0 + draw_w + 5, y + fm.ascent() // 2, label)

            # Tick marks
            painter.drawLine(x0 - 3, y, x0, y)
            painter.drawLine(x0 + draw_w, y, x0 + draw_w + 3, y)

    def _draw_freq_guide(self, painter, x0, draw_w, draw_h, my):
        """Draw frequency readout at mouse position in spectrogram mode."""
        if self._spec_db is None or draw_h <= 0:
            return

        mel_min = self._mel_view_min
        mel_max = self._mel_view_max
        mel_range = mel_max - mel_min
        if mel_range <= 0:
            return

        frac = 1.0 - (my / draw_h)
        frac = max(0.0, min(frac, 1.0))
        mel = mel_min + frac * mel_range
        freq = _mel_to_hz(mel)

        if freq >= 1000:
            freq_label = f"{freq / 1000:.1f} kHz"
        else:
            freq_label = f"{freq:.0f} Hz"

        painter.setFont(QFont("Consolas", 7))
        label_color = QColor(180, 180, 180, 120)
        painter.setPen(label_color)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(freq_label)
        label_y = int(my) + fm.ascent() // 2
        # Draw inside the waveform area (labels are too wide for the margin)
        painter.drawText(x0 + 4, label_y, freq_label)
        painter.drawText(x0 + draw_w - tw - 4, label_y, freq_label)

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

    def _draw_issue_overlays(self, painter, x0, draw_w, draw_h):
        """Draw detector issue overlays. Works in both display modes.

        In waveform mode, overlays span full height or per-channel lanes.
        In spectrogram mode, overlays with frequency bounds are mapped to
        the visible mel range; overlays without bounds span full height.
        """
        if not self._issues or not self._enabled_overlays:
            return

        nch = self._num_channels
        lane_h = draw_h / max(nch, 1)
        is_spec = self._display_mode == "spectrogram"

        # Precompute mel range for spectrogram frequency mapping
        if is_spec:
            mel_range = self._mel_view_max - self._mel_view_min
        else:
            mel_range = 0.0

        for issue in self._issues:
            if issue.label not in self._enabled_overlays:
                continue
            sev_val = issue.severity.value if hasattr(issue.severity, "value") else str(issue.severity)
            fill = self._SEVERITY_OVERLAY.get(sev_val, QColor(255, 255, 255, 30))
            border = self._SEVERITY_BORDER.get(sev_val, QColor(255, 255, 255, 60))

            # Horizontal bounds (time) — same in both modes
            ix1 = x0 + self._sample_to_x(issue.sample_start, draw_w)
            ix2 = (x0 + self._sample_to_x(issue.sample_end + 1, draw_w)
                   if issue.sample_end is not None else ix1)
            rx = ix1
            rw = max(ix2 - ix1, 2)

            # Vertical bounds
            if is_spec and issue.freq_min_hz is not None and issue.freq_max_hz is not None and mel_range > 0:
                # Map frequency bounds to pixel y via mel scale
                mel_lo = _hz_to_mel(issue.freq_min_hz)
                mel_hi = _hz_to_mel(issue.freq_max_hz)
                # y=0 is top (high freq), y=draw_h is bottom (low freq)
                frac_top = (mel_hi - self._mel_view_min) / mel_range
                frac_bot = (mel_lo - self._mel_view_min) / mel_range
                y_top = int(draw_h * (1.0 - frac_top))
                y_bot = int(draw_h * (1.0 - frac_bot))
                # Clamp to visible area
                y_top = max(0, min(y_top, int(draw_h)))
                y_bot = max(0, min(y_bot, int(draw_h)))
                if y_top >= y_bot:
                    continue  # entirely outside visible freq range
                ry = y_top
                rh = y_bot - y_top
            elif not is_spec:
                # Waveform mode: per-channel or full height
                if issue.channel is None:
                    ry = 0
                    rh = int(draw_h)
                else:
                    ch = issue.channel
                    if ch < nch:
                        ry = int(ch * lane_h)
                        rh = int(lane_h)
                    else:
                        continue
            else:
                # Spectrogram mode, no frequency bounds — full height
                ry = 0
                rh = int(draw_h)

            painter.fillRect(rx, ry, rw, rh, fill)
            painter.setPen(QPen(border, 1))
            painter.drawRect(rx, ry, rw, rh)

    def _draw_time_scale(self, painter, x0, draw_w, draw_h):
        """Draw horizontal time axis with adaptive tick labels below the waveform."""
        if self._samplerate <= 0 or draw_w <= 0:
            return

        view_start_sec = self._view_start / self._samplerate
        view_end_sec = self._view_end / self._samplerate
        visible_dur = view_end_sec - view_start_sec
        if visible_dur <= 0:
            return

        _NICE_INTERVALS = [
            0.001, 0.002, 0.005,
            0.01, 0.02, 0.05,
            0.1, 0.2, 0.5,
            1, 2, 5, 10, 15, 30,
            60, 120, 300, 600, 1800, 3600,
        ]
        _MIN_TICK_PX = 60

        # Pick smallest nice interval that keeps ticks ≥ _MIN_TICK_PX apart
        interval = _NICE_INTERVALS[-1]
        for ni in _NICE_INTERVALS:
            px_per_tick = ni / visible_dur * draw_w
            if px_per_tick >= _MIN_TICK_PX:
                interval = ni
                break

        # Determine label format based on tick interval
        if interval >= 1.0:
            def _fmt(t):
                m = int(t) // 60
                s = int(t) % 60
                return f"{m}:{s:02d}"
        elif interval >= 0.1:
            def _fmt(t):
                m = int(t) // 60
                s = t - m * 60
                return f"{m}:{s:04.1f}"
        elif interval >= 0.01:
            def _fmt(t):
                m = int(t) // 60
                s = t - m * 60
                return f"{m}:{s:05.2f}"
        else:
            def _fmt(t):
                m = int(t) // 60
                s = t - m * 60
                return f"{m}:{s:06.3f}"

        scale_font = QFont("Consolas", 7)
        painter.setFont(scale_font)
        fm = painter.fontMetrics()

        label_color = QColor(COLORS["dim"])
        tick_pen = QPen(label_color, 1)

        grid_color = QColor(COLORS["accent"])
        grid_color.setAlpha(25)
        grid_pen = QPen(grid_color, 1, Qt.DotLine)

        # First tick at or after view_start, aligned to interval
        first_tick = (int(view_start_sec / interval) + 1) * interval
        if abs(view_start_sec / interval - round(view_start_sec / interval)) < 1e-9:
            first_tick = round(view_start_sec / interval) * interval

        t = first_tick
        bottom_y = int(draw_h)
        while t <= view_end_sec + interval * 0.01:
            frac = (t - view_start_sec) / visible_dur
            px = x0 + int(frac * draw_w)

            if px < x0 or px > x0 + draw_w:
                t += interval
                continue

            # Vertical grid line through waveform area
            painter.setPen(grid_pen)
            painter.drawLine(px, 0, px, bottom_y)

            # Tick mark
            painter.setPen(tick_pen)
            painter.drawLine(px, bottom_y, px, bottom_y + 4)

            # Label
            label = _fmt(t)
            tw = fm.horizontalAdvance(label)
            lx = px - tw // 2
            ly = bottom_y + 4 + fm.ascent()
            painter.drawText(int(lx), int(ly), label)

            t += interval

    def _ensure_peak_computed(self):
        """Lazily compute peak sample position on first demand."""
        if not self._peak_dirty:
            return
        self._peak_dirty = False
        if not self._channels:
            return
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

    def _ensure_rms_max_computed(self):
        """Lazily compute RMS max sample position on first demand."""
        if not self._rms_max_dirty:
            return
        self._rms_max_dirty = False
        self._compute_rms_max_sample()

    def _draw_markers(self, painter, x0, draw_w, h, nch, lane_h):
        """Draw peak and max RMS marker vertical lines."""
        self._ensure_peak_computed()
        self._ensure_rms_max_computed()

        marker_font = QFont("Consolas", 7, QFont.Bold)
        _CROSS_HALF = 6  # half-width of horizontal crosshair

        # Peak marker (magenta, solid)
        if self._peak_sample >= 0:
            px = x0 + self._sample_to_x(self._peak_sample, draw_w)
            if x0 <= px <= x0 + draw_w:
                peak_color = QColor(180, 50, 220, 250)
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
                rms_color = QColor(40, 160, 220, 250)
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
        self._spec_image = None
        self._spec_cache_key = ()
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        self.setFocus()  # grab keyboard focus for R/T shortcuts
        if self._total_samples > 0 and event.button() == Qt.LeftButton:
            x0, draw_w = self._draw_area()
            h = self.height()
            draw_h = h - self._MARGIN_BOTTOM
            my = event.position().y()
            sample = self._x_to_sample(event.position().x() - x0, draw_w)
            self._cursor_sample = sample

            # Compute semantic y value
            if self._display_mode == "spectrogram":
                mel_range = self._mel_view_max - self._mel_view_min
                if draw_h > 0 and mel_range > 0:
                    frac = max(0.0, min(1.0 - my / draw_h, 1.0))
                    self._cursor_y_value = self._mel_view_min + frac * mel_range
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
        self.update()  # repaint for crosshair guide

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

        # Marker tooltips (waveform mode only)
        if self._display_mode == "waveform":
            _MARKER_PX_TOL = 6
            if self._show_markers and self._peak_sample >= 0:
                peak_px = x0 + self._sample_to_x(self._peak_sample, draw_w)
                if abs(mx - peak_px) <= _MARKER_PX_TOL:
                    tips.append(f"Peak: {self._peak_db:.1f} dBFS")
            if self._show_markers and self._rms_max_sample >= 0:
                rms_px = x0 + self._sample_to_x(self._rms_max_sample, draw_w)
                if abs(mx - rms_px) <= _MARKER_PX_TOL:
                    tips.append(f"Max RMS: {self._rms_max_db:.1f} dBFS")

        # Issue tooltips (both modes, only for enabled overlays)
        for issue in self._issues:
            if issue.label not in self._enabled_overlays:
                continue
            s_start = issue.sample_start
            s_end = issue.sample_end if issue.sample_end is not None else s_start
            # Expand narrow regions by tolerance for easier hit-testing
            hit_start = s_start - tolerance
            hit_end = s_end + tolerance
            if sample < hit_start or sample > hit_end:
                continue
            # In waveform mode check channel match; in spectrogram mode skip channel check
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
        """Mouse-wheel navigation.

        Ctrl + wheel            — horizontal zoom (centered on pointer)
        Ctrl + Shift + wheel    — vertical zoom
        Shift + Alt + wheel     — scroll up / down (frequency pan, spectrogram)
        Shift + wheel           — scroll left / right
        """
        if self._total_samples <= 0:
            event.ignore()
            return

        mods = event.modifiers()
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.angleDelta().x()
        if delta == 0:
            event.ignore()
            return

        ctrl = bool(mods & Qt.ControlModifier)
        shift = bool(mods & Qt.ShiftModifier)
        alt = bool(mods & Qt.AltModifier)

        if ctrl and shift:
            # ── Vertical zoom ─────────────────────────────────────────
            if self._display_mode == "spectrogram":
                # Frequency zoom anchored at mouse cursor
                draw_h = self.height() - self._MARGIN_BOTTOM
                my = event.position().y()
                mel_range = self._mel_view_max - self._mel_view_min
                anchor_mel = None
                if draw_h > 0 and mel_range > 0:
                    frac = max(0.0, min(1.0 - my / draw_h, 1.0))
                    anchor_mel = self._mel_view_min + frac * mel_range
                factor = 2 / 3 if delta > 0 else 3 / 2
                self._freq_zoom(factor, anchor_mel)
            else:
                if delta > 0:
                    self._vscale = min(self._vscale * 1.25, 20.0)
                else:
                    self._vscale = max(self._vscale / 1.25, 0.1)
            self.update()
            event.accept()

        elif ctrl:
            # ── Horizontal zoom (centered on pointer) ─────────────────
            x0, draw_w = self._draw_area()
            mx = event.position().x()
            frac = max(0.0, min((mx - x0) / max(draw_w, 1), 1.0))
            anchor_sample = self._x_to_sample(mx - x0, draw_w)
            anchor_sample = max(self._view_start,
                                min(anchor_sample, self._view_end))

            view_len = self._view_end - self._view_start
            if delta > 0:
                new_len = max(view_len * 2 // 3, 100)
            else:
                new_len = min(view_len * 3 // 2, self._total_samples)

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
            self._invalidate_peaks()
            self.update()
            event.accept()

        elif shift and alt:
            # ── Scroll up / down (frequency pan, spectrogram only) ───
            if self._display_mode == "spectrogram":
                mel_range = self._mel_view_max - self._mel_view_min
                mel_full_min = _hz_to_mel(_SPEC_F_MIN)
                mel_full_max = _hz_to_mel(min(_SPEC_F_MAX, self._samplerate / 2.0))
                scroll = mel_range / 8
                if delta < 0:
                    scroll = -scroll
                if self._invert_v:
                    scroll = -scroll
                new_min = self._mel_view_min + scroll
                new_max = self._mel_view_max + scroll
                if new_min < mel_full_min:
                    new_min = mel_full_min
                    new_max = new_min + mel_range
                if new_max > mel_full_max:
                    new_max = mel_full_max
                    new_min = new_max - mel_range
                self._mel_view_min = max(new_min, mel_full_min)
                self._mel_view_max = min(new_max, mel_full_max)
                self._spec_image = None
                self._spec_cache_key = ()
                self.update()
            event.accept()

        elif shift:
            # ── Scroll left / right ───────────────────────────────────
            view_len = self._view_end - self._view_start
            scroll_amount = max(1, view_len // 8)
            if delta < 0:
                scroll_amount = -scroll_amount  # scroll left
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
            self._invalidate_rms_only()
            if not self._scroll_pending:
                self._scroll_pending = True
                self._scroll_timer.start()
            event.accept()

        else:
            event.ignore()

    def _flush_scroll(self):
        """Coalesce rapid scroll events into a single repaint."""
        self._scroll_pending = False
        self.update()

    def keyPressEvent(self, event):
        """DAW keyboard shortcuts: R = zoom in, T = zoom out.

        When the mouse is hovering over the waveform, zoom is centered on
        the mouse guide position.  Otherwise falls back to cursor position.
        """
        key = event.key()
        if key == Qt.Key_R:
            self._zoom_at_guide(zoom_in=True)
        elif key == Qt.Key_T:
            self._zoom_at_guide(zoom_in=False)
        else:
            super().keyPressEvent(event)

    def _zoom_at_guide(self, zoom_in: bool):
        """Zoom centered on mouse guide position (or cursor if not hovering)."""
        if self._total_samples <= 0:
            return
        view_len = self._view_end - self._view_start

        x0, draw_w = self._draw_area()
        if self._mouse_x >= 0:
            # Mouse is hovering — use guide position
            frac = max(0.0, min((self._mouse_x - x0) / max(draw_w, 1), 1.0))
            anchor = self._x_to_sample(self._mouse_x - x0, draw_w)
            anchor = max(self._view_start, min(anchor, self._view_end))
        else:
            # Not hovering — fall back to cursor
            anchor = max(self._view_start, min(self._cursor_sample, self._view_end))
            frac = (anchor - self._view_start) / max(view_len, 1)

        if zoom_in:
            new_len = max(view_len * 2 // 3, 100)
        else:
            new_len = min(view_len * 3 // 2, self._total_samples)

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
        self._invalidate_peaks()
        self.update()

    # ── Zoom / vertical-scale public API ──────────────────────────────────

    def _invalidate_peaks(self):
        self._peaks_cache = []
        self._cached_view = (0, 0, 0)
        self._rms_envelope = []
        self._rms_combined = []
        self._rms_cache_key = (0, 0, 0)

    def _invalidate_rms_only(self):
        """Invalidate RMS envelope cache but keep peaks for incremental updates."""
        self._rms_envelope = []
        self._rms_combined = []
        self._rms_cache_key = (0, 0, 0)

    def zoom_fit(self):
        """Reset horizontal zoom and vertical scale to show the entire file."""
        self._view_start = 0
        self._view_end = self._total_samples
        self._vscale = 1.0
        self._mel_view_min = _hz_to_mel(_SPEC_F_MIN)
        self._mel_view_max = _hz_to_mel(min(_SPEC_F_MAX,
                                            self._samplerate / 2.0))
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
        """Increase vertical amplitude scale / zoom freq in spectrogram."""
        if self._display_mode == "spectrogram":
            anchor = self._cursor_y_value if self._cursor_y_value is not None else None
            self._freq_zoom(2 / 3, anchor)
        else:
            self._vscale = min(self._vscale * 1.5, 20.0)
        self.update()

    def scale_down(self):
        """Decrease vertical amplitude scale / zoom freq out spectrogram."""
        if self._display_mode == "spectrogram":
            anchor = self._cursor_y_value if self._cursor_y_value is not None else None
            self._freq_zoom(3 / 2, anchor)
        else:
            self._vscale = max(self._vscale / 1.5, 0.1)
        self.update()

    def _freq_zoom(self, factor: float, anchor_mel: float | None = None):
        """Zoom the mel frequency range by *factor* around *anchor_mel*.

        If anchor_mel is None, zoom around the view center.
        """
        mel_range = self._mel_view_max - self._mel_view_min
        mel_full_min = _hz_to_mel(_SPEC_F_MIN)
        mel_full_max = _hz_to_mel(min(_SPEC_F_MAX, self._samplerate / 2.0))
        if anchor_mel is not None:
            anchor = max(self._mel_view_min, min(anchor_mel, self._mel_view_max))
            frac = ((anchor - self._mel_view_min) / mel_range
                    if mel_range > 0 else 0.5)
        else:
            anchor = (self._mel_view_min + self._mel_view_max) / 2.0
            frac = 0.5
        new_range = mel_range * factor
        new_range = max(new_range, 50.0)
        new_range = min(new_range, mel_full_max - mel_full_min)
        new_min = anchor - frac * new_range
        new_max = anchor + (1.0 - frac) * new_range
        if new_min < mel_full_min:
            new_min = mel_full_min
            new_max = new_min + new_range
        if new_max > mel_full_max:
            new_max = mel_full_max
            new_min = new_max - new_range
        self._mel_view_min = max(new_min, mel_full_min)
        self._mel_view_max = min(new_max, mel_full_max)
        self._spec_image = None
        self._spec_cache_key = ()

    # ── RMS overlay ───────────────────────────────────────────────────────

    def set_rms_data(self, window_samples: int):
        """Set the RMS window size.  Per-channel envelopes are computed
        on demand from the already-loaded channel data."""
        self._rms_window_samples = max(window_samples, 0)
        self._rms_envelope = []
        self._rms_cache_key = (0, 0, 0)
        # Defer RMS max computation to first paint
        self._rms_max_sample = -1
        self._rms_max_db = float('-inf')
        self._rms_max_amplitude = 0.0
        self._rms_max_dirty = bool(self._channels and window_samples > 0)
        self.update()

    def toggle_markers(self, on: bool):
        """Enable or disable the peak and RMS max markers."""
        self._show_markers = on
        self.update()

    def toggle_rms_lr(self, on: bool):
        """Enable or disable the per-channel RMS overlay."""
        self._show_rms_lr = on
        self.update()

    def toggle_rms_avg(self, on: bool):
        """Enable or disable the combined (average) RMS overlay."""
        self._show_rms_avg = on
        self.update()

    def set_enabled_overlays(self, labels: set[str]):
        """Set which detector issue overlays are visible by label."""
        self._enabled_overlays = set(labels)
        self.update()

    def set_display_mode(self, mode: str):
        """Switch between 'waveform' and 'spectrogram' display modes."""
        if mode not in ("waveform", "spectrogram"):
            return
        self._display_mode = mode
        self._spec_image = None
        self._spec_cache_key = ()
        self.update()

    def set_invert_scroll(self, mode: str):
        """Set scroll inversion mode: 'default', 'horizontal', 'vertical', 'both'."""
        self._invert_h = mode in ("horizontal", "both")
        self._invert_v = mode in ("vertical", "both")

    def set_wf_antialias(self, enabled: bool):
        """Enable or disable anti-aliased waveform lines."""
        self._wf_antialias = enabled
        self.update()

    def set_wf_line_width(self, width: int):
        """Set waveform outline / RMS line width in pixels (1 or 2)."""
        self._wf_line_width = max(1, min(width, 3))
        self.update()

    def set_colormap(self, name: str):
        """Set the spectrogram colormap by name."""
        if name not in SPECTROGRAM_COLORMAPS:
            return
        self._colormap = name
        self._spec_image = None
        self._spec_cache_key = ()
        self.update()

    def set_spec_fft(self, n_fft: int):
        """Change the FFT size and recompute the spectrogram."""
        if n_fft == self._spec_n_fft:
            return
        self._spec_n_fft = n_fft
        self._recompute_spectrogram()

    def set_spec_window(self, window: str):
        """Change the FFT window function and recompute the spectrogram."""
        if window == self._spec_window:
            return
        self._spec_window = window
        self._recompute_spectrogram()

    def set_spec_db_floor(self, val: float):
        """Change the dB floor for spectrogram normalization."""
        if val == self._spec_db_floor:
            return
        self._spec_db_floor = val
        self._spec_image = None
        self._spec_cache_key = ()
        self.update()

    def set_spec_db_ceil(self, val: float):
        """Change the dB ceiling for spectrogram normalization."""
        if val == self._spec_db_ceil:
            return
        self._spec_db_ceil = val
        self._spec_image = None
        self._spec_cache_key = ()
        self.update()

    def _recompute_spectrogram(self):
        """Launch a background thread to recompute the mel spectrogram."""
        if not self._channels:
            return
        # Cancel any in-flight spectrogram worker
        if self._spec_recompute_worker is not None:
            self._spec_recompute_worker.cancel()
            self._spec_recompute_worker.finished.disconnect()
            self._spec_recompute_worker = None
        self._spec_db = None
        self._spec_image = None
        self._spec_cache_key = ()
        self.update()
        worker = SpectrogramRecomputeWorker(
            self._channels, self._samplerate,
            n_fft=self._spec_n_fft, window=self._spec_window,
            parent=self,
        )
        worker.finished.connect(self._on_spec_recomputed)
        self._spec_recompute_worker = worker
        worker.start()

    def _on_spec_recomputed(self, spec_db):
        """Slot called when the spectrogram recompute finishes."""
        self._spec_db = spec_db
        self._spec_image = None
        self._spec_cache_key = ()
        self._spec_recompute_worker = None
        self.update()

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

        Uses pre-cached cumsum arrays (computed once per track by the
        background worker) to derive sliding-window RMS, then downsamples
        to pixel resolution.

        Results:
            ``_rms_envelope``  – list (per channel) of np.ndarray
            ``_rms_combined``  – np.ndarray (avg across channels)
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

        # Compute window-means only for the view-relevant slice of the
        # cumsum — O(view_length + 2*win) instead of O(total_samples).
        have_cumsums = len(self._rms_cumsums) == len(self._channels)

        ch_wms: list[np.ndarray] = []
        wm_offset = 0  # global wm index of ch_wms[][0]
        for ch_idx, ch_data in enumerate(self._channels):
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
            # View-local slice only
            wm_lo = max(0, vs - half_win - win)
            wm_hi = min(n_wm_total, ve + half_win + win)
            wm_offset = wm_lo
            ch_wms.append(
                (cs[wm_lo + win : wm_hi + win] - cs[wm_lo : wm_hi]) / win)

        # Combined (average across channels) — already view-local sized
        min_len = min(len(wm) for wm in ch_wms)
        if min_len > 1 and len(ch_wms) > 1:
            combined_wm = np.mean(
                np.column_stack([wm[:min_len] for wm in ch_wms]), axis=1)
        elif ch_wms:
            combined_wm = ch_wms[0][:min_len].copy()
        else:
            combined_wm = np.zeros(1, dtype=np.float64)

        def _downsample(wm: np.ndarray, offset: int) -> np.ndarray:
            """Downsample a view-local wm slice to *width* pixels.

            *offset* is the global wm index of wm[0].
            """
            n_wm = len(wm)
            if n_wm == 0:
                return np.zeros(width)

            # Map pixel bins to global wm indices, then to local
            pixel_edges = np.arange(width + 1)
            s_edges = vs + pixel_edges * view_len // width
            global_wm = np.clip(s_edges - half_win, 0, offset + n_wm)
            local_wm = np.clip(global_wm - offset, 0, n_wm)

            first = int(local_wm[0])
            last = int(local_wm[-1])
            last = max(last, first + 1)
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
