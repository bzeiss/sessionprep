from .bimodal_normalize import BimodalNormalizeProcessor


def default_processors():
    """Returns all built-in audio processors."""
    return [
        BimodalNormalizeProcessor(),
    ]


__all__ = [
    "default_processors",
    "BimodalNormalizeProcessor",
]
