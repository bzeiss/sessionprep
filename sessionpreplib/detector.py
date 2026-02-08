from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .config import ParamSpec
from .models import DetectorResult, TrackContext, SessionContext


class TrackDetector(ABC):
    """Operates on a single track."""
    id: str = ""
    name: str = ""
    depends_on: list[str] = []

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        """Return parameter specifications for this detector.

        Each :class:`ParamSpec` describes one configuration key the
        detector reads in :meth:`configure`.  Used for validation,
        config-file generation, and the preferences UI.
        """
        return []

    @classmethod
    @abstractmethod
    def html_help(cls) -> str:
        """Return HTML help text with Description, Results, and
        Interpretation sections.  Displayed as tooltip in the GUI."""
        ...

    def configure(self, config: dict[str, Any]) -> None:
        """
        Pull relevant keys from config dict. Called once at pipeline
        construction. Should raise ConfigError on invalid values.
        """
        pass

    @abstractmethod
    def analyze(self, track: TrackContext) -> DetectorResult:
        """Analyze one track. Return a DetectorResult."""
        ...

    def clean_message(self) -> str | None:
        """
        Message to display when ALL tracks pass this detector.
        Return None to suppress the clean line.
        """
        return None


class SessionDetector(ABC):
    """
    Operates across all tracks (e.g., format/length consistency).
    These detectors inherently compare across files and cannot produce
    a meaningful result from a single track.
    """
    id: str = ""
    name: str = ""

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        """Return parameter specifications for this detector."""
        return []

    @classmethod
    @abstractmethod
    def html_help(cls) -> str:
        """Return HTML help text with Description, Results, and
        Interpretation sections.  Displayed as tooltip in the GUI."""
        ...

    def configure(self, config: dict[str, Any]) -> None:
        pass

    @abstractmethod
    def analyze(self, session: SessionContext) -> list[DetectorResult]:
        """
        Analyze the full session. Returns a list of DetectorResults
        (typically one per affected track, plus optionally a session-level
        summary result).
        """
        ...

    def clean_message(self) -> str | None:
        return None
