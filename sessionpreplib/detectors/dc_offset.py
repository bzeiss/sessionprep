from __future__ import annotations

import numpy as np

from ..config import ParamSpec
from ..detector import TrackDetector
from ..models import DetectorResult, IssueLocation, Severity, TrackContext
from ..audio import dbfs_offset, linear_to_db, is_silent


class DCOffsetDetector(TrackDetector):
    id = "dc_offset"
    name = "DC Offset"
    depends_on = ["silence"]

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        return super().config_params() + [
            ParamSpec(
                key="dc_offset_warn_db", type=(int, float), default=-40.0, max=0.0,
                label="DC offset warning threshold (dB)",
                description="DC offset above this level triggers a warning.",
            ),
        ]

    @classmethod
    def html_help(cls) -> str:
        return (
            "<b>Description</b><br/>"
            "Measures the DC offset (constant voltage bias) of each channel. "
            "Reports the worst-case offset across channels in dBFS."
            "<br/><br/>"
            "<b>Results</b><br/>"
            "<b>OK</b> – DC offset is below the warning threshold.<br/>"
            "<b>ATTENTION</b> – Significant DC offset detected."
            "<br/><br/>"
            "<b>Interpretation</b><br/>"
            "DC offset wastes headroom and can cause clicks at edit points. "
            "Apply a DC removal filter or a high-pass filter at ~5 Hz before "
            "mastering."
        )

    def configure(self, config):
        super().configure(config)
        self.warn_db = config.get("dc_offset_warn_db", -40.0)
        self._db_offset = dbfs_offset(config)

    def analyze(self, track: TrackContext) -> DetectorResult:
        if is_silent(track):
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="silent track",
                data={"dc_db": float(-np.inf), "dc_warn": False},
            )

        data = track.audio_data
        if data.size > 0:
            if data.ndim > 1 and data.shape[1] > 1:
                dc_linear = float(np.max(np.abs(np.mean(data, axis=0))))
            else:
                flat = data.flatten() if data.ndim > 1 else data
                dc_linear = float(np.abs(np.mean(flat)))
        else:
            dc_linear = 0.0

        dc_db = linear_to_db(dc_linear)
        dc_warn = bool(np.isfinite(dc_db) and dc_db > self.warn_db)
        dc_db_display = dc_db + self._db_offset

        if dc_warn:
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.ATTENTION,
                summary=f"DC offset {dc_db_display:.1f} dBFS",
                data={"dc_db": dc_db, "dc_warn": True},
                hint="consider DC removal",
                issues=[IssueLocation(
                    sample_start=0,
                    sample_end=track.total_samples - 1,
                    channel=None,
                    severity=Severity.ATTENTION,
                    label="dc_offset",
                    description=f"DC offset {dc_db_display:.1f} dBFS",
                )],
            )

        return DetectorResult(
            detector_id=self.id,
            severity=Severity.CLEAN,
            summary="no DC offset issue",
            data={"dc_db": dc_db, "dc_warn": False},
        )

    def clean_message(self) -> str | None:
        return "No DC offset issues detected"
