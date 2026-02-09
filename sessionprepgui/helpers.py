"""Pure helper functions used across the GUI — no widget dependencies."""

from __future__ import annotations

from .theme import COLORS


# ---------------------------------------------------------------------------
# Severity ranking
# ---------------------------------------------------------------------------

SEVERITY_RANK = {"problem": 0, "attention": 1, "information": 2, "clean": 3}
SEVERITY_LABELS = {
    "problem": ("PROBLEMS", COLORS["problems"]),
    "attention": ("ATTENTION", COLORS["attention"]),
    "information": ("OK", COLORS["information"]),
    "clean": ("OK", COLORS["clean"]),
}


def track_analysis_label(track, detectors=None) -> tuple[str, str]:
    """Return (label, color_hex) for the worst detector severity.

    Labels: ``"PROBLEMS"`` (red), ``"ATTENTION"`` (yellow), ``"OK"`` (green).

    Parameters
    ----------
    track : TrackContext
    detectors : list | None
        If provided, each detector's ``is_relevant()`` is checked and
        irrelevant results are excluded from the worst-severity calculation.
        Pass ``session.detectors`` after processors have run.
    """
    if track.status != "OK":
        return "Error", COLORS["problems"]

    det_map = {d.id: d for d in detectors} if detectors else {}

    worst = "clean"
    for det_id, result in track.detector_results.items():
        det_inst = det_map.get(det_id)
        if det_inst and hasattr(det_inst, 'is_relevant') and not det_inst.is_relevant(result, track):
            continue
        sev = result.severity.value if hasattr(result.severity, "value") else str(result.severity)
        if SEVERITY_RANK.get(sev, 99) < SEVERITY_RANK.get(worst, 99):
            worst = sev

    label, color = SEVERITY_LABELS.get(worst, ("OK", COLORS["clean"]))
    return label, color


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def esc(text: str) -> str:
    """Escape HTML special characters."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def fmt_time(seconds: float) -> str:
    """Format seconds as mm:ss or hh:mm:ss."""
    seconds = max(0.0, seconds)
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
