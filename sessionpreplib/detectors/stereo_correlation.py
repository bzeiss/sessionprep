from __future__ import annotations

import numpy as np

from ..config import ParamSpec
from ..detector import TrackDetector
from ..models import DetectorResult, Severity, TrackContext
from ..audio import get_stereo_channels_dc_removed, is_silent


class StereoCorrelationDetector(TrackDetector):
    id = "stereo_correlation"
    name = "Stereo Compatibility"
    depends_on = ["silence"]

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        return [
            ParamSpec(
                key="corr_warn", type=(int, float), default=-0.3,
                min=-1.0, max=1.0,
                label="Stereo correlation warning threshold",
                description="Correlation below this value triggers a stereo-compatibility warning.",
            ),
        ]

    @classmethod
    def html_help(cls) -> str:
        return (
            "<b>Description</b><br/>"
            "Computes the Pearson correlation coefficient between the left and "
            "right channels after DC removal."
            "<br/><br/>"
            "<b>Results</b><br/>"
            "<b>OK</b> – Correlation is above the warning threshold.<br/>"
            "<b>INFO</b> – Correlation is below threshold (reported with value)."
            "<br/><br/>"
            "<b>Interpretation</b><br/>"
            "Low or negative correlation indicates significant phase differences "
            "between channels. This can cause level loss or cancellation in mono "
            "playback (phone speakers, mono PA systems). Review stereo widening "
            "or mid/side processing."
        )

    def configure(self, config):
        self.corr_warn = config.get("corr_warn", -0.3)

    def analyze(self, track: TrackContext) -> DetectorResult:
        if is_silent(track):
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="silent track",
                data={"lr_corr": None, "corr_warn": False},
            )

        dc_removed = get_stereo_channels_dc_removed(track)
        if dc_removed is None:
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="mono track",
                data={"lr_corr": None, "corr_warn": False},
            )

        l, r, _step = dc_removed
        denom = float(np.sqrt(np.dot(l, l) * np.dot(r, r)))
        if denom > 0:
            lr_corr = float(np.dot(l, r) / denom)
            corr_warn = lr_corr < float(self.corr_warn)
        else:
            lr_corr = None
            corr_warn = False

        if corr_warn:
            if lr_corr is not None:
                summary = f"corr {lr_corr:.2f} (< {self.corr_warn:g})"
            else:
                summary = "corr < threshold"
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.INFO,
                summary=summary,
                data={"lr_corr": lr_corr, "corr_warn": True},
            )

        return DetectorResult(
            detector_id=self.id,
            severity=Severity.CLEAN,
            summary="stereo correlation OK",
            data={"lr_corr": lr_corr, "corr_warn": False},
        )
