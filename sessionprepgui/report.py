"""HTML report rendering for the GUI."""

from __future__ import annotations

import math

from .theme import COLORS, FILE_COLOR_TRANSIENT, FILE_COLOR_SUSTAINED, FILE_COLOR_SILENT
from .helpers import esc


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------

def render_summary_html(
    summary: dict,
    *,
    show_hints: bool = True,
    show_faders: bool = True,
) -> str:
    """Render a diagnostic summary dict as styled HTML."""
    problems = summary.get("problems") or []
    attention = summary.get("attention") or []
    information = summary.get("information") or []
    clean = summary.get("clean") or []
    normalization_hints = summary.get("normalization_hints") or []
    clean_count = int(summary.get("clean_count", 0))
    total_ok = int(summary.get("total_ok", 0))

    def item_count(groups):
        return sum(len(g.get("items") or []) for g in groups)

    def render_groups(groups, color):
        html = ""
        any_printed = False
        for g in groups:
            title = g.get("title", "")
            hint = g.get("hint")
            items = g.get("items") or []
            if not items and not g.get("standalone"):
                continue
            header = esc(title)
            if hint:
                header += f" &rarr; <i>{esc(hint)}</i>"
            html += f'<div style="margin-left:16px; color:{color};">&bull; {header}</div>\n'
            for item in items:
                html += f'<div style="margin-left:32px; color:{COLORS["dim"]};">* {esc(item)}</div>\n'
            any_printed = True
        if not any_printed:
            html += f'<div style="margin-left:16px; color:{COLORS["clean"]};">&bull; None</div>\n'
        return html

    parts = []
    parts.append(f'<div style="color:{COLORS["heading"]}; font-size:13pt; font-weight:bold; '
                 f'margin-bottom:8px;">Session Health: {clean_count}/{total_ok} file(s) CLEAN</div>')

    # Overview
    overview = summary.get("overview") or {}
    if overview.get("most_common_sr") is not None:
        sr = overview["most_common_sr"]
        bd = overview.get("most_common_bd", "?")
        parts.append(f'<div style="color:{COLORS["dim"]}; margin-bottom:8px;">'
                     f'Session format: {sr} Hz / {bd}</div>')

    section_spacing = 'margin-top:14px;'

    # Problems
    parts.append(f'<div style="color:{COLORS["problems"]}; font-size:12pt; font-weight:bold; {section_spacing}">'
                 f'\U0001f534 PROBLEMS ({item_count(problems)})</div>')
    parts.append(render_groups(problems, COLORS["problems"]))

    # Attention
    parts.append(f'<div style="color:{COLORS["attention"]}; font-size:12pt; font-weight:bold; {section_spacing}">'
                 f'\U0001f7e1 ATTENTION ({item_count(attention)})</div>')
    parts.append(render_groups(attention, COLORS["attention"]))

    # Information
    parts.append(f'<div style="color:{COLORS["information"]}; font-size:12pt; font-weight:bold; {section_spacing}">'
                 f'\U0001f535 INFORMATION ({item_count(information)})</div>')
    parts.append(render_groups(information, COLORS["information"]))

    # Clean
    parts.append(f'<div style="color:{COLORS["clean"]}; font-size:12pt; font-weight:bold; {section_spacing}">'
                 f'\U0001f7e2 CLEAN</div>')
    parts.append(render_groups(clean, COLORS["clean"]))

    # Normalization hints (optional)
    if show_hints:
        parts.append(f'<div style="color:{COLORS["hints"]}; font-size:12pt; font-weight:bold; {section_spacing}">'
                     f'\U0001f50e Normalization Hints</div>')
        if normalization_hints:
            for hint in normalization_hints:
                parts.append(f'<div style="margin-left:16px; color:{COLORS["hints"]};">'
                             f'&bull; {esc(hint)}</div>')
        else:
            parts.append(f'<div style="margin-left:16px; color:{COLORS["clean"]};">'
                         f'&bull; None</div>')

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Fader table
# ---------------------------------------------------------------------------

def render_fader_table_html(session) -> str:
    """Render the fader offset table as an HTML table."""
    rows = []
    for t in session.tracks:
        if t.status != "OK":
            rows.append(
                f'<tr><td style="color:{COLORS["problems"]}">{esc(t.filename)}</td>'
                f'<td>Error</td><td>&mdash;</td><td>&mdash;</td><td>&mdash;</td>'
                f'<td style="color:{COLORS["problems"]}">ERR</td></tr>'
            )
            continue

        pr = next(iter(t.processor_results.values()), None) if t.processor_results else None
        fmt_str = f"{t.samplerate/1000:.0f}k/{t.bitdepth}"

        if pr and pr.classification == "Silent":
            rows.append(
                f'<tr><td style="color:{COLORS["dim"]}">{esc(t.filename)}</td>'
                f'<td>{fmt_str}</td><td>Silent</td><td>0.0 dB</td><td>0.0 dB</td>'
                f'<td style="color:{COLORS["attention"]}">SILENT</td></tr>'
            )
            continue

        classification = pr.classification if pr else "Unknown"
        gain_db = pr.gain_db if pr else 0.0
        fader_offset = pr.data.get("fader_offset", 0.0) if pr else 0.0

        is_clipped = False
        clip_r = t.detector_results.get("clipping")
        if clip_r:
            is_clipped = bool(clip_r.data.get("is_clipped"))

        type_color = FILE_COLOR_TRANSIENT.name() if "Transient" in classification else FILE_COLOR_SUSTAINED.name()
        status_color = COLORS["problems"] if is_clipped else COLORS["clean"]
        status_label = "CLIP" if is_clipped else "OK"

        rows.append(
            f'<tr>'
            f'<td>{esc(t.filename)}</td>'
            f'<td>{fmt_str}</td>'
            f'<td style="color:{type_color}">{esc(classification)}</td>'
            f'<td>{gain_db:+.1f} dB</td>'
            f'<td style="font-weight:bold; color:{COLORS["clean"]}">{fader_offset:+.1f} dB</td>'
            f'<td style="color:{status_color}">{status_label}</td>'
            f'</tr>'
        )

    header = (
        '<table cellpadding="4" cellspacing="0" style="border-collapse:collapse; width:100%;">'
        '<tr style="border-bottom:1px solid #555;">'
        f'<th align="left" style="color:{COLORS["heading"]}">Track</th>'
        f'<th align="left" style="color:{COLORS["heading"]}">Format</th>'
        f'<th align="center" style="color:{COLORS["heading"]}">Type</th>'
        f'<th align="right" style="color:{COLORS["heading"]}">Gain</th>'
        f'<th align="right" style="color:{COLORS["heading"]}">Fader</th>'
        f'<th align="right" style="color:{COLORS["heading"]}">Status</th>'
        '</tr>'
    )
    return header + "\n".join(rows) + "</table>"


# ---------------------------------------------------------------------------
# Normalization analysis table (used by per-track detail)
# ---------------------------------------------------------------------------

def _render_norm_table(pr, db_offset: float) -> str:
    """Render the Normalization Analysis as summary line + comparison table."""
    d = pr.data
    cls_text = pr.classification or "Unknown"

    # Classification color
    if "Transient" in cls_text:
        type_color = FILE_COLOR_TRANSIENT.name()
    elif "Sustained" in cls_text:
        type_color = FILE_COLOR_SUSTAINED.name()
    elif cls_text == "Skip":
        type_color = FILE_COLOR_SILENT.name()
    else:
        type_color = COLORS["dim"]

    # Skip / Silent / Unknown: single-line, no breakdown
    if cls_text in ("Skip", "Silent", "Unknown"):
        return (
            f'<div style="margin-left:8px;">'
            f'Classification: <span style="color:{type_color}; font-weight:bold;">'
            f'{esc(cls_text)}</span> &mdash; no normalization</div>'
        )

    # --- Summary line ---
    summary = (
        f'<div style="margin-left:8px; margin-top:4px;">'
        f'<span style="color:{type_color}; font-weight:bold;">{esc(cls_text)}</span>'
        f' &nbsp;&middot;&nbsp; {esc(pr.method)}'
        f' &nbsp;&middot;&nbsp; <b>{pr.gain_db:+.1f} dB</b>'
        f'</div>'
    )

    # --- Comparison table ---
    def fmt_abs(val):
        """Format absolute dB value with display offset."""
        if not math.isfinite(val):
            return "&minus;&infin;"
        return f"{val + db_offset:.1f}"

    def fmt_rel(val):
        """Format relative dB value (no offset)."""
        if not math.isfinite(val):
            return "&minus;&infin;"
        return f"{val:+.1f}"

    det_peak = d.get("detected_peak_db", float("-inf"))
    det_rms = d.get("detected_rms_db", float("-inf"))
    tgt_peak = d.get("target_peak", -6.0)
    tgt_rms = d.get("target_rms", -18.0)
    anchor_label = d.get("rms_anchor_label", "")
    gain_for_peak = d.get("gain_for_peak", 0.0)
    gain_for_rms = d.get("gain_for_rms", 0.0)

    rms_metric = f"RMS ({anchor_label})" if anchor_label else "RMS"
    is_transient = "Transient" in cls_text
    is_peak_limited = pr.method == "Peak Limited"

    # Determine which row is the active (chosen) gain path
    peak_active = is_transient or is_peak_limited
    rms_active = not is_transient and not is_peak_limited

    # Styles
    hdr = (f'color:{COLORS["heading"]}; font-weight:bold; font-size:9pt;'
           f' padding:3px 16px 3px 0; border-bottom:1px solid {COLORS["accent"]};')
    cell = 'padding:3px 16px 3px 0; white-space:nowrap;'
    dim = COLORS["dim"]
    active_color = COLORS["clean"]
    inactive_color = COLORS["text"]

    def row_style(active):
        c = active_color if active else inactive_color
        w = "font-weight:bold;" if active else ""
        return c, w

    # Peak row
    pk_c, pk_w = row_style(peak_active)
    pk_note = ""
    if is_peak_limited:
        pk_note = f'<span style="color:{dim}; font-size:9pt;"> (chosen, limiting)</span>'
    elif is_transient:
        pk_note = f'<span style="color:{dim}; font-size:9pt;"> (chosen)</span>'

    # RMS row
    rms_c, rms_w = row_style(rms_active)
    rms_note = ""
    if is_peak_limited:
        rms_note = f'<span style="color:{dim}; font-size:9pt;"> (would exceed peak)</span>'
    elif rms_active:
        rms_note = f'<span style="color:{dim}; font-size:9pt;"> (chosen)</span>'

    table = (
        f'<table cellpadding="0" cellspacing="0" '
        f'style="margin-left:8px; margin-top:12px;">'
        # Header
        f'<tr>'
        f'<td style="{hdr}"></td>'
        f'<td style="{hdr}">Detected</td>'
        f'<td style="{hdr}">Target</td>'
        f'<td style="{hdr}">Gain</td>'
        f'</tr>'
        # Peak row
        f'<tr>'
        f'<td style="{cell} color:{dim};">Peak</td>'
        f'<td style="{cell} color:{pk_c}; {pk_w}">{fmt_abs(det_peak)} dBFS</td>'
        f'<td style="{cell} color:{pk_c}; {pk_w}">{tgt_peak:.1f} dBFS</td>'
        f'<td style="{cell} color:{pk_c}; {pk_w}">{fmt_rel(gain_for_peak)} dB{pk_note}</td>'
        f'</tr>'
        # RMS row
        f'<tr>'
        f'<td style="{cell} color:{dim};">{rms_metric}</td>'
        f'<td style="{cell} color:{rms_c}; {rms_w}">{fmt_abs(det_rms)} dBFS</td>'
        f'<td style="{cell} color:{rms_c}; {rms_w}">{tgt_rms:.1f} dBFS</td>'
        f'<td style="{cell} color:{rms_c}; {rms_w}">{fmt_rel(gain_for_rms)} dB{rms_note}</td>'
        f'</tr>'
        f'</table>'
    )

    return summary + table


# ---------------------------------------------------------------------------
# Per-track detail
# ---------------------------------------------------------------------------

def render_track_detail_html(track, db_offset: float = 0.0) -> str:
    """Render per-track detail as styled HTML.

    Parameters
    ----------
    track : TrackContext
    db_offset : float
        dBFS display offset (e.g. AES17 +3.01 dB). Applied to all
        absolute dB values shown in the normalization analysis table.
    """
    parts = []
    parts.append(f'<div style="color:{COLORS["heading"]}; font-size:13pt; font-weight:bold;">'
                 f'{esc(track.filename)}</div>')

    if track.status != "OK":
        parts.append(f'<div style="color:{COLORS["problems"]}; margin-top:8px;">'
                     f'Status: {esc(track.status)}</div>')
    else:
        # File info
        fmt = f"{track.samplerate} Hz / {track.bitdepth} / {track.channels}ch"
        dur = f"{track.duration_sec:.2f}s ({track.total_samples} samples)"
        parts.append(f'<div style="color:{COLORS["dim"]}; margin-top:6px;">{fmt}</div>')
        parts.append(f'<div style="color:{COLORS["dim"]};">{dur}</div>')

        # Processor result
        pr = next(iter(track.processor_results.values()), None) if track.processor_results else None
        if pr:
            parts.append(f'<div style="margin-top:12px; color:{COLORS["heading"]}; '
                         f'font-weight:bold;">Normalization Analysis</div>')
            parts.append(_render_norm_table(pr, db_offset))
            if track.group:
                parts.append(f'<div style="margin-left:8px; margin-top:4px;">'
                             f'Group: {esc(track.group)}</div>')

        # Detector results
        if track.detector_results:
            parts.append(f'<div style="margin-top:20px; color:{COLORS["heading"]}; '
                         f'font-weight:bold;">Detectors</div>')
            parts.append(
                '<table cellpadding="3" cellspacing="2" '
                'style="margin-left:8px; margin-top:4px;">'
            )
            for det_id, result in track.detector_results.items():
                sev = result.severity.value if hasattr(result.severity, "value") else str(result.severity)
                sev_color, sev_label = {
                    "problem":     (COLORS["problems"],    "PROBLEM"),
                    "attention":   (COLORS["attention"],   "ATTENTION"),
                    "information": (COLORS["information"], "INFO"),
                    "clean":       (COLORS["clean"],       "OK"),
                }.get(sev, (COLORS["information"], "INFO"))

                parts.append(
                    f'<tr>'
                    f'<td width="90" style="background-color:{sev_color}; color:#000;'
                    f' font-weight:bold; font-size:8pt; text-align:center;'
                    f' padding:2px 8px;">'
                    f'{sev_label}</td>'
                    f'<td style="padding-left:6px; white-space:nowrap;">'
                    f'<a href="detector:{det_id}" style="color:{COLORS["text"]}; '
                    f'text-decoration:none;"><b>{esc(det_id)}</b></a></td>'
                    f'<td style="padding-left:6px; color:{COLORS["dim"]};">'
                    f'{esc(result.summary)}</td>'
                    f'</tr>'
                )
            parts.append('</table>')

    return "\n".join(parts)
