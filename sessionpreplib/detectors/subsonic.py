from __future__ import annotations

import numpy as np

from ..config import ParamSpec
from ..detector import TrackDetector
from ..models import DetectorResult, IssueLocation, Severity, TrackContext
from ..audio import subsonic_ratio_db, is_silent


class SubsonicDetector(TrackDetector):
    id = "subsonic"
    name = "Subsonic Content"
    depends_on = ["silence"]

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        return [
            ParamSpec(
                key="subsonic_hz", type=(int, float), default=30.0,
                min=0.0, min_exclusive=True,
                label="Subsonic cutoff frequency (Hz)",
                description="Frequency below which energy is considered subsonic.",
            ),
            ParamSpec(
                key="subsonic_warn_ratio_db", type=(int, float), default=-20.0,
                max=0.0,
                label="Subsonic warning ratio (dB)",
                description="Subsonic-to-total ratio above this triggers a warning.",
            ),
        ]

    @classmethod
    def html_help(cls) -> str:
        return (
            "<b>Description</b><br/>"
            "Measures the energy ratio of sub-bass content below a configurable "
            "cutoff frequency relative to the total signal energy."
            "<br/><br/>"
            "<b>Results</b><br/>"
            "<b>OK</b> – Subsonic energy is below the warning threshold.<br/>"
            "<b>ATTENTION</b> – Significant subsonic energy detected (ratio in dB)."
            "<br/><br/>"
            "<b>Interpretation</b><br/>"
            "Excessive subsonic energy wastes headroom and can cause speaker "
            "excursion or rumble issues during playback. Consider applying a "
            "high-pass filter at the cutoff frequency."
        )

    def configure(self, config):
        self.cutoff_hz = config.get("subsonic_hz", 30.0)
        self.warn_ratio_db = config.get("subsonic_warn_ratio_db", -20.0)

    def analyze(self, track: TrackContext) -> DetectorResult:
        if is_silent(track):
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="silent track",
                data={"subsonic_ratio_db": float(-np.inf), "subsonic_warn": False},
            )

        ratio_db = subsonic_ratio_db(
            track.audio_data, track.samplerate, float(self.cutoff_hz)
        )
        warn = bool(
            np.isfinite(ratio_db) and float(ratio_db) >= float(self.warn_ratio_db)
        )

        if warn:
            if np.isfinite(ratio_db):
                summary = f"subsonic energy {float(ratio_db):.1f} dB (<= {self.cutoff_hz:g} Hz)"
            else:
                summary = "subsonic content detected"
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.ATTENTION,
                summary=summary,
                data={"subsonic_ratio_db": float(ratio_db), "subsonic_warn": True},
                hint=f"consider HPF ~{self.cutoff_hz:g} Hz",
                issues=[IssueLocation(
                    sample_start=0,
                    sample_end=track.total_samples - 1,
                    channel=None,
                    severity=Severity.ATTENTION,
                    label="subsonic",
                    description=summary,
                )],
            )

        return DetectorResult(
            detector_id=self.id,
            severity=Severity.CLEAN,
            summary="no significant subsonic content",
            data={"subsonic_ratio_db": float(ratio_db), "subsonic_warn": False},
        )

    def clean_message(self) -> str | None:
        return "No significant subsonic content detected"
