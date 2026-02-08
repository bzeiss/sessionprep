from __future__ import annotations

from ..detector import TrackDetector
from ..models import DetectorResult, IssueLocation, Severity, TrackContext
from ..audio import get_peak


class SilenceDetector(TrackDetector):
    id = "silence"
    name = "Silent Files"
    depends_on = []

    @classmethod
    def html_help(cls) -> str:
        return (
            "<b>Description</b><br/>"
            "Checks whether a track is completely silent (peak amplitude is zero)."
            "<br/><br/>"
            "<b>Results</b><br/>"
            "<b>OK</b> – The file contains audio content.<br/>"
            "<b>ATTENTION</b> – The entire file is silent."
            "<br/><br/>"
            "<b>Interpretation</b><br/>"
            "Silent files may be placeholders, accidentally empty exports, "
            "or intentionally blank tracks. Confirm with the mix engineer "
            "before mastering."
        )

    def analyze(self, track: TrackContext) -> DetectorResult:
        peak = get_peak(track)
        silent = peak == 0.0

        if silent:
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.ATTENTION,
                summary="silent",
                data={"is_silent": True},
                hint="confirm intentional",
                issues=[IssueLocation(
                    sample_start=0,
                    sample_end=track.total_samples - 1,
                    channel=None,
                    severity=Severity.ATTENTION,
                    label="silence",
                    description="entire file is silent",
                )],
            )
        return DetectorResult(
            detector_id=self.id,
            severity=Severity.CLEAN,
            summary="not silent",
            data={"is_silent": False},
        )

    def clean_message(self) -> str | None:
        return "No silent files detected"
