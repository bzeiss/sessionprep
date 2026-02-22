"""Session save / load for SessionPrep GUI.

Serialises the full analysis state (detector results, processor results,
user edits) to a ``.spsession`` JSON file so a session can be restored
without re-running analysis.

Format versioning
-----------------
``CURRENT_VERSION`` is bumped whenever the schema changes.  ``_MIGRATIONS``
maps version N → a callable that upgrades a raw dict from version N to N+1.
``load_session()`` applies all necessary migrations before returning.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from sessionpreplib.models import (
    DetectorResult,
    IssueLocation,
    ProcessorResult,
    Severity,
    TrackContext,
    TransferEntry,
)
from sessionpreplib.topology import (
    ChannelRoute,
    TopologyEntry,
    TopologyMapping,
    TopologySource,
)

# ---------------------------------------------------------------------------
# Version & migration table
# ---------------------------------------------------------------------------

CURRENT_VERSION: int = 3

# Each entry upgrades from key-version to key+1.
_MIGRATIONS: dict[int, Callable[[dict], dict]] = {
    1: lambda d: {
        **d,
        "topology": None,
        "transfer_manifest": [],
        "version": 2,
    },
    2: lambda d: {
        **d,
        "topology_applied": False,
        "version": 3,
    },
}


def _migrate(data: dict) -> dict:
    """Upgrade *data* in-place from its stored version to CURRENT_VERSION."""
    v = data.get("version", 1)
    if v > CURRENT_VERSION:
        raise ValueError(
            f"Session file was saved with a newer version of SessionPrep "
            f"(file version {v}, this build supports up to {CURRENT_VERSION}). "
            f"Please upgrade SessionPrep."
        )
    while v < CURRENT_VERSION:
        fn = _MIGRATIONS.get(v)
        if fn is None:
            raise ValueError(
                f"No migration path from version {v} to {v + 1}."
            )
        data = fn(data)
        v += 1
    return data


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _ser_issue(issue: IssueLocation) -> dict:
    return {
        "sample_start": issue.sample_start,
        "sample_end": issue.sample_end,
        "channel": issue.channel,
        "severity": issue.severity.value,
        "label": issue.label,
        "description": issue.description,
        "freq_min_hz": issue.freq_min_hz,
        "freq_max_hz": issue.freq_max_hz,
    }


def _deser_issue(d: dict) -> IssueLocation:
    return IssueLocation(
        sample_start=d["sample_start"],
        sample_end=d.get("sample_end"),
        channel=d.get("channel"),
        severity=Severity(d["severity"]),
        label=d.get("label", ""),
        description=d.get("description", ""),
        freq_min_hz=d.get("freq_min_hz"),
        freq_max_hz=d.get("freq_max_hz"),
    )


def _ser_detector_result(r: DetectorResult) -> dict:
    return {
        "detector_id": r.detector_id,
        "severity": r.severity.value,
        "summary": r.summary,
        "detail_lines": r.detail_lines,
        "hint": r.hint,
        "error": r.error,
        "data": _make_json_safe(r.data),
        "issues": [_ser_issue(i) for i in r.issues],
    }


def _deser_detector_result(d: dict) -> DetectorResult:
    return DetectorResult(
        detector_id=d["detector_id"],
        severity=Severity(d["severity"]),
        summary=d.get("summary", ""),
        data=d.get("data", {}),
        detail_lines=d.get("detail_lines", []),
        hint=d.get("hint"),
        error=d.get("error"),
        issues=[_deser_issue(i) for i in d.get("issues", [])],
    )


def _ser_processor_result(r: ProcessorResult) -> dict:
    return {
        "processor_id": r.processor_id,
        "gain_db": r.gain_db,
        "classification": r.classification,
        "method": r.method,
        "data": _make_json_safe(r.data),
        "error": r.error,
    }


def _deser_processor_result(d: dict) -> ProcessorResult:
    return ProcessorResult(
        processor_id=d["processor_id"],
        gain_db=d.get("gain_db", 0.0),
        classification=d.get("classification", ""),
        method=d.get("method", ""),
        data=d.get("data", {}),
        error=d.get("error"),
    )


def _make_json_safe(obj: Any) -> Any:
    """Recursively convert non-JSON-serialisable values to safe equivalents."""
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(v) for v in obj]
    if isinstance(obj, float):
        # Handle inf / nan
        if obj != obj or obj == float("inf") or obj == float("-inf"):
            return None
        return obj
    if hasattr(obj, "value"):  # Enum
        return obj.value
    if isinstance(obj, (int, str, bool, type(None))):
        return obj
    return str(obj)


def _serialize_track(track: TrackContext) -> dict:
    return {
        "status": track.status,
        "channels": track.channels,
        "samplerate": track.samplerate,
        "total_samples": track.total_samples,
        "bitdepth": track.bitdepth,
        "subtype": track.subtype,
        "duration_sec": track.duration_sec,
        "group": track.group,
        "classification_override": track.classification_override,
        "rms_anchor_override": track.rms_anchor_override,
        "processor_skip": sorted(track.processor_skip),
        "detector_results": {
            k: _ser_detector_result(v)
            for k, v in track.detector_results.items()
        },
        "processor_results": {
            k: _ser_processor_result(v)
            for k, v in track.processor_results.items()
        },
    }


def _deserialize_track(filename: str, source_dir: str, d: dict) -> TrackContext:
    filepath = os.path.join(source_dir, filename)
    # If file no longer exists, mark as error
    if not os.path.isfile(filepath):
        status = "Error"
    else:
        status = d.get("status", "OK")

    track = TrackContext(
        filename=filename,
        filepath=filepath,
        audio_data=None,
        samplerate=d.get("samplerate", 0),
        channels=d.get("channels", 0),
        total_samples=d.get("total_samples", 0),
        bitdepth=d.get("bitdepth", ""),
        subtype=d.get("subtype", ""),
        duration_sec=d.get("duration_sec", 0.0),
        status=status,
    )
    track.group = d.get("group")
    track.classification_override = d.get("classification_override")
    track.rms_anchor_override = d.get("rms_anchor_override")
    track.processor_skip = set(d.get("processor_skip", []))
    track.detector_results = {
        k: _deser_detector_result(v)
        for k, v in d.get("detector_results", {}).items()
    }
    track.processor_results = {
        k: _deser_processor_result(v)
        for k, v in d.get("processor_results", {}).items()
    }
    return track


# ---------------------------------------------------------------------------
# Topology serialisation helpers
# ---------------------------------------------------------------------------

def _ser_channel_route(r: ChannelRoute) -> dict:
    return {
        "source_channel": r.source_channel,
        "target_channel": r.target_channel,
        "gain": r.gain,
    }


def _deser_channel_route(d: dict) -> ChannelRoute:
    return ChannelRoute(
        source_channel=d["source_channel"],
        target_channel=d["target_channel"],
        gain=d.get("gain", 1.0),
    )


def _ser_topology_source(s: TopologySource) -> dict:
    return {
        "input_filename": s.input_filename,
        "routes": [_ser_channel_route(r) for r in s.routes],
    }


def _deser_topology_source(d: dict) -> TopologySource:
    return TopologySource(
        input_filename=d["input_filename"],
        routes=[_deser_channel_route(r) for r in d.get("routes", [])],
    )


def _ser_topology_entry(e: TopologyEntry) -> dict:
    return {
        "output_filename": e.output_filename,
        "output_channels": e.output_channels,
        "sources": [_ser_topology_source(s) for s in e.sources],
    }


def _deser_topology_entry(d: dict) -> TopologyEntry:
    return TopologyEntry(
        output_filename=d["output_filename"],
        output_channels=d.get("output_channels", 1),
        sources=[_deser_topology_source(s) for s in d.get("sources", [])],
    )


def _ser_topology(mapping: TopologyMapping | None) -> dict | None:
    if mapping is None:
        return None
    return {
        "entries": [_ser_topology_entry(e) for e in mapping.entries],
    }


def _deser_topology(d: dict | None) -> TopologyMapping | None:
    if d is None:
        return None
    return TopologyMapping(
        entries=[_deser_topology_entry(e) for e in d.get("entries", [])],
    )


# ---------------------------------------------------------------------------
# Transfer manifest serialisation helpers
# ---------------------------------------------------------------------------

def _ser_transfer_entry(e: TransferEntry) -> dict:
    return {
        "entry_id": e.entry_id,
        "output_filename": e.output_filename,
        "daw_track_name": e.daw_track_name,
        "group": e.group,
    }


def _deser_transfer_entry(d: dict) -> TransferEntry:
    return TransferEntry(
        entry_id=d["entry_id"],
        output_filename=d["output_filename"],
        daw_track_name=d.get("daw_track_name", d["output_filename"]),
        group=d.get("group"),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_session(path: str, data: dict) -> None:
    """Serialise *data* to a ``.spsession`` JSON file at *path*.

    *data* is the raw dict assembled by the mainwindow (already plain-Python
    types except for ``TrackContext`` objects under ``"tracks"``).
    """
    payload: dict[str, Any] = {
        "version": CURRENT_VERSION,
        "source_dir": data["source_dir"],
        "active_config_preset": data.get("active_config_preset", "Default"),
        "session_config": data.get("session_config"),
        "session_groups": data.get("session_groups", []),
        "daw_state": _make_json_safe(data.get("daw_state", {})),
        "tracks": {
            track.filename: _serialize_track(track)
            for track in data.get("tracks", [])
        },
        "topology": _ser_topology(data.get("topology")),
        "transfer_manifest": [
            _ser_transfer_entry(e)
            for e in data.get("transfer_manifest", [])
        ],
        "topology_applied": data.get("topology_applied", False),
        "prepare_state": data.get("prepare_state", "none"),
        "base_transfer_manifest": [
            _ser_transfer_entry(e)
            for e in data.get("base_transfer_manifest", [])
        ],
        "use_processed": data.get("use_processed", False),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def load_session(path: str) -> dict:
    """Load and migrate a ``.spsession`` file.

    Returns a plain dict with keys:
    - ``source_dir`` (str)
    - ``active_config_preset`` (str)
    - ``session_config`` (dict | None)
    - ``session_groups`` (list)
    - ``daw_state`` (dict)
    - ``tracks`` (list[TrackContext]) — audio_data is None; filepath validated

    Raises ``ValueError`` on version mismatch or missing required fields.
    Raises ``json.JSONDecodeError`` / ``OSError`` on file errors.
    """
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    raw = _migrate(raw)

    source_dir = raw.get("source_dir", "")
    if not source_dir:
        raise ValueError("Session file is missing 'source_dir'.")

    tracks = [
        _deserialize_track(fname, source_dir, tdata)
        for fname, tdata in raw.get("tracks", {}).items()
    ]

    topology = _deser_topology(raw.get("topology"))
    transfer_manifest = [
        _deser_transfer_entry(e)
        for e in raw.get("transfer_manifest", [])
    ]

    return {
        "source_dir": source_dir,
        "active_config_preset": raw.get("active_config_preset", "Default"),
        "session_config": raw.get("session_config"),
        "session_groups": raw.get("session_groups", []),
        "daw_state": raw.get("daw_state", {}),
        "tracks": tracks,
        "topology": topology,
        "transfer_manifest": transfer_manifest,
        "topology_applied": raw.get("topology_applied", False),
        "prepare_state": raw.get("prepare_state", "none"),
        "base_transfer_manifest": [
            _deser_transfer_entry(e)
            for e in raw.get("base_transfer_manifest", [])
        ],
        "use_processed": raw.get("use_processed", False),
    }
