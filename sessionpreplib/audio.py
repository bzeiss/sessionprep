from __future__ import annotations

import os
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import stft as scipy_stft

from .models import TrackContext
from .chunks import chunk_ids as _chunk_ids


# Supported audio file extensions (lowercase, with leading dot)
AUDIO_EXTENSIONS = ('.wav', '.aif', '.aiff')


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


def discover_track(filepath: str) -> TrackContext:
    """Read audio file metadata without loading audio data.

    Returns a TrackContext with filename, filepath, channels, samplerate,
    total_samples, bitdepth, subtype, duration_sec populated.
    ``audio_data`` is left as ``None``.
    """
    info = sf.info(filepath)
    return TrackContext(
        filename=os.path.basename(filepath),
        filepath=filepath,
        audio_data=None,
        samplerate=info.samplerate,
        channels=info.channels,
        total_samples=info.frames,
        bitdepth=_SUBTYPE_MAP.get(info.subtype, info.subtype),
        subtype=info.subtype,
        duration_sec=info.duration,
    )


def load_track(filepath: str) -> TrackContext:
    """Read an audio file (WAV/AIFF) and return a fully populated TrackContext."""
    info = sf.info(filepath)
    data, samplerate = sf.read(filepath, dtype='float64')
    channels = 1 if data.ndim == 1 else data.shape[1]
    try:
        cids = _chunk_ids(filepath)
    except (ValueError, OSError):
        cids = []
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
        chunk_ids=cids,
    )


def discover_audio_files(
    root_dir: str,
    recursive: bool = False,
    skip_folders: set[str] | None = None,
) -> list[str]:
    """Return a sorted list of audio file paths relative to *root_dir*.

    When *recursive* is ``False``, returns bare filenames (flat listing).
    When ``True``, walks subdirectories (symlinks are **not** followed)
    and returns forward-slash–separated relative paths such as
    ``"drums/01_Kick.wav"``.

    Directories whose name appears in *skip_folders* are pruned from the
    walk (e.g. ``{"sp_01_tracklayout", "sp_02_prepared"}``).
    """
    from .utils import protools_sort_key

    skip = skip_folders or set()
    result: list[str] = []

    if not recursive:
        for fname in os.listdir(root_dir):
            if fname.lower().endswith(AUDIO_EXTENSIONS):
                result.append(fname)
    else:
        for dirpath, dirnames, filenames in os.walk(root_dir, followlinks=False):
            # Prune skipped directories in-place so os.walk won't descend
            dirnames[:] = [
                d for d in dirnames if d not in skip
            ]
            for fname in filenames:
                if fname.lower().endswith(AUDIO_EXTENSIONS):
                    rel = os.path.relpath(
                        os.path.join(dirpath, fname), root_dir)
                    # Normalise to forward slashes for cross-platform keys
                    result.append(rel.replace("\\", "/"))

    result.sort(key=protools_sort_key)
    return result


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


def subsonic_stft_analysis(
    signal: np.ndarray,
    samplerate: int,
    cutoff_hz: float,
    *,
    window_ms: int = 500,
    hop_ms: int | None = None,
    abs_gate_db: float = -40.0,
    silence_rms: float = 1e-7,
) -> tuple[float, list[tuple[int, int, float]]]:
    """Single-pass STFT subsonic analysis on a 1-D signal.

    Uses :func:`scipy.signal.stft` to compute the short-time Fourier transform
    in one vectorised call, then derives both per-window and whole-file
    subsonic-to-total energy ratios from the resulting power spectrum.

    Parameters
    ----------
    signal : 1-D ndarray
        Single-channel audio samples.
    samplerate : int
        Sample rate in Hz.
    cutoff_hz : float
        Frequency below which energy is considered subsonic.
    window_ms : int
        Analysis window length in milliseconds.
    hop_ms : int or None
        Hop between windows in milliseconds.  Defaults to *window_ms* (no
        overlap).
    abs_gate_db : float
        Absolute subsonic power gate.  Windows where the estimated subsonic
        level (``rms_db + ratio_db``) is below this are set to ``-inf``.
    silence_rms : float
        RMS threshold below which a window is considered silent.

    Returns
    -------
    whole_file_ratio_db : float
        Subsonic-to-total energy ratio for the entire signal (dB).
    per_window_ratios : list of (sample_start, sample_end, ratio_db)
        Per-window subsonic ratios.  Silent or gated windows have ``-inf``.
    """
    _NEG_INF = float(-np.inf)

    if signal.ndim != 1 or signal.size < 8 or samplerate <= 0:
        return _NEG_INF, []

    original_size = signal.size
    nperseg = max(8, int(samplerate * window_ms / 1000))
    hop_samples = max(1, int(samplerate * ((hop_ms or window_ms)) / 1000))
    noverlap = nperseg - hop_samples

    # Pad signal so scipy.signal.stft (boundary=None) includes the tail.
    # Without padding, partial last windows are dropped.
    remainder = (original_size - nperseg) % hop_samples if original_size > nperseg else original_size
    if remainder != 0:
        pad_len = hop_samples - remainder
        padded = np.pad(signal, (0, pad_len)).astype(np.float64)
    else:
        padded = signal.astype(np.float64)

    # Power-of-2 FFT size for speed (does not change window shape or overlap)
    nfft = 1 << (nperseg - 1).bit_length()

    # --- Vectorised per-window RMS (for silence + absolute power gates) ---
    # Compute RMS on hop-aligned non-overlapping chunks of the original signal
    # to match the STFT frame positions.
    n_frames = (padded.size - noverlap) // hop_samples
    # Build RMS from hop-aligned windows (same start positions as STFT frames)
    rms_arr = np.empty(n_frames, dtype=np.float64)
    for i in range(n_frames):
        s = i * hop_samples
        e = min(s + nperseg, original_size)
        if e <= s:
            rms_arr[i] = 0.0
        else:
            chunk = padded[s:e]
            rms_arr[i] = float(np.sqrt(np.mean(chunk ** 2)))

    # --- STFT ---
    _f, _t, Zxx = scipy_stft(
        padded, fs=samplerate, nperseg=nperseg, nfft=nfft,
        noverlap=noverlap, window='hann', boundary=None,
    )
    power = np.abs(Zxx) ** 2  # (n_freq_bins, n_frames)
    actual_frames = power.shape[1]

    # Trim RMS array if frame counts diverge (shouldn't happen, but be safe)
    if rms_arr.size > actual_frames:
        rms_arr = rms_arr[:actual_frames]
    elif rms_arr.size < actual_frames:
        rms_arr = np.pad(rms_arr, (0, actual_frames - rms_arr.size))

    # --- Frequency bin indices (integer slicing, computed once) ---
    dc_bin = 1  # skip DC at index 0
    cutoff_bin = min(
        int(np.floor(cutoff_hz * nfft / samplerate)) + 1,
        power.shape[0],
    )

    # --- Vectorised band / total power ---
    band = np.sum(power[dc_bin:cutoff_bin, :], axis=0)   # (n_frames,)
    total = np.sum(power[dc_bin:, :], axis=0)             # (n_frames,)

    # Per-window ratio
    with np.errstate(divide='ignore', invalid='ignore'):
        ratios = np.where(
            (band > 0) & (total > 0),
            10.0 * np.log10(band / total),
            -np.inf,
        )

    # --- Vectorised gates ---
    # Silence gate
    ratios[rms_arr < silence_rms] = -np.inf

    # Absolute power gate: subsonic_abs = rms_db + ratio
    with np.errstate(divide='ignore', invalid='ignore'):
        rms_db = np.where(rms_arr > 0, 20.0 * np.log10(rms_arr), -200.0)
    abs_mask = np.isfinite(ratios) & ((rms_db + ratios) < abs_gate_db)
    ratios[abs_mask] = -np.inf

    # --- Whole-file ratio (aggregated from per-frame power) ---
    valid = rms_arr >= silence_rms
    total_band = float(np.sum(band[valid]))
    total_all = float(np.sum(total[valid]))
    if total_band > 0 and total_all > 0:
        whole_ratio = float(10.0 * np.log10(total_band / total_all))
    else:
        whole_ratio = _NEG_INF

    # --- Build result tuples ---
    results: list[tuple[int, int, float]] = []
    for i in range(actual_frames):
        s = i * hop_samples
        e = min(s + nperseg, original_size) - 1
        results.append((s, e, float(ratios[i])))

    return whole_ratio, results


# ---------------------------------------------------------------------------
# Windowed stereo correlation + mono folddown
# ---------------------------------------------------------------------------

def windowed_stereo_correlation(
    left: np.ndarray,
    right: np.ndarray,
    samplerate: int,
    window_ms: int = 500,
    silence_rms: float = 1e-7,
) -> tuple[
    float, float,
    list[tuple[int, int, float, float]],
]:
    """Windowed Pearson correlation and mono folddown loss for stereo L/R.

    Parameters
    ----------
    left, right : 1-D float arrays
        Full-resolution left and right channel samples.
    samplerate : int
        Sample rate in Hz.
    window_ms : int
        Analysis window length in milliseconds.
    silence_rms : float
        RMS threshold below which a window is considered silent.

    Returns
    -------
    whole_corr : float
        Whole-file Pearson correlation (NaN if silent/invalid).
    whole_mono_loss_db : float
        Whole-file mono folddown loss in dB (0.0 if no loss, inf if total
        cancellation).
    per_window : list of (sample_start, sample_end, corr, mono_loss_db)
        Per-window results.  Silent windows have (NaN, NaN).
    """
    _NAN = float('nan')

    if (left.ndim != 1 or right.ndim != 1
            or left.size < 8 or left.size != right.size
            or samplerate <= 0):
        return _NAN, 0.0, []

    n = left.size
    win_samples = max(8, int(samplerate * window_ms / 1000))
    n_win = (n + win_samples - 1) // win_samples

    # Pad to exact multiple of win_samples
    padded_len = n_win * win_samples
    if padded_len != n:
        l = np.pad(left.astype(np.float64), (0, padded_len - n))
        r = np.pad(right.astype(np.float64), (0, padded_len - n))
    else:
        l = left.astype(np.float64)
        r = right.astype(np.float64)

    # Reshape to (n_win, win_samples)
    L = l.reshape(n_win, win_samples)
    R = r.reshape(n_win, win_samples)

    # Per-window DC removal
    L = L - L.mean(axis=1, keepdims=True)
    R = R - R.mean(axis=1, keepdims=True)

    # Per-window dot products (vectorized)
    dot_ll = np.sum(L * L, axis=1)
    dot_rr = np.sum(R * R, axis=1)
    dot_lr = np.sum(L * R, axis=1)

    # Per-window RMS for silence gating
    rms_l = np.sqrt(dot_ll / win_samples)
    rms_r = np.sqrt(dot_rr / win_samples)
    active = np.maximum(rms_l, rms_r) >= silence_rms

    # --- Per-window correlation ---
    denom = np.sqrt(dot_ll * dot_rr)
    with np.errstate(divide='ignore', invalid='ignore'):
        corr = np.where(denom > 0, dot_lr / denom, 0.0)
    corr[~active] = _NAN

    # --- Per-window mono folddown loss ---
    # stereo_power = (dot_ll + dot_rr) / 2
    # mono_power   = (dot_ll + 2*dot_lr + dot_rr) / 4
    stereo_p = (dot_ll + dot_rr) / 2.0
    mono_p = (dot_ll + 2.0 * dot_lr + dot_rr) / 4.0
    with np.errstate(divide='ignore', invalid='ignore'):
        mono_loss = np.where(
            (mono_p > 0) & (stereo_p > 0),
            10.0 * np.log10(stereo_p / mono_p),
            np.where(stereo_p > 0, np.inf, 0.0),
        )
    mono_loss[~active] = _NAN

    # --- Whole-file aggregation (from cumulative dot products) ---
    sum_ll = float(np.sum(dot_ll[active]))
    sum_rr = float(np.sum(dot_rr[active]))
    sum_lr = float(np.sum(dot_lr[active]))

    whole_denom = np.sqrt(sum_ll * sum_rr)
    whole_corr = float(sum_lr / whole_denom) if whole_denom > 0 else _NAN

    whole_stereo_p = (sum_ll + sum_rr) / 2.0
    whole_mono_p = (sum_ll + 2.0 * sum_lr + sum_rr) / 4.0
    if whole_mono_p > 0 and whole_stereo_p > 0:
        whole_mono_loss = float(10.0 * np.log10(whole_stereo_p / whole_mono_p))
    elif whole_stereo_p > 0:
        whole_mono_loss = float('inf')
    else:
        whole_mono_loss = 0.0

    # --- Build result tuples ---
    results: list[tuple[int, int, float, float]] = []
    for i in range(n_win):
        s = i * win_samples
        e = min(s + win_samples, n) - 1
        results.append((s, e, float(corr[i]), float(mono_loss[i])))

    return whole_corr, whole_mono_loss, results
