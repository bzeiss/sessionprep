"""Abstract base class for DAW integration processors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .config import ParamSpec
from .models import DawCommand, DawCommandResult, SessionContext


class DawProcessor(ABC):
    """Abstract base for DAW integration processors.

    Each concrete subclass handles one DAW (e.g. ProToolsProcessor,
    DAWProjectProcessor).  The processor owns all communication logic
    internally — the ABC only defines the lifecycle contract.

    Execution model (Option B):
        transfer()/sync() build a list of DawCommand objects internally,
        execute them via processor-private dispatch, and return
        DawCommandResult objects.  Commands are plain data; the processor
        is the executor.

    Lifecycle (called by GUI/CLI, not Pipeline):
        1. configure(config)        — read ParamSpec values
        2. check_connectivity()     — verify DAW is reachable
        3. fetch(session)           — pull DAW state into session.daw_state
        4. transfer(session)        — initial full push to DAW
        5. sync(session)            — incremental delta push

    Ad-hoc commands (called by GUI tools, outside lifecycle):
        6. execute_commands(session, commands) — run externally-built commands
    """

    id: str = ""
    name: str = ""
    fader_ceiling_db: float = 12.0

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        """Base returns the enabled toggle. Subclasses call super() + [...]."""
        return [
            ParamSpec(
                key=f"{cls.id}_enabled",
                type=bool,
                default=True,
                label="Enabled",
                description=(
                    "Whether this DAW processor is available for selection "
                    "in the toolbar. Disable if you never use this DAW."
                ),
            ),
        ]

    def configure(self, config: dict[str, Any]) -> None:
        """Read config values. Subclasses should call super().configure(config)."""
        self._enabled: bool = config.get(f"{self.id}_enabled", True)
        self._connected: bool = False

    @property
    def enabled(self) -> bool:
        """Whether this processor is available for selection."""
        return self._enabled

    @property
    def connected(self) -> bool:
        """Whether the last connectivity check succeeded."""
        return self._connected

    @abstractmethod
    def check_connectivity(self) -> tuple[bool, str]:
        """Test whether the DAW is reachable.

        Returns (ok, message).  For socket-based DAWs (Pro Tools PTSL)
        this checks the connection.  For file-based DAWs (DAWProject)
        this might validate the output path.
        """
        ...

    @abstractmethod
    def fetch(self, session: SessionContext) -> SessionContext:
        """Pull external state from the DAW into the session.

        Populates session.daw_state[self.id] with fetched data
        (routing folders, track list, colors, etc.).  The GUI can
        then display this data in the Session Setup panel.
        """
        ...

    def resolve_output_path(
        self,
        session: SessionContext,
        parent_widget=None,
    ) -> str | None:
        """Resolve the output path for a transfer, optionally prompting the user.

        Called by the GUI before starting a transfer worker.

        Return values
        -------------
        ``None``
            User cancelled — the GUI should abort.
        ``""``
            No file path needed (e.g. gRPC-based processors like Pro Tools).
        non-empty str
            Absolute path to write the output file to.

        The default implementation returns ``""`` (no path, no dialog).
        File-based processors (e.g. DAWproject) override this to compute a
        default path and show a save-file dialog.
        """
        return ""

    @abstractmethod
    def transfer(
        self,
        session: SessionContext,
        output_path: str,
        progress_cb=None,
    ) -> list[DawCommandResult]:
        """Initial full push of session data to the DAW.

        Parameters
        ----------
        session:
            The current session context.
        output_path:
            Explicit destination path for the output file (e.g.
            ``/path/to/session.dawproject``).  The caller is responsible
            for choosing this path — supervised callers show a file dialog;
            unsupervised (batch) callers derive it from track/session data.
        progress_cb:
            Optional ``(current, total, message)`` callback.

        Returns the list of DawCommandResult for this batch.
        """
        ...

    @abstractmethod
    def sync(self, session: SessionContext) -> list[DawCommandResult]:
        """Incremental update — send only what changed since last transfer.

        Compares current session state against the snapshot stored by
        transfer() (in session.daw_state[self.id]) and sends only the
        deltas.  Same internal dispatch as transfer().
        """
        ...

    @abstractmethod
    def execute_commands(
        self, session: SessionContext, commands: list[DawCommand],
    ) -> list[DawCommandResult]:
        """Execute ad-hoc commands built by the GUI/CLI.

        Same internal dispatch as transfer()/sync(), but the commands
        are constructed externally — e.g. a GUI color picker builds
        DawCommand("set_color", "Kick", {"color_name": "Red"}) and
        hands it to the processor.

        Results are appended to session.daw_command_log.
        """
        ...
