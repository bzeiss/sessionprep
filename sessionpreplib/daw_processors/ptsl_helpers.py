"""Generic, reusable PTSL helper functions.

Stateless functions that wrap individual Pro Tools Scripting Library
(PTSL) commands.  They operate on a connected py-ptsl ``Engine`` and
plain Python data — no knowledge of SessionContext, DawProcessor, or
transfer phases.
"""

from __future__ import annotations

import json
import os
from typing import Any

try:
    from sessionprepgui.log import dbg
except ImportError:
    def dbg(msg: str) -> None:  # type: ignore[misc]
        pass


# ── Low-level request / response ─────────────────────────────────────

def run_command(engine, command_id, body: dict,
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


# ── Response helpers ─────────────────────────────────────────────────

def extract_clip_ids(resp: dict) -> list[str]:
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


def extract_track_id(resp: dict) -> str:
    """Extract the track ID from a CId_CreateNewTracks response.

    Expected path: resp['created_track_ids'][0]
    """
    try:
        return resp['created_track_ids'][0]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(
            f"Failed to extract track_id from create response: {resp}"
        ) from e


# ── Session queries ──────────────────────────────────────────────────

def get_color_palette(engine, target: str = "CPTarget_Tracks") -> list[str]:
    """Fetch the Pro Tools color palette.  Returns ``[]`` on failure."""
    from ptsl import PTSL_pb2 as pt
    try:
        resp = run_command(
            engine, pt.CommandId.CId_GetColorPalette,
            {"color_palette_target": target})
        return (resp or {}).get("color_list", [])
    except Exception:
        return []


def get_session_audio_dir(engine) -> str:
    """Return the session's ``Audio Files`` folder path."""
    session_ptx = engine.session_path()
    return os.path.join(os.path.dirname(session_ptx), "Audio Files")


# ── Batch job lifecycle ──────────────────────────────────────────────

def create_batch_job(engine, name: str, description: str,
                     timeout: int = 30000) -> str | None:
    """Create a PTSL batch job.  Returns the job ID, or ``None``."""
    from ptsl import PTSL_pb2 as pt
    try:
        resp = run_command(
            engine, pt.CommandId.CId_CreateBatchJob,
            {"job": {
                "name": name,
                "description": description,
                "timeout": timeout,
                "is_cancelable": True,
                "cancel_on_failure": False,
            }})
        job_id = (resp or {}).get("id")
        dbg(f"Batch job created: {job_id}")
        return job_id
    except Exception as exc:
        dbg(f"Batch job creation failed: {exc}")
        return None


def complete_batch_job(engine, batch_job_id: str) -> None:
    """Complete a PTSL batch job."""
    from ptsl import PTSL_pb2 as pt
    try:
        run_command(engine, pt.CommandId.CId_CompleteBatchJob,
                    {"id": batch_job_id})
        dbg("Batch job completed")
    except Exception as exc:
        dbg(f"CompleteBatchJob failed: {exc}")


def cancel_batch_job(engine, batch_job_id: str) -> None:
    """Cancel a PTSL batch job (silent on failure)."""
    from ptsl import PTSL_pb2 as pt
    try:
        run_command(engine, pt.CommandId.CId_CancelBatchJob,
                    {"id": batch_job_id})
    except Exception:
        pass


# ── Audio import ─────────────────────────────────────────────────────

def batch_import_audio(
    engine, filepaths: list[str],
    batch_job_id: str | None = None, progress: int = 0,
) -> dict | None:
    """Import audio files into the clip list.

    Calls ``CId_ImportAudioToClipList`` with the given file paths.
    Returns the raw response dict (caller parses clip IDs).
    """
    from ptsl import PTSL_pb2 as pt
    dbg(f"batch_import_audio: {len(filepaths)} files")
    return run_command(
        engine, pt.CommandId.CId_ImportAudioToClipList,
        {"file_list": filepaths},
        batch_job_id=batch_job_id, progress=progress)


# ── Track operations ─────────────────────────────────────────────────

def create_track(
    engine, name: str, track_format: str,
    track_type: str = "TT_Audio",
    timebase: str = "TTB_Samples",
    folder_name: str | None = None,
    batch_job_id: str | None = None, progress: int = 0,
) -> str:
    """Create a new track and return its track ID.

    When *folder_name* is given the track is inserted as the last child
    of that folder.
    """
    from ptsl import PTSL_pb2 as pt
    body: dict[str, Any] = {
        "number_of_tracks": 1,
        "track_name": name,
        "track_format": track_format,
        "track_type": track_type,
        "track_timebase": timebase,
    }
    if folder_name is not None:
        body["insertion_point_track_name"] = folder_name
        body["insertion_point_position"] = "TIPoint_Last"
    resp = run_command(
        engine, pt.CommandId.CId_CreateNewTracks, body,
        batch_job_id=batch_job_id, progress=progress)
    return extract_track_id(resp or {})


def spot_clips(
    engine, clip_ids: list[str], track_id: str,
    batch_job_id: str | None = None, progress: int = 0,
) -> None:
    """Spot clips on a track at the session start (sample 0)."""
    from ptsl import PTSL_pb2 as pt
    run_command(
        engine, pt.CommandId.CId_SpotClipsByID,
        {
            "src_clips": clip_ids,
            "dst_track_id": track_id,
            "dst_location_data": {
                "location_type": "SLType_Start",
                "location": {
                    "location": "0",
                    "time_type": "TLType_Samples",
                },
            },
        },
        batch_job_id=batch_job_id, progress=progress)


def colorize_tracks(
    engine, track_names: list[str], color_index: int,
    batch_job_id: str | None = None, progress: int = 0,
) -> None:
    """Set the color of one or more tracks by palette index."""
    from ptsl import PTSL_pb2 as pt
    run_command(
        engine, pt.CommandId.CId_SetTrackColor,
        {"track_names": track_names, "color_index": color_index},
        batch_job_id=batch_job_id, progress=progress)


def set_track_volume(
    engine, track_id: str, volume_db: float,
    batch_job_id: str | None = None, progress: int = 0,
) -> None:
    """Set a track's fader volume to *volume_db* (direct dB value).

    Uses ``CId_SetTrackControlBreakpoints`` with ``TCType_Volume`` on
    ``TSId_MainOut`` at sample 0.  The *volume_db* value maps directly
    to dBFS (e.g. ``-12.0`` sets the fader to −12 dB).
    """
    from ptsl import PTSL_pb2 as pt
    run_command(
        engine, pt.CommandId.CId_SetTrackControlBreakpoints,
        {
            "track_id": track_id,
            "control_id": {
                "section": "TSId_MainOut",
                "control_type": "TCType_Volume",
            },
            "breakpoints": [{
                "time": {
                    "location": "0",
                    "time_type": "TLType_Samples",
                },
                "value": volume_db,
            }],
        },
        batch_job_id=batch_job_id, progress=progress)
