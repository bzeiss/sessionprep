from __future__ import annotations

from ..config import ParamSpec
from ..detector import TrackDetector
from ..models import DetectorResult, IssueLocation, Severity, TrackContext
from ..audio import detect_clipping_ranges, is_silent


class ClippingDetector(TrackDetector):
    id = "clipping"
    name = "Digital Clipping"
    depends_on = ["silence"]

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        return [
            ParamSpec(
                key="clip_consecutive", type=int, default=3, min=1,
                label="Consecutive clipped samples",
                description="Minimum consecutive samples at full scale to count as a clipping run.",
            ),
            ParamSpec(
                key="clip_report_max_ranges", type=int, default=10, min=1,
                label="Max reported clipping ranges",
                description="Maximum number of clipping ranges to report per track.",
            ),
        ]

    @classmethod
    def html_help(cls) -> str:
        return (
            "<b>Description</b><br/>"
            "Detects digital clipping — runs of consecutive samples at full "
            "scale (0 dBFS). A configurable minimum run length avoids false "
            "positives from isolated full-scale samples."
            "<br/><br/>"
            "<b>Results</b><br/>"
            "<b>OK</b> – No clipping detected.<br/>"
            "<b>PROBLEM</b> – Clipping runs found, with count and locations."
            "<br/><br/>"
            "<b>Interpretation</b><br/>"
            "Digital clipping truncates the waveform at the ceiling, causing "
            "audible distortion. Request a reprint at lower levels or review "
            "the limiter / master-bus settings."
        )

    def configure(self, config):
        self.consecutive = config.get("clip_consecutive", 3)
        self.max_ranges = config.get("clip_report_max_ranges", 10)

    def analyze(self, track: TrackContext) -> DetectorResult:
        if is_silent(track):
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="silent track",
                data={"is_clipped": False, "runs": 0, "ranges": []},
            )

        runs, ranges = detect_clipping_ranges(
            track.audio_data, self.consecutive, self.max_ranges
        )

        if runs == 0:
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="no clipping",
                data={"is_clipped": False, "runs": 0, "ranges": []},
            )

        detail = [f"samples {s}-{e} ch={ch}" for s, e, ch in ranges]
        issues = [
            IssueLocation(
                sample_start=s,
                sample_end=e,
                channel=ch,
                severity=Severity.PROBLEM,
                label="clipping",
                description=f"clipping at samples {s}\u2013{e}"
                            + (f" (ch {ch})" if ch is not None else ""),
            )
            for s, e, ch in ranges
        ]
        return DetectorResult(
            detector_id=self.id,
            severity=Severity.PROBLEM,
            summary=f"clipping detected ({runs} clipped ranges)",
            data={"is_clipped": True, "runs": runs, "ranges": ranges},
            detail_lines=detail,
            hint="request reprint / check limiting",
            issues=issues,
        )

    def clean_message(self) -> str | None:
        return "No digital clipping detected"
