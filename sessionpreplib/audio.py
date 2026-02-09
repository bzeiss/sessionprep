from __future__ import annotations

import os
from typing import Any

import numpy as np
import soundfile as sf

from .models import TrackContext


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

AES17_OFFSET = 3.0103  # dB offset: 20 * log10(sqrt(2))


def db_to_linear(db: float) -> float:
    return 10 ** (db / 20.0)


def linear_to_db(linear: float) -> float:
    if linear <= 0:
        return float(-np.inf)
    return float(20 * np.log10(linear))


def dbfs_offset(config: dict) -> float:
    """Return the dBFS offset for the configured convention.

    Standard → 0.0; AES17 → +3.0103 dB.
    """
    return AES17_OFFSET if config.get("dbfs_convention") == "aes17" else 0.0


def format_duration(samples: int, samplerate: int) -> str:
    if samplerate <= 0:
        return "00:00.000"
    seconds = samples / samplerate
    m = int(seconds // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{m:02d}:{s:02d}.{ms:03d}"


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

_SUBTYPE_MAP = {
    'PCM_16': '16-bit',
    'PCM_24': '24-bit',
    'PCM_32': '32-bit',
    'FLOAT': '32-bit Float',
    'DOUBLE': '64-bit Float',
}


def load_track(filepath: str) -> TrackContext:
    """Read a WAV file and return a fully populated TrackContext."""
    info = sf.info(filepath)
    data, samplerate = sf.read(filepath, dtype='float64')
    channels = 1 if data.ndim == 1 else data.shape[1]
    return TrackContext(
        filename=os.path.basename(filepath),
        filepath=filepath,
        audio_data=data,
        samplerate=samplerate,
        channels=channels,
        total_samples=info.frames,
        bitdepth=_SUBTYPE_MAP.get(info.subtype, info.subtype),
        subtype=info.subtype,
        duration_sec=info.duration,
    )


def write_track(track: TrackContext, output_path: str) -> None:
    """Write track audio_data to WAV, preserving original subtype."""
    sf.write(output_path, track.audio_data, track.samplerate, subtype=track.subtype)


# ---------------------------------------------------------------------------
# Cached helpers — expensive computations stored on TrackContext._cache
# ---------------------------------------------------------------------------

def get_peak(track: TrackContext) -> float:
    """Peak linear amplitude. Cached."""
    if "peak_linear" not in track._cache:
        if track.audio_data is not None and track.audio_data.size > 0:
            track._cache["peak_linear"] = float(np.max(np.abs(track.audio_data)))
        else:
            track._cache["peak_linear"] = 0.0
    return track._cache["peak_linear"]


def get_peak_db(track: TrackContext) -> float:
    """Peak in dBFS. Cached."""
    if "peak_db" not in track._cache:
        track._cache["peak_db"] = linear_to_db(get_peak(track))
    return track._cache["peak_db"]


def is_silent(track: TrackContext) -> bool:
    """True if the file is absolute silence (peak == 0)."""
    return get_peak(track) == 0.0


def get_rms_window_means(
    track: TrackContext,
    window_ms: int,
    stereo_mode: str,
) -> np.ndarray:
    """
    Momentary RMS window means via sliding-window cumsum.
    Cached on track._cache.
    """
    key = f"rms_window_means_{window_ms}_{stereo_mode}"
    if key not in track._cache:
        data = track.audio_data
        samplerate = track.samplerate

        if data.ndim > 1 and data.shape[1] > 1:
            if stereo_mode == "sum":
                squared = np.sum(data.astype(np.float64) ** 2, axis=1)
            else:  # avg
                squared = np.mean(data.astype(np.float64) ** 2, axis=1)
        else:
            flat = data.flatten() if data.ndim > 1 else data
            squared = flat.astype(np.float64) ** 2

        window_samples = max(1, int((window_ms / 1000) * samplerate))

        if len(squared) <= window_samples:
            window_means = np.array([np.mean(squared)], dtype=np.float64)
        else:
            cumsum = np.cumsum(squared, dtype=np.float64)
            cumsum = np.concatenate(([0.0], cumsum))
            window_sums = cumsum[window_samples:] - cumsum[:-window_samples]
            window_means = window_sums / window_samples

        track._cache[key] = window_means
        track._cache[f"window_samples_{window_ms}"] = window_samples
    return track._cache[key]


def get_window_samples(track: TrackContext, window_ms: int) -> int:
    """Number of samples per RMS window. Cached as side-effect of get_rms_window_means."""
    key = f"window_samples_{window_ms}"
    if key not in track._cache:
        track._cache[key] = max(1, int((window_ms / 1000) * track.samplerate))
    return track._cache[key]


def get_gated_rms_data(
    track: TrackContext,
    window_ms: int,
    stereo_mode: str,
    gate_relative_db: float,
) -> dict[str, Any]:
    """
    Gated RMS analysis data. Returns dict with:
        active_means, active_mask, max_window_db, window_means
    Cached on track._cache.
    """
    key = f"gated_rms_{window_ms}_{stereo_mode}_{gate_relative_db}"
    if key not in track._cache:
        window_means = get_rms_window_means(track, window_ms, stereo_mode)
        floor = np.finfo(np.float64).tiny
        window_rms_db = 10.0 * np.log10(np.maximum(window_means, floor))
        max_window_db = float(np.max(window_rms_db)) if window_rms_db.size else float(-np.inf)
        gate_threshold_db = max_window_db - float(gate_relative_db)
        active_mask = window_rms_db >= gate_threshold_db
        active_means = window_means[active_mask] if active_mask.size else window_means
        if active_means.size == 0:
            active_means = window_means
            active_mask = np.ones(window_means.shape, dtype=bool)

        track._cache[key] = {
            "active_means": active_means,
            "active_mask": active_mask,
            "max_window_db": max_window_db,
            "window_means": window_means,
        }
    return track._cache[key]


# ---------------------------------------------------------------------------
# Stereo helpers (cached)
# ---------------------------------------------------------------------------

def get_stereo_channels_subsampled(
    track: TrackContext,
    max_samples: int = 200000,
) -> tuple[np.ndarray, np.ndarray, int] | None:
    """
    Subsampled raw L/R channels (NOT DC-removed).
    Returns (l_raw, r_raw, step) or None for mono/invalid.
    """
    key = f"stereo_channels_subsampled_{max_samples}"
    if key not in track._cache:
        data = track.audio_data
        if data is not None and data.ndim > 1 and data.shape[1] == 2 and data.shape[0] > 1:
            l_full = data[:, 0].astype(np.float64)
            r_full = data[:, 1].astype(np.float64)
            step = max(1, int(l_full.size // max_samples))
            l_raw = l_full[::step]
            r_raw = r_full[::step]
            track._cache[key] = (l_raw, r_raw, step)
        else:
            track._cache[key] = None
    return track._cache[key]


def get_stereo_channels_dc_removed(
    track: TrackContext,
    max_samples: int = 200000,
) -> tuple[np.ndarray, np.ndarray, int] | None:
    """
    Subsampled DC-removed L/R channels.
    Returns (l, r, step) or None for mono/invalid.
    """
    key = f"stereo_channels_dc_removed_{max_samples}"
    if key not in track._cache:
        raw = get_stereo_channels_subsampled(track, max_samples)
        if raw is None:
            track._cache[key] = None
        else:
            l_raw, r_raw, step = raw
            l = l_raw - np.mean(l_raw)
            r = r_raw - np.mean(r_raw)
            track._cache[key] = (l, r, step)
    return track._cache[key]


def get_stereo_rms(
    track: TrackContext,
    max_samples: int = 200000,
) -> tuple[float, float, float, float] | None:
    """
    Per-channel RMS from DC-removed subsampled stereo.
    Returns (l_rms_lin, r_rms_lin, l_rms_db, r_rms_db) or None.
    """
    key = f"stereo_rms_{max_samples}"
    if key not in track._cache:
        dc_removed = get_stereo_channels_dc_removed(track, max_samples)
        if dc_removed is None:
            track._cache[key] = None
        else:
            l, r, _step = dc_removed
            l_rms_lin = float(np.sqrt(np.mean(l ** 2)))
            r_rms_lin = float(np.sqrt(np.mean(r ** 2)))
            l_rms_db = linear_to_db(l_rms_lin)
            r_rms_db = linear_to_db(r_rms_lin)
            track._cache[key] = (l_rms_lin, r_rms_lin, l_rms_db, r_rms_db)
    return track._cache[key]


# ---------------------------------------------------------------------------
# Stateless DSP functions
# ---------------------------------------------------------------------------

def detect_clipping_ranges(
    data: np.ndarray,
    threshold_count: int,
    max_ranges: int = 10,
) -> tuple[int, list[tuple[int, int, int | None]]]:
    """Return (count, ranges) where ranges are (start_sample, end_sample, channel).

    ``channel`` is an int (0-based) for multi-channel data or ``None`` for mono.
    """
    clip_level = 0.9999

    def ranges_from_mask(mask: np.ndarray) -> list[tuple[int, int]]:
        if mask.size == 0:
            return []
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            return []
        splits = np.flatnonzero(np.diff(idx) > 1)
        starts = np.concatenate(([0], splits + 1))
        ends = np.concatenate((splits, [idx.size - 1]))
        ranges = []
        for s_i, e_i in zip(starts, ends):
            start = int(idx[s_i])
            end = int(idx[e_i])
            if (end - start + 1) >= int(threshold_count):
                ranges.append((start, end))
        return ranges

    ranges: list[tuple[int, int, int | None]] = []
    if data.ndim > 1 and data.shape[1] > 1:
        for ch in range(int(data.shape[1])):
            mask = np.abs(data[:, ch]) >= clip_level
            for s, e in ranges_from_mask(mask):
                ranges.append((s, e, ch))
            if len(ranges) >= int(max_ranges):
                break
    else:
        flat_data = data.flatten() if data.ndim > 1 else data
        mask = np.abs(flat_data) >= clip_level
        for s, e in ranges_from_mask(mask):
            ranges.append((s, e, None))

    if not ranges:
        return 0, []

    return len(ranges), ranges[:int(max_ranges)]


def subsonic_ratio_db(
    data: np.ndarray,
    samplerate: int,
    cutoff_hz: float,
    max_samples: int = 200000,
) -> float:
    """FFT-based subsonic energy ratio (dB relative to full-band power)."""
    if data.size == 0 or samplerate <= 0:
        return float(-np.inf)

    if data.ndim > 1:
        mono = np.mean(data.astype(np.float64), axis=1)
    else:
        mono = data.astype(np.float64)

    if mono.size == 0:
        return float(-np.inf)

    step = max(1, int(mono.size // max_samples))
    x = mono[::step]
    if x.size < 8:
        return float(-np.inf)

    x = x - float(np.mean(x))
    w = np.hanning(x.size)
    X = np.fft.rfft(x * w)
    p = np.abs(X) ** 2
    freqs = np.fft.rfftfreq(x.size, d=float(step) / float(samplerate))

    non_dc = freqs > 0.0
    total = float(np.sum(p[non_dc]))
    if total <= 0.0:
        return float(-np.inf)

    band = float(np.sum(p[(freqs > 0.0) & (freqs <= float(cutoff_hz))]))
    if band <= 0.0:
        return float(-np.inf)

    return float(10.0 * np.log10(band / total))


def subsonic_ratio_db_1d(
    signal: np.ndarray,
    samplerate: int,
    cutoff_hz: float,
    max_samples: int = 200000,
) -> float:
    """FFT-based subsonic energy ratio for a **1-D** signal (single channel).

    Identical algorithm to :func:`subsonic_ratio_db` but requires a 1-D input
    (no mono-down-mix step).
    """
    if signal.ndim != 1 or signal.size == 0 or samplerate <= 0:
        return float(-np.inf)

    x = signal.astype(np.float64)
    step = max(1, int(x.size // max_samples))
    x = x[::step]
    if x.size < 8:
        return float(-np.inf)

    x = x - float(np.mean(x))
    w = np.hanning(x.size)
    X = np.fft.rfft(x * w)
    p = np.abs(X) ** 2
    freqs = np.fft.rfftfreq(x.size, d=float(step) / float(samplerate))

    non_dc = freqs > 0.0
    total = float(np.sum(p[non_dc]))
    if total <= 0.0:
        return float(-np.inf)

    band = float(np.sum(p[(freqs > 0.0) & (freqs <= float(cutoff_hz))]))
    if band <= 0.0:
        return float(-np.inf)

    return float(10.0 * np.log10(band / total))


def subsonic_windowed_ratios(
    signal: np.ndarray,
    samplerate: int,
    cutoff_hz: float,
    window_ms: int = 500,
    hop_ms: int | None = None,
) -> list[tuple[int, int, float]]:
    """Compute per-window subsonic energy ratios on a **1-D** signal.

    Returns a list of ``(sample_start, sample_end, ratio_db)`` tuples, one per
    analysis window.  Windows that contain too little data or where the total
    power is negligible are assigned ``-inf``.

    Parameters
    ----------
    signal : 1-D ndarray
    samplerate : int
    cutoff_hz : float
    window_ms : int
        Window length in milliseconds.  Must be large enough for reasonable
        frequency resolution at *cutoff_hz*.
    hop_ms : int or None
        Hop between windows in milliseconds.  Defaults to *window_ms* (no
        overlap).
    """
    if signal.ndim != 1 or signal.size == 0 or samplerate <= 0:
        return []

    win_samples = max(8, int(samplerate * window_ms / 1000))
    hop_samples = int(samplerate * (hop_ms or window_ms) / 1000)
    hop_samples = max(1, hop_samples)

    results: list[tuple[int, int, float]] = []
    pos = 0
    n = signal.size
    while pos < n:
        end = min(pos + win_samples, n)
        chunk = signal[pos:end].astype(np.float64)
        if chunk.size < 8:
            results.append((pos, end - 1, float(-np.inf)))
            pos += hop_samples
            continue

        # Quick gate: skip true digital silence
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        if rms < 1e-7:  # effectively zero
            results.append((pos, end - 1, float(-np.inf)))
            pos += hop_samples
            continue

        chunk = chunk - float(np.mean(chunk))
        w = np.hanning(chunk.size)
        X = np.fft.rfft(chunk * w)
        p = np.abs(X) ** 2
        freqs = np.fft.rfftfreq(chunk.size, d=1.0 / float(samplerate))

        non_dc = freqs > 0.0
        total = float(np.sum(p[non_dc]))
        if total <= 0.0:
            results.append((pos, end - 1, float(-np.inf)))
            pos += hop_samples
            continue

        band = float(np.sum(p[(freqs > 0.0) & (freqs <= float(cutoff_hz))]))
        ratio = float(10.0 * np.log10(band / total)) if band > 0.0 else float(-np.inf)

        # Absolute subsonic power gate: even if the *ratio* is high, the
        # subsonic energy must be loud enough to actually matter.  Amp hum
        # and noise in quiet gaps can dominate the spectrum but their
        # absolute level is too low to waste headroom.
        # subsonic_abs_db = window_rms_db + ratio_db
        if np.isfinite(ratio) and rms > 0:
            rms_db = float(20.0 * np.log10(rms))
            subsonic_abs_db = rms_db + ratio
            if subsonic_abs_db < -40.0:  # subsonic power below −40 dBFS
                ratio = float(-np.inf)

        results.append((pos, end - 1, ratio))
        pos += hop_samples

    return results
