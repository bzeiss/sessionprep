from .bimodal_normalize import BimodalNormalizeProcessor
from .mono_downmix import MonoDownmixProcessor


def default_processors():
    """Returns all built-in audio processors."""
    return [
        BimodalNormalizeProcessor(),
        MonoDownmixProcessor(),
    ]


__all__ = [
    "default_processors",
    "BimodalNormalizeProcessor",
    "MonoDownmixProcessor",
]
