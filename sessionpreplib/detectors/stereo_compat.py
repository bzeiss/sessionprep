from __future__ import annotations

import math

import numpy as np

from ..config import ParamSpec
from ..detector import TrackDetector
from ..models import DetectorResult, IssueLocation, Severity, TrackContext
from ..audio import is_silent, windowed_stereo_correlation


class StereoCompatDetector(TrackDetector):
    id = "stereo_compat"
    name = "Stereo Compatibility"
    depends_on = ["silence"]

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        return super().config_params() + [
            ParamSpec(
                key="corr_warn", type=(int, float), default=-0.3,
                min=-1.0, max=1.0,
                label="Stereo correlation warning threshold",
                description=(
                    "Correlation below this value triggers a stereo-compatibility "
                    "warning. Values near +1 indicate highly correlated (mono-"
                    "compatible) stereo; values near 0 indicate wide stereo; "
                    "negative values indicate phase cancellation."
                ),
            ),
            ParamSpec(
                key="mono_loss_warn_db", type=(int, float), default=6.0,
                min=0.0, min_exclusive=True,
                label="Mono folddown loss warning (dB)",
                description=(
                    "Mono fold-down loss above this level triggers a warning. "
                    "This measures how much RMS level is lost when summing "
                    "stereo to mono via (L+R)/2."
                ),
            ),
            ParamSpec(
                key="corr_windowed", type=bool, default=True,
                label="Windowed analysis",
                description=(
                    "When enabled, stereo compatibility is analyzed per window "
                    "to localize regions with poor mono compatibility. The "
                    "whole-file summary is always computed regardless."
                ),
            ),
            ParamSpec(
                key="corr_window_ms", type=int, default=500,
                min=100, max=5000,
                label="Analysis window (ms)",
                description=(
                    "Window length for windowed stereo analysis. 500 ms gives "
                    "roughly musical-bar resolution at moderate tempos. Shorter "
                    "windows reveal transient phase issues but are noisier."
                ),
            ),
            ParamSpec(
                key="corr_max_regions", type=int, default=20,
                min=1, max=200,
                label="Max reported regions",
                description="Maximum number of low-correlation regions to report per file.",
            ),
        ]

    @classmethod
    def html_help(cls) -> str:
        return (
            "<b>Description</b><br/>"
            "Analyzes stereo compatibility by computing the Pearson correlation "
            "coefficient between left and right channels and measuring mono "
            "fold-down loss ((L+R)/2 vs. stereo RMS)."
            "<br/><br/>"
            "<b>Correlation values</b><br/>"
            "<table style='margin:4px 0; font-size:8pt;'>"
            "<tr><td><b>+1.0</b></td><td style='padding-left:8px;'>"
            "Identical L/R (dual mono) \u2014 perfect mono compatibility</td></tr>"
            "<tr><td><b>+0.5 to +1.0</b></td><td style='padding-left:8px;'>"
            "Typical stereo \u2014 good mono compatibility</td></tr>"
            "<tr><td><b>0.0</b></td><td style='padding-left:8px;'>"
            "Uncorrelated \u2014 wide stereo, ~3 dB mono loss</td></tr>"
            "<tr><td><b>&lt; 0</b></td><td style='padding-left:8px;'>"
            "Out of phase \u2014 mono cancellation, level loss or full "
            "cancellation</td></tr>"
            "</table>"
            "<br/>"
            "<b>Mono folddown loss</b><br/>"
            "Measures how much RMS level is lost when summing L+R to mono. "
            "High loss means significant cancellation affecting phone speakers, "
            "mono PA systems, and broadcast."
            "<br/><br/>"
            "<b>Windowed analysis</b> (optional)<br/>"
            "When enabled, the signal is split into windows and both metrics "
            "are computed per window. Contiguous windows that exceed either "
            "threshold are merged into regions with precise sample ranges. "
            "This helps identify localized phase problems (a widener plugin, "
            "phase-inverted reverb tail, mid/side EQ) that a whole-file "
            "average might mask."
            "<br/><br/>"
            "<b>Results</b><br/>"
            "<b>OK</b> \u2013 Stereo compatibility is acceptable.<br/>"
            "<b>INFO</b> \u2013 Whole-file correlation or mono loss exceeds threshold.<br/>"
            "<b>ATTENTION</b> \u2013 Localized regions with poor mono compatibility."
            "<br/><br/>"
            "<b>Interpretation</b><br/>"
            "Review stereo widening, panning, or mid/side processing in the "
            "flagged regions. Consider checking the mix in mono to verify "
            "acceptability."
        )

    def configure(self, config):
        super().configure(config)
        self.corr_warn = config.get("corr_warn", -0.3)
        self.mono_loss_warn_db = config.get("mono_loss_warn_db", 6.0)
        self.windowed = config.get("corr_windowed", True)
        self.window_ms = config.get("corr_window_ms", 500)
        self.max_regions = config.get("corr_max_regions", 20)

    def analyze(self, track: TrackContext) -> DetectorResult:
        if is_silent(track):
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="silent track",
                data={"lr_corr": None, "mono_loss_db": None,
                      "corr_warn": False, "mono_warn": False},
            )

        data = track.audio_data
        if data is None or data.ndim < 2 or data.shape[1] < 2:
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="not stereo",
                data={"lr_corr": None, "mono_loss_db": None,
                      "corr_warn": False, "mono_warn": False},
            )

        if data.shape[1] > 2:
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="multichannel (>2ch), skipped",
                data={"lr_corr": None, "mono_loss_db": None,
                      "corr_warn": False, "mono_warn": False},
            )

        left = data[:, 0]
        right = data[:, 1]

        whole_corr, whole_mono_loss, win_results = windowed_stereo_correlation(
            left, right, track.samplerate,
            window_ms=int(self.window_ms),
        )

        corr_threshold = float(self.corr_warn)
        mono_threshold = float(self.mono_loss_warn_db)

        # Whole-file warnings
        corr_warn = (not math.isnan(whole_corr)
                     and whole_corr < corr_threshold)
        mono_warn = (math.isfinite(whole_mono_loss)
                     and whole_mono_loss > mono_threshold)

        result_data: dict = {
            "lr_corr": whole_corr if not math.isnan(whole_corr) else None,
            "mono_loss_db": whole_mono_loss if math.isfinite(whole_mono_loss) else (
                float('inf') if whole_mono_loss == float('inf') else None),
            "corr_warn": corr_warn,
            "mono_warn": mono_warn,
        }

        # If nothing triggers at whole-file level, return CLEAN early
        # (windowed analysis can still upgrade to ATTENTION below)
        any_whole_warn = corr_warn or mono_warn

        # --- Windowed analysis ---
        issues: list[IssueLocation] = []
        detail_lines: list[str] = []
        windowed_regions: list[dict] = []
        has_attention_regions = False

        if self.windowed and win_results:
            windowed_regions = self._windowed_analysis(
                track, win_results, corr_threshold, mono_threshold,
                issues, detail_lines,
            )
            result_data["windowed_regions"] = windowed_regions
            has_attention_regions = len(issues) > 0

            if windowed_regions:
                min_corr = min(r["min_corr"] for r in windowed_regions)
                max_loss = max(r["max_mono_loss_db"] for r in windowed_regions
                               if math.isfinite(r["max_mono_loss_db"]))  \
                    if any(math.isfinite(r["max_mono_loss_db"])
                           for r in windowed_regions) else 0.0
                result_data["min_window_corr"] = min_corr
                result_data["max_window_mono_loss_db"] = max_loss

        # --- Determine severity ---
        if has_attention_regions:
            severity = Severity.ATTENTION
        elif any_whole_warn:
            severity = Severity.INFO
        else:
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="stereo compatibility OK",
                data=result_data,
            )

        # --- Build summary ---
        summary_parts: list[str] = []
        if corr_warn and result_data["lr_corr"] is not None:
            summary_parts.append(
                f"corr {result_data['lr_corr']:.2f} (< {corr_threshold:g})")
        if mono_warn and result_data["mono_loss_db"] is not None:
            ml = result_data["mono_loss_db"]
            if math.isfinite(ml):
                summary_parts.append(
                    f"mono loss {ml:.1f} dB (> {mono_threshold:g} dB)")
            else:
                summary_parts.append(
                    f"mono loss \u221e dB (> {mono_threshold:g} dB)")
        if has_attention_regions and not summary_parts:
            # Whole-file OK but regions are bad
            mc = result_data.get("min_window_corr")
            if mc is not None:
                summary_parts.append(
                    f"regions: min corr {mc:.2f}")

        summary = ", ".join(summary_parts) if summary_parts else "stereo compat issue"

        # Whole-file issue span as fallback when no windowed regions
        if not issues:
            issues.append(IssueLocation(
                sample_start=0,
                sample_end=track.total_samples - 1,
                channel=None,
                severity=severity,
                label=self.id,
                description=summary,
            ))

        hint_parts = []
        if corr_warn or has_attention_regions:
            hint_parts.append("review stereo widening / mid-side processing")
        if mono_warn:
            hint_parts.append("check mix in mono")

        return DetectorResult(
            detector_id=self.id,
            severity=severity,
            summary=summary,
            data=result_data,
            detail_lines=detail_lines,
            hint=", ".join(hint_parts) if hint_parts else None,
            issues=issues,
        )

    # ------------------------------------------------------------------
    # Windowed analysis helper
    # ------------------------------------------------------------------

    def _windowed_analysis(
        self,
        track: TrackContext,
        win_results: list[tuple[int, int, float, float]],
        corr_threshold: float,
        mono_threshold: float,
        issues: list[IssueLocation],
        detail_lines: list[str],
    ) -> list[dict]:
        """Merge per-window results into contiguous regions.

        A window is flagged if its correlation is below *corr_threshold*
        **or** its mono loss exceeds *mono_threshold*.

        If no threshold-based regions are found but the whole-file analysis
        triggered a warning, falls back to marking active-signal windows
        so ATTENTION always has at least one overlay.
        """
        max_reg = int(self.max_regions)

        # Primary: merge windows exceeding either threshold
        regions = self._merge_regions(
            win_results, corr_threshold, mono_threshold)

        # Fallback: if no regions found, mark active-signal windows
        if not regions:
            regions = self._find_active_regions(
                win_results, track.samplerate, int(self.window_ms))

        # Sort by worst correlation (ascending) and cap
        regions.sort(key=lambda r: r["min_corr"])
        regions = regions[:max_reg]

        # Build issues and detail lines
        for reg in regions:
            corr_str = f"corr {reg['min_corr']:.2f}"
            loss = reg["max_mono_loss_db"]
            if math.isfinite(loss) and loss > 0.01:
                loss_str = f" / mono loss {loss:.1f} dB"
            else:
                loss_str = ""

            t_start = reg["sample_start"] / track.samplerate
            t_end = reg["sample_end"] / track.samplerate
            time_str = f"{_fmt_time(t_start)}\u2013{_fmt_time(t_end)}"

            desc = f"{corr_str}{loss_str} ({time_str})"
            issues.append(IssueLocation(
                sample_start=reg["sample_start"],
                sample_end=reg["sample_end"],
                channel=None,
                severity=Severity.ATTENTION,
                label=self.id,
                description=desc,
            ))

        if regions:
            worst = regions[0]
            detail_lines.append(
                f"Windowed: {len(regions)} region(s) with poor stereo compat")
            loss = worst["max_mono_loss_db"]
            loss_part = f" / mono loss {loss:.1f} dB" if math.isfinite(loss) else ""
            t_s = worst["sample_start"] / track.samplerate
            t_e = worst["sample_end"] / track.samplerate
            detail_lines.append(
                f"Worst: corr {worst['min_corr']:.2f}{loss_part} "
                f"at {_fmt_time(t_s)}\u2013{_fmt_time(t_e)}")

        return regions

    @staticmethod
    def _merge_regions(
        win_results: list[tuple[int, int, float, float]],
        corr_threshold: float,
        mono_threshold: float,
    ) -> list[dict]:
        """Merge contiguous windows that exceed either threshold."""
        regions: list[dict] = []
        current: dict | None = None
        for s_start, s_end, corr, mono_loss in win_results:
            if math.isnan(corr):
                # Silent window — close current region
                if current is not None:
                    regions.append(current)
                    current = None
                continue
            exceeds = (corr < corr_threshold
                       or (math.isfinite(mono_loss)
                           and mono_loss > mono_threshold))
            if exceeds:
                if current is None:
                    current = {
                        "sample_start": s_start,
                        "sample_end": s_end,
                        "min_corr": corr,
                        "max_mono_loss_db": mono_loss if math.isfinite(mono_loss) else 0.0,
                    }
                else:
                    current["sample_end"] = s_end
                    current["min_corr"] = min(current["min_corr"], corr)
                    ml = mono_loss if math.isfinite(mono_loss) else 0.0
                    current["max_mono_loss_db"] = max(
                        current["max_mono_loss_db"], ml)
            else:
                if current is not None:
                    regions.append(current)
                    current = None
        if current is not None:
            regions.append(current)
        return regions

    @staticmethod
    def _find_active_regions(
        win_results: list[tuple[int, int, float, float]],
        samplerate: int,
        window_ms: int,
    ) -> list[dict]:
        """Mark active-signal windows as regions (fallback)."""
        regions: list[dict] = []
        current: dict | None = None
        for s_start, s_end, corr, mono_loss in win_results:
            if math.isnan(corr):
                if current is not None:
                    regions.append(current)
                    current = None
                continue
            if current is None:
                current = {
                    "sample_start": s_start,
                    "sample_end": s_end,
                    "min_corr": corr,
                    "max_mono_loss_db": mono_loss if math.isfinite(mono_loss) else 0.0,
                }
            else:
                current["sample_end"] = s_end
                current["min_corr"] = min(current["min_corr"], corr)
                ml = mono_loss if math.isfinite(mono_loss) else 0.0
                current["max_mono_loss_db"] = max(
                    current["max_mono_loss_db"], ml)
        if current is not None:
            regions.append(current)
        return regions

    # ------------------------------------------------------------------
    # HTML rendering
    # ------------------------------------------------------------------

    def render_html(self, result: DetectorResult,
                    track: TrackContext | None = None) -> str:
        eff = self.effective_severity(result)
        if eff is None:
            return ""
        if not self.is_relevant(result, track):
            return ""

        sev = eff.value
        sev_color, sev_label = {
            "problem":     ("#ff4444", "PROBLEM"),
            "attention":   ("#ffaa00", "ATTENTION"),
            "information": ("#4499ff", "INFO"),
            "info":        ("#4499ff", "INFO"),
            "clean":       ("#44cc44", "OK"),
        }.get(sev, ("#4499ff", "INFO"))

        d = result.data or {}
        parts: list[str] = []

        lr_corr = d.get("lr_corr")
        if lr_corr is not None:
            parts.append(f"corr {lr_corr:.2f}")

        mono_loss = d.get("mono_loss_db")
        if mono_loss is not None and math.isfinite(mono_loss):
            parts.append(f"mono loss {mono_loss:.1f} dB")
        elif mono_loss == float('inf'):
            parts.append("mono loss \u221e dB")

        regions = d.get("windowed_regions", [])
        if regions:
            parts.append(f"{len(regions)} region(s)")

        summary = " \u00b7 ".join(parts) if parts else str(result.summary)

        return (
            f'<tr>'
            f'<td width="90" style="background-color:{sev_color}; color:#000;'
            f' font-weight:bold; font-size:8pt; text-align:center;'
            f' padding:2px 8px;">'
            f'{sev_label}</td>'
            f'<td style="padding-left:6px; white-space:nowrap;">'
            f'<a href="detector:{self.id}" style="color:#dddddd;'
            f' text-decoration:none;"><b>{self.id}</b></a></td>'
            f'<td style="padding-left:6px; color:#888888;">'
            f'{summary}</td>'
            f'</tr>'
        )

    def clean_message(self) -> str | None:
        return "No stereo compatibility issues detected"


def _fmt_time(seconds: float) -> str:
    """Format seconds as M:SS.s"""
    m = int(seconds) // 60
    s = seconds - m * 60
    return f"{m}:{s:04.1f}"
