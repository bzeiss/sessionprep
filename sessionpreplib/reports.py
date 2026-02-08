from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from typing import Any

import numpy as np

from .audio import dbfs_offset, format_duration
from .models import SessionContext
from .utils import protools_sort_key


def _get_primary_processor_result(track):
    """Return the first processor result on a track, or None."""
    if track.processor_results:
        return next(iter(track.processor_results.values()))
    return None


def generate_report(
    session: SessionContext,
    config: dict[str, Any],
    output_path: str,
    warnings: list[str],
    diagnostic_summary_text: str | None = None,
) -> str:
    """Generate a text report with fader positions and diagnostics."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    source_dir = config.get("_source_dir", "")

    lines = [
        "=" * 80,
        "SessionPrep Report",
        "=" * 80,
        f"Generated: {timestamp}",
        f"Source Directory: {os.path.abspath(source_dir)}",
        f"Output Directory: {os.path.dirname(output_path)}",
        f"Target RMS: {config.get('target_rms', -18.0)} dBFS | Target Peak: {config.get('target_peak', -6.0)} dBFS",
        f"Clipping Detection Threshold: {config.get('clip_consecutive', 3)} consecutive samples @ \u00b11.0",
        "",
    ]

    if diagnostic_summary_text:
        lines.extend([
            diagnostic_summary_text,
            "",
        ])

    # Track groups section
    grouped = {}
    for t in session.tracks:
        if t.status != "OK" or t.group is None:
            continue
        pr = _get_primary_processor_result(t)
        gain = pr.gain_db if pr else 0.0
        grouped.setdefault(t.group, {"gain_db": gain, "members": []})
        grouped[t.group]["members"].append(t.filename)

    if grouped:
        lines.extend([
            "-" * 80,
            "TRACK GROUPS (Identical Gain Applied)",
            "-" * 80,
            "",
        ])
        for gid in sorted(grouped.keys()):
            g = grouped[gid]
            gain_db = g.get("gain_db", 0.0)
            lines.append(f"{gid} | Gain Applied: {gain_db:+.1f} dB")
            for name in sorted(g.get("members") or [], key=protools_sort_key):
                lines.append(f"  - {name}")
            lines.append("")

    # Fader positions
    lines.extend([
        "-" * 80,
        "FADER POSITIONS (Set these in your DAW to restore original balance)",
        "-" * 80,
        "",
        "{:<40} {:>12} {:>12}".format("TRACK", "FADER", "TYPE"),
        "-" * 80,
    ])

    for t in session.tracks:
        if t.status != "OK":
            continue
        pr = _get_primary_processor_result(t)
        fader = pr.data.get("fader_offset", 0) if pr else 0
        classification = pr.classification if pr else "Unknown"
        fader_str = "{:+.1f} dB".format(fader)
        lines.append("{:<40} {:>12} {:>12}".format(
            t.filename[:38], fader_str, classification
        ))

    # Tail report
    rms_anchor = config.get("rms_anchor", "percentile")
    window = config.get("window", 400)
    rms_percentile = config.get("rms_percentile", 95.0)
    tail_max_regions = config.get("tail_max_regions", 20)
    tail_min_exceed_db = config.get("tail_min_exceed_db", 3.0)
    tail_hop_ms = config.get("tail_hop_ms", 10)

    lines.extend([
        "",
        "-" * 80,
        "MOMENTARY RMS UPPER TAIL (Expected Windows Above Anchor)",
        "-" * 80,
        f"Anchor Mode: {rms_anchor} | Window: {window} ms" + (
            f" | Percentile: P{rms_percentile:g}" if rms_anchor == "percentile" else ""
        ),
        f"Report Limit: {tail_max_regions} regions per file | Min Exceed: {tail_min_exceed_db} dB | Hop: {tail_hop_ms} ms",
        "",
    ])

    if rms_anchor != "percentile":
        lines.append("Tail reporting is only computed for percentile anchoring.")
        lines.append("")
    else:
        any_tail_reported = False
        for t in session.tracks:
            if t.status != "OK":
                continue
            sil_r = t.detector_results.get("silence")
            if sil_r and sil_r.data.get("is_silent"):
                continue

            tail_r = t.detector_results.get("tail_exceedance")
            if not tail_r:
                continue
            regions = tail_r.data.get("tail_regions") or []
            if not regions:
                continue

            summary = tail_r.data.get("tail_summary") or {}
            anchor_db = summary.get("anchor_db", float('-inf'))
            total_dur = summary.get("total_duration_sec", 0.0)
            max_exceed = summary.get("max_exceed_db", 0.0)

            lines.append(f"{t.filename}")
            lines.append(f"  Anchor RMS: {anchor_db:.2f} dBFS")
            lines.append(f"  Tail Regions Reported: {int(summary.get('regions', 0))} | Total Tail Duration: {total_dur:.3f}s | Max Exceed: +{max_exceed:.2f} dB")

            any_tail_reported = True
            for i, reg in enumerate(regions, start=1):
                lines.append(
                    "  {:>2}. {} - {} | samples {}-{} | max +{:.2f} dB (RMS {:.2f} dBFS)".format(
                        i,
                        reg["start_time"],
                        reg["end_time"],
                        reg["start_sample"],
                        reg["end_sample"],
                        reg["max_exceed_db"],
                        reg["max_rms_db"],
                    )
                )
            lines.append("")

        if not any_tail_reported:
            lines.append("No significant tail exceedances above threshold were found.")
            lines.append("")

    # File overview
    lines.extend([
        "",
        "-" * 80,
        "FILE OVERVIEW",
        "-" * 80,
        "",
        "{:<25} {:>8} {:>8} {:>10}".format(
            "TRACK", "SR(kHz)", "BIT", "DUR"
        ),
        "-" * 80,
    ])

    errors = []
    for t in session.tracks:
        if t.status != "OK":
            errors.append((t.filename, t.status))
            continue

        sr_khz = f"{t.samplerate/1000:.1f}"
        dur_fmt = format_duration(t.total_samples, t.samplerate)
        lines.append("{:<25} {:>8} {:>8} {:>10}".format(
            t.filename[:23],
            sr_khz,
            t.bitdepth,
            dur_fmt,
        ))

    if errors:
        lines.extend([
            "",
            "-" * 80,
            "FILES WITH ERRORS",
            "-" * 80,
        ])
        for name, status in errors:
            lines.append(f"{name}: {status}")

    lines.extend([
        "",
        "=" * 80,
        "HOW TO USE:",
        "1. Use this output folder as the source for a fresh DAW session import",
        "2. Apply the fader offsets (manually or via automation from the JSON export)",
        "3. Your original balance is restored with optimal gain staging",
        "=" * 80,
    ])

    report_text = "\n".join(lines)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    return report_text


def save_json(
    session: SessionContext,
    config: dict[str, Any],
    output_path: str,
    warnings: list[str],
) -> None:
    """Generate JSON output for automation tools."""
    source_dir = config.get("_source_dir", "")

    data = {
        "schema_version": "1.0",
        "session": {
            "timestamp": datetime.now().isoformat(),
            "source_directory": os.path.abspath(source_dir),
            "warnings": warnings,
            "config": {
                "target_rms": config.get("target_rms", -18.0),
                "target_peak": config.get("target_peak", -6.0),
                "stereo_mode": config.get("stereo_mode", "avg"),
                "window_ms": config.get("window", 400),
                "rms_anchor": config.get("rms_anchor", "percentile"),
                "rms_percentile": config.get("rms_percentile", 95.0),
                "tail_hop_ms": config.get("tail_hop_ms", 10),
            }
        },
        "tracks": []
    }

    sort_index = 0
    for t in session.tracks:
        if t.status != "OK":
            continue

        pr = _get_primary_processor_result(t)
        clip_r = t.detector_results.get("clipping")
        sil_r = t.detector_results.get("silence")
        mono_r = t.detector_results.get("mono_folddown")

        is_clipped = bool(clip_r.data.get("is_clipped")) if clip_r else False
        is_silent = bool(sil_r.data.get("is_silent")) if sil_r else False
        mono_loss_db = mono_r.data.get("mono_loss_db") if mono_r else None
        mono_warn = bool(mono_r.data.get("mono_warn")) if mono_r else False

        fader_offset = pr.data.get("fader_offset", 0) if pr else 0
        classification = pr.classification if pr else "Unknown"
        gain_db = pr.gain_db if pr else 0
        gain_db_individual = pr.data.get("gain_db_individual", gain_db) if pr else 0

        data["tracks"].append({
            "sort_index": sort_index,
            "filename": t.filename,
            "diagnostics": {
                "samplerate": int(t.samplerate),
                "bitdepth": t.bitdepth,
                "duration_sec": float(t.duration_sec),
                "samples": int(t.total_samples),
                "is_clipped": bool(is_clipped),
                "is_silent": bool(is_silent),
                "mono_loss_db": None if mono_loss_db is None else float(mono_loss_db),
                "mono_loss_warn": bool(mono_warn),
            },
            "processing": {
                "fader_db": round(float(fader_offset), 2),
                "classification": classification,
                "gain_applied_db": round(float(gain_db), 2),
                "gain_individual_db": round(float(gain_db_individual), 2),
                "group": t.group,
            }
        })
        sort_index += 1

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def build_warnings(
    session: SessionContext,
    config: dict[str, Any],
) -> list[str]:
    """Build the warnings list from detector results (for JSON/report compat)."""
    warnings = list(session.warnings)

    ok_tracks = [t for t in session.tracks if t.status == "OK"]

    # SR mismatch warnings
    sr_counter = Counter(t.samplerate for t in ok_tracks)
    if len(sr_counter) > 1:
        most_common_sr = sr_counter.most_common(1)[0][0]
        warnings.append(f"Samplerate mismatch detected! Most common: {most_common_sr} Hz")
        for t in ok_tracks:
            if t.samplerate != most_common_sr:
                warnings.append(f"  - {t.filename} is {t.samplerate} Hz")

    # BD mismatch warnings
    bd_counter = Counter(t.bitdepth for t in ok_tracks)
    if len(bd_counter) > 1:
        most_common_bd = bd_counter.most_common(1)[0][0]
        warnings.append(f"Bit-depth mismatch detected! Most common: {most_common_bd}")
        for t in ok_tracks:
            if t.bitdepth != most_common_bd:
                warnings.append(f"  - {t.filename} is {t.bitdepth}")

    # Clipping warnings
    clipped = [t for t in ok_tracks
               if (t.detector_results.get("clipping") or
                   type('', (), {"data": {}})()).data.get("is_clipped")]
    if clipped:
        clip_consec = config.get("clip_consecutive", 3)
        warnings.append(f"Digital Clipping detected in source (>{clip_consec} samples @ \u00b11.0):")
        for t in clipped:
            warnings.append(f"  - {t.filename}")

    # DC offset warnings
    dc_warn_tracks = [t for t in ok_tracks
                      if (t.detector_results.get("dc_offset") or
                          type('', (), {"data": {}})()).data.get("dc_warn")]
    if dc_warn_tracks:
        dc_thresh = config.get("dc_offset_warn_db", -40.0)
        _off = dbfs_offset(config)
        warnings.append(f"DC offset detected (>{dc_thresh} dBFS):")
        for t in dc_warn_tracks:
            dc_r = t.detector_results.get("dc_offset")
            dc_db = dc_r.data.get("dc_db", float('-inf')) if dc_r else float('-inf')
            if np.isfinite(dc_db):
                warnings.append(f"  - {t.filename} is {dc_db + _off:.1f} dBFS")
            else:
                warnings.append(f"  - {t.filename}")

    # Silent file warnings
    silent = [t for t in ok_tracks
              if (t.detector_results.get("silence") or
                  type('', (), {"data": {}})()).data.get("is_silent")]
    if silent:
        warnings.append("Silent files detected:")
        for t in silent:
            warnings.append(f"  - {t.filename}")

    return warnings
