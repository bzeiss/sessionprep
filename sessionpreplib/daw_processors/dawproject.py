"""DAWproject file-based DAW processor."""

from __future__ import annotations

import os
import zipfile
from typing import Any

from ..daw_processor import DawProcessor
from ..models import DawCommand, DawCommandResult, SessionContext


class DawProjectDawProcessor(DawProcessor):
    """DAW processor that writes .dawproject files.

    DAWproject is an open interchange format for DAW sessions.
    This processor generates .dawproject files from the session state
    rather than communicating with a running DAW instance.

    Each configured template becomes a separate instance with its own
    ``id`` and ``name``, created via :meth:`create_instances`.
    """

    id = "dawproject"
    name = "DAWproject"
    fader_ceiling_db: float = 24.0

    def __init__(
        self,
        *,
        instance_index: int | None = None,
        template_name: str = "",
        template_path: str = "",
        template_fader_ceiling_db: float = 24.0,
    ):
        self._instance_index = instance_index
        self._template_name = template_name
        self._template_path = template_path
        if instance_index is not None:
            self.id = f"dawproject_{instance_index}"
            self.name = f"DAWproject \u2013 {template_name}"
            self.fader_ceiling_db = template_fader_ceiling_db

    # ── Factory ────────────────────────────────────────────────────────

    @classmethod
    def create_instances(
        cls, flat_config: dict[str, Any],
    ) -> list[DawProjectDawProcessor]:
        """Create one processor instance per configured template.

        Reads ``dawproject_templates`` from *flat_config*.  Each entry
        is a dict with keys ``name``, ``template_path``, and optionally
        ``fader_ceiling_db``.  Returns an empty list when no templates
        are configured (the base "DAWproject" entry in the dropdown is
        suppressed in that case).
        """
        templates = flat_config.get("dawproject_templates", [])
        if not isinstance(templates, list):
            return []
        instances: list[DawProjectDawProcessor] = []
        for idx, tpl in enumerate(templates):
            if not isinstance(tpl, dict):
                continue
            name = tpl.get("name", "").strip()
            path = tpl.get("template_path", "").strip()
            ceiling = float(tpl.get("fader_ceiling_db", 24.0))
            if not name or not path:
                continue
            instances.append(cls(
                instance_index=idx,
                template_name=name,
                template_path=path,
                template_fader_ceiling_db=ceiling,
            ))
        return instances

    # ── Config ─────────────────────────────────────────────────────────

    def configure(self, config: dict[str, Any]) -> None:
        # For template instances the enabled toggle is governed by the
        # base dawproject_enabled key.
        saved = config.get(f"{self.id}_enabled")
        if saved is None:
            config[f"{self.id}_enabled"] = config.get("dawproject_enabled", True)
        super().configure(config)

    # ── Lifecycle ──────────────────────────────────────────────────────

    def check_connectivity(self) -> tuple[bool, str]:
        if not self._template_path:
            return False, "No template file configured."
        if not os.path.isfile(self._template_path):
            return False, f"Template not found: {self._template_path}"
        try:
            with zipfile.ZipFile(self._template_path, "r") as zf:
                if "project.xml" not in zf.namelist():
                    return False, "Template ZIP missing project.xml."
        except zipfile.BadZipFile:
            return False, "Template file is not a valid ZIP archive."
        return True, f"Template OK: {os.path.basename(self._template_path)}"

    def fetch(self, session: SessionContext) -> SessionContext:
        # Sprint 2: parse template structure and populate daw_state
        return session

    def transfer(self, session: SessionContext,
                 progress_cb=None) -> list[DawCommandResult]:
        # Sprint 2: write populated .dawproject to output folder
        return []

    def sync(self, session: SessionContext) -> list[DawCommandResult]:
        return []

    def execute_commands(
        self, session: SessionContext, commands: list[DawCommand],
    ) -> list[DawCommandResult]:
        return []
