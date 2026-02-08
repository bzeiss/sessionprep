from __future__ import annotations

from collections import Counter

from ..detector import SessionDetector
from ..models import DetectorResult, Severity, SessionContext
from ..audio import format_duration


class LengthConsistencyDetector(SessionDetector):
    id = "length_consistency"
    name = "File Length Consistency"

    @classmethod
    def html_help(cls) -> str:
        return (
            "<b>Description</b><br/>"
            "Compares file lengths across all session files, normalised to a "
            "common sample rate, to identify deviations from the most common "
            "length."
            "<br/><br/>"
            "<b>Results</b><br/>"
            "<b>OK</b> – File length matches the session's most common length.<br/>"
            "<b>PROBLEM</b> – Length differs (reported with sample count and duration)."
            "<br/><br/>"
            "<b>Interpretation</b><br/>"
            "In a well-prepared session all stems should have the same length "
            "(typically the full session duration). Mismatches indicate files "
            "that may not be properly aligned or were exported from different "
            "time ranges. Request aligned re-exports."
        )

    def analyze(self, session: SessionContext) -> list[DetectorResult]:
        ok_tracks = [t for t in session.tracks if t.status == "OK"]
        if not ok_tracks:
            return []

        most_common_sr = session.config.get("_most_common_sr")
        if most_common_sr is None:
            # Compute it ourselves if format_consistency hasn't run
            from collections import Counter as C
            sr_counter = C(t.samplerate for t in ok_tracks)
            most_common_sr = sr_counter.most_common(1)[0][0] if sr_counter else None

        if most_common_sr is None or most_common_sr <= 0:
            return []

        # Compute equivalent lengths normalized to most common SR
        eq_len_counter: Counter = Counter()
        track_eq_map: dict[str, int] = {}
        for t in ok_tracks:
            sr_i = int(t.samplerate)
            n_i = int(t.total_samples)
            if sr_i <= 0:
                continue
            eq = int((n_i * int(most_common_sr) + (sr_i // 2)) // sr_i)
            eq_len_counter[eq] += 1
            track_eq_map[t.filename] = eq

        if not eq_len_counter:
            return []

        most_common_len = eq_len_counter.most_common(1)[0][0]
        most_common_len_fmt = format_duration(int(most_common_len), int(most_common_sr))

        # Store for other consumers
        session.config["_most_common_len"] = most_common_len
        session.config["_most_common_len_fmt"] = most_common_len_fmt

        results = []
        for t in ok_tracks:
            eq = track_eq_map.get(t.filename)
            if eq is None:
                continue
            if eq != int(most_common_len):
                eq_fmt = format_duration(int(eq), int(most_common_sr))
                results.append(DetectorResult(
                    detector_id=self.id,
                    severity=Severity.PROBLEM,
                    summary=f"length mismatch ({int(eq)} samples / {eq_fmt})",
                    data={
                        "expected_samples": int(most_common_len),
                        "expected_duration_fmt": most_common_len_fmt,
                        "actual_samples": int(eq),
                        "actual_duration_fmt": eq_fmt,
                        "filename": t.filename,
                    },
                    hint="request aligned exports",
                ))
            else:
                results.append(DetectorResult(
                    detector_id=self.id,
                    severity=Severity.CLEAN,
                    summary="length OK",
                    data={"filename": t.filename},
                ))

        return results

    def clean_message(self) -> str | None:
        return "No inconsistent file lengths"
