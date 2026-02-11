from __future__ import annotations

import math

import numpy as np

from ..config import ParamSpec
from ..processor import AudioProcessor, PRIORITY_NORMALIZE
from ..models import ProcessorResult, TrackContext
from ..audio import db_to_linear, dbfs_offset


class BimodalNormalizeProcessor(AudioProcessor):
    id = "bimodal_normalize"
    name = "Bimodal Normalization"
    priority = PRIORITY_NORMALIZE

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        return [
            ParamSpec(
                key="target_rms", type=(int, float), default=-18.0,
                min=-80.0, max=0.0,
                label="Target RMS (dBFS)",
                description="Sustained tracks are RMS-normalized to this level.",
            ),
            ParamSpec(
                key="target_peak", type=(int, float), default=-6.0,
                min=-80.0, max=0.0,
                label="Target peak (dBFS)",
                description="Transient tracks are peak-normalized to this level.",
            ),
        ]

    def configure(self, config):
        self.target_rms = config.get("target_rms", -18.0)
        self.target_peak = config.get("target_peak", -6.0)
        self._db_offset = dbfs_offset(config)
        anchor_mode = config.get("rms_anchor", "percentile")
        if anchor_mode == "max":
            self._rms_anchor_label = "max"
        else:
            pct = config.get("rms_percentile", 95.0)
            self._rms_anchor_label = f"p{pct:g}"

    def process(self, track: TrackContext) -> ProcessorResult:
        # Read from audio_classifier detector
        crest_result = track.detector_results.get("audio_classifier")
        silence_result = track.detector_results.get("silence")

        if silence_result and silence_result.data.get("is_silent"):
            return ProcessorResult(
                processor_id=self.id,
                gain_db=0.0,
                classification="Silent",
                method="None",
                data={"gain_db_individual": 0.0},
            )

        if crest_result is None:
            return ProcessorResult(
                processor_id=self.id,
                gain_db=0.0,
                classification="Unknown",
                method="None",
                data={"gain_db_individual": 0.0},
                error="audio_classifier detector result missing",
            )

        # Check for user override from the GUI dropdown
        override = track.classification_override
        if override == "Skip":
            return ProcessorResult(
                processor_id=self.id,
                gain_db=0.0,
                classification="Skip",
                method="None",
                data={"gain_db_individual": 0.0},
            )

        peak_db = crest_result.data["peak_db"]
        rms_anchor_db = crest_result.data["rms_anchor_db"]

        if override in ("Transient", "Sustained"):
            classification = override
            is_transient = override == "Transient"
        else:
            classification = crest_result.data["classification"]
            is_transient = crest_result.data["is_transient"]

        # Compute both gain paths for transparency
        gain_for_peak = self.target_peak - peak_db
        gain_for_rms = self.target_rms - rms_anchor_db

        if is_transient:
            gain = gain_for_peak
            method = f"Peak → {self.target_peak:.0f} dB"
        else:
            gain = min(gain_for_rms, gain_for_peak)
            if gain == gain_for_rms:
                method = f"RMS → {self.target_rms:.0f} dB"
            else:
                method = "Peak Limited"

        return ProcessorResult(
            processor_id=self.id,
            gain_db=float(gain),
            classification=classification,
            method=method,
            data={
                "gain_db_individual": float(gain),
                "target_peak": self.target_peak,
                "target_rms": self.target_rms,
                "detected_peak_db": peak_db,
                "detected_rms_db": rms_anchor_db,
                "rms_anchor_label": self._rms_anchor_label,
                "gain_for_peak": float(gain_for_peak),
                "gain_for_rms": float(gain_for_rms),
            },
        )

    def render_html(self, result: ProcessorResult, track=None, *, verbose: bool = False) -> str:
        """Render the normalization analysis as summary line + comparison table."""
        d = result.data
        cls_text = result.classification or "Unknown"

        # Classification color
        if "Transient" in cls_text:
            type_color = "#cc77ff"
        elif "Sustained" in cls_text:
            type_color = "#44cccc"
        elif cls_text == "Skip":
            type_color = "#888888"
        else:
            type_color = "#888888"

        # Skip / Silent / Unknown: single-line, no breakdown
        if cls_text in ("Skip", "Silent", "Unknown"):
            return (
                f'<div style="margin-left:8px;">'
                f'Classification: <span style="color:{type_color}; font-weight:bold;">'
                f'{cls_text}</span> &mdash; no normalization</div>'
            )

        off = self._db_offset

        def fmt_abs(val):
            if not math.isfinite(val):
                return "&minus;&infin;"
            return f"{val + off:.1f}"

        def fmt_rel(val):
            if not math.isfinite(val):
                return "&minus;&infin;"
            return f"{val:+.1f}"

        det_peak = d.get("detected_peak_db", float("-inf"))
        det_rms = d.get("detected_rms_db", float("-inf"))
        tgt_peak = d.get("target_peak", self.target_peak)
        tgt_rms = d.get("target_rms", self.target_rms)
        anchor_label = d.get("rms_anchor_label", self._rms_anchor_label)
        gain_for_peak = d.get("gain_for_peak", 0.0)
        gain_for_rms = d.get("gain_for_rms", 0.0)

        rms_metric = f"RMS ({anchor_label})" if anchor_label else "RMS"
        is_transient = "Transient" in cls_text
        is_peak_limited = result.method == "Peak Limited"

        peak_active = is_transient or is_peak_limited
        rms_active = not is_transient and not is_peak_limited

        # --- Summary line ---
        summary = (
            f'<div style="margin-left:8px; margin-top:4px;">'
            f'<span style="color:{type_color}; font-weight:bold;">{cls_text}</span>'
            f' &nbsp;&middot;&nbsp; {result.method}'
            f' &nbsp;&middot;&nbsp; <b>{result.gain_db:+.1f} dB</b>'
            f'</div>'
        )

        # --- Comparison table ---
        hdr = ('color:#ffffff; font-weight:bold; font-size:9pt;'
               ' padding:3px 16px 3px 0; border-bottom:1px solid #3a3a3a;')
        cell = 'padding:3px 16px 3px 0; white-space:nowrap;'
        dim = "#888888"
        active_color = "#44cc44"
        inactive_color = "#dddddd"

        def row_style(active):
            c = active_color if active else inactive_color
            w = "font-weight:bold;" if active else ""
            return c, w

        pk_c, pk_w = row_style(peak_active)
        pk_note = ""
        if is_peak_limited:
            pk_note = f'<span style="color:{dim}; font-size:9pt;"> (chosen, limiting)</span>'
        elif is_transient:
            pk_note = f'<span style="color:{dim}; font-size:9pt;"> (chosen)</span>'

        rms_c, rms_w = row_style(rms_active)
        rms_note = ""
        if is_peak_limited:
            rms_note = f'<span style="color:{dim}; font-size:9pt;"> (would exceed peak)</span>'
        elif rms_active:
            rms_note = f'<span style="color:{dim}; font-size:9pt;"> (chosen)</span>'

        table = (
            f'<table cellpadding="0" cellspacing="0" '
            f'style="margin-left:8px; margin-top:12px;">'
            f'<tr>'
            f'<td style="{hdr}"></td>'
            f'<td style="{hdr}">Detected</td>'
            f'<td style="{hdr}">Target</td>'
            f'<td style="{hdr}">Gain</td>'
            f'</tr>'
            f'<tr>'
            f'<td style="{cell} color:{dim};">Peak</td>'
            f'<td style="{cell} color:{pk_c}; {pk_w}">{fmt_abs(det_peak)} dBFS</td>'
            f'<td style="{cell} color:{pk_c}; {pk_w}">{tgt_peak:.1f} dBFS</td>'
            f'<td style="{cell} color:{pk_c}; {pk_w}">{fmt_rel(gain_for_peak)} dB{pk_note}</td>'
            f'</tr>'
            f'<tr>'
            f'<td style="{cell} color:{dim};">{rms_metric}</td>'
            f'<td style="{cell} color:{rms_c}; {rms_w}">{fmt_abs(det_rms)} dBFS</td>'
            f'<td style="{cell} color:{rms_c}; {rms_w}">{tgt_rms:.1f} dBFS</td>'
            f'<td style="{cell} color:{rms_c}; {rms_w}">{fmt_rel(gain_for_rms)} dB{rms_note}</td>'
            f'</tr>'
            f'</table>'
        )

        html = summary + table

        # Verbose: append classification analysis metrics
        if verbose and track is not None:
            cr = track.detector_results.get("audio_classifier")
            if cr is not None:
                crest = cr.data.get("crest", 0.0)
                decay = cr.data.get("decay_db", 0.0)
                density = cr.data.get("density", 0.0)
                html += (
                    f'<div style="margin-left:8px; margin-top:6px;'
                    f' color:#888888; font-size:9pt;">'
                    f'Analysis: Crest {crest:.1f} dB'
                    f' &middot; Decay {decay:.1f} dB'
                    f' &middot; Density {density:.0%}'
                    f'</div>'
                )

        return html

    def apply(self, track: TrackContext, result: ProcessorResult) -> np.ndarray:
        if result.classification == "Silent" or result.gain_db == 0.0:
            return track.audio_data
        linear_gain = db_to_linear(float(result.gain_db))
        return track.audio_data * linear_gain
