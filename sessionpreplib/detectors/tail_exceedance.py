from __future__ import annotations

import numpy as np

from ..config import ParamSpec
from ..detector import TrackDetector
from ..models import DetectorResult, IssueLocation, Severity, TrackContext
from ..audio import (
    get_rms_window_means,
    get_gated_rms_data,
    get_window_samples,
    is_silent,
    linear_to_db,
    format_duration,
)


class TailExceedanceDetector(TrackDetector):
    id = "tail_exceedance"
    name = "Tail Regions Exceeded Anchor"
    depends_on = ["silence", "audio_classifier"]

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        return super().config_params() + [
            ParamSpec(
                key="tail_min_exceed_db", type=(int, float), default=3.0,
                min=0.0, min_exclusive=True,
                label="Minimum tail exceedance (dB)",
                description="RMS windows exceeding the anchor by at least this many dB are flagged.",
            ),
            ParamSpec(
                key="tail_max_regions", type=int, default=20, min=1,
                label="Max tail exceedance regions",
                description="Maximum number of exceedance regions to report per track.",
            ),
            ParamSpec(
                key="tail_hop_ms", type=int, default=10, min=1,
                label="Tail hop size (ms)",
                description="Hop between RMS window evaluations for tail detection.",
            ),
        ]

    @classmethod
    def html_help(cls) -> str:
        return (
            "<b>Description</b><br/>"
            "Identifies regions where the RMS level significantly exceeds the "
            "percentile-based anchor RMS. Uses a hop-based scan to find "
            "contiguous loud sections."
            "<br/><br/>"
            "<b>Results</b><br/>"
            "<b>OK</b> – No tail exceedances found.<br/>"
            "<b>ATTENTION</b> – Regions found that exceed the anchor by more "
            "than the configured threshold (reported with count and dB values)."
            "<br/><br/>"
            "<b>Interpretation</b><br/>"
            "Tail exceedance regions are significantly louder than the track's "
            "typical level. This may indicate automation rides, unexpected "
            "volume spikes, or sections that need level matching. Consider "
            "section-based gain riding."
        )

    def is_relevant(self, result: DetectorResult, track: TrackContext | None = None) -> bool:
        """Tail exceedance is only meaningful when normalizing to the RMS anchor.

        If the processor chose a peak-based method (Transient peak targeting
        or Sustained peak-limited), the anchor is irrelevant and this
        detector's result would be noise.
        """
        if track and track.processor_results:
            pr = next(iter(track.processor_results.values()), None)
            if pr and pr.method and "RMS" not in pr.method:
                return False
        return True

    def configure(self, config):
        super().configure(config)
        self.window_ms = config.get("window", 400)
        self.stereo_mode = config.get("stereo_mode", "avg")
        self.rms_anchor_mode = config.get("rms_anchor", "percentile")
        self.rms_percentile = config.get("rms_percentile", 95.0)
        self.gate_relative_db = config.get("gate_relative_db", 40.0)
        self.tail_min_exceed_db = config.get("tail_min_exceed_db", 3.0)
        self.tail_max_regions = config.get("tail_max_regions", 20)
        self.tail_hop_ms = config.get("tail_hop_ms", 10)

    def analyze(self, track: TrackContext) -> DetectorResult:
        empty_data = {
            "tail_regions": [],
            "tail_summary": {
                "regions": 0,
                "total_duration_sec": 0.0,
                "max_exceed_db": 0.0,
                "anchor_db": float(-np.inf),
            },
        }

        if is_silent(track):
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="silent track",
                data=empty_data,
            )

        if self.rms_anchor_mode != "percentile":
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="tail reporting only for percentile anchoring",
                data=empty_data,
            )

        # Read anchor_mean from audio_classifier detector result
        crest_result = track.detector_results.get("audio_classifier")
        if crest_result is None:
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="audio_classifier result missing",
                data=empty_data,
            )

        anchor_mean = crest_result.data.get("rms_anchor_mean", 0.0)
        rms_anchor_db = crest_result.data.get("rms_anchor_db", float(-np.inf))

        if anchor_mean <= 0:
            empty_data["tail_summary"]["anchor_db"] = rms_anchor_db
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="anchor is zero",
                data=empty_data,
            )

        # Get shared cached data
        window_means = get_rms_window_means(track, self.window_ms, self.stereo_mode)
        gated = get_gated_rms_data(
            track, self.window_ms, self.stereo_mode, self.gate_relative_db
        )
        active_mask = gated["active_mask"]
        window_samples = get_window_samples(track, self.window_ms)
        samplerate = track.samplerate
        floor = np.finfo(np.float64).tiny

        hop_samples = max(1, int((self.tail_hop_ms / 1000) * samplerate))
        starts_all = np.arange(0, window_means.size, hop_samples, dtype=np.int64)
        sampled_active_mask = active_mask[starts_all]
        active_hop_indices = starts_all[sampled_active_mask]
        sampled_means = window_means[active_hop_indices]

        exceed_db = 10.0 * np.log10(np.maximum(sampled_means, floor) / anchor_mean)
        mask = exceed_db > float(self.tail_min_exceed_db)

        idx = np.flatnonzero(mask)
        if idx.size == 0:
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="no significant tail exceedances",
                data={
                    "tail_regions": [],
                    "tail_summary": {
                        "regions": 0,
                        "total_duration_sec": 0.0,
                        "max_exceed_db": 0.0,
                        "anchor_db": float(rms_anchor_db),
                    },
                },
            )

        splits = np.flatnonzero(np.diff(idx) > 1)
        starts = np.concatenate(([0], splits + 1))
        ends = np.concatenate((splits, [idx.size - 1]))

        data_shape_0 = int(track.audio_data.shape[0]) if track.audio_data is not None else 0
        regions = []
        for s_i, e_i in zip(starts, ends):
            i_start = int(idx[s_i])
            i_end = int(idx[e_i])

            sample_start = int(active_hop_indices[i_start])
            sample_end = min(
                int(active_hop_indices[i_end]) + window_samples, data_shape_0
            )

            region_exceed_max = float(np.max(exceed_db[i_start:i_end + 1]))
            region_mean_max = float(np.max(sampled_means[i_start:i_end + 1]))
            region_rms_max_db = linear_to_db(np.sqrt(region_mean_max))

            regions.append({
                "start_sample": sample_start,
                "end_sample": sample_end,
                "start_time": format_duration(sample_start, samplerate),
                "end_time": format_duration(sample_end, samplerate),
                "max_exceed_db": region_exceed_max,
                "max_rms_db": region_rms_max_db,
            })

        # Pick top regions by exceedance, keep chronological order
        regions.sort(key=lambda r: r["max_exceed_db"], reverse=True)
        regions = regions[:int(self.tail_max_regions)]
        regions.sort(key=lambda r: r["start_sample"])

        total_duration_sec = sum(
            (r["end_sample"] - r["start_sample"]) / samplerate for r in regions
        )
        max_exceed = max((r["max_exceed_db"] for r in regions), default=0.0)

        tail_summary = {
            "regions": len(regions),
            "total_duration_sec": float(total_duration_sec),
            "max_exceed_db": float(max_exceed),
            "anchor_db": float(rms_anchor_db),
        }

        summary_text = (
            f"{len(regions)} tail region(s) exceed anchor "
            f"by >{self.tail_min_exceed_db:g} dB (max +{max_exceed:.1f} dB)"
        )

        issues = [
            IssueLocation(
                sample_start=r["start_sample"],
                sample_end=r["end_sample"],
                channel=None,
                severity=Severity.ATTENTION,
                label="tail_exceedance",
                description=f"tail exceeds anchor by +{r['max_exceed_db']:.1f} dB "
                            f"({r['start_time']}–{r['end_time']})",
            )
            for r in regions
        ]
        return DetectorResult(
            detector_id=self.id,
            severity=Severity.ATTENTION,
            summary=summary_text,
            data={"tail_regions": regions, "tail_summary": tail_summary},
            hint="check for section-based riding",
            issues=issues,
        )
