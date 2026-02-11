from __future__ import annotations

import numpy as np

from ..config import ParamSpec
from ..detector import TrackDetector
from ..models import DetectorResult, Severity, TrackContext
from ..audio import (
    get_peak_db,
    get_gated_rms_data,
    is_silent,
    linear_to_db,
)
from ..utils import matches_keywords


class AudioClassifierDetector(TrackDetector):
    id = "audio_classifier"
    name = "Audio Classifier"
    depends_on = ["silence"]

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        return [
            ParamSpec(
                key="crest_threshold", type=(int, float), default=12.0,
                min=0.0, min_exclusive=True,
                label="Crest factor threshold (dB)",
                description="Crest factor above this classifies a track as transient.",
            ),
            ParamSpec(
                key="decay_lookahead_ms", type=int, default=200,
                min=50, max=1000,
                label="Decay lookahead (ms)",
                description=(
                    "Time window after the loudest moment to measure energy "
                    "decay. Used to distinguish true transients (fast decay) "
                    "from sustained content with sharp attacks."
                ),
            ),
            ParamSpec(
                key="decay_db_threshold", type=(int, float), default=12.0,
                min=3.0, max=30.0,
                label="Decay threshold (dB)",
                description=(
                    "Energy drop within the lookahead window that confirms a "
                    "transient. Higher values require a sharper drop."
                ),
            ),
            ParamSpec(
                key="sparse_density_threshold", type=float, default=0.25,
                min=0.0, max=1.0,
                label="Sparse track density threshold",
                description=(
                    "Fraction of active (non-silent) RMS windows below which "
                    "a track is classified as transient regardless of crest "
                    "and decay. Catches sparse percussion like toms, crashes, "
                    "and FX hits that have moderate crest and slow decay."
                ),
            ),
        ]

    @classmethod
    def html_help(cls) -> str:
        return (
            "<b>Description</b><br/>"
            "Classifies a track as Transient or Sustained using three "
            "metrics: crest factor (peak-to-RMS ratio), envelope decay "
            "rate (how fast energy drops after the loudest moment), and "
            "density (fraction of the track containing active content). "
            "Very sparse tracks are classified as Transient regardless "
            "of crest and decay. Otherwise, crest and decay vote together "
            "with decay acting as tiebreaker."
            "<br/><br/>"
            "<b>Results</b><br/>"
            "Classification metrics (crest, decay, density) are shown "
            "under Bimodal Normalization when report verbosity is set "
            "to verbose."
            "<br/><br/>"
            "<b>Interpretation</b><br/>"
            "Sparse track (density below threshold) → Transient.<br/>"
            "High crest + fast decay → drums, percussion (Transient).<br/>"
            "Low crest + slow decay → pads, bass, vocals (Sustained).<br/>"
            "High crest + slow decay → plucked/piano (Sustained).<br/>"
            "Low crest + fast decay → compressed drums (Transient).<br/>"
            "The classification determines which normalization "
            "strategy is applied during processing."
        )

    def is_relevant(self, result: DetectorResult, track: TrackContext | None = None) -> bool:
        return False

    def configure(self, config):
        self.window_ms = config.get("window", 400)
        self.stereo_mode = config.get("stereo_mode", "avg")
        self.rms_anchor_mode = config.get("rms_anchor", "percentile")
        self.rms_percentile = config.get("rms_percentile", 95.0)
        self.gate_relative_db = config.get("gate_relative_db", 40.0)
        self.crest_threshold = config.get("crest_threshold", 12.0)
        self.decay_lookahead_ms = config.get("decay_lookahead_ms", 200)
        self.decay_db_threshold = config.get("decay_db_threshold", 12.0)
        self.sparse_density_threshold = config.get("sparse_density_threshold", 0.25)
        self.force_transient = config.get("force_transient", [])
        self.force_sustained = config.get("force_sustained", [])

    def analyze(self, track: TrackContext) -> DetectorResult:
        if is_silent(track):
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="silent track",
                data={
                    "peak_db": float(-np.inf),
                    "rms_max_db": float(-np.inf),
                    "rms_anchor_db": float(-np.inf),
                    "rms_anchor_mean": 0.0,
                    "crest": 0.0,
                    "decay_db": 0.0,
                    "density": 0.0,
                    "classification": "Silent",
                    "is_transient": False,
                    "near_threshold": False,
                },
            )

        peak_db = get_peak_db(track)
        gated = get_gated_rms_data(
            track, self.window_ms, self.stereo_mode, self.gate_relative_db
        )
        active_means = gated["active_means"]
        window_means = gated["window_means"]

        max_mean = float(np.max(active_means)) if active_means.size else 0.0
        rms_max_db = float(linear_to_db(np.sqrt(max_mean)))

        if self.rms_anchor_mode == "max":
            anchor_mean = max_mean
        else:
            anchor_mean = float(np.percentile(active_means, self.rms_percentile))

        rms_anchor_db = float(linear_to_db(np.sqrt(anchor_mean)))

        # Crest factor: based on MAX window RMS (same as original)
        if np.isfinite(peak_db) and np.isfinite(rms_max_db):
            crest = float(peak_db - rms_max_db)
        else:
            crest = 0.0

        # --- Envelope decay rate ---
        # Build a short-window (~10 ms) energy envelope from raw audio
        # so transient details are not smeared by the main RMS window.
        # Then measure how much energy drops in the lookahead region
        # after the peak.  Uses the median of the tail quarter to be
        # robust against single-window outliers.
        _DECAY_ENV_MS = 10  # short envelope resolution
        sr = track.samplerate
        decay_env_samples = max(1, int((_DECAY_ENV_MS / 1000.0) * sr))

        audio = track.audio_data
        if audio.ndim > 1 and audio.shape[1] > 1:
            mono_sq = np.mean(audio.astype(np.float64) ** 2, axis=1)
        else:
            mono_sq = (audio.flatten().astype(np.float64)) ** 2

        n = len(mono_sq)
        if n > decay_env_samples:
            cs = np.empty(n + 1, dtype=np.float64)
            cs[0] = 0.0
            np.cumsum(mono_sq, out=cs[1:])
            env = (cs[decay_env_samples:] - cs[: n - decay_env_samples + 1]) / decay_env_samples
        else:
            env = np.array([np.mean(mono_sq)], dtype=np.float64)

        floor = np.finfo(np.float64).tiny
        peak_env_idx = int(np.argmax(env))
        lookahead_env = max(1, int((self.decay_lookahead_ms / 1000.0) * sr))
        decay_end = min(len(env), peak_env_idx + 1 + lookahead_env)

        if decay_end > peak_env_idx + 1:
            tail_slice = env[peak_env_idx + 1 : decay_end]
            # Use the median of the last quarter of the tail for stability
            quarter = max(1, len(tail_slice) // 4)
            tail_median = float(np.median(tail_slice[-quarter:]))
        else:
            tail_median = float(env[peak_env_idx])

        peak_env_db = 10.0 * np.log10(max(float(env[peak_env_idx]), floor))
        tail_env_db = 10.0 * np.log10(max(tail_median, floor))
        decay_db = float(peak_env_db - tail_env_db)  # positive = energy dropped

        # --- Density: fraction of windows above the relative gate ---
        density = (
            float(len(active_means) / len(window_means))
            if len(window_means) > 0 else 1.0
        )

        # --- Three-metric classification ---
        crest_says_transient = crest > self.crest_threshold
        decay_says_transient = decay_db > self.decay_db_threshold
        is_sparse = density < self.sparse_density_threshold

        # Classification with overrides
        if matches_keywords(track.filename, self.force_transient):
            is_transient = True
            classification = "Transient (Forced)"
        elif matches_keywords(track.filename, self.force_sustained):
            is_transient = False
            classification = "Sustained (Forced)"
        elif is_sparse and (crest_says_transient or decay_says_transient):
            # Sparse track + at least one dynamic metric agrees → percussion
            is_transient = True
            classification = "Transient"
        elif crest_says_transient and decay_says_transient:
            # Both agree → high-confidence transient
            is_transient = True
            classification = "Transient"
        elif not crest_says_transient and not decay_says_transient:
            # Both agree → high-confidence sustained
            is_transient = False
            classification = "Sustained"
        elif crest_says_transient and not decay_says_transient:
            # High crest but slow decay → plucked / piano / compressed
            is_transient = False
            classification = "Sustained"
        else:
            # Low crest but fast decay → compressed drums / loop
            is_transient = True
            classification = "Transient"

        # Near-threshold flag: ambiguous when either metric is close
        forced = "Forced" in classification
        near_threshold = (
            not forced
            and np.isfinite(crest)
            and (abs(crest - self.crest_threshold) < 2.0
                 or abs(decay_db - self.decay_db_threshold) < 2.0)
        )

        summary = (
            f"crest {crest:.1f} dB, decay {decay_db:.1f} dB, "
            f"density {density:.0%}, {classification}"
        )

        return DetectorResult(
            detector_id=self.id,
            severity=Severity.INFO,
            summary=summary,
            data={
                "peak_db": float(peak_db),
                "rms_max_db": float(rms_max_db),
                "rms_anchor_db": float(rms_anchor_db),
                "rms_anchor_mean": float(anchor_mean),
                "crest": float(crest),
                "decay_db": float(decay_db),
                "density": float(density),
                "classification": classification,
                "is_transient": bool(is_transient),
                "near_threshold": bool(near_threshold),
            },
        )
