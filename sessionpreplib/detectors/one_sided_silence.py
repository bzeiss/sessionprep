from __future__ import annotations

import numpy as np

from ..config import ParamSpec
from ..detector import TrackDetector
from ..models import DetectorResult, IssueLocation, Severity, TrackContext
from ..audio import dbfs_offset, get_stereo_rms, is_silent, linear_to_db


class OneSidedSilenceDetector(TrackDetector):
    id = "one_sided_silence"
    name = "One-Sided Silence"
    depends_on = ["silence"]

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        return super().config_params() + [
            ParamSpec(
                key="one_sided_silence_db", type=(int, float), default=-80.0,
                max=0.0,
                label="One-sided silence threshold (dB)",
                description="RMS below this level on one channel triggers a warning.",
            ),
        ]

    @classmethod
    def html_help(cls) -> str:
        return (
            "<b>Description</b><br/>"
            "Detects stereo files where one channel is silent while the other "
            "contains audio content."
            "<br/><br/>"
            "<b>Results</b><br/>"
            "<b>OK</b> – Both channels have audio content.<br/>"
            "<b>ATTENTION</b> – One channel is silent (reported with side and "
            "per-channel RMS levels)."
            "<br/><br/>"
            "<b>Interpretation</b><br/>"
            "One-sided silence usually indicates a mono source exported as "
            "stereo with incorrect channel routing, or a missing channel. "
            "Check the stereo export settings and channel routing in the DAW."
        )

    def configure(self, config):
        super().configure(config)
        self.threshold_db = config.get("one_sided_silence_db", -80.0)
        self._db_offset = dbfs_offset(config)

    def analyze(self, track: TrackContext) -> DetectorResult:
        if is_silent(track):
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="silent track",
                data={
                    "one_sided_silence": False,
                    "one_sided_silence_side": None,
                    "l_rms_db": float(-np.inf),
                    "r_rms_db": float(-np.inf),
                },
            )

        stereo_rms = get_stereo_rms(track)
        if stereo_rms is None:
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="mono track",
                data={
                    "one_sided_silence": False,
                    "one_sided_silence_side": None,
                    "l_rms_db": float(-np.inf),
                    "r_rms_db": float(-np.inf),
                },
            )

        l_rms_lin, r_rms_lin, l_rms_db, r_rms_db = stereo_rms
        silence_lin = float(10.0 ** (self.threshold_db / 20.0))

        one_sided = False
        side = None
        if l_rms_lin <= silence_lin and r_rms_lin > silence_lin:
            one_sided = True
            side = "L"
        elif r_rms_lin <= silence_lin and l_rms_lin > silence_lin:
            one_sided = True
            side = "R"

        data = {
            "one_sided_silence": bool(one_sided),
            "one_sided_silence_side": side,
            "l_rms_db": l_rms_db,
            "r_rms_db": r_rms_db,
        }

        if one_sided:
            off = self._db_offset

            def fmt_db(x):
                v = float(x) + off
                return f"{v:.1f}" if np.isfinite(x) else "-inf"

            if side:
                summary = (
                    f"one-sided silence ({side}) "
                    f"(L {fmt_db(l_rms_db)} dBFS, R {fmt_db(r_rms_db)} dBFS)"
                )
            else:
                summary = (
                    f"one-sided silence "
                    f"(L {fmt_db(l_rms_db)} dBFS, R {fmt_db(r_rms_db)} dBFS)"
                )
            ch_idx = 0 if side == "L" else 1 if side == "R" else None
            issues = [IssueLocation(
                sample_start=0,
                sample_end=track.total_samples - 1,
                channel=ch_idx,
                severity=Severity.ATTENTION,
                label="one_sided_silence",
                description=f"channel {side} is silent",
            )]
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.ATTENTION,
                summary=summary,
                data=data,
                hint="check stereo export / channel routing",
                issues=issues,
            )

        return DetectorResult(
            detector_id=self.id,
            severity=Severity.CLEAN,
            summary="no one-sided silence",
            data=data,
        )

    def clean_message(self) -> str | None:
        return "No one-sided silent stereo files detected"
