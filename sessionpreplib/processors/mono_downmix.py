from __future__ import annotations

from typing import Any

import numpy as np

from ..config import ParamSpec
from ..processor import AudioProcessor, PRIORITY_POST
from ..models import ProcessorResult, TrackContext


class MonoDownmixProcessor(AudioProcessor):
    """Downmix multi-channel audio to mono.

    This is currently a stub — it computes what *would* happen but
    ``apply()`` returns the audio unchanged.  A real implementation
    would sum/average channels and return a mono array.
    """

    id = "mono_downmix"
    name = "Mono Downmix"
    shorthand = "MD"
    priority = PRIORITY_POST  # runs after normalization

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        return super().config_params() + []

    def configure(self, config: dict[str, Any]) -> None:
        super().configure(config)

    def process(self, track: TrackContext) -> ProcessorResult:
        if track.channels == 1:
            return ProcessorResult(
                processor_id=self.id,
                classification="Mono",
                method="pass-through (already mono)",
                gain_db=0.0,
                data={},
            )
        return ProcessorResult(
            processor_id=self.id,
            classification="Stereo",
            method=f"downmix {track.channels}ch \u2192 1ch (stub)",
            gain_db=0.0,
            data={"original_channels": track.channels},
        )

    def render_html(self, result: ProcessorResult, track: TrackContext | None = None,
                    *, verbose: bool = False) -> str:
        # Pass-through results are clutter unless verbose
        if not verbose and result.gain_db == 0.0 and "pass-through" in (result.method or ""):
            return ""
        return super().render_html(result, track, verbose=verbose)

    def apply(self, track: TrackContext, result: ProcessorResult) -> np.ndarray:
        # Stub: return audio unchanged for now
        return track.audio_data
