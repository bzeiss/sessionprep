from __future__ import annotations

from ..daw_processor import DawProcessor
from .protools import ProToolsDawProcessor
from .dawproject import DawProjectDawProcessor


def default_daw_processors() -> list[DawProcessor]:
    """Returns all built-in DAW processors."""
    return [
        ProToolsDawProcessor(),
        DawProjectDawProcessor(),
    ]
