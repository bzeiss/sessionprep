"""Pro Tools DAW processor (PTSL-based)."""

from __future__ import annotations


import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..models import ParamSpec
from ..daw_processor import DawProcessor
from ..models import DawCommand, DawCommandResult, SessionContext
from . import ptsl_helpers as ptslh

import logging

log = logging.getLogger(__name__)


# Re-export color helpers from ptsl_helpers (private aliases for
# backward compatibility within this module).
_parse_argb = ptslh.parse_argb
_srgb_to_linear = ptslh.srgb_to_linear
_rgb_to_lab = ptslh.rgb_to_lab
_closest_palette_index = ptslh.closest_palette_index


class ProToolsDawProcessor(DawProcessor):
    """DAW processor for Avid Pro Tools via the PTSL scripting SDK.

    Communicates with Pro Tools over a gRPC connection specified by
    host and port.  The company_name and application_name are sent
    during the PTSL handshake to identify the client.
    """

    id = "protools"
    name = "Pro Tools"

    def __init__(
        self,
        *,
        instance_index: int | None = None,
        instance_group: str = "",
        instance_name: str = "",
    ):
        self._instance_index = instance_index
        self._instance_group = instance_group
        self._instance_name = instance_name
        if instance_index is not None:
            self.id = f"protools_{instance_index}"
            if instance_group:
                self.name = f"Pro Tools \u2013 {instance_group} / {instance_name}"
            else:
                self.name = f"Pro Tools \u2013 {instance_name}"

    @classmethod
    def create_instances(
        cls,
        flat_config: dict[str, Any],
    ) -> list[ProToolsDawProcessor]:
        """Create one processor instance per configured template.

        Reads ``protools_templates`` from *flat_config*.  Each entry
        is a dict with key ``name`` and ``group``. Returns an empty list when no templates
        are configured.
        """
        templates = flat_config.get("protools_templates", [])
        if not isinstance(templates, list):
            return []
        instances: list[ProToolsDawProcessor] = []
        for idx, tpl in enumerate(templates):
            if not isinstance(tpl, dict):
                continue
            group = tpl.get("group", "").strip()
            name = tpl.get("name", "").strip()
            if not name or not group:
                continue
            instances.append(
                cls(
                    instance_index=idx,
                    instance_group=group,
                    instance_name=name,
                )
            )
        return instances

    @classmethod
    def config_params(cls) -> list[ParamSpec]:
        return super().config_params() + [
            ParamSpec(
                key="protools_temp_dir",
                type=str,
                default="",
                label="Temporary project directory",
                description=(
                    "Directory where temporary Pro Tools projects are created "
                    "from the referenced templates. Leave empty to use the system temp directory."
                ),
                widget_hint="path_picker_folder",
            ),
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
            ParamSpec(
                key="protools_command_delay",
                type=float,
                default=0.5,
                label="Command Delay (s)",
                description=(
                    "Seconds to wait between Pro Tools commands "
                    "(folder select, import, etc.)."
                ),
                min=0.1,
                max=5.0,
            ),
        ]

    def configure(self, config: dict[str, Any]) -> None:
        saved = config.get(f"{self.id}_enabled")
        if saved is None:
            config[f"{self.id}_enabled"] = config.get("protools_enabled", True)
        super().configure(config)
        self._project_dir: str = config.get("protools_project_dir", "")
        self._temp_dir: str = config.get("protools_temp_dir", "")
        self._company_name: str = config.get("protools_company_name", "github.com")
        self._application_name: str = config.get(
            "protools_application_name", "sessionprep"
        )
        self._host: str = config.get("protools_host", "localhost")
        self._port: int = config.get("protools_port", 31416)
        self._command_delay: float = config.get("protools_command_delay", 0.5)

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

            from . import ptsl_helpers as ptslh

            if not ptslh.wait_for_host_ready(
                engine, timeout=25.0, sleep_time=self._command_delay
            ):
                self._connected = False
                return (
                    False,
                    "Connected, but Pro Tools is busy or not ready. Please bring its window to the front.",
                )

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

    def fetch(self, session: SessionContext, progress_cb=None) -> SessionContext:
        if not self._temp_dir:
            raise RuntimeError(
                "The 'Temporary project directory' is not configured in Preferences."
            )
        if not os.path.isdir(self._temp_dir):
            raise RuntimeError(
                f"The configured temporary project directory does not exist: {self._temp_dir}"
            )

        # 1. Resolve Path to template file
        import platform
        from pathlib import Path

        system = platform.system()
        template_dir = None
        if system == "Windows":
            template_dir = Path.home() / "Documents" / "Pro Tools" / "Session Templates"
        elif system == "Darwin":
            template_dir = Path.home() / "Documents" / "Pro Tools" / "Session Templates"

        template_file = None
        current_mtime = None
        if template_dir:
            # e.g SessionPrep / MiniTemplate.ptxt
            template_file = (
                template_dir / self._instance_group / f"{self._instance_name}.ptxt"
            )
            if template_file.is_file():
                current_mtime = template_file.stat().st_mtime

        # 2. Check Cache
        from sessionpreplib.config import get_app_dir
        import json

        cache_file = Path(get_app_dir()) / "pt_template_cache.json"
        cache_data = {}
        if cache_file.is_file():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
            except Exception:
                cache_data = {}

        cache_key = f"{self._instance_group}/{self._instance_name}"
        if current_mtime is not None and cache_key in cache_data:
            entry = cache_data[cache_key]
            if entry.get("mtime") == current_mtime:
                # Fast Cache Hit
                if progress_cb:
                    progress_cb(
                        100,
                        100,
                        f"Loaded structure for '{self._instance_name}' from cache.",
                    )

                folders = entry.get("folders", [])

                pt_state = session.daw_state.get(self.id, {})
                old_assignments: dict[str, str] = pt_state.get("assignments", {})
                valid_ids = {f["id"] for f in folders}
                assignments = {
                    fname: fid
                    for fname, fid in old_assignments.items()
                    if fid in valid_ids
                }

                session.daw_state[self.id] = {
                    "folders": folders,
                    "assignments": assignments,
                }
                return session

        try:
            from ptsl import Engine
            from ptsl import PTSL_pb2 as pt
        except ImportError:
            return session

        engine = None
        temp_session_name = None
        try:
            if progress_cb:
                progress_cb(10, 100, "Connecting to Pro Tools...")

            address = f"{self._host}:{self._port}"
            engine = Engine(
                company_name=self._company_name,
                application_name=self._application_name,
                address=address,
            )

            if progress_cb:
                progress_cb(15, 100, "Waiting for Pro Tools to become ready...")

            if not ptslh.wait_for_host_ready(
                engine, timeout=25.0, sleep_time=self._command_delay
            ):
                raise RuntimeError(
                    "Pro Tools is busy or not ready. Please bring its window to the front to wake it."
                )

            if ptslh.is_session_open(engine):
                raise RuntimeError("PRO_TOOLS_SESSION_OPEN")

            import uuid

            temp_session_name = f"SessionPrep_Temp_{uuid.uuid4().hex[:8]}"

            if progress_cb:
                progress_cb(
                    30,
                    100,
                    f"Creating temporary session from template '{self._instance_group} / {self._instance_name}'...",
                )

            # Create the temporary session from the template
            ptslh.create_session_from_template(
                engine,
                temp_session_name,
                self._temp_dir,
                self._instance_group,
                self._instance_name,
            )

            if progress_cb:
                progress_cb(70, 100, "Reading track folder structure...")

            all_tracks = engine.track_list()
            folders: list[dict[str, Any]] = []
            for track in all_tracks:
                if track.type in (pt.TrackType.RoutingFolder, pt.TrackType.BasicFolder):
                    folder_type = (
                        "routing"
                        if track.type == pt.TrackType.RoutingFolder
                        else "basic"
                    )
                    folders.append(
                        {
                            "id": track.id,
                            "name": track.name,
                            "folder_type": folder_type,
                            "index": track.index,
                            "parent_id": track.parent_folder_id or None,
                        }
                    )

            if progress_cb:
                progress_cb(90, 100, "Cleaning up temporary session...")

            # Preserve existing assignments where folder IDs still match
            pt_state = session.daw_state.get(self.id, {})
            old_assignments: dict[str, str] = pt_state.get("assignments", {})
            valid_ids = {f["id"] for f in folders}
            assignments = {
                fname: fid for fname, fid in old_assignments.items() if fid in valid_ids
            }

            session.daw_state[self.id] = {
                "folders": folders,
                "assignments": assignments,
            }

            # Write cache
            if current_mtime is not None:
                cache_data[cache_key] = {"mtime": current_mtime, "folders": folders}
                try:
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(cache_data, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    log.debug(f"Failed to write template cache: {e}")

        except Exception:
            raise
        finally:
            if engine is not None:
                if temp_session_name:
                    try:
                        ptslh.close_session(engine)
                    except Exception as e:
                        log.debug(f"Failed to close temp session: {e}")
                try:
                    engine.close()
                except Exception:
                    pass

            if temp_session_name:
                # Defensive deletion of the temporary session folder
                target_dir = os.path.join(self._temp_dir, temp_session_name)
                ptx_file = os.path.join(target_dir, f"{temp_session_name}.ptx")

                # Extreme safety checks to ensure we only delete what we created:
                # 1. Ensure target_dir actually exists
                # 2. Ensure target_dir is exactly a direct child of the configured temp dir
                # 3. Ensure target_dir contains our specific UUID .ptx file
                if (
                    os.path.isdir(target_dir)
                    and os.path.dirname(os.path.abspath(target_dir))
                    == os.path.abspath(self._temp_dir)
                    and os.path.isfile(ptx_file)
                ):
                    import shutil
                    import time

                    # Retry loop to handle delayed file locks on Windows from Pro Tools closing
                    for _ in range(10):  # Try for up to 5 seconds
                        try:
                            shutil.rmtree(target_dir, ignore_errors=True)
                            if not os.path.exists(target_dir):
                                break
                        except Exception:
                            pass
                        time.sleep(0.5)

            if progress_cb:
                progress_cb(100, 100, "Fetch complete")

        return session

    def _resolve_group_color(
        self,
        group_name: str | None,
        session: SessionContext,
    ) -> str | None:
        """Return the ARGB hex for *group_name*, or ``None``."""
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
                return c.get("argb")
        return None

    def _open_engine(self):
        """Create and return a connected PTSL Engine."""
        from ptsl import Engine

        address = f"{self._host}:{self._port}"
        return Engine(
            company_name=self._company_name,
            application_name=self._application_name,
            address=address,
        )

    def _get_optimal_session_specs(self, session: SessionContext) -> tuple[str, str]:
        """Determine most common sample rate and bit depth from output tracks.

        Returns (sample_rate_enum, bit_depth_enum).
        """
        from collections import Counter

        rates = [t.samplerate for t in session.output_tracks if t.samplerate > 0]
        # bitdepth is string, e.g. "PCM_24". Try to extract numeric part.
        depths = []
        for t in session.output_tracks:
            bd = str(t.bitdepth).upper()
            if "32" in bd:
                depths.append(32)
            elif "24" in bd:
                depths.append(24)
            elif "16" in bd:
                depths.append(16)

        # Default fallback
        common_rate = Counter(rates).most_common(1)[0][0] if rates else 48000
        common_depth = Counter(depths).most_common(1)[0][0] if depths else 24

        rate_map = {
            44100: "SR_44100",
            48000: "SR_48000",
            88200: "SR_88200",
            96000: "SR_96000",
            176400: "SR_176400",
            192000: "SR_192000",
        }
        depth_map = {16: "Bit16", 24: "Bit24", 32: "Bit32Float"}

        return (
            rate_map.get(common_rate, "SR_48000"),
            depth_map.get(common_depth, "Bit24"),
        )

    def transfer(
        self,
        session: SessionContext,
        output_path: str,
        progress_cb=None,
        close_when_done: bool = True,
    ) -> list[DawCommandResult]:
        """Create a new Pro Tools session from a template and import audio.

        The transfer is structured in phases:
          0. Connect and verify empty workspace
          1. Determine optimal specs and create new session
          2. Batch import all audio files in one call
          3. Per-track: create track + spot clip  (parallel, 6 workers)
          4. Batch colorize by group
          5. Set fader offsets
          6. Complete and save session

        Args:
            session: The current session context.
            output_path: Not used for Pro Tools (it uses internal prefs).
            progress_cb: Optional callable(current, total, message).

        Returns:
            List of DawCommandResult for each operation attempted.
        """
        log.debug("transfer() called")
        try:
            from ptsl import PTSL_pb2 as pt  # noqa: F401 – validates install
        except ImportError:
            log.debug("py-ptsl not installed")
            return [
                DawCommandResult(
                    command=DawCommand("transfer", "", {}),
                    success=False,
                    error="py-ptsl package not installed",
                )
            ]

        pt_state = session.daw_state.get(self.id, {})
        assignments: dict[str, str] = pt_state.get("assignments", {})
        folders = pt_state.get("folders", [])
        track_order = pt_state.get("track_order", {})

        if not assignments:
            log.debug("No assignments, returning early")
            return []

        # Build lookups
        folder_map = {f["id"]: f for f in folders}
        manifest_map = {e.entry_id: e for e in session.transfer_manifest}
        out_track_map = {t.filename: t for t in session.output_tracks}

        # Build ordered work list: [(entry_id, folder_id), ...]
        work: list[tuple[str, str]] = []
        seen: set[str] = set()
        for fid, ordered_names in track_order.items():
            for eid in ordered_names:
                if eid in assignments and assignments[eid] == fid:
                    work.append((eid, fid))
                    seen.add(eid)
        for eid, fid in sorted(assignments.items()):
            if eid not in seen:
                work.append((eid, fid))

        results: list[DawCommandResult] = []
        engine = None
        delay = self._command_delay
        batch_job_id: str | None = None

        try:
            if progress_cb:
                progress_cb(0, 100, "Connecting to Pro Tools...")
            engine = self._open_engine()

            if progress_cb:
                progress_cb(2, 100, "Waiting for Pro Tools to become ready...")

            if not ptslh.wait_for_host_ready(
                engine, timeout=25.0, sleep_time=self._command_delay
            ):
                raise RuntimeError(
                    "Pro Tools is busy or not ready. Please bring its window to the front to wake it."
                )

            # ── 0. Setup & Safety Checks ─────────────────────────

            if not self._project_dir:
                raise RuntimeError("Pro Tools 'Project directory' is not configured.")
            if not os.path.isdir(self._project_dir):
                raise RuntimeError(
                    f"Pro Tools 'Project directory' does not exist: {self._project_dir}"
                )
            if not session.project_name:
                raise RuntimeError("Project name is empty.")

            if ptslh.is_session_open(engine):
                raise RuntimeError("PRO_TOOLS_SESSION_OPEN")

            if progress_cb:
                progress_cb(5, 100, "Calculating audio specifications...")

            rate_enum, depth_enum = self._get_optimal_session_specs(session)

            # ── 1. Create New Session ────────────────────────────

            if progress_cb:
                progress_cb(10, 100, f"Creating session '{session.project_name}'...")

            try:
                ptslh.create_session_from_template(
                    engine,
                    session.project_name,
                    self._project_dir,
                    self._instance_group,
                    self._instance_name,
                    sample_rate=rate_enum,
                    bit_depth=depth_enum,
                )
                results.append(
                    DawCommandResult(
                        command=DawCommand("create_session", session.project_name, {}),
                        success=True,
                    )
                )
            except Exception as e:
                return [
                    DawCommandResult(
                        command=DawCommand("create_session", session.project_name, {}),
                        success=False,
                        error=str(e),
                    )
                ]

            # Re-fetch color palette from the new session
            pt_palette = ptslh.get_color_palette(engine)
            group_palette_idx: dict[str, int] = {}
            if pt_palette:
                for entry in session.transfer_manifest:
                    if entry.group and entry.group not in group_palette_idx:
                        argb = self._resolve_group_color(entry.group, session)
                        if argb:
                            idx = _closest_palette_index(argb, pt_palette)
                            if idx is not None:
                                group_palette_idx[entry.group] = idx

            audio_files_dir = ptslh.get_session_audio_dir(engine)

            # Validate work items and collect filepaths
            valid_work: list[tuple[str, str, str, str, str, Any]] = []
            for eid, fid in work:
                folder = folder_map.get(fid)
                if not folder:
                    continue
                entry = manifest_map.get(eid)
                if not entry:
                    continue
                out_tc = out_track_map.get(entry.output_filename)
                audio_path = (
                    (out_tc.processed_filepath or out_tc.filepath) if out_tc else None
                )
                if not out_tc or not audio_path:
                    continue

                filepath = os.path.abspath(audio_path)
                track_stem = os.path.splitext(entry.daw_track_name)[0]
                track_format = "TF_Mono" if out_tc.channels == 1 else "TF_Stereo"
                valid_work.append(
                    (eid, fid, filepath, track_stem, track_format, out_tc)
                )

            if not valid_work:
                log.debug("No valid work items")
                return results

            # ── 2. Batch Import ──────────────────────────────────

            batch_job_id = ptslh.create_batch_job(
                engine, "SessionPrep Create", f"Importing {len(valid_work)} tracks"
            )

            if progress_cb:
                progress_cb(20, 100, "Importing audio to clip list...")

            all_filepaths = list(dict.fromkeys(fp for _, _, fp, _, _, _ in valid_work))
            clip_id_map: dict[str, list[str]] = {}
            import_failures: set[str] = set()

            try:
                import_resp = ptslh.batch_import_audio(
                    engine, all_filepaths, batch_job_id=batch_job_id, progress=25
                )
                time.sleep(delay)

                if import_resp:
                    for entry in import_resp.get("file_list", []):
                        orig = entry.get("original_input_path", "")
                        dest_list = entry.get("destination_file_list", [])
                        if dest_list:
                            ids = dest_list[0].get("clip_id_list", [])
                            if ids:
                                clip_id_map[os.path.normcase(orig)] = list(ids)
                    for fail in import_resp.get("failure_list", []):
                        fail_path = fail.get("original_input_path", "")
                        import_failures.add(os.path.normcase(fail_path))

                results.append(
                    DawCommandResult(
                        command=DawCommand("batch_import", "", {}), success=True
                    )
                )
            except Exception as e:
                if batch_job_id:
                    ptslh.cancel_batch_job(engine, batch_job_id)
                return [
                    DawCommandResult(
                        command=DawCommand("batch_import", "", {}),
                        success=False,
                        error=str(e),
                    )
                ]

            # ── 3. Parallel Track Creation + Spot ────────────────

            color_groups: dict[int, list[str]] = {}
            created_tracks: list[tuple[str, str, Any]] = []
            spot_work = []
            for step, (_, fid, filepath_val, track_stem, track_format, tc) in enumerate(
                valid_work
            ):
                clip_ids = clip_id_map.get(os.path.normcase(filepath_val))
                if not clip_ids or os.path.normcase(filepath_val) in import_failures:
                    continue
                spot_work.append(
                    (step, fid, filepath_val, track_stem, track_format, tc, clip_ids)
                )

            def _create_and_spot(item):
                (
                    step_val,
                    fid_val,
                    _,
                    track_stem_val,
                    track_format_val,
                    tc_val,
                    clip_ids_val,
                ) = item
                folder_name = folder_map[fid_val]["name"]
                pct = 30 + int(50 * step_val / max(len(valid_work), 1))

                try:
                    tid = ptslh.create_track(
                        engine,
                        track_stem_val,
                        track_format_val,
                        folder_name=folder_name,
                        batch_job_id=batch_job_id,
                        progress=pct,
                    )
                    ptslh.spot_clips(
                        engine,
                        clip_ids_val,
                        tid,
                        batch_job_id=batch_job_id,
                        progress=pct,
                    )

                    cinfo = (
                        (group_palette_idx[tc_val.group], track_stem_val)
                        if tc_val.group in group_palette_idx
                        else None
                    )
                    return True, (track_stem_val, tid, tc_val), cinfo, None
                except Exception as ex:
                    return False, None, None, str(ex)

            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = [pool.submit(_create_and_spot, item) for item in spot_work]
                for i, fut in enumerate(as_completed(futures)):
                    ok, tinfo, cinfo, _ = fut.result()
                    if ok:
                        created_tracks.append(tinfo)
                        if cinfo:
                            color_groups.setdefault(cinfo[0], []).append(cinfo[1])
                    if progress_cb:
                        progress_cb(
                            30 + int(50 * i / len(spot_work)),
                            100,
                            f"Created {i + 1}/{len(spot_work)} tracks",
                        )

            # ── 4. Colorize ──────────────────────────────────────

            for cidx, names in color_groups.items():
                try:
                    ptslh.colorize_tracks(
                        engine, names, cidx, batch_job_id=batch_job_id, progress=90
                    )
                except Exception:
                    pass

            # ── 5. Faders ────────────────────────────────────────

            proc_id = "bimodal_normalize"
            bn_enabled = session.config.get(f"{proc_id}_enabled", True)
            if bn_enabled:
                for _, t_id, tc in created_tracks:
                    if proc_id in tc.processor_skip:
                        continue
                    pr = tc.processor_results.get(proc_id)
                    if not pr or pr.classification in ("Silent", "Skip"):
                        continue
                    fader_db = pr.data.get("fader_offset", 0.0)
                    log.debug(
                        f"Fader logic for {t_id}: classification={pr.classification}, fader_db={fader_db}"
                    )
                    if fader_db == 0.0:
                        continue
                    try:
                        ptslh.set_track_volume(
                            engine,
                            t_id,
                            fader_db,
                            batch_job_id=batch_job_id,
                            progress=95,
                        )
                    except Exception as e:
                        log.debug(f"Fader set failed for {t_id}: {e}")

            # ── 6. Save & Close ──────────────────────────────────

            if progress_cb:
                msg = "Saving and closing session..." if close_when_done else "Saving session..."
                progress_cb(98, 100, msg)

            if batch_job_id:
                ptslh.complete_batch_job(engine, batch_job_id)
                batch_job_id = None

            try:
                if close_when_done:
                    ptslh.close_session(engine, save_on_close=True, delay=delay)
                    results.append(
                        DawCommandResult(
                            command=DawCommand("close_session", "", {}), success=True
                        )
                    )
                else:
                    ptslh.save_session(engine)
                    results.append(
                        DawCommandResult(
                            command=DawCommand("save_session", "", {}), success=True
                        )
                    )
            except Exception as e:
                cmd_name = "close_session" if close_when_done else "save_session"
                results.append(
                    DawCommandResult(
                        command=DawCommand(cmd_name, "", {}),
                        success=False,
                        error=str(e),
                    )
                )

        except Exception as e:
            results.append(
                DawCommandResult(
                    command=DawCommand("create_project", "", {}),
                    success=False,
                    error=str(e),
                )
            )
        finally:
            if batch_job_id and engine:
                ptslh.cancel_batch_job(engine, batch_job_id)
            if engine:
                engine.close()

        if progress_cb:
            progress_cb(100, 100, "Project creation complete")
        return results

    def sync(self, session: SessionContext) -> list[DawCommandResult]:
        return []

    def execute_commands(
        self,
        session: SessionContext,
        commands: list[DawCommand],
    ) -> list[DawCommandResult]:
        return []
