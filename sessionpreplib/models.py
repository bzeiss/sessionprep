from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import numpy as np


class Severity(Enum):
    CLEAN = "clean"
    INFO = "info"
    ATTENTION = "attention"
    PROBLEM = "problem"


class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class IssueLocation:
    """A detected issue at a specific position or region in the waveform.

    Attributes:
        sample_start: Start sample position (inclusive).
        sample_end:   End sample position (inclusive). None for point issues.
        channel:      Channel index (0 = first/left, 1 = second/right, …).
                      None means the issue affects all channels together.
        severity:     Severity of this specific issue.
        label:        Machine-readable tag, e.g. "clipping", "tail_exceedance".
        description:  Human-readable text shown in tooltips / overlays.
    """
    sample_start: int
    sample_end: int | None
    channel: int | None
    severity: Severity
    label: str
    description: str
    freq_min_hz: float | None = None
    freq_max_hz: float | None = None


@dataclass
class DetectorResult:
    detector_id: str
    severity: Severity
    summary: str
    data: dict[str, Any]
    detail_lines: list[str] = field(default_factory=list)
    hint: str | None = None
    error: str | None = None
    issues: list[IssueLocation] = field(default_factory=list)


@dataclass
class ProcessorResult:
    processor_id: str
    gain_db: float
    classification: str
    method: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class DawCommand:
    """A single operation to perform against a DAW.

    Plain data object — the DawProcessor that created it is responsible
    for execution.  undo_params captures the state needed to reverse
    the operation (e.g. the previous fader value).
    """
    command_type: str
    target: str
    params: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    undo_params: dict[str, Any] | None = None


@dataclass
class DawCommandResult:
    """Outcome of executing a single DawCommand."""
    command: DawCommand
    success: bool
    error: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class TrackContext:
    filename: str
    filepath: str
    audio_data: np.ndarray | None
    samplerate: int
    channels: int
    total_samples: int
    bitdepth: str
    subtype: str
    duration_sec: float
    status: str = "OK"
    detector_results: dict[str, DetectorResult] = field(default_factory=dict)
    processor_results: dict[str, ProcessorResult] = field(default_factory=dict)
    group: str | None = None
    classification_override: str | None = None
    rms_anchor_override: str | None = None
    chunk_ids: list[str] = field(default_factory=list)
    processed_filepath: str | None = None
    applied_processors: list[str] = field(default_factory=list)
    processor_skip: set[str] = field(default_factory=set)
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class SessionContext:
    tracks: list[TrackContext]
    config: dict[str, Any]
    groups: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    detectors: list = field(default_factory=list)
    processors: list = field(default_factory=list)
    daw_state: dict[str, Any] = field(default_factory=dict)
    daw_command_log: list[DawCommandResult] = field(default_factory=list)
    prepare_state: str = "none"


@dataclass
class SessionResult:
    session: SessionContext
    daw_commands: list[DawCommand] = field(default_factory=list)
    diagnostic_summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionJob:
    job_id: str
    source_dir: str
    config: dict[str, Any]
    status: JobStatus = JobStatus.PENDING
    priority: int = 0
    result: SessionResult | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
