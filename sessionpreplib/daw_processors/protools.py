"""Pro Tools DAW processor (PTSL-based)."""

from __future__ import annotations

import json
import math
import os
import time
from typing import Any

from ..config import ParamSpec
from ..daw_processor import DawProcessor
from ..models import DawCommand, DawCommandResult, SessionContext

try:
    from sessionprepgui.log import dbg
except ImportError:
    def dbg(msg: str) -> None:  # type: ignore[misc]
        pass


def _parse_argb(argb: str) -> tuple[int, int, int]:
    """Parse '#ffRRGGBB' ARGB hex string to (R, G, B) ints."""
    h = argb.lstrip("#")
    if len(h) == 8:
        return int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)
    if len(h) == 6:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 128, 128, 128


def _srgb_to_linear(c: float) -> float:
    """Convert sRGB channel [0..1] to linear."""
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _rgb_to_lab(r: int, g: int, b: int) -> tuple[float, float, float]:
    """Convert sRGB (0-255) to CIE L*a*b* (D65 illuminant)."""
    # sRGB → linear → XYZ (D65)
    rl = _srgb_to_linear(r / 255.0)
    gl = _srgb_to_linear(g / 255.0)
    bl = _srgb_to_linear(b / 255.0)
    x = (0.4124564 * rl + 0.3575761 * gl + 0.1804375 * bl) / 0.95047
    y = 0.2126729 * rl + 0.7151522 * gl + 0.0721750 * bl
    z = (0.0193339 * rl + 0.1191920 * gl + 0.9503041 * bl) / 1.08883

    def f(t: float) -> float:
        if t > 0.008856:
            return t ** (1.0 / 3.0)
        return 7.787 * t + 16.0 / 116.0

    L = 116.0 * f(y) - 16.0
    a = 500.0 * (f(x) - f(y))
    b_ = 200.0 * (f(y) - f(z))
    return L, a, b_


def _closest_palette_index(
    target_argb: str, palette: list[str],
) -> int | None:
    """Find the palette index whose colour is perceptually closest.

    Uses CIE L*a*b* Euclidean distance.  Returns ``None`` if palette
    is empty.
    """
    if not palette:
        return None
    tr, tg, tb = _parse_argb(target_argb)
    tL, ta, tb_ = _rgb_to_lab(tr, tg, tb)
    best_idx = 0
    best_dist = float("inf")
    for idx, entry in enumerate(palette):
        pr, pg, pb = _parse_argb(entry)
        pL, pa, pb2 = _rgb_to_lab(pr, pg, pb)
        dist = math.sqrt((tL - pL) ** 2 + (ta - pa) ** 2 + (tb_ - pb2) ** 2)
        if dist < best_dist:
            best_dist = dist
            best_idx = idx
    return best_idx


def _extract_clip_ids(resp: dict) -> list[str]:
    """Extract all clip IDs from a CId_ImportAudioToClipList response.

    Stereo files produce two clip IDs (L + R); mono files produce one.
    All must be passed to CId_SpotClipsByID for correct placement.

    Expected path:
      resp['file_list'][0]['destination_file_list'][0]['clip_id_list']
    """
    try:
        ids = resp['file_list'][0]['destination_file_list'][0][
            'clip_id_list']
        if not ids:
            raise ValueError("clip_id_list is empty")
        return list(ids)
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise RuntimeError(
            f"Failed to extract clip_ids from import response: {resp}"
        ) from e


def _extract_track_id(resp: dict) -> str:
    """Extract the track ID from a CId_CreateNewTracks response.

    Expected path: resp['created_track_ids'][0]
    """
    try:
        return resp['created_track_ids'][0]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(
            f"Failed to extract track_id from create response: {resp}"
        ) from e


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
        super().configure(config)
        self._company_name: str = config.get("protools_company_name", "github.com")
        self._application_name: str = config.get("protools_application_name", "sessionprep")
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

    def _resolve_group_color(
        self, group_name: str | None, session: SessionContext,
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

    @staticmethod
    def _run_command(engine, command_id, body: dict,
                     batch_job_id: str | None = None,
                     progress: int = 0) -> dict | None:
        """Send a PTSL command, optionally within a batch job.

        py-ptsl's ``Client._send_sync_request`` does not populate the
        ``versioned_request_header_json`` field required for batch job
        headers, so this helper constructs the ``Request`` protobuf
        directly and talks to ``raw_client``.

        Args:
            engine: A connected py-ptsl ``Engine`` instance.
            command_id: PTSL ``CommandId`` enum value.
            body: Request body dict (will be JSON-serialised).
            batch_job_id: If set, includes the batch job header.
            progress: Batch job progress percentage (0-100).

        Returns:
            Parsed response dict, or ``None`` for empty responses.

        Raises:
            RuntimeError: On ``Failed`` or unexpected response status.
        """
        from ptsl import PTSL_pb2 as pt
        from google.protobuf import json_format

        header_kwargs: dict[str, Any] = {
            "task_id": "",
            "session_id": engine.client.session_id,
            "command": command_id,
            "version": 2025,
            "version_minor": 10,
            "version_revision": 0,
        }
        if batch_job_id is not None:
            header_kwargs["versioned_request_header_json"] = json.dumps(
                {"batch_job_header": {"id": batch_job_id,
                                      "progress": progress}})

        request = pt.Request(
            header=pt.RequestHeader(**header_kwargs),
            request_body_json=json.dumps(body),
        )
        response = engine.client.raw_client.SendGrpcRequest(request)

        if response.header.status == pt.Failed:
            err_json = response.response_error_json
            try:
                err_obj = json_format.Parse(err_json, pt.ResponseError())
                errors = list(err_obj.errors)
                if errors:
                    e = errors[0]
                    msg = (f"ErrType {e.command_error_type}: "
                           f"{pt.CommandErrorType.Name(e.command_error_type)}"
                           f" ({e.command_error_message})")
                else:
                    msg = err_json
            except Exception:
                msg = err_json
            raise RuntimeError(msg)

        if response.header.status == pt.Completed:
            if len(response.response_body_json) > 0:
                return json.loads(response.response_body_json)
            return None

        status_name = pt.TaskStatus.Name(response.header.status)
        raise RuntimeError(
            f"Unexpected response status {response.header.status} "
            f"({status_name})")

    def transfer(self, session: SessionContext,
                 progress_cb=None) -> list[DawCommandResult]:
        """Import assigned tracks into Pro Tools folders and colorize.

        Uses a PTSL batch job to wrap all operations, providing a
        modal progress dialog in Pro Tools and preventing user
        interaction during the transfer.

        The transfer is structured in phases:
          0. Setup (palette, session path) — before batch job
          1. Create batch job
          2. Batch import all audio files in one call
          3. Per-track: create track + spot clip
          4. Batch colorize by group
          5. Complete batch job

        Args:
            session: The current session context.
            progress_cb: Optional callable(current, total, message) for
                progress reporting.

        Returns:
            List of DawCommandResult for each operation attempted.
        """
        dbg("transfer() called")
        try:
            from ptsl import PTSL_pb2 as pt
        except ImportError:
            dbg("py-ptsl not installed")
            return [DawCommandResult(
                command=DawCommand("transfer", "", {}),
                success=False, error="py-ptsl package not installed",
            )]

        pt_state = session.daw_state.get(self.id, {})
        assignments: dict[str, str] = pt_state.get("assignments", {})
        folders = pt_state.get("folders", [])
        track_order = pt_state.get("track_order", {})
        dbg(f"assignments={len(assignments)}, "
            f"folders={len(folders)}, track_order={len(track_order)}")
        if not assignments:
            dbg("No assignments, returning early")
            return []

        # Build lookups
        folder_map = {f["pt_id"]: f for f in folders}
        track_map = {t.filename: t for t in session.tracks}

        # Build ordered work list: [(filename, folder_id), ...]
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
        dbg(f"work list: {total} items")
        results: list[DawCommandResult] = []
        engine = None
        delay = self._command_delay
        batch_job_id: str | None = None

        try:
            dbg("Opening engine...")
            engine = self._open_engine()
            dbg("Engine opened")

            # ── Phase 0: Setup (before batch job) ────────────────

            # Fetch PT color palette for matching
            pt_palette: list[str] = []
            try:
                resp = self._run_command(
                    engine, pt.CommandId.CId_GetColorPalette,
                    {"color_palette_target": "CPTarget_Tracks"})
                pt_palette = (resp or {}).get("color_list", [])
            except Exception:
                pass  # colorization will be skipped if palette unavailable

            # Pre-compute group → palette index
            group_palette_idx: dict[str, int] = {}
            if pt_palette:
                for track in session.tracks:
                    if track.group and track.group not in group_palette_idx:
                        argb = self._resolve_group_color(
                            track.group, session)
                        if argb:
                            idx = _closest_palette_index(argb, pt_palette)
                            if idx is not None:
                                group_palette_idx[track.group] = idx

            # Resolve session "Audio Files" folder for copy/convert
            session_ptx = engine.session_path()
            audio_files_dir = os.path.join(
                os.path.dirname(session_ptx), "Audio Files")
            dbg(f"Phase 0 done: palette={len(pt_palette)}, "
                f"audio_dir={audio_files_dir}")

            # Validate work items and collect filepaths for batch import
            use_processed = session.config.get("_use_processed", False)
            # valid_work: [(fname, fid, filepath, track_stem, track_format, tc)]
            valid_work: list[tuple[str, str, str, str, str, Any]] = []
            for fname, fid in work:
                folder = folder_map.get(fid)
                if not folder:
                    results.append(DawCommandResult(
                        command=DawCommand("import_to_clip_list", fname,
                                           {"folder_id": fid}),
                        success=False, error=f"Folder {fid} not found"))
                    continue
                tc = track_map.get(fname)
                if not tc:
                    results.append(DawCommandResult(
                        command=DawCommand("import_to_clip_list", fname,
                                           {"folder_name": folder["name"]}),
                        success=False,
                        error=f"Track {fname} not in session"))
                    continue
                if (use_processed and tc.processed_filepath
                        and os.path.isfile(tc.processed_filepath)):
                    filepath = os.path.abspath(tc.processed_filepath)
                else:
                    filepath = os.path.abspath(tc.filepath)
                track_stem = os.path.splitext(fname)[0]
                track_format = (
                    "TF_Mono" if tc.channels == 1 else "TF_Stereo")
                valid_work.append(
                    (fname, fid, filepath, track_stem, track_format, tc))

            if not valid_work:
                dbg("No valid work items, returning early")
                return results

            total = len(valid_work)
            dbg(f"{total} valid work items")

            # ── Phase 1: Create batch job ────────────────────────

            dbg("Phase 1: Creating batch job...")
            try:
                bj_resp = self._run_command(
                    engine, pt.CommandId.CId_CreateBatchJob,
                    {"job": {
                        "name": "SessionPrep Transfer",
                        "description": f"Importing {total} tracks",
                        "timeout": 30000,
                        "is_cancelable": True,
                        "cancel_on_failure": False,
                    }})
                batch_job_id = (bj_resp or {}).get("id")
                dbg(f"Batch job created: {batch_job_id}")
            except Exception as exc:
                dbg(f"Batch job creation failed: {exc}")
                batch_job_id = None  # fall back to non-batch mode

            # ── Phase 2: Batch import all files ──────────────────

            if progress_cb:
                progress_cb(0, total, "Importing audio to clip list…")

            all_filepaths = [fp for _, _, fp, _, _, _ in valid_work]
            clip_cmd = DawCommand(
                "batch_import_to_clip_list", "",
                {"file_count": len(all_filepaths),
                 "destination": audio_files_dir})

            # filepath → list[clip_id]
            clip_id_map: dict[str, list[str]] = {}
            import_failures: set[str] = set()
            dbg(f"Phase 2: Batch importing {len(all_filepaths)} files...")
            for fp in all_filepaths:
                dbg(f"  file: {fp}  exists={os.path.isfile(fp)}")
            dbg(f"  dest: {audio_files_dir}  "
                f"exists={os.path.isdir(audio_files_dir)}")
            import_body: dict[str, Any] = {
                "file_list": all_filepaths,
            }
            dbg(f"  request body: {import_body}")
            try:
                import_resp = self._run_command(
                    engine, pt.CommandId.CId_ImportAudioToClipList,
                    import_body,
                    batch_job_id=batch_job_id, progress=5)
                dbg(f"Import response: {import_resp}")
                time.sleep(delay)

                # Map response entries back by original_input_path
                if import_resp:
                    for entry in import_resp.get("file_list", []):
                        orig = entry.get("original_input_path", "")
                        dest_list = entry.get("destination_file_list", [])
                        if dest_list:
                            ids = dest_list[0].get("clip_id_list", [])
                            if ids:
                                # Normalize path case — PT returns
                                # lowercase drive letters on Windows
                                clip_id_map[os.path.normcase(orig)] = \
                                    list(ids)

                    for fail in import_resp.get("failure_list", []):
                        fail_path = fail.get("original_input_path", "")
                        import_failures.add(os.path.normcase(fail_path))

                dbg(f"clip_id_map: {len(clip_id_map)} entries, "
                    f"failures: {len(import_failures)}")
                results.append(DawCommandResult(
                    command=clip_cmd, success=True))
            except Exception as e:
                dbg(f"Batch import FAILED: {e}")
                results.append(DawCommandResult(
                    command=clip_cmd, success=False, error=str(e)))
                # Cannot continue without clip IDs
                if batch_job_id:
                    try:
                        self._run_command(
                            engine, pt.CommandId.CId_CancelBatchJob,
                            {"id": batch_job_id})
                    except Exception:
                        pass
                    batch_job_id = None
                session.daw_command_log.extend(results)
                return results

            # ── Phase 3: Per-track create + spot ─────────────────

            # Collect created track stems by color_index for batch colorize
            color_groups: dict[int, list[str]] = {}

            for step, (fname, fid, filepath, track_stem, track_format,
                       tc) in enumerate(valid_work):
                folder = folder_map[fid]
                folder_name = folder["name"]

                # Progress: 10→90 spread across tracks
                pct = 10 + int(80 * step / max(total, 1))
                if progress_cb:
                    progress_cb(step, total,
                                f"Creating {track_stem} → {folder_name}")

                # Check if import succeeded for this file
                clip_ids = clip_id_map.get(os.path.normcase(filepath))
                if not clip_ids or os.path.normcase(filepath) in import_failures:
                    results.append(DawCommandResult(
                        command=DawCommand("create_track", fname,
                                           {"track_name": track_stem}),
                        success=False,
                        error=f"Import failed for {fname}"))
                    continue

                dbg(f"Phase 3 [{step+1}/{total}]: "
                    f"create+spot {track_stem} -> {folder_name}")
                # --- Create new track inside target folder ---
                create_cmd = DawCommand(
                    "create_track", fname,
                    {"track_name": track_stem,
                     "folder_name": folder_name,
                     "format": track_format})
                try:
                    # NOTE: select_tracks_by_name may be redundant since
                    # CId_CreateNewTracks specifies insertion_point_track_name.
                    # Commented out for performance; uncomment if tracks
                    # don't land inside the target folder.
                    # engine.select_tracks_by_name([folder_name])
                    # time.sleep(delay)

                    create_resp = self._run_command(
                        engine, pt.CommandId.CId_CreateNewTracks,
                        {
                            "number_of_tracks": 1,
                            "track_name": track_stem,
                            "track_format": track_format,
                            "track_type": "TT_Audio",
                            "track_timebase": "TTB_Samples",
                            "insertion_point_track_name": folder_name,
                            "insertion_point_position": "TIPoint_Last",
                        },
                        batch_job_id=batch_job_id, progress=pct)
                    time.sleep(delay)
                    new_track_id = _extract_track_id(create_resp or {})
                    dbg(f"  Created track: {new_track_id}")
                    results.append(DawCommandResult(
                        command=create_cmd, success=True))
                except Exception as e:
                    dbg(f"  Create FAILED: {e}")
                    results.append(DawCommandResult(
                        command=create_cmd, success=False, error=str(e)))
                    continue

                # --- Spot clip on the new track at session start ---
                spot_cmd = DawCommand(
                    "spot_clip", fname,
                    {"clip_ids": clip_ids, "track_id": new_track_id})
                try:
                    self._run_command(
                        engine, pt.CommandId.CId_SpotClipsByID,
                        {
                            "src_clips": clip_ids,
                            "dst_track_id": new_track_id,
                            "dst_location_data": {
                                "location_type": "SLType_Start",
                                "location": {
                                    "location": "0",
                                    "time_type": "TLType_Samples",
                                },
                            },
                        },
                        batch_job_id=batch_job_id, progress=pct)
                    # No delay after spot — next command is a new create
                    dbg("  Spotted clip OK")
                    results.append(DawCommandResult(
                        command=spot_cmd, success=True))
                except Exception as e:
                    dbg(f"  Spot FAILED: {e}")
                    results.append(DawCommandResult(
                        command=spot_cmd, success=False, error=str(e)))
                    continue

                # Collect for batch colorization
                if tc.group in group_palette_idx:
                    cidx = group_palette_idx[tc.group]
                    color_groups.setdefault(cidx, []).append(track_stem)

            # ── Phase 4: Batch colorize by group ─────────────────
            dbg(f"Phase 4: Colorizing {len(color_groups)} groups")
            for color_idx, track_names in color_groups.items():
                color_cmd = DawCommand(
                    "set_track_color", "",
                    {"color_index": color_idx,
                     "track_names": track_names})
                try:
                    self._run_command(
                        engine, pt.CommandId.CId_SetTrackColor,
                        {"track_names": track_names,
                         "color_index": color_idx},
                        batch_job_id=batch_job_id, progress=95)
                    results.append(DawCommandResult(
                        command=color_cmd, success=True))
                except Exception as e:
                    results.append(DawCommandResult(
                        command=color_cmd, success=False, error=str(e)))

            # ── Phase 5: Complete batch job ──────────────────────

            dbg("Phase 5: Completing batch job...")
            if batch_job_id:
                try:
                    self._run_command(
                        engine, pt.CommandId.CId_CompleteBatchJob,
                        {"id": batch_job_id})
                    dbg("Batch job completed")
                except Exception as exc:
                    dbg(f"CompleteBatchJob failed: {exc}")
                batch_job_id = None

            # Store transfer snapshot for future sync()
            pt_state["last_transfer"] = {
                "assignments": dict(assignments),
                "track_order": {k: list(v)
                                for k, v in track_order.items()},
            }
            session.daw_command_log.extend(results)

        except Exception as e:
            dbg(f"UNCAUGHT EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            results.append(DawCommandResult(
                command=DawCommand("transfer", "", {}),
                success=False, error=str(e),
            ))
        finally:
            # Cancel batch job if still open (e.g. due to exception)
            if batch_job_id and engine is not None:
                try:
                    self._run_command(
                        engine, pt.CommandId.CId_CancelBatchJob,
                        {"id": batch_job_id})
                except Exception:
                    pass
            if engine is not None:
                try:
                    engine.close()
                except Exception:
                    pass

        dbg(f"transfer() done, {len(results)} results")
        return results

    def sync(self, session: SessionContext) -> list[DawCommandResult]:
        return []

    def execute_commands(
        self, session: SessionContext, commands: list[DawCommand],
    ) -> list[DawCommandResult]:
        return []
