from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from .config import ParamSpec
from .models import ProcessorResult, TrackContext


# Priority band constants
PRIORITY_CLEANUP = 0
PRIORITY_NORMALIZE = 100
PRIORITY_POST = 200
PRIORITY_FINALIZE = 900


class AudioProcessor(ABC):
    """
    Transforms audio data. Runs after detectors.
    process() computes what to do (pure analysis, no side effects).
    apply() performs the transformation on audio data.
    """
    id: str = ""
    name: str = ""
    priority: int = PRIORITY_NORMALIZE

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        """Return parameter specifications for this processor."""
        return []

    def configure(self, config: dict[str, Any]) -> None:
        pass

    @abstractmethod
    def process(self, track: TrackContext) -> ProcessorResult:
        """
        Decide what to do. Reads detector_results.
        Does NOT mutate audio_data. Returns a ProcessorResult.
        Used in both dry-run and execute mode.
        """
        ...

    def render_html(self, result: ProcessorResult, track: TrackContext | None = None,
                    *, verbose: bool = False) -> str:
        """Return an HTML fragment for this processor's result.

        Override in subclasses for richer output (e.g. comparison tables).
        The default renders classification, method, and gain.

        Parameters
        ----------
        result : ProcessorResult
        track : TrackContext | None
            The full track context (with detector and processor results)
            so that the processor can decide its own rendering relevance.
        verbose : bool
            When True, include additional analytical detail.
        """
        cls_text = result.classification or "Unknown"
        return (
            f'<div style="margin-left:8px;">'
            f'Classification: <b>{cls_text}</b>'
            f' &nbsp;&middot;&nbsp; {result.method}'
            f' &nbsp;&middot;&nbsp; {result.gain_db:+.1f} dB'
            f'</div>'
        )

    @abstractmethod
    def apply(self, track: TrackContext, result: ProcessorResult) -> np.ndarray:
        """
        Apply the transformation to track.audio_data.
        Returns the modified audio array.
        Only called in execute mode.
        """
        ...
