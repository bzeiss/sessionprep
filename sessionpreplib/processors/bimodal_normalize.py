from __future__ import annotations

import numpy as np

from ..config import ParamSpec
from ..processor import AudioProcessor, PRIORITY_NORMALIZE
from ..models import ProcessorResult, TrackContext
from ..audio import db_to_linear


class BimodalNormalizeProcessor(AudioProcessor):
    id = "bimodal_normalize"
    name = "Bimodal Normalization"
    priority = PRIORITY_NORMALIZE

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        return [
            ParamSpec(
                key="target_rms", type=(int, float), default=-18.0,
                min=-80.0, max=0.0,
                label="Target RMS (dBFS)",
                description="Sustained tracks are RMS-normalized to this level.",
            ),
            ParamSpec(
                key="target_peak", type=(int, float), default=-6.0,
                min=-80.0, max=0.0,
                label="Target peak (dBFS)",
                description="Transient tracks are peak-normalized to this level.",
            ),
        ]

    def configure(self, config):
        self.target_rms = config.get("target_rms", -18.0)
        self.target_peak = config.get("target_peak", -6.0)

    def process(self, track: TrackContext) -> ProcessorResult:
        # Read from crest_factor detector
        crest_result = track.detector_results.get("crest_factor")
        silence_result = track.detector_results.get("silence")

        if silence_result and silence_result.data.get("is_silent"):
            return ProcessorResult(
                processor_id=self.id,
                gain_db=0.0,
                classification="Silent",
                method="None",
                data={"gain_db_individual": 0.0},
            )

        if crest_result is None:
            return ProcessorResult(
                processor_id=self.id,
                gain_db=0.0,
                classification="Unknown",
                method="None",
                data={"gain_db_individual": 0.0},
                error="crest_factor detector result missing",
            )

        peak_db = crest_result.data["peak_db"]
        rms_anchor_db = crest_result.data["rms_anchor_db"]
        classification = crest_result.data["classification"]
        is_transient = crest_result.data["is_transient"]

        if is_transient:
            # TRANSIENT — normalize to peak
            gain = self.target_peak - peak_db
            method = f"Peak → {self.target_peak:.0f} dB"
        else:
            # SUSTAINED — normalize to RMS, but respect peak ceiling
            gain_for_rms = self.target_rms - rms_anchor_db
            gain_for_peak = self.target_peak - peak_db
            gain = min(gain_for_rms, gain_for_peak)

            if gain == gain_for_rms:
                method = f"RMS → {self.target_rms:.0f} dB"
            else:
                method = "Peak Limited"

        return ProcessorResult(
            processor_id=self.id,
            gain_db=float(gain),
            classification=classification,
            method=method,
            data={"gain_db_individual": float(gain)},
        )

    def apply(self, track: TrackContext, result: ProcessorResult) -> np.ndarray:
        if result.classification == "Silent" or result.gain_db == 0.0:
            return track.audio_data
        linear_gain = db_to_linear(float(result.gain_db))
        return track.audio_data * linear_gain
