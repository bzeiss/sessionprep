from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import numpy as np

from .models import (
    DetectorResult,
    ProcessorResult,
    Severity,
    TrackContext,
    SessionContext,
)
from .detector import TrackDetector, SessionDetector
from .processor import AudioProcessor
from .events import EventBus
from .audio import load_track, write_track, format_duration, linear_to_db, AUDIO_EXTENSIONS
from .config import ConfigError, validate_config
from .utils import (
    protools_sort_key,
    parse_group_specs,
    assign_groups_to_files_with_policy,
)


class Pipeline:
    def __init__(
        self,
        detectors: list,
        audio_processors: list[AudioProcessor] | None = None,
        config: dict[str, Any] | None = None,
        event_bus: EventBus | None = None,
        max_workers: int | None = None,
    ):
        self.config = config or {}
        self.event_bus = event_bus
        self.max_workers = max_workers or min(os.cpu_count() or 4, 8)

        # Separate track vs session detectors
        self.track_detectors: list[TrackDetector] = [
            d for d in detectors if isinstance(d, TrackDetector)
        ]
        self.session_detectors: list[SessionDetector] = [
            d for d in detectors if isinstance(d, SessionDetector)
        ]

        # Topologically sort track detectors by depends_on
        self.track_detectors = _topo_sort_detectors(self.track_detectors)

        # Sort audio processors by priority
        self.audio_processors: list[AudioProcessor] = sorted(
            audio_processors or [], key=lambda p: p.priority
        )

        # Configure all components
        for d in self.track_detectors:
            d.configure(self.config)
        for d in self.session_detectors:
            d.configure(self.config)
        for p in self.audio_processors:
            p.configure(self.config)

        # Validate
        self._validate()

    def _validate(self):
        """Validate pipeline configuration at construction time."""
        all_ids = set()
        for d in self.track_detectors:
            if d.id in all_ids:
                raise ConfigError(f"Duplicate detector ID: {d.id}")
            all_ids.add(d.id)
        for d in self.session_detectors:
            if d.id in all_ids:
                raise ConfigError(f"Duplicate detector ID: {d.id}")
            all_ids.add(d.id)
        for p in self.audio_processors:
            if p.id in all_ids:
                raise ConfigError(f"Duplicate processor ID: {p.id}")
            all_ids.add(p.id)

    def _emit(self, event_type: str, **data):
        if self.event_bus:
            self.event_bus.emit(event_type, **data)

    # ------------------------------------------------------------------
    # Phase 1: Analyze (run all detectors)
    # ------------------------------------------------------------------

    def _analyze_track(self, track: TrackContext, idx: int, total: int):
        """Run all track-level detectors for a single track (thread-safe)."""
        self._emit("track.analyze_start", filename=track.filename,
                   index=idx, total=total)
        for det in self.track_detectors:
            try:
                self._emit("detector.start", detector_id=det.id,
                           filename=track.filename)
                result = det.analyze(track)
                track.detector_results[det.id] = result
                self._emit("detector.complete", detector_id=det.id,
                           filename=track.filename,
                           severity=result.severity)
            except Exception as e:
                track.detector_results[det.id] = DetectorResult(
                    detector_id=det.id,
                    severity=Severity.PROBLEM,
                    summary=f"detector error: {e}",
                    data={},
                    error=str(e),
                )
        self._emit("track.analyze_complete", filename=track.filename,
                   index=idx, total=total)

    def analyze(self, session: SessionContext) -> SessionContext:
        """Run all track-level and session-level detectors.

        Track-level detectors run in parallel across files using a thread pool.
        Session-level detectors run sequentially after all tracks complete.
        """
        total = len(session.tracks)
        ok_items = [
            (idx, track)
            for idx, track in enumerate(session.tracks)
            if track.status == "OK"
        ]

        workers = min(self.max_workers, len(ok_items)) if ok_items else 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._analyze_track, track, idx, total): track
                for idx, track in ok_items
            }
            for future in as_completed(futures):
                exc = future.exception()
                if exc:
                    track = futures[future]
                    self._emit("track.analyze_complete",
                               filename=track.filename,
                               index=0, total=total)

        # Session-level detectors
        track_map = {t.filename: t for t in session.tracks}
        for det in self.session_detectors:
            try:
                self._emit("session_detector.start", detector_id=det.id)
                results = det.analyze(session)
                session.config[f"_session_det_{det.id}"] = results
                # Distribute per-track results back into each track
                for result in results:
                    fname = result.data.get("filename")
                    if fname and fname in track_map:
                        track_map[fname].detector_results[det.id] = result
                self._emit("session_detector.complete", detector_id=det.id)
            except Exception as e:
                session.config[f"_session_det_{det.id}"] = [
                    DetectorResult(
                        detector_id=det.id,
                        severity=Severity.PROBLEM,
                        summary=f"session detector error: {e}",
                        data={},
                        error=str(e),
                    )
                ]

        # Store configured detector instances on the session for render-time access
        session.detectors = self.track_detectors + self.session_detectors

        return session

    # ------------------------------------------------------------------
    # Phase 2: Plan (run audio processors, compute gains)
    # ------------------------------------------------------------------

    def _plan_track(self, track: TrackContext, idx: int, total: int):
        """Run all audio processors for a single track (thread-safe)."""
        self._emit("track.plan_start", filename=track.filename,
                   index=idx, total=total)
        for proc in self.audio_processors:
            try:
                self._emit("processor.start", processor_id=proc.id,
                           filename=track.filename)
                result = proc.process(track)
                track.processor_results[proc.id] = result
                self._emit("processor.complete", processor_id=proc.id,
                           filename=track.filename)
            except Exception as e:
                track.processor_results[proc.id] = ProcessorResult(
                    processor_id=proc.id,
                    gain_db=0.0,
                    classification="Error",
                    method="None",
                    error=str(e),
                )
        self._emit("track.plan_complete", filename=track.filename,
                   index=idx, total=total)

    def plan(self, session: SessionContext) -> SessionContext:
        """
        Run all audio processors in priority order.
        Computes gains and classifications without modifying audio.
        Also handles group gain levelling and fader offsets.

        Per-track processors run in parallel using a thread pool.
        Group levelling and fader offsets run after all tracks complete.
        """
        total = len(session.tracks)
        ok_items = [
            (idx, track)
            for idx, track in enumerate(session.tracks)
            if track.status == "OK"
        ]

        workers = min(self.max_workers, len(ok_items)) if ok_items else 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._plan_track, track, idx, total): track
                for idx, track in ok_items
            }
            for future in as_completed(futures):
                exc = future.exception()
                if exc:
                    track = futures[future]
                    self._emit("track.plan_complete",
                               filename=track.filename,
                               index=0, total=total)

        # --- Group gain levelling ---
        self._apply_group_levels(session)

        # --- Fader offsets ---
        self._compute_fader_offsets(session)

        # Store configured processor instances on the session for render-time access
        session.processors = list(self.audio_processors)

        return session

    def _apply_group_levels(self, session: SessionContext):
        """Apply group levels for gain-linked groups (minimum gain of the group).

        Reads ``_gain_linked_groups`` from *session.config* — a set of group
        names whose members should share the same gain.  If the key is absent
        **all** groups are levelled (backward-compatible CLI behaviour).
        """
        linked: set[str] | None = session.config.get("_gain_linked_groups")

        for proc in self.audio_processors:
            by_gid: dict[str, list[TrackContext]] = {}
            for track in session.tracks:
                if track.status != "OK" or track.group is None:
                    continue
                pr = track.processor_results.get(proc.id)
                if pr is None or pr.classification == "Silent":
                    continue
                # Preserve the per-track gain before any group adjustment
                if "original_gain_db" not in pr.data:
                    pr.data["original_gain_db"] = pr.gain_db
                by_gid.setdefault(track.group, []).append(track)

            for gid, members in by_gid.items():
                if linked is not None and gid not in linked:
                    continue
                orig = [m.processor_results[proc.id].data["original_gain_db"]
                        for m in members]
                group_gain = min(orig) if orig else 0.0
                for m in members:
                    m.processor_results[proc.id].gain_db = float(group_gain)

    def _compute_fader_offsets(self, session: SessionContext):
        """Compute fader offsets (inverse of gain) with anchor adjustment."""
        for proc in self.audio_processors:
            valid = []
            for track in session.tracks:
                if track.status != "OK":
                    continue
                pr = track.processor_results.get(proc.id)
                if pr is None:
                    continue
                if pr.classification == "Silent":
                    pr.data["fader_offset"] = 0.0
                else:
                    pr.data["fader_offset"] = -float(pr.gain_db)
                    valid.append(track)

            # Anchor adjustment
            anchor_name = self.config.get("anchor")
            normalize_faders = self.config.get("normalize_faders", False)

            anchor_offset = 0.0
            if anchor_name:
                anchor_track = next(
                    (t for t in valid
                     if anchor_name.lower() in t.filename.lower()),
                    None,
                )
                if anchor_track:
                    pr = anchor_track.processor_results.get(proc.id)
                    if pr:
                        anchor_offset = pr.data.get("fader_offset", 0.0)
            elif normalize_faders and valid:
                fader_offsets = [
                    t.processor_results[proc.id].data.get("fader_offset", 0.0)
                    for t in valid
                ]
                anchor_offset = max(fader_offsets) if fader_offsets else 0.0

            # Store anchor offset for GUI-side recomputation
            session.config[f"_anchor_offset_{proc.id}"] = anchor_offset

            if anchor_offset != 0.0:
                for track in valid:
                    pr = track.processor_results.get(proc.id)
                    if pr:
                        pr.data["fader_offset"] = pr.data.get("fader_offset", 0.0) - anchor_offset

    # ------------------------------------------------------------------
    # Phase 3: Execute (apply gains, write files)
    # ------------------------------------------------------------------

    def execute(
        self,
        session: SessionContext,
        output_dir: str,
        backup_dir: str | None = None,
        is_overwriting: bool = False,
    ) -> SessionContext:
        """Apply audio processor gains and write files."""
        import shutil

        os.makedirs(output_dir, exist_ok=True)
        if backup_dir and is_overwriting:
            os.makedirs(backup_dir, exist_ok=True)

        total = len(session.tracks)
        for idx, track in enumerate(session.tracks):
            if track.status != "OK":
                continue

            self._emit("track.write_start", filename=track.filename,
                       index=idx, total=total)

            try:
                src_filepath = track.filepath
                dst_filepath = os.path.join(output_dir, track.filename)

                if is_overwriting and backup_dir:
                    backup_path = os.path.join(backup_dir, track.filename)
                    if not os.path.exists(backup_path):
                        shutil.copy2(src_filepath, backup_path)

                # Apply all processors in order
                for proc in self.audio_processors:
                    pr = track.processor_results.get(proc.id)
                    if pr and pr.error is None:
                        track.audio_data = proc.apply(track, pr)

                # Compute output analysis (for reporting)
                if track.audio_data is not None and track.audio_data.size > 0:
                    out_peak_lin = float(np.max(np.abs(track.audio_data)))
                    out_peak_db = linear_to_db(out_peak_lin)
                else:
                    out_peak_db = float(-np.inf)

                # Store output stats on all processor results
                for proc in self.audio_processors:
                    pr = track.processor_results.get(proc.id)
                    if pr:
                        pr.data["out_peak"] = out_peak_db

                write_track(track, dst_filepath)

            except Exception as e:
                track.status = f"Error: {e}"

            self._emit("track.write_complete", filename=track.filename,
                       index=idx, total=total)

        return session


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _load_one_track(
    source_dir: str,
    filename: str,
    idx: int,
    total: int,
    event_bus: EventBus | None,
) -> TrackContext:
    """Load a single WAV file (used by thread pool in load_session)."""
    filepath = os.path.join(source_dir, filename)
    if event_bus:
        event_bus.emit("track.load", filename=filename,
                       index=idx, total=total)
    try:
        return load_track(filepath)
    except Exception as e:
        return TrackContext(
            filename=filename,
            filepath=filepath,
            audio_data=None,
            samplerate=0,
            channels=0,
            total_samples=0,
            bitdepth="",
            subtype="",
            duration_sec=0.0,
            status=f"Error: {e}",
        )


def load_session(
    source_dir: str,
    config: dict[str, Any],
    event_bus: EventBus | None = None,
) -> SessionContext:
    """
    Load all WAV files from source_dir into a SessionContext.
    Files are loaded in parallel using a thread pool.
    Handles group assignment.
    """
    files = sorted(
        [f for f in os.listdir(source_dir) if f.lower().endswith(AUDIO_EXTENSIONS)],
        key=protools_sort_key,
    )

    total = len(files)
    workers = min(os.cpu_count() or 4, 8, total) if total else 1
    tracks: list[TrackContext] = [None] * total  # type: ignore[list-item]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _load_one_track, source_dir, filename, idx, total, event_bus
            ): idx
            for idx, filename in enumerate(files)
        }
        for future in as_completed(futures):
            idx = futures[future]
            tracks[idx] = future.result()

    session = SessionContext(tracks=tracks, config=dict(config))

    # Group assignment
    group_args = config.get("group", [])
    group_overlap_policy = config.get("group_overlap", "warn")
    group_specs = parse_group_specs(group_args)

    if group_specs:
        filenames = [t.filename for t in tracks]
        assignments, overlaps = assign_groups_to_files_with_policy(
            filenames, group_specs, overlap_policy=group_overlap_policy,
        )
        session.groups = assignments
        session.group_overlaps = overlaps

        for track in tracks:
            track.group = assignments.get(track.filename)

        # Generate overlap warnings
        if overlaps:
            if group_overlap_policy == "merge":
                for fname, matched in overlaps:
                    root_gid = assignments.get(fname)
                    matched_str = ", ".join(matched)
                    session.warnings.append(
                        f"Grouping overlap: {fname} matched multiple groups "
                        f"({matched_str}); merged into {root_gid}"
                    )
            else:
                for fname, keep_gid, drop_gid in overlaps:
                    session.warnings.append(
                        f"Grouping overlap: {fname} matched multiple groups "
                        f"({keep_gid}, {drop_gid}); using {keep_gid}"
                    )

    return session


def _topo_sort_detectors(detectors: list[TrackDetector]) -> list[TrackDetector]:
    """
    Topological sort by depends_on.
    Raises ConfigError if a cycle is detected or a dependency is missing.
    """
    by_id = {d.id: d for d in detectors}
    all_ids = set(by_id.keys())

    # Validate dependencies exist
    for d in detectors:
        for dep in (d.depends_on or []):
            if dep not in all_ids:
                raise ConfigError(
                    f"Detector '{d.id}' depends on '{dep}' which does not exist"
                )

    # Kahn's algorithm
    in_degree = {d.id: 0 for d in detectors}
    adjacency: dict[str, list[str]] = {d.id: [] for d in detectors}
    for d in detectors:
        for dep in (d.depends_on or []):
            adjacency[dep].append(d.id)
            in_degree[d.id] += 1

    queue = [did for did, deg in in_degree.items() if deg == 0]
    sorted_ids = []
    while queue:
        node = queue.pop(0)
        sorted_ids.append(node)
        for neighbor in adjacency.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(sorted_ids) != len(detectors):
        raise ConfigError("Cyclic dependency detected among track detectors")

    return [by_id[did] for did in sorted_ids]
