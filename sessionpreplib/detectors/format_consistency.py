from __future__ import annotations

from collections import Counter

from ..detector import SessionDetector
from ..models import DetectorResult, Severity, SessionContext


class FormatConsistencyDetector(SessionDetector):
    id = "format_consistency"
    name = "Session Format Consistency"
    shorthand = "FC"

    @classmethod
    def html_help(cls) -> str:
        return (
            "<b>Description</b><br/>"
            "Compares the sample rate and bit depth of all session files "
            "against the most common format to identify deviations."
            "<br/><br/>"
            "<b>Results</b><br/>"
            "<b>OK</b> – File matches the session's most common format.<br/>"
            "<b>PROBLEM</b> – Sample rate and/or bit depth differs."
            "<br/><br/>"
            "<b>Interpretation</b><br/>"
            "Format mismatches can cause sample-rate conversion artifacts or "
            "bit-depth truncation during mixing and mastering. All files in a "
            "session should share the same sample rate and bit depth. Request "
            "corrected exports for mismatched files."
        )

    def analyze(self, session: SessionContext) -> list[DetectorResult]:
        ok_tracks = [t for t in session.tracks if t.status == "OK"]
        if not ok_tracks:
            return []

        sr_counter = Counter(t.samplerate for t in ok_tracks)
        bd_counter = Counter(t.bitdepth for t in ok_tracks)

        most_common_sr = sr_counter.most_common(1)[0][0] if sr_counter else None
        most_common_bd = bd_counter.most_common(1)[0][0] if bd_counter else None

        # Store on session config for other consumers
        session.config["_most_common_sr"] = most_common_sr
        session.config["_most_common_bd"] = most_common_bd

        results = []
        for t in ok_tracks:
            reasons = []
            if most_common_sr is not None and t.samplerate != most_common_sr:
                reasons.append(f"{t.samplerate} Hz")
            if most_common_bd is not None and t.bitdepth != most_common_bd:
                reasons.append(f"{t.bitdepth}")

            if reasons:
                details = ", ".join(reasons)
                results.append(DetectorResult(
                    detector_id=self.id,
                    severity=Severity.PROBLEM,
                    summary=f"format mismatch ({details})",
                    data={
                        "expected_sr": most_common_sr,
                        "expected_bd": most_common_bd,
                        "actual_sr": t.samplerate,
                        "actual_bd": t.bitdepth,
                        "mismatch_reasons": reasons,
                        "filename": t.filename,
                    },
                    hint="request corrected exports",
                ))
            else:
                results.append(DetectorResult(
                    detector_id=self.id,
                    severity=Severity.CLEAN,
                    summary="format OK",
                    data={"filename": t.filename},
                ))

        return results

    def clean_message(self) -> str | None:
        return "No inconsistent session formats"
