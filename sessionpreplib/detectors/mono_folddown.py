from __future__ import annotations

import numpy as np

from ..config import ParamSpec
from ..detector import TrackDetector
from ..models import DetectorResult, Severity, TrackContext
from ..audio import get_stereo_channels_dc_removed, is_silent


class MonoFolddownDetector(TrackDetector):
    id = "mono_folddown"
    name = "Mono Fold-Down Loss"
    depends_on = ["silence"]

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        return super().config_params() + [
            ParamSpec(
                key="mono_loss_warn_db", type=(int, float), default=6.0,
                min=0.0, min_exclusive=True,
                label="Mono folddown loss warning (dB)",
                description="Mono fold-down loss above this level triggers a warning.",
            ),
        ]

    @classmethod
    def html_help(cls) -> str:
        return (
            "<b>Description</b><br/>"
            "Compares the RMS level of the stereo signal to its mono fold-down "
            "(L+R)/2, measuring how much level is lost when summing to mono."
            "<br/><br/>"
            "<b>Results</b><br/>"
            "<b>OK</b> – Mono fold-down loss is acceptable.<br/>"
            "<b>INFO</b> – Significant mono loss detected (reported in dB)."
            "<br/><br/>"
            "<b>Interpretation</b><br/>"
            "High mono fold-down loss means significant cancellation when "
            "summing L+R. This affects mono playback on phone speakers, some "
            "club systems, and broadcast. Review stereo widening, panning, or "
            "mid/side processing."
        )

    def configure(self, config):
        super().configure(config)
        self.mono_loss_warn_db = config.get("mono_loss_warn_db", 6.0)

    def analyze(self, track: TrackContext) -> DetectorResult:
        if is_silent(track):
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="silent track",
                data={"mono_loss_db": None, "mono_warn": False},
            )

        dc_removed = get_stereo_channels_dc_removed(track)
        if dc_removed is None:
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.CLEAN,
                summary="mono track",
                data={"mono_loss_db": None, "mono_warn": False},
            )

        l, r, _step = dc_removed
        stereo_rms_lin = float(np.sqrt(np.mean(0.5 * (l ** 2 + r ** 2))))
        mono = 0.5 * (l + r)
        mono_rms_lin = float(np.sqrt(np.mean(mono ** 2)))

        mono_loss_db = None
        mono_warn = False

        if stereo_rms_lin > 1e-12:
            if mono_rms_lin <= 1e-12:
                mono_loss_db = float("inf")
                mono_warn = True
            else:
                mono_loss_db = float(20.0 * np.log10(stereo_rms_lin / mono_rms_lin))
                mono_warn = mono_loss_db > float(self.mono_loss_warn_db)

        if mono_warn:
            if mono_loss_db is not None and np.isfinite(mono_loss_db):
                summary = f"mono loss {mono_loss_db:.1f} dB (> {self.mono_loss_warn_db:g} dB)"
            else:
                summary = f"mono loss inf dB (> {self.mono_loss_warn_db:g} dB)"
            return DetectorResult(
                detector_id=self.id,
                severity=Severity.INFO,
                summary=summary,
                data={"mono_loss_db": mono_loss_db, "mono_warn": True},
            )

        return DetectorResult(
            detector_id=self.id,
            severity=Severity.CLEAN,
            summary="mono fold-down OK",
            data={"mono_loss_db": mono_loss_db, "mono_warn": False},
        )
