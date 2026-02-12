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
class DawAction:
    action_type: str
    target: str
    params: dict[str, Any]
    source: str
    priority: int = 0


@dataclass
class DawActionResult:
    action: DawAction
    success: bool
    error: str | None = None


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
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class SessionContext:
    tracks: list[TrackContext]
    config: dict[str, Any]
    groups: dict[str, str] = field(default_factory=dict)
    group_overlaps: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    detectors: list = field(default_factory=list)
    processors: list = field(default_factory=list)


@dataclass
class SessionResult:
    session: SessionContext
    daw_actions: list[DawAction] = field(default_factory=list)
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
