"""Waveform background computation: colormaps, mel spectrogram, load workers."""

from __future__ import annotations

import threading

import numpy as np

from PySide6.QtCore import QThread, Signal
from scipy.signal import stft as scipy_stft


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
# Spectrogram computation (used by background workers)
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


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

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
        if data is None or data.size == 0:
            return
        if data.ndim == 1:
            channels = [np.ascontiguousarray(data)]
        else:
            channels = [
                np.ascontiguousarray(data[:, ch])
                for ch in range(data.shape[1])
            ]
        if not channels:
            return
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
