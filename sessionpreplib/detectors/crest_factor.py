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


class CrestFactorDetector(TrackDetector):
    id = "crest_factor"
    name = "Crest Factor & Classification"
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
        ]

    @classmethod
    def html_help(cls) -> str:
        return (
            "<b>Description</b><br/>"
            "Measures the crest factor (peak-to-RMS ratio) of the track and "
            "classifies it as Transient or Sustained based on a configurable "
            "threshold. Supports keyword-based overrides to force a classification."
            "<br/><br/>"
            "<b>Results</b><br/>"
            "<b>INFO</b> – Always reported with the crest factor value and "
            "the resulting classification (Transient / Sustained)."
            "<br/><br/>"
            "<b>Interpretation</b><br/>"
            "High crest factor → dynamic, transient-heavy content (drums, "
            "percussion). Low crest factor → sustained content (pads, bass, "
            "vocals). The classification determines which normalization "
            "strategy is applied during processing."
        )

    def configure(self, config):
        self.window_ms = config.get("window", 400)
        self.stereo_mode = config.get("stereo_mode", "avg")
        self.rms_anchor_mode = config.get("rms_anchor", "percentile")
        self.rms_percentile = config.get("rms_percentile", 95.0)
        self.gate_relative_db = config.get("gate_relative_db", 40.0)
        self.crest_threshold = config.get("crest_threshold", 12.0)
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

        # Classification with overrides
        if matches_keywords(track.filename, self.force_transient):
            is_transient = True
            classification = "Transient (Forced)"
        elif matches_keywords(track.filename, self.force_sustained):
            is_transient = False
            classification = "Sustained (Forced)"
        else:
            is_transient = crest > self.crest_threshold
            classification = "Transient" if is_transient else "Sustained"

        # Near-threshold flag for normalization hints
        forced = "Forced" in classification
        near_threshold = (
            not forced
            and np.isfinite(crest)
            and abs(crest - self.crest_threshold) < 2.0
        )

        summary = (
            f"crest {crest:.1f} dB, {classification}"
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
                "classification": classification,
                "is_transient": bool(is_transient),
                "near_threshold": bool(near_threshold),
            },
        )
