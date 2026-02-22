"""Pro Tools DAW processor (PTSL-based)."""

from __future__ import annotations

import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..config import ParamSpec
from ..daw_processor import DawProcessor
from ..models import DawCommand, DawCommandResult, SessionContext
from . import ptsl_helpers as ptslh

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
                        "id": track.id,
                        "name": track.name,
                        "folder_type": folder_type,
                        "index": track.index,
                        "parent_id": track.parent_folder_id or None,
                    })

            # Preserve existing assignments where folder IDs still match
            pt_state = session.daw_state.get(self.id, {})
            old_assignments: dict[str, str] = pt_state.get("assignments", {})
            valid_ids = {f["id"] for f in folders}
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

    def transfer(
        self,
        session: SessionContext,
        output_path: str,
        progress_cb=None,
    ) -> list[DawCommandResult]:
        """Import assigned tracks into Pro Tools folders and colorize.

        Uses a PTSL batch job to wrap all operations, providing a
        modal progress dialog in Pro Tools and preventing user
        interaction during the transfer.

        The transfer is structured in phases:
          0. Setup (palette, session path) — before batch job
          1. Create batch job
          2. Batch import all audio files in one call
          3. Per-track: create track + spot clip  (parallel, 6 workers)
          4. Batch colorize by group
          4.5. Set fader offsets (when using processed files)
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
            from ptsl import PTSL_pb2 as pt  # noqa: F401 – validates install
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
        folder_map = {f["id"]: f for f in folders}
        manifest_map = {
            e.entry_id: e for e in session.transfer_manifest}
        out_track_map = {
            t.filename: t for t in session.output_tracks}

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

            # ── Setup (before batch job) ─────────────────────────

            pt_palette = ptslh.get_color_palette(engine)

            # Pre-compute group → palette index
            group_palette_idx: dict[str, int] = {}
            if pt_palette:
                for entry in session.transfer_manifest:
                    if entry.group and entry.group not in group_palette_idx:
                        argb = self._resolve_group_color(
                            entry.group, session)
                        if argb:
                            idx = _closest_palette_index(argb, pt_palette)
                            if idx is not None:
                                group_palette_idx[entry.group] = idx

            audio_files_dir = ptslh.get_session_audio_dir(engine)
            dbg(f"Setup done: palette={len(pt_palette)}, "
                f"audio_dir={audio_files_dir}")

            # Validate work items and collect filepaths for batch import
            # valid_work: [(entry_id, fid, filepath, track_stem, track_format, out_tc)]
            valid_work: list[tuple[str, str, str, str, str, Any]] = []
            for eid, fid in work:
                folder = folder_map.get(fid)
                if not folder:
                    results.append(DawCommandResult(
                        command=DawCommand("import_to_clip_list", eid,
                                           {"folder_id": fid}),
                        success=False, error=f"Folder {fid} not found"))
                    continue
                entry = manifest_map.get(eid)
                if not entry:
                    results.append(DawCommandResult(
                        command=DawCommand("import_to_clip_list", eid,
                                           {"folder_name": folder["name"]}),
                        success=False,
                        error=f"Manifest entry {eid} not found"))
                    continue
                out_tc = out_track_map.get(entry.output_filename)
                audio_path = (
                    out_tc.processed_filepath or out_tc.filepath
                ) if out_tc else None
                if not out_tc or not audio_path:
                    results.append(DawCommandResult(
                        command=DawCommand("import_to_clip_list", eid,
                                           {"folder_name": folder["name"]}),
                        success=False,
                        error=f"Output track not found for {entry.output_filename}"))
                    continue
                filepath = os.path.abspath(audio_path)
                track_stem = os.path.splitext(entry.daw_track_name)[0]
                track_format = (
                    "TF_Mono" if out_tc.channels == 1 else "TF_Stereo")
                valid_work.append(
                    (eid, fid, filepath, track_stem, track_format, out_tc))

            if not valid_work:
                dbg("No valid work items, returning early")
                return results

            total = len(valid_work)
            dbg(f"{total} valid work items")

            # ── Create batch job ───────────────────────────────

            batch_job_id = ptslh.create_batch_job(
                engine, "SessionPrep Transfer",
                f"Importing {total} tracks")

            # ── Batch import all files ─────────────────────────

            if progress_cb:
                progress_cb(0, total, "Importing audio to clip list…")

            # Deduplicate: multiple manifest entries may share the same file
            all_filepaths = list(dict.fromkeys(
                fp for _, _, fp, _, _, _ in valid_work))
            clip_cmd = DawCommand(
                "batch_import_to_clip_list", "",
                {"file_count": len(all_filepaths),
                 "destination": audio_files_dir})

            # filepath → list[clip_id]
            clip_id_map: dict[str, list[str]] = {}
            import_failures: set[str] = set()
            dbg(f"Batch importing {len(all_filepaths)} files...")
            try:
                import_resp = ptslh.batch_import_audio(
                    engine, all_filepaths,
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
                    ptslh.cancel_batch_job(engine, batch_job_id)
                    batch_job_id = None
                session.daw_command_log.extend(results)
                return results

            # ── Per-track create + spot (parallel) ──────────────────

            # Collect created track stems by color_index for batch colorize
            color_groups: dict[int, list[str]] = {}
            # Collect (track_stem, track_id, tc) for fader setting
            created_tracks: list[tuple[str, str, Any]] = []

            # Filter out tracks whose import failed before submitting
            spot_work: list[tuple[int, str, str, str, str, str, Any, list[str]]] = []
            for step, (fname, fid, filepath, track_stem, track_format,
                       tc) in enumerate(valid_work):
                clip_ids = clip_id_map.get(os.path.normcase(filepath))
                if not clip_ids or os.path.normcase(filepath) in import_failures:
                    results.append(DawCommandResult(
                        command=DawCommand("create_track", fname,
                                           {"track_name": track_stem}),
                        success=False,
                        error=f"Import failed for {fname}"))
                    continue
                spot_work.append(
                    (step, fname, fid, filepath, track_stem,
                     track_format, tc, clip_ids))

            def _create_and_spot(
                item: tuple[int, str, str, str, str, str, Any, list[str]],
            ) -> tuple[
                list[DawCommandResult],
                tuple[str, str, Any] | None,
                tuple[int, str] | None,
            ]:
                """Create one track and spot its clip.  Thread-safe."""
                (step, fname, fid, filepath, track_stem,
                 track_format, tc, clip_ids) = item
                folder_name = folder_map[fid]["name"]
                pct = 10 + int(80 * step / max(total, 1))
                step_results: list[DawCommandResult] = []

                dbg(f"[{step+1}/{total}] create+spot "
                    f"{track_stem} -> {folder_name}")

                # --- Create new track inside target folder ---
                create_cmd = DawCommand(
                    "create_track", fname,
                    {"track_name": track_stem,
                     "folder_name": folder_name,
                     "format": track_format})
                try:
                    new_track_id = ptslh.create_track(
                        engine, track_stem, track_format,
                        folder_name=folder_name,
                        batch_job_id=batch_job_id, progress=pct)
                    dbg(f"  Created track: {new_track_id}")
                    step_results.append(DawCommandResult(
                        command=create_cmd, success=True))
                except Exception as e:
                    dbg(f"  Create FAILED: {e}")
                    step_results.append(DawCommandResult(
                        command=create_cmd, success=False,
                        error=str(e)))
                    return step_results, None, None

                # --- Spot clip on the new track at session start ---
                spot_cmd = DawCommand(
                    "spot_clip", fname,
                    {"clip_ids": clip_ids, "track_id": new_track_id})
                try:
                    ptslh.spot_clips(
                        engine, clip_ids, new_track_id,
                        batch_job_id=batch_job_id, progress=pct)
                    dbg("  Spotted clip OK")
                    step_results.append(DawCommandResult(
                        command=spot_cmd, success=True))
                except Exception as e:
                    dbg(f"  Spot FAILED: {e}")
                    step_results.append(DawCommandResult(
                        command=spot_cmd, success=False, error=str(e)))
                    return step_results, None, None

                track_info = (track_stem, new_track_id, tc)
                color_info: tuple[int, str] | None = None
                if tc.group in group_palette_idx:
                    color_info = (group_palette_idx[tc.group], track_stem)
                return step_results, track_info, color_info

            dbg(f"Submitting {len(spot_work)} create+spot tasks "
                f"to pool (max_workers=6)")
            completed = 0
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {
                    pool.submit(_create_and_spot, item): item
                    for item in spot_work
                }
                for fut in as_completed(futures):
                    step_results, track_info, color_info = fut.result()
                    results.extend(step_results)
                    if track_info:
                        created_tracks.append(track_info)
                    if color_info:
                        cidx, t_stem = color_info
                        color_groups.setdefault(cidx, []).append(t_stem)
                    completed += 1
                    if progress_cb:
                        progress_cb(
                            completed, len(spot_work),
                            f"Created {completed}/{len(spot_work)} tracks")

            # ── Batch colorize by group ────────────────────────

            dbg(f"Colorizing {len(color_groups)} groups")
            for color_idx, track_names in color_groups.items():
                color_cmd = DawCommand(
                    "set_track_color", "",
                    {"color_index": color_idx,
                     "track_names": track_names})
                try:
                    ptslh.colorize_tracks(
                        engine, track_names, color_idx,
                        batch_job_id=batch_job_id, progress=95)
                    results.append(DawCommandResult(
                        command=color_cmd, success=True))
                except Exception as e:
                    results.append(DawCommandResult(
                        command=color_cmd, success=False, error=str(e)))

            # ── Set fader offsets ──────────────────────────────────

            proc_id = "bimodal_normalize"
            bn_enabled = session.config.get(f"{proc_id}_enabled", True)
            if bn_enabled:
                fader_count = 0
                for t_stem, t_id, tc in created_tracks:
                    if proc_id in tc.processor_skip:
                        continue
                    pr = tc.processor_results.get(proc_id)
                    if not pr or pr.classification in ("Silent", "Skip"):
                        continue
                    fader_db = pr.data.get("fader_offset", 0.0)
                    if fader_db == 0.0:
                        continue
                    fader_cmd = DawCommand(
                        "set_fader", t_stem,
                        {"track_id": t_id, "value": fader_db})
                    try:
                        ptslh.set_track_volume(
                            engine, t_id, fader_db,
                            batch_job_id=batch_job_id, progress=97)
                        dbg(f"  Fader {t_stem}: {fader_db:+.1f} dB")
                        results.append(DawCommandResult(
                            command=fader_cmd, success=True))
                        fader_count += 1
                    except Exception as e:
                        dbg(f"  Fader {t_stem} FAILED: {e}")
                        results.append(DawCommandResult(
                            command=fader_cmd, success=False,
                            error=str(e)))
                dbg(f"Fader offsets set on {fader_count} tracks")

            # ── Complete batch job ──────────────────────────────

            if batch_job_id:
                ptslh.complete_batch_job(engine, batch_job_id)
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
                ptslh.cancel_batch_job(engine, batch_job_id)
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
