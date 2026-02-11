from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .config import ParamSpec
from .models import DetectorResult, Severity, TrackContext, SessionContext

_REPORT_AS_MAP: dict[str, Severity] = {
    "problem": Severity.PROBLEM,
    "attention": Severity.ATTENTION,
    "info": Severity.INFO,
}

def _report_as_param(det_id: str) -> ParamSpec:
    """Build a detector-specific ``report_as`` ParamSpec."""
    return ParamSpec(
        key=f"{det_id}_report_as", type=str, default="default",
        choices=["default", "problem", "attention", "info", "skip"],
        label="Report as",
        description=(
            "Override how this detector's findings are categorized. "
            "'default' uses the detector's own severity. "
            "'skip' hides all results from reports and overlays."
        ),
    )


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
        return [_report_as_param(cls.id)]

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
        self._report_as: str = config.get(f"{self.id}_report_as", "default")

    @abstractmethod
    def analyze(self, track: TrackContext) -> DetectorResult:
        """Analyze one track. Return a DetectorResult."""
        ...

    def effective_severity(self, result: DetectorResult) -> Severity | None:
        """Return the display severity for *result*, applying ``report_as``.

        Returns ``None`` when ``report_as`` is ``"skip"`` (exclude from
        all presentation).  CLEAN results are never remapped.
        """
        if result.severity == Severity.CLEAN:
            return Severity.CLEAN
        report_as = getattr(self, "_report_as", "default")
        if report_as == "skip":
            return None
        if report_as == "default":
            return result.severity
        return _REPORT_AS_MAP.get(report_as, result.severity)

    def is_relevant(self, result: DetectorResult, track: TrackContext | None = None) -> bool:
        """Return whether this detector's result is meaningful for *track*.

        Override in subclasses to suppress results that are not applicable
        given the full track context (e.g. other detector or processor
        results).  Called by both ``render_html`` and the diagnostic
        summary builder.  Default: ``True``.
        """
        return True

    def render_html(self, result: DetectorResult, track: TrackContext | None = None) -> str:
        """Return an HTML table row for this detector's result.

        Override in subclasses for richer per-detector output.
        The default renders ``severity | id | summary``.
        Returns an empty string when :meth:`is_relevant` is ``False``
        or when ``report_as`` is ``"skip"``.

        Parameters
        ----------
        result : DetectorResult
        track : TrackContext | None
            The full track context (with detector and processor results)
            so that the detector can decide its own rendering relevance.
        """
        eff = self.effective_severity(result)
        if eff is None:
            return ""
        if not self.is_relevant(result, track):
            return ""
        sev = eff.value
        sev_color, sev_label = {
            "problem":     ("#ff4444", "PROBLEM"),
            "attention":   ("#ffaa00", "ATTENTION"),
            "information": ("#4499ff", "INFO"),
            "clean":       ("#44cc44", "OK"),
        }.get(sev, ("#4499ff", "INFO"))
        summary = (
            str(result.summary)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return (
            f'<tr>'
            f'<td width="90" style="background-color:{sev_color}; color:#000;'
            f' font-weight:bold; font-size:8pt; text-align:center;'
            f' padding:2px 8px;">'
            f'{sev_label}</td>'
            f'<td style="padding-left:6px; white-space:nowrap;">'
            f'<a href="detector:{self.id}" style="color:#dddddd;'
            f' text-decoration:none;"><b>{self.id}</b></a></td>'
            f'<td style="padding-left:6px; color:#888888;">'
            f'{summary}</td>'
            f'</tr>'
        )

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
        return [_report_as_param(cls.id)]

    @classmethod
    @abstractmethod
    def html_help(cls) -> str:
        """Return HTML help text with Description, Results, and
        Interpretation sections.  Displayed as tooltip in the GUI."""
        ...

    def configure(self, config: dict[str, Any]) -> None:
        self._report_as: str = config.get(f"{self.id}_report_as", "default")

    def effective_severity(self, result: DetectorResult) -> Severity | None:
        """Return the display severity for *result*, applying ``report_as``."""
        if result.severity == Severity.CLEAN:
            return Severity.CLEAN
        report_as = getattr(self, "_report_as", "default")
        if report_as == "skip":
            return None
        if report_as == "default":
            return result.severity
        return _REPORT_AS_MAP.get(report_as, result.severity)

    @abstractmethod
    def analyze(self, session: SessionContext) -> list[DetectorResult]:
        """
        Analyze the full session. Returns a list of DetectorResults
        (typically one per affected track, plus optionally a session-level
        summary result).
        """
        ...

    def render_html(self, result: DetectorResult, track: TrackContext | None = None) -> str:
        """Return an HTML table row for this detector's result."""
        eff = self.effective_severity(result)
        if eff is None:
            return ""
        sev = eff.value
        sev_color, sev_label = {
            "problem":     ("#ff4444", "PROBLEM"),
            "attention":   ("#ffaa00", "ATTENTION"),
            "information": ("#4499ff", "INFO"),
            "clean":       ("#44cc44", "OK"),
        }.get(sev, ("#4499ff", "INFO"))
        summary = (
            str(result.summary)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return (
            f'<tr>'
            f'<td width="90" style="background-color:{sev_color}; color:#000;'
            f' font-weight:bold; font-size:8pt; text-align:center;'
            f' padding:2px 8px;">'
            f'{sev_label}</td>'
            f'<td style="padding-left:6px; white-space:nowrap;">'
            f'<a href="detector:{self.id}" style="color:#dddddd;'
            f' text-decoration:none;"><b>{self.id}</b></a></td>'
            f'<td style="padding-left:6px; color:#888888;">'
            f'{summary}</td>'
            f'</tr>'
        )

    def clean_message(self) -> str | None:
        return None
