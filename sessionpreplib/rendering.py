from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from typing import Any

import numpy as np

from .models import (
    DetectorResult,
    Severity,
    SessionContext,
    TrackContext,
)
from .audio import dbfs_offset, format_duration, linear_to_db


# --------------------------------------------------------------------------
# Diagnostic summary builder (data transformation, not rendering)
# --------------------------------------------------------------------------

def build_diagnostic_summary(
    session: SessionContext,
    track_detectors: list | None = None,
    session_detectors: list | None = None,
) -> dict[str, Any]:
    """
    Aggregate detector results into the four-category summary.
    Returns a dict that any Renderer can consume.

    Structure:
    {
        "problems": [...],
        "attention": [...],
        "information": [...],
        "clean": [...],
        "normalization_hints": [...],
        "clean_count": int,
        "total_ok": int,
        "overview": {...},
    }
    """
    ok_tracks = [t for t in session.tracks if t.status == "OK"]
    total_ok = len(ok_tracks)
    _off = dbfs_offset(session.config)

    def add_group(dst, title, hint, items, standalone=False):
        if not items and not standalone:
            return
        dst.append({
            "title": title,
            "hint": hint,
            "items": items or [],
            "standalone": bool(standalone),
        })

    problems_groups = []
    attention_groups = []
    info_groups = []
    clean_groups = []

    # --- File errors ---
    file_errors = []
    for t in session.tracks:
        if t.status != "OK":
            file_errors.append(f"{t.filename}: {t.status}")
    add_group(problems_groups, "File errors", None, file_errors)

    # --- Format consistency (session-level) ---
    format_results = session.config.get("_session_det_format_consistency", [])
    most_common_sr = session.config.get("_most_common_sr")
    most_common_bd = session.config.get("_most_common_bd")

    format_mismatch_items = []
    mismatch_names = set()
    for r in format_results:
        if r.severity != Severity.PROBLEM:
            continue
        fname = r.data.get("filename", "")
        reasons = r.data.get("mismatch_reasons", [])
        details = ", ".join(reasons) if reasons else "mismatch"
        format_mismatch_items.append(f"{fname}: format mismatch ({details})")
        mismatch_names.add(fname)

    format_matches = total_ok - len(format_mismatch_items)
    common_fmt = None
    if most_common_sr is not None and most_common_bd is not None:
        common_fmt = f"{most_common_sr} Hz / {most_common_bd}"

    format_summary = None
    if total_ok:
        format_summary = (
            f"{format_matches}/{total_ok} file(s) match the most common session format"
            + (f" ({common_fmt})" if common_fmt else "")
        )

    mismatch_title = "Format mismatches"
    if format_summary:
        mismatch_title = f"Format mismatches. Deviations from {format_summary}"

    add_group(problems_groups, mismatch_title, "request corrected exports",
              format_mismatch_items)

    if total_ok and most_common_sr is not None and most_common_bd is not None and not format_mismatch_items:
        add_group(clean_groups,
                  f"No inconsistent session formats ({most_common_sr} Hz / {most_common_bd})",
                  None, [], standalone=True)

    # --- Length consistency (session-level) ---
    length_results = session.config.get("_session_det_length_consistency", [])
    most_common_len = session.config.get("_most_common_len")
    most_common_len_fmt = session.config.get("_most_common_len_fmt")

    length_mismatch_items = []
    length_mismatch_names = set()
    for r in length_results:
        if r.severity != Severity.PROBLEM:
            continue
        fname = r.data.get("filename", "")
        actual_samples = r.data.get("actual_samples")
        actual_fmt = r.data.get("actual_duration_fmt", "")
        if actual_samples is not None:
            length_mismatch_items.append(
                f"{fname}: length mismatch ({int(actual_samples)} samples / {actual_fmt})"
            )
        else:
            length_mismatch_items.append(f"{fname}: length mismatch")
        length_mismatch_names.add(fname)

    length_matches = total_ok - len(length_mismatch_items)

    length_summary = None
    if total_ok and most_common_len is not None:
        length_summary = (
            f"{length_matches}/{total_ok} file(s) match the most common length"
            + (f" ({int(most_common_len)} samples / {most_common_len_fmt})"
               if most_common_len_fmt
               else f" ({int(most_common_len)} samples)")
        )

    length_mismatch_title = "Length mismatches"
    if length_summary:
        length_mismatch_title = f"Length mismatches. Deviations from {length_summary}"

    add_group(problems_groups, length_mismatch_title, "request aligned exports",
              length_mismatch_items)

    if total_ok and most_common_len is not None and not length_mismatch_items:
        fmt = f"{int(most_common_len)} samples" + (
            f" / {most_common_len_fmt}" if most_common_len_fmt else ""
        )
        add_group(clean_groups, f"No inconsistent file lengths ({fmt})",
                  None, [], standalone=True)

    # --- Per-track detector aggregation ---
    clipped_items = []
    dc_items = []
    stereo_compat_items = []
    dual_mono_items = []
    silent_items = []
    one_sided_items = []
    subsonic_items = []
    tail_items = []

    issue_names = set(mismatch_names) | set(length_mismatch_names)

    for t in ok_tracks:
        # Clipping
        clip_r = t.detector_results.get("clipping")
        if clip_r and clip_r.data.get("is_clipped"):
            runs = int(clip_r.data.get("runs", 0))
            clipped_items.append(
                f"{t.filename}: clipping detected ({runs} clipped ranges)"
            )
            issue_names.add(t.filename)

        # DC offset
        dc_r = t.detector_results.get("dc_offset")
        if dc_r and dc_r.data.get("dc_warn"):
            dc_db = dc_r.data.get("dc_db", float(-np.inf))
            if np.isfinite(dc_db):
                dc_items.append(f"{t.filename}: DC offset {dc_db + _off:.1f} dBFS")
            else:
                dc_items.append(f"{t.filename}: DC offset issue")
            issue_names.add(t.filename)

        # Stereo compatibility (correlation + mono folddown combined)
        parts = []
        corr_r = t.detector_results.get("stereo_correlation")
        if corr_r and corr_r.data.get("corr_warn"):
            lr_corr = corr_r.data.get("lr_corr")
            corr_warn_val = session.config.get("corr_warn", -0.3)
            if lr_corr is None:
                parts.append("corr < threshold")
            else:
                parts.append(f"corr {float(lr_corr):.2f} (< {float(corr_warn_val):g})")

        mono_r = t.detector_results.get("mono_folddown")
        if mono_r and mono_r.data.get("mono_warn"):
            mono_loss_db = mono_r.data.get("mono_loss_db")
            mono_warn_val = session.config.get("mono_loss_warn_db", 6.0)
            if mono_loss_db is None:
                parts.append("mono loss > threshold")
            elif np.isfinite(mono_loss_db):
                parts.append(
                    f"mono loss {float(mono_loss_db):.1f} dB (> {float(mono_warn_val):g} dB)"
                )
            else:
                parts.append(
                    f"mono loss inf dB (> {float(mono_warn_val):g} dB)"
                )

        if parts:
            stereo_compat_items.append(f"{t.filename}: " + ", ".join(parts))

        # Dual mono
        dm_r = t.detector_results.get("dual_mono")
        if dm_r and dm_r.data.get("dual_mono"):
            dual_mono_items.append(f"{t.filename}: dual-mono (identical L/R)")

        # Silent
        sil_r = t.detector_results.get("silence")
        if sil_r and sil_r.data.get("is_silent"):
            silent_items.append(f"{t.filename}: silent")
            issue_names.add(t.filename)

        # One-sided silence
        oss_r = t.detector_results.get("one_sided_silence")
        if oss_r and oss_r.data.get("one_sided_silence"):
            side = oss_r.data.get("one_sided_silence_side")
            l_db = oss_r.data.get("l_rms_db", float(-np.inf))
            r_db = oss_r.data.get("r_rms_db", float(-np.inf))

            def fmt_db(x):
                return f"{float(x) + _off:.1f}" if np.isfinite(x) else "-inf"

            if side:
                one_sided_items.append(
                    f"{t.filename}: one-sided silence ({side}) "
                    f"(L {fmt_db(l_db)} dBFS, R {fmt_db(r_db)} dBFS)"
                )
            else:
                one_sided_items.append(
                    f"{t.filename}: one-sided silence "
                    f"(L {fmt_db(l_db)} dBFS, R {fmt_db(r_db)} dBFS)"
                )
            issue_names.add(t.filename)

        # Subsonic
        sub_r = t.detector_results.get("subsonic")
        if sub_r and sub_r.data.get("subsonic_warn"):
            ratio_db = sub_r.data.get("subsonic_ratio_db", float(-np.inf))
            cutoff_hz = session.config.get("subsonic_hz", 30.0)
            if np.isfinite(ratio_db):
                subsonic_items.append(
                    f"{t.filename}: subsonic energy {float(ratio_db):.1f} dB "
                    f"(<= {float(cutoff_hz):g} Hz)"
                )
            else:
                subsonic_items.append(f"{t.filename}: subsonic content detected")
            issue_names.add(t.filename)

        # Tail exceedance
        tail_r = t.detector_results.get("tail_exceedance")
        if tail_r:
            summary = tail_r.data.get("tail_summary", {})
            regions = int(summary.get("regions", 0))
            if regions > 0:
                max_exceed = float(summary.get("max_exceed_db", 0.0))
                tail_min = session.config.get("tail_min_exceed_db", 3.0)
                tail_items.append(
                    f"{t.filename}: {regions} tail region(s) exceed anchor "
                    f"by >{float(tail_min):g} dB (max +{max_exceed:.1f} dB)"
                )
                issue_names.add(t.filename)

    # Build groups
    add_group(problems_groups, "Digital clipping",
              "request reprint / check limiting", clipped_items)
    add_group(attention_groups, "DC offset",
              "consider DC removal", dc_items)
    add_group(info_groups, "Stereo compatibility", None, stereo_compat_items)
    add_group(info_groups, "Dual-mono (identical L/R)", None, dual_mono_items)
    add_group(attention_groups, "Silent files",
              "confirm intentional", silent_items)
    add_group(attention_groups, "One-sided silence",
              "check stereo export / channel routing", one_sided_items)

    subsonic_hz = session.config.get("subsonic_hz", 30.0)
    add_group(attention_groups, "Subsonic content",
              f"consider HPF ~{float(subsonic_hz):g} Hz", subsonic_items)
    add_group(attention_groups, "Tail regions exceeded anchor",
              "check for section-based riding", tail_items)

    # Grouping overlaps
    grouping_items = []
    for w in session.warnings:
        if str(w).startswith("Grouping overlap:"):
            grouping_items.append(str(w))
    add_group(attention_groups, "Grouping overlaps",
              "review group patterns", grouping_items)

    # Clean summary items
    if total_ok and not clipped_items:
        add_group(clean_groups, "No digital clipping detected",
                  None, [], standalone=True)
    if total_ok and not dc_items:
        add_group(clean_groups, "No DC offset issues detected",
                  None, [], standalone=True)
    if total_ok and not silent_items:
        add_group(clean_groups, "No silent files detected",
                  None, [], standalone=True)
    if total_ok and not one_sided_items:
        add_group(clean_groups, "No one-sided silent stereo files detected",
                  None, [], standalone=True)
    if total_ok and not subsonic_items:
        add_group(clean_groups, "No significant subsonic content detected",
                  None, [], standalone=True)

    # --- Normalization hints ---
    normalization_hints = []
    crest_threshold = session.config.get("crest_threshold", 12.0)
    for t in ok_tracks:
        crest_r = t.detector_results.get("crest_factor")
        if crest_r is None:
            continue
        if crest_r.data.get("near_threshold"):
            crest = crest_r.data.get("crest", 0.0)
            normalization_hints.append(
                f"{t.filename}: near transient/sustained threshold "
                f"(crest {crest:.1f} dB vs {crest_threshold:.1f} dB). "
                f"Consider forcing classification if it feels wrong."
            )

    # Count clean tracks
    clean_count = len([t for t in ok_tracks if t.filename not in issue_names])

    # --- Overview stats ---
    non_silent = [
        t for t in ok_tracks
        if not (t.detector_results.get("silence") or DetectorResult(
            "", Severity.CLEAN, "", {}
        )).data.get("is_silent", False)
    ]

    def safe_peak(t):
        cr = t.detector_results.get("crest_factor")
        v = cr.data.get("peak_db", float(-np.inf)) if cr else float(-np.inf)
        return v + _off if np.isfinite(v) else v

    def safe_rms(t):
        cr = t.detector_results.get("crest_factor")
        v = cr.data.get("rms_anchor_db", float(-np.inf)) if cr else float(-np.inf)
        return v + _off if np.isfinite(v) else v

    peak_candidates = [t for t in non_silent if np.isfinite(safe_peak(t))]
    rms_candidates = [t for t in non_silent if np.isfinite(safe_rms(t))]

    overview = {
        "total_ok": total_ok,
        "most_common_sr": most_common_sr,
        "most_common_bd": most_common_bd,
        "most_common_len": most_common_len,
        "most_common_len_fmt": most_common_len_fmt,
    }

    if peak_candidates:
        peak_values = [safe_peak(t) for t in peak_candidates]
        overview["loudest_peak"] = max(peak_candidates, key=safe_peak).filename
        overview["loudest_peak_db"] = max(peak_values)
        overview["quietest_peak"] = min(peak_candidates, key=safe_peak).filename
        overview["quietest_peak_db"] = min(peak_values)
        overview["median_peak_db"] = float(np.median(peak_values))

    if rms_candidates:
        rms_values = [safe_rms(t) for t in rms_candidates]
        overview["loudest_rms"] = max(rms_candidates, key=safe_rms).filename
        overview["loudest_rms_db"] = max(rms_values)
        overview["quietest_rms"] = min(rms_candidates, key=safe_rms).filename
        overview["quietest_rms_db"] = min(rms_values)
        overview["median_rms_db"] = float(np.median(rms_values))

    return {
        "problems": problems_groups,
        "attention": attention_groups,
        "information": info_groups,
        "clean": clean_groups,
        "normalization_hints": normalization_hints,
        "clean_count": int(clean_count),
        "total_ok": int(total_ok),
        "overview": overview,
    }


# --------------------------------------------------------------------------
# Plain-text renderer (for report files)
# --------------------------------------------------------------------------

def render_diagnostic_summary_text(summary: dict[str, Any]) -> str:
    """Render the diagnostic summary as plain text (for sessionprep.txt)."""
    problems = summary.get("problems") or []
    attention = summary.get("attention") or []
    information = summary.get("information") or []
    clean = summary.get("clean") or []
    normalization_hints = summary.get("normalization_hints") or []
    clean_count = int(summary.get("clean_count", 0))
    total_ok = int(summary.get("total_ok", 0))

    def item_count(groups):
        return sum(len(g.get("items") or []) for g in groups)

    def render_groups(groups, compact=False):
        lines = []
        for g in groups:
            title = g.get("title")
            hint = g.get("hint")
            items = g.get("items") or []
            if not items and not g.get("standalone"):
                continue
            header = f"{title}"
            if hint:
                header = f"{header} -> {hint}"
            lines.append(f"   - {header}")
            for item in items:
                lines.append(f"     * {item}")
            if not compact:
                lines.append("")
        return lines

    lines = []
    lines.append(f"\U0001f9fe Session Health Summary: {clean_count}/{total_ok} file(s) CLEAN")
    lines.append("")
    lines.append(f"\U0001f534 PROBLEMS ({item_count(problems)})")
    problem_lines = render_groups(problems)
    if problem_lines:
        lines.extend(problem_lines)
    else:
        lines.append("   - None")

    lines.append("")
    lines.append(f"\U0001f7e1 ATTENTION ({item_count(attention)})")
    attention_lines = render_groups(attention)
    if attention_lines:
        lines.extend(attention_lines)
    else:
        lines.append("   - None")

    lines.append("")
    lines.append(f"\U0001f535 INFORMATION ({item_count(information)})")
    info_lines = render_groups(information)
    if info_lines:
        lines.extend(info_lines)
    else:
        lines.append("   - None")

    lines.append("")
    lines.append("\U0001f7e2 CLEAN")
    clean_lines = render_groups(clean, compact=True)
    if clean_lines:
        lines.extend(clean_lines)
    else:
        lines.append("   - None")

    lines.append("")
    lines.append("\U0001f50e Normalization hints")
    if normalization_hints:
        for hint in normalization_hints:
            lines.append(f"   - {hint}")
    else:
        lines.append("   - None")
    return "\n".join(lines)
