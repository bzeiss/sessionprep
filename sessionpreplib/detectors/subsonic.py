from __future__ import annotations

import numpy as np

from ..config import ParamSpec
from ..detector import TrackDetector
from ..models import DetectorResult, IssueLocation, Severity, TrackContext
from ..audio import is_silent, subsonic_stft_analysis


class SubsonicDetector(TrackDetector):
    id = "subsonic"
    name = "Subsonic Content"
    shorthand = "SB"
    depends_on = ["silence"]

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        return super().config_params() + [
            ParamSpec(
                key="subsonic_hz", type=(int, float), default=30.0,
                min=0.0, min_exclusive=True,
                label="Subsonic cutoff frequency (Hz)",
                description=(
                    "Frequencies below this are checked for unwanted energy. "
                    "30 Hz is a common high-pass filter point for most "
                    "instruments and a standard cutoff for subsonic content."
                ),
            ),
            ParamSpec(
                key="subsonic_warn_ratio_db", type=(int, float), default=-20.0,
                max=0.0,
                label="Subsonic sensitivity (dB)",
                description=(
                    "How much subsonic energy relative to the overall signal "
                    "triggers a warning. Lower values are more sensitive. "
                    "At the default (\u221220 dB), the detector flags tracks where "
                    "subsonic content accounts for roughly 1% or more of "
                    "total energy. On a single track this is barely "
                    "noticeable, but when 8\u201312 tracks with correlated "
                    "subsonic content (e.g. drum mics picking up room "
                    "rumble) are summed, it can cause audible low-end "
                    "buildup and wasted headroom on the mix bus. "
                    "Use \u221225 for stricter checking, or \u221215 to only "
                    "flag severe cases."
                ),
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
            "Checks for unwanted low-frequency energy below the cutoff "
            "frequency. This energy is usually inaudible but wastes "
            "headroom, can cause speaker excursion, and may introduce "
            "rumble on full-range playback systems."
            "<br/><br/>"
            "<b>Sensitivity guide</b><br/>"
            "The sensitivity value controls how much subsonic energy "
            "(relative to the overall signal) triggers a warning:<br/>"
            "<table style='margin:4px 0; font-size:8pt;'>"
            "<tr><td><b>\u221210 dB</b></td><td style='padding-left:8px;'>"
            "Severe \u2014 ~10% of energy, audible rumble</td></tr>"
            "<tr><td><b>\u221215 dB</b></td><td style='padding-left:8px;'>"
            "Significant \u2014 ~3% of energy, clearly worth filtering</td></tr>"
            "<tr><td><b>\u221220 dB</b></td><td style='padding-left:8px;'>"
            "Default \u2014 ~1%, small per track but adds up when "
            "summing many tracks</td></tr>"
            "<tr><td><b>\u221225 dB</b></td><td style='padding-left:8px;'>"
            "Strict \u2014 ~0.3%, for critical mastering work</td></tr>"
            "</table>"
            "<br/>"
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
            "<b>OK</b> \u2013 Subsonic energy is below the sensitivity threshold.<br/>"
            "<b>ATTENTION</b> \u2013 Significant subsonic energy detected."
            "<br/><br/>"
            "<b>Interpretation</b><br/>"
            "Consider applying a high-pass filter at or near the cutoff "
            "frequency. Common practice is to HPF most tracks at 30\u201340 Hz "
            "to clean up the low end without affecting the audible bass."
        )

    def configure(self, config):
        super().configure(config)
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

        # --- Single-pass STFT per channel ---
        channels_to_analyze: list[tuple[int | None, np.ndarray]] = []
        if nch >= 2 and data.ndim == 2:
            for ch in range(nch):
                channels_to_analyze.append((ch, data[:, ch]))
        else:
            channels_to_analyze.append(
                (None, data if data.ndim == 1 else data[:, 0])
            )

        ch_ratios: dict[int, float] = {}
        ch_warn: dict[int, bool] = {}
        ch_win_ratios: dict[int | None, list[tuple[int, int, float]]] = {}

        for ch, signal in channels_to_analyze:
            whole_ratio, win_ratios = subsonic_stft_analysis(
                signal, track.samplerate, cutoff,
                window_ms=int(self.window_ms),
            )
            ch_key = ch if ch is not None else 0
            ch_ratios[ch_key] = whole_ratio
            ch_warn[ch_key] = bool(np.isfinite(whole_ratio) and whole_ratio >= threshold)
            ch_win_ratios[ch] = win_ratios

        # Combined ratio: worst (highest) per-channel ratio — more
        # conservative than mono-downmix (no phase-cancellation masking).
        combined_ratio = max(ch_ratios.values()) if ch_ratios else float(-np.inf)
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
                ch_win_ratios, channels_to_analyze,
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
                    freq_min_hz=0.0,
                    freq_max_hz=float(self.cutoff_hz),
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
                        freq_min_hz=0.0,
                        freq_max_hz=float(self.cutoff_hz),
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
        ch_win_ratios: dict[int | None, list[tuple[int, int, float]]],
        channels_to_analyze: list[tuple[int | None, np.ndarray]],
    ) -> list[dict]:
        """Merge pre-computed per-window subsonic ratios into contiguous
        regions.  Appends to *issues* and *detail_lines* in-place.
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

        max_reg = int(self.max_regions)
        all_regions: list[dict] = []

        for ch, _signal in channels_to_analyze:
            ratios = ch_win_ratios.get(ch, [])
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
                freq_min_hz=0.0,
                freq_max_hz=float(self.cutoff_hz),
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

        Computes per-window RMS via vectorised reshape, finds the loudest
        window, and marks windows within *gate_db* of the loudest as active.
        Returns merged regions in the same dict format as
        :meth:`_merge_regions`.

        This is the same relative-gating concept used by the RMS anchor
        analysis — it reliably separates musical content from amp noise.
        """
        if signal.ndim != 1 or signal.size == 0:
            return []

        n = signal.size
        win_samples = max(8, int(samplerate * window_ms / 1000))
        n_win = (n + win_samples - 1) // win_samples
        padded = np.pad(
            signal.astype(np.float64), (0, n_win * win_samples - n),
        )
        frames = padded.reshape(n_win, win_samples)
        rms = np.sqrt(np.mean(frames ** 2, axis=1))

        with np.errstate(divide='ignore'):
            rms_db = np.where(rms > 1e-10, 20.0 * np.log10(rms), -200.0)

        max_rms_db = float(np.max(rms_db))
        gate_threshold = max_rms_db - gate_db
        active = rms_db >= gate_threshold

        # Merge contiguous active windows via diff
        changes = np.diff(active.astype(np.int8), prepend=0, append=0)
        starts = np.where(changes == 1)[0]
        ends = np.where(changes == -1)[0] - 1

        regions: list[dict] = []
        for s_idx, e_idx in zip(starts, ends):
            s_sample = int(s_idx * win_samples)
            e_sample = min(int((e_idx + 1) * win_samples), n) - 1
            max_r = float(np.max(rms_db[s_idx:e_idx + 1]))
            regions.append({
                "sample_start": s_sample,
                "sample_end": e_sample,
                "max_ratio_db": max_r,
            })
        return regions

    def clean_message(self) -> str | None:
        return "No significant subsonic content detected"
