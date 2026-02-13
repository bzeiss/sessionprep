"""DAWproject file-based DAW processor."""

from __future__ import annotations

from typing import Any

from ..daw_processor import DawProcessor
from ..models import DawCommand, DawCommandResult, SessionContext


class DawProjectDawProcessor(DawProcessor):
    """DAW processor that writes .dawproject files.

    DAWproject is an open interchange format for DAW sessions.
    This processor generates .dawproject files from the session state
    rather than communicating with a running DAW instance.
    """

    id = "dawproject"
    name = "DAWproject"

    def configure(self, config: dict[str, Any]) -> None:
        super().configure(config)

    def check_connectivity(self) -> tuple[bool, str]:
        return False, "DAWproject export not yet implemented."

    def fetch(self, session: SessionContext) -> SessionContext:
        return session

    def transfer(self, session: SessionContext) -> list[DawCommandResult]:
        return []

    def sync(self, session: SessionContext) -> list[DawCommandResult]:
        return []

    def execute_commands(
        self, session: SessionContext, commands: list[DawCommand],
    ) -> list[DawCommandResult]:
        return []
