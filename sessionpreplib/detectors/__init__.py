from .silence import SilenceDetector
from .clipping import ClippingDetector
from .dc_offset import DCOffsetDetector
from .stereo_correlation import StereoCorrelationDetector
from .dual_mono import DualMonoDetector
from .mono_folddown import MonoFolddownDetector
from .one_sided_silence import OneSidedSilenceDetector
from .subsonic import SubsonicDetector
from .audio_classifier import AudioClassifierDetector
from .tail_exceedance import TailExceedanceDetector
from .format_consistency import FormatConsistencyDetector
from .length_consistency import LengthConsistencyDetector


def default_detectors():
    """Returns all built-in detectors in a reasonable default order."""
    return [
        SilenceDetector(),
        ClippingDetector(),
        DCOffsetDetector(),
        StereoCorrelationDetector(),
        DualMonoDetector(),
        MonoFolddownDetector(),
        OneSidedSilenceDetector(),
        SubsonicDetector(),
        AudioClassifierDetector(),
        TailExceedanceDetector(),
        FormatConsistencyDetector(),
        LengthConsistencyDetector(),
    ]


def detector_help_map() -> dict[str, str]:
    """Return a mapping of detector_id → html_help for all built-in detectors."""
    return {d.id: d.__class__.html_help() for d in default_detectors()}


__all__ = [
    "default_detectors",
    "SilenceDetector",
    "ClippingDetector",
    "DCOffsetDetector",
    "StereoCorrelationDetector",
    "DualMonoDetector",
    "MonoFolddownDetector",
    "OneSidedSilenceDetector",
    "SubsonicDetector",
    "AudioClassifierDetector",
    "TailExceedanceDetector",
    "FormatConsistencyDetector",
    "LengthConsistencyDetector",
]
