"""DAWproject file-based DAW processor."""

from __future__ import annotations

import logging
import math
import os
import zipfile
from typing import Any

from ..daw_processor import DawProcessor
from ..models import DawCommand, DawCommandResult, SessionContext

log = logging.getLogger(__name__)


def _db_to_linear(db: float) -> float:
    """Convert decibels to linear gain (0 dB → 1.0)."""
    return math.pow(10.0, db / 20.0)


def _argb_to_rgb_hex(argb: str) -> str | None:
    """Convert an ARGB hex string (e.g. 'FF3399CC') to '#rrggbb'."""
    argb = argb.lstrip("#")
    if len(argb) == 8:
        return f"#{argb[2:]}"
    if len(argb) == 6:
        return f"#{argb}"
    return None


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
        try:
            from dawproject import (  # noqa: F401
                ContentType, DawProject, Referenceable,
            )
        except ImportError:
            raise RuntimeError(
                "dawproject package not installed. "
                "Install with: pip install dawproject")

        Referenceable.reset_id()
        project = DawProject.load_project(self._template_path)

        folders: list[dict[str, Any]] = []
        self._walk_structure(
            project.structure, folders, parent_id=None, counter=[0])

        # Preserve existing assignments where folder IDs still match
        dp_state = session.daw_state.get(self.id, {})
        old_assignments: dict[str, str] = dp_state.get("assignments", {})
        valid_ids = {f["id"] for f in folders}
        assignments = {
            fname: fid for fname, fid in old_assignments.items()
            if fid in valid_ids
        }

        session.daw_state[self.id] = {
            "folders": folders,
            "assignments": assignments,
        }
        return session

    def _walk_structure(
        self,
        tracks: list,
        folders: list[dict[str, Any]],
        parent_id: str | None,
        counter: list[int],
    ) -> None:
        """Recursively collect folder tracks from the project structure."""
        from dawproject import ContentType

        for track in tracks:
            ct_values = set()
            for ct in getattr(track, "content_type", []):
                if isinstance(ct, ContentType):
                    ct_values.add(ct)
                else:
                    try:
                        ct_values.add(ContentType(ct))
                    except ValueError:
                        pass

            if ContentType.TRACKS in ct_values:
                folder_type = "routing" if track.channel else "basic"
                folders.append({
                    "id": track.id,
                    "name": track.name or "(unnamed)",
                    "folder_type": folder_type,
                    "index": counter[0],
                    "parent_id": parent_id,
                })
                counter[0] += 1
                # Recurse into nested tracks
                self._walk_structure(
                    track.tracks, folders, parent_id=track.id,
                    counter=counter)

    def transfer(self, session: SessionContext,
                 progress_cb=None) -> list[DawCommandResult]:
        try:
            from dawproject import (
                Arrangement, Audio, Channel, Clips, ContentType,
                DawProject, FileReference, Lanes, MetaData,
                MixerRole, RealParameter, Referenceable, TimeUnit,
                Track, Unit, Utility,
            )
        except ImportError:
            return [DawCommandResult(
                command=DawCommand("transfer", "", {}),
                success=False,
                error="dawproject package not installed",
            )]

        dp_state = session.daw_state.get(self.id, {})
        assignments: dict[str, str] = dp_state.get("assignments", {})
        daw_folders = dp_state.get("folders", [])
        track_order = dp_state.get("track_order", {})

        if not assignments:
            return []

        results: list[DawCommandResult] = []

        # ── Determine output path ─────────────────────────────────
        source_dir = session.config.get("_source_dir", "")
        output_folder = session.config.get("_output_folder", "processed")
        if not source_dir:
            return [DawCommandResult(
                command=DawCommand("transfer", "", {}),
                success=False, error="No source directory set")]

        output_dir = os.path.join(source_dir, output_folder)
        os.makedirs(output_dir, exist_ok=True)

        safe_name = self._template_name or "dawproject"
        safe_name = "".join(
            c if c.isalnum() or c in " _-" else "_" for c in safe_name)
        output_path = os.path.join(output_dir, f"{safe_name}.dawproject")

        # ── Load template ─────────────────────────────────────────
        Referenceable.reset_id()
        try:
            project = DawProject.load_project(self._template_path)
        except Exception as e:
            return [DawCommandResult(
                command=DawCommand("load_template", "", {}),
                success=False, error=f"Failed to load template: {e}")]

        # Build folder ID → Track object lookup from the loaded project
        folder_track_map: dict[str, Any] = {}
        self._build_folder_map(project.structure, folder_track_map)

        # Build lookups
        folder_dict_map = {f["id"]: f for f in daw_folders}
        track_map = {t.filename: t for t in session.tracks}

        # Build ordered work list
        work: list[tuple[str, str]] = []
        seen: set[str] = set()
        for fid, ordered_names in track_order.items():
            for fname in ordered_names:
                if fname in assignments and assignments[fname] == fid:
                    work.append((fname, fid))
                    seen.add(fname)
        for fname, fid in sorted(assignments.items()):
            if fname not in seen:
                work.append((fname, fid))

        total = len(work)
        if progress_cb:
            progress_cb(0, total, "Building DAWproject\u2026")

        # ── Ensure arrangement exists ─────────────────────────────
        if project.arrangement is None:
            project.arrangement = Arrangement(
                lanes=Lanes(time_unit=TimeUnit.SECONDS))
        if project.arrangement.lanes is None:
            project.arrangement.lanes = Lanes(time_unit=TimeUnit.SECONDS)

        use_processed = session.config.get("_use_processed", False)

        # ── Create tracks and clips ───────────────────────────────
        for step, (fname, fid) in enumerate(work):
            folder_track = folder_track_map.get(fid)
            folder_dict = folder_dict_map.get(fid)
            tc = track_map.get(fname)

            if not folder_track or not tc:
                results.append(DawCommandResult(
                    command=DawCommand("add_track", fname,
                                       {"folder_id": fid}),
                    success=False,
                    error=f"Folder or track not found: {fid} / {fname}"))
                continue

            # Resolve audio file path
            if (use_processed and tc.processed_filepath
                    and os.path.isfile(tc.processed_filepath)):
                audio_path = os.path.abspath(tc.processed_filepath)
            else:
                audio_path = os.path.abspath(tc.filepath)

            # Compute fader volume (linear)
            fader_db = 0.0
            if tc.processor_results:
                pr = next(iter(tc.processor_results.values()), None)
                if pr and pr.data:
                    fader_db = pr.data.get("fader_offset", 0.0)
            volume_linear = _db_to_linear(fader_db)

            # Resolve group color → #rrggbb
            track_color = self._resolve_track_color(tc.group, session)

            # Create the track with channel
            track_name = os.path.splitext(fname)[0]
            new_track = Utility.create_track(
                name=track_name,
                content_types={ContentType.AUDIO},
                mixer_role=MixerRole.REGULAR,
                volume=volume_linear,
                pan=0.5,
            )

            # Set color
            if track_color:
                new_track.color = track_color

            # Route to folder's channel
            if folder_track.channel is not None:
                new_track.channel.destination = folder_track.channel

            # Add to folder's children
            folder_track.tracks.append(new_track)

            # Also add to project.structure top-level if not already
            # (some DAWs expect all tracks at the structure level)

            # Create audio clip in arrangement
            audio = Audio(
                time_unit=TimeUnit.SECONDS,
                file=FileReference(
                    path=audio_path.replace("\\", "/"),
                    external=True),
                sample_rate=tc.samplerate,
                channels=tc.channels,
                duration=tc.duration_sec,
            )
            clip = Utility.create_clip(
                content=audio, time=0.0, duration=tc.duration_sec)
            clips = Utility.create_clips(clip)

            # Create a Lanes entry for this track in the arrangement
            track_lane = Lanes(
                track=new_track,
                time_unit=TimeUnit.SECONDS,
                lanes=[clips],
            )
            project.arrangement.lanes.lanes.append(track_lane)

            results.append(DawCommandResult(
                command=DawCommand("add_track", fname,
                                   {"folder": folder_dict.get("name", ""),
                                    "fader_db": fader_db}),
                success=True))

            if progress_cb:
                progress_cb(step + 1, total,
                            f"Added {track_name} ({step + 1}/{total})")

        # ── Save ──────────────────────────────────────────────────
        try:
            metadata = DawProject.load_metadata(self._template_path)
        except Exception:
            metadata = MetaData()

        try:
            DawProject.save(project, metadata, {}, output_path)
            results.append(DawCommandResult(
                command=DawCommand("save_project", output_path, {}),
                success=True))
            log.info("DAWproject saved to %s", output_path)
        except Exception as e:
            results.append(DawCommandResult(
                command=DawCommand("save_project", output_path, {}),
                success=False, error=f"Failed to save: {e}"))

        session.daw_command_log.extend(results)
        return results

    def _build_folder_map(
        self, tracks: list, folder_map: dict[str, Any],
    ) -> None:
        """Recursively build a mapping of folder ID → Track object."""
        from dawproject import ContentType

        for track in tracks:
            ct_values = set()
            for ct in getattr(track, "content_type", []):
                if isinstance(ct, ContentType):
                    ct_values.add(ct)
                else:
                    try:
                        ct_values.add(ContentType(ct))
                    except ValueError:
                        pass
            if ContentType.TRACKS in ct_values:
                folder_map[track.id] = track
                self._build_folder_map(track.tracks, folder_map)

    def _resolve_track_color(
        self, group_name: str | None, session: SessionContext,
    ) -> str | None:
        """Return ``#rrggbb`` for the track's group color, or ``None``."""
        if not group_name:
            return None
        groups = session.config.get("gui", {}).get("groups", [])
        color_name: str | None = None
        for g in groups:
            if g.get("name") == group_name:
                color_name = g.get("color")
                break
        if not color_name:
            return None
        colors = session.config.get("gui", {}).get("colors", [])
        for c in colors:
            if c.get("name") == color_name:
                argb = c.get("argb")
                if argb:
                    return _argb_to_rgb_hex(argb)
        return None

    def sync(self, session: SessionContext) -> list[DawCommandResult]:
        return []

    def execute_commands(
        self, session: SessionContext, commands: list[DawCommand],
    ) -> list[DawCommandResult]:
        return []
