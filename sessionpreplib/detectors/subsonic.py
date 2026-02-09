from __future__ import annotations

import numpy as np

from ..config import ParamSpec
from ..detector import TrackDetector
from ..models import DetectorResult, IssueLocation, Severity, TrackContext
from ..audio import (
    is_silent,
    subsonic_ratio_db,
    subsonic_ratio_db_1d,
    subsonic_windowed_ratios,
)


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
            ParamSpec(
                key="subsonic_windowed", type=bool, default=True,
                label="Windowed analysis",
                description=(
                    "When enabled, subsonic issue regions are localized to the "
                    "specific windows that exceed the threshold, instead of "
                    "spanning the entire file. The whole-file analysis summary "
                    "is always performed regardless."
                ),
            ),
            ParamSpec(
                key="subsonic_window_ms", type=int, default=500,
                min=100, max=5000,
                label="Analysis window (ms)",
                description=(
                    "Window length for windowed subsonic analysis. Longer windows "
                    "give better frequency resolution but coarser time localization."
                ),
            ),
            ParamSpec(
                key="subsonic_max_regions", type=int, default=20,
                min=1, max=200,
                label="Max reported regions",
                description="Maximum number of subsonic regions to report per file.",
            ),
        ]

    @classmethod
    def html_help(cls) -> str:
        return (
            "<b>Description</b><br/>"
            "Measures the energy ratio of sub-bass content below a configurable "
            "cutoff frequency relative to the total signal energy."
            "<br/><br/>"
            "<b>Per-channel analysis</b><br/>"
            "For stereo and multi-channel files, each channel is analyzed "
            "independently. If only one channel triggers the warning, the issue "
            "is reported for that specific channel."
            "<br/><br/>"
            "<b>Windowed analysis</b> (optional)<br/>"
            "When enabled, the signal is split into windows and subsonic energy "
            "is measured per window. Contiguous windows that exceed the threshold "
            "are merged into regions with precise sample ranges. This helps "
            "identify localized subsonic problems (bass drops, HVAC bleed in "
            "quiet sections) that a whole-file average might miss."
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
        self.windowed = config.get("subsonic_windowed", True)
        self.window_ms = config.get("subsonic_window_ms", 500)
        self.max_regions = config.get("subsonic_max_regions", 20)

    def analyze(self, track: TrackContext) -> DetectorResult:
        if is_silent(track):
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="silent track",
                data={"subsonic_ratio_db": float(-np.inf), "subsonic_warn": False,
                      "per_channel": {}},
            )

        cutoff = float(self.cutoff_hz)
        threshold = float(self.warn_ratio_db)
        data = track.audio_data
        nch = track.channels

        # --- Whole-file combined (mono) ratio (backward-compatible) ---
        combined_ratio = subsonic_ratio_db(data, track.samplerate, cutoff)

        # --- Per-channel analysis ---
        ch_ratios: dict[int, float] = {}
        ch_warn: dict[int, bool] = {}
        if nch >= 2 and data.ndim == 2:
            for ch in range(nch):
                r = subsonic_ratio_db_1d(data[:, ch], track.samplerate, cutoff)
                ch_ratios[ch] = r
                ch_warn[ch] = bool(np.isfinite(r) and r >= threshold)
        elif data.ndim == 1:
            ch_ratios[0] = combined_ratio
            ch_warn[0] = bool(
                np.isfinite(combined_ratio) and combined_ratio >= threshold
            )

        any_ch_warn = any(ch_warn.values())
        combined_warn = bool(
            np.isfinite(combined_ratio) and combined_ratio >= threshold
        )

        # Build per-channel data dict for result
        per_channel_data = {
            ch: {"ratio_db": ch_ratios[ch], "warn": ch_warn[ch]}
            for ch in sorted(ch_ratios)
        }

        result_data: dict = {
            "subsonic_ratio_db": float(combined_ratio),
            "subsonic_warn": combined_warn or any_ch_warn,
            "per_channel": per_channel_data,
        }

        # --- Determine if we should warn ---
        if not (combined_warn or any_ch_warn):
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="no significant subsonic content",
                data=result_data,
            )

        # --- Build summary and issues ---
        issues: list[IssueLocation] = []
        detail_lines: list[str] = []
        warn_channels = [ch for ch, w in ch_warn.items() if w]
        all_channels_warn = len(warn_channels) == nch

        # Summary text
        if combined_warn:
            summary = (
                f"subsonic energy {float(combined_ratio):.1f} dB "
                f"(<= {self.cutoff_hz:g} Hz)"
            )
        elif any_ch_warn:
            parts = []
            for ch in warn_channels:
                parts.append(f"ch {ch}: {ch_ratios[ch]:.1f} dB")
            summary = (
                f"subsonic energy on {', '.join(parts)} "
                f"(<= {self.cutoff_hz:g} Hz)"
            )

        # Per-channel detail lines
        if nch >= 2:
            for ch in sorted(ch_ratios):
                r = ch_ratios[ch]
                tag = " ⚠" if ch_warn.get(ch) else ""
                if np.isfinite(r):
                    detail_lines.append(f"Channel {ch}: {r:.1f} dB{tag}")

        # --- Windowed analysis or whole-file issues ---
        windowed_regions: list[dict] = []
        if self.windowed:
            windowed_regions = self._windowed_analysis(
                track, cutoff, threshold, issues, detail_lines,
            )
            result_data["windowed_regions"] = windowed_regions

        # If windowed produced no regions (or windowed is off), fall back to
        # a whole-file issue span so ATTENTION always has at least one overlay.
        if not issues:
            if all_channels_warn or nch == 1:
                issues.append(IssueLocation(
                    sample_start=0,
                    sample_end=track.total_samples - 1,
                    channel=None,
                    severity=Severity.ATTENTION,
                    label="subsonic",
                    description=summary,
                ))
            else:
                for ch in warn_channels:
                    desc = (
                        f"subsonic energy ch {ch}: {ch_ratios[ch]:.1f} dB "
                        f"(<= {self.cutoff_hz:g} Hz)"
                    )
                    issues.append(IssueLocation(
                        sample_start=0,
                        sample_end=track.total_samples - 1,
                        channel=ch,
                        severity=Severity.ATTENTION,
                        label="subsonic",
                        description=desc,
                    ))

        return DetectorResult(
            detector_id=self.id,
            severity=Severity.ATTENTION,
            summary=summary,
            data=result_data,
            detail_lines=detail_lines if detail_lines else [],
            hint=f"consider HPF ~{self.cutoff_hz:g} Hz",
            issues=issues,
        )

    # ------------------------------------------------------------------
    # Windowed analysis helper
    # ------------------------------------------------------------------

    def _windowed_analysis(
        self,
        track: TrackContext,
        cutoff: float,
        threshold: float,
        issues: list[IssueLocation],
        detail_lines: list[str],
    ) -> list[dict]:
        """Run per-window subsonic analysis and merge exceeding windows into
        contiguous regions.  Appends to *issues* and *detail_lines* in-place.
        Returns a list of region dicts for the result data.

        Two-tier approach:
        1. Try to find windows where the subsonic ratio exceeds a relaxed
           threshold (6 dB below configured).  These are concentrated subsonic
           problems.
        2. If no ratio-based regions are found, fall back to marking all
           windows that have *any* measurable signal (finite ratio — i.e. they
           passed the absolute subsonic power gate in the DSP function).
           Since the whole-file analysis already confirmed subsonic content,
           these active-signal windows are where that content lives.  This
           avoids painting silent gaps between notes.
        """
        _WINDOWED_RELAX_DB = 6.0
        windowed_threshold = threshold - _WINDOWED_RELAX_DB

        data = track.audio_data
        nch = track.channels
        max_reg = int(self.max_regions)
        all_regions: list[dict] = []
        all_ratios: list[tuple[int | None, list[tuple[int, int, float]]]] = []

        # Analyze each channel (or mono)
        channels_to_analyze: list[tuple[int | None, np.ndarray]] = []
        if nch >= 2 and data.ndim == 2:
            for ch in range(nch):
                channels_to_analyze.append((ch, data[:, ch]))
        else:
            channels_to_analyze.append((None, data if data.ndim == 1 else data[:, 0]))

        for ch, signal in channels_to_analyze:
            ratios = subsonic_windowed_ratios(
                signal, track.samplerate, cutoff,
                window_ms=int(self.window_ms),
            )
            all_ratios.append((ch, ratios))
            # Merge contiguous exceeding windows into regions
            regions = self._merge_regions(ratios, windowed_threshold)
            for reg in regions:
                reg["channel"] = ch
            all_regions.extend(regions)

        # Fallback: if no ratio-based regions found, mark active-signal
        # windows using an RMS envelope gate.  The whole-file analysis already
        # confirmed subsonic content; the active regions are where it lives.
        # This reliably separates musical content from amp noise/silence.
        if not all_regions:
            for ch, signal in channels_to_analyze:
                regions = self._find_active_regions(
                    signal, track.samplerate, int(self.window_ms),
                )
                for reg in regions:
                    reg["channel"] = ch
                all_regions.extend(regions)

        # Sort by worst ratio (descending) and cap
        all_regions.sort(key=lambda r: -r["max_ratio_db"])
        all_regions = all_regions[:max_reg]

        # Build issues and detail lines from regions
        for reg in all_regions:
            ch = reg["channel"]
            desc = (
                f"subsonic region {reg['max_ratio_db']:.1f} dB "
                f"(<= {self.cutoff_hz:g} Hz)"
            )
            if ch is not None:
                desc = f"ch {ch}: {desc}"
            issues.append(IssueLocation(
                sample_start=reg["sample_start"],
                sample_end=reg["sample_end"],
                channel=ch,
                severity=Severity.ATTENTION,
                label="subsonic",
                description=desc,
            ))

        if all_regions:
            detail_lines.append(
                f"Windowed analysis: {len(all_regions)} region(s) "
                f"exceeding {self.warn_ratio_db:g} dB"
            )

        return all_regions

    @staticmethod
    def _merge_regions(
        ratios: list[tuple[int, int, float]],
        threshold: float,
    ) -> list[dict]:
        """Merge contiguous windows that exceed *threshold* into regions."""
        regions: list[dict] = []
        current: dict | None = None
        for s_start, s_end, ratio in ratios:
            exceeds = bool(np.isfinite(ratio) and ratio >= threshold)
            if exceeds:
                if current is None:
                    current = {
                        "sample_start": s_start,
                        "sample_end": s_end,
                        "max_ratio_db": ratio,
                    }
                else:
                    current["sample_end"] = s_end
                    current["max_ratio_db"] = max(current["max_ratio_db"], ratio)
            else:
                if current is not None:
                    regions.append(current)
                    current = None
        if current is not None:
            regions.append(current)
        return regions

    @staticmethod
    def _find_active_regions(
        signal: np.ndarray,
        samplerate: int,
        window_ms: int,
        gate_db: float = 20.0,
    ) -> list[dict]:
        """Find contiguous regions where the signal is active (not noise/silence).

        Computes per-window RMS, finds the loudest window, and marks windows
        within *gate_db* of the loudest as active.  Returns merged regions in
        the same dict format as :meth:`_merge_regions`.

        This is the same relative-gating concept used by the RMS anchor
        analysis — it reliably separates musical content from amp noise.
        """
        if signal.ndim != 1 or signal.size == 0:
            return []

        win_samples = max(8, int(samplerate * window_ms / 1000))
        # Compute per-window RMS
        windows: list[tuple[int, int, float]] = []
        pos = 0
        n = signal.size
        while pos < n:
            end = min(pos + win_samples, n)
            chunk = signal[pos:end].astype(np.float64)
            rms = float(np.sqrt(np.mean(chunk ** 2))) if chunk.size > 0 else 0.0
            rms_db = float(20.0 * np.log10(rms)) if rms > 1e-10 else -200.0
            windows.append((pos, end - 1, rms_db))
            pos += win_samples

        if not windows:
            return []

        max_rms_db = max(w[2] for w in windows)
        gate_threshold = max_rms_db - gate_db

        # Merge contiguous active windows
        regions: list[dict] = []
        current: dict | None = None
        for s_start, s_end, rms_db in windows:
            if rms_db >= gate_threshold:
                if current is None:
                    current = {
                        "sample_start": s_start,
                        "sample_end": s_end,
                        "max_ratio_db": rms_db,  # store RMS as proxy
                    }
                else:
                    current["sample_end"] = s_end
                    current["max_ratio_db"] = max(current["max_ratio_db"], rms_db)
            else:
                if current is not None:
                    regions.append(current)
                    current = None
        if current is not None:
            regions.append(current)
        return regions

    def clean_message(self) -> str | None:
        return "No significant subsonic content detected"
