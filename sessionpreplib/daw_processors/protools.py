"""Pro Tools DAW processor (PTSL-based)."""

from __future__ import annotations

from typing import Any

from ..config import ParamSpec
from ..daw_processor import DawProcessor
from ..models import DawCommand, DawCommandResult, SessionContext


class ProToolsDawProcessor(DawProcessor):
    """DAW processor for Avid Pro Tools via the PTSL scripting SDK.

    Communicates with Pro Tools over a gRPC connection specified by
    host and port.  The company_name and application_name are sent
    during the PTSL handshake to identify the client.
    """

    id = "protools"
    name = "Pro Tools"

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        return super().config_params() + [
            ParamSpec(
                key="protools_company_name",
                type=str,
                default="github.com",
                label="Company Name",
                description="Company name sent during the PTSL handshake.",
            ),
            ParamSpec(
                key="protools_application_name",
                type=str,
                default="sessionprep",
                label="Application Name",
                description="Application name sent during the PTSL handshake.",
            ),
            ParamSpec(
                key="protools_host",
                type=str,
                default="localhost",
                label="Host",
                description="Hostname or IP address of the Pro Tools PTSL server.",
            ),
            ParamSpec(
                key="protools_port",
                type=int,
                default=31416,
                label="Port",
                description="Port number of the Pro Tools PTSL server.",
                min=1,
                max=65535,
            ),
        ]

    def configure(self, config: dict[str, Any]) -> None:
        super().configure(config)
        self._company_name: str = config.get("protools_company_name", "github.com")
        self._application_name: str = config.get("protools_application_name", "sessionprep")
        self._host: str = config.get("protools_host", "localhost")
        self._port: int = config.get("protools_port", 31416)

    def check_connectivity(self) -> tuple[bool, str]:
        try:
            from ptsl import Engine
        except ImportError:
            self._connected = False
            return False, "py-ptsl package not installed"

        engine = None
        try:
            address = f"{self._host}:{self._port}"
            engine = Engine(
                company_name=self._company_name,
                application_name=self._application_name,
                address=address,
            )
            version = engine.ptsl_version()
            if version < 2025:
                self._connected = False
                return False, "Protocol 2025 or newer required"
            self._connected = True
            return True, f"Protocol: {version}"
        except Exception as e:
            self._connected = False
            return False, str(e)
        finally:
            if engine is not None:
                try:
                    engine.close()
                except Exception:
                    pass

    def fetch(self, session: SessionContext) -> SessionContext:
        try:
            from ptsl import Engine
            from ptsl import PTSL_pb2 as pt
        except ImportError:
            return session

        engine = None
        try:
            address = f"{self._host}:{self._port}"
            engine = Engine(
                company_name=self._company_name,
                application_name=self._application_name,
                address=address,
            )
            all_tracks = engine.track_list()
            folders: list[dict[str, Any]] = []
            for track in all_tracks:
                if track.type in (pt.TrackType.RoutingFolder, pt.TrackType.BasicFolder):
                    folder_type = (
                        "routing" if track.type == pt.TrackType.RoutingFolder
                        else "basic"
                    )
                    folders.append({
                        "pt_id": track.id,
                        "name": track.name,
                        "folder_type": folder_type,
                        "index": track.index,
                        "parent_id": track.parent_folder_id or None,
                    })

            # Preserve existing assignments where folder IDs still match
            pt_state = session.daw_state.get(self.id, {})
            old_assignments: dict[str, str] = pt_state.get("assignments", {})
            valid_ids = {f["pt_id"] for f in folders}
            assignments = {
                fname: fid for fname, fid in old_assignments.items()
                if fid in valid_ids
            }

            session.daw_state[self.id] = {
                "folders": folders,
                "assignments": assignments,
            }
        except Exception:
            raise
        finally:
            if engine is not None:
                try:
                    engine.close()
                except Exception:
                    pass
        return session

    def transfer(self, session: SessionContext) -> list[DawCommandResult]:
        return []

    def sync(self, session: SessionContext) -> list[DawCommandResult]:
        return []

    def execute_commands(
        self, session: SessionContext, commands: list[DawCommand],
    ) -> list[DawCommandResult]:
        return []
