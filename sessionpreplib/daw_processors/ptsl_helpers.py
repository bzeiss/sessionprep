"""Generic, reusable PTSL helper functions.

Stateless functions that wrap individual Pro Tools Scripting Library
(PTSL) commands.  They operate on a connected py-ptsl ``Engine`` and
plain Python data — no knowledge of SessionContext, DawProcessor, or
transfer phases.
"""

from __future__ import annotations

import json
import math
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

def is_session_open(engine) -> bool:
    """Check if Pro Tools currently has a session open.

    Returns True if a session is open, False otherwise.
    """
    try:
        # If a session is open, session_name() will return a non-empty string.
        name = engine.session_name()
        return bool(name)
    except Exception:
        # PTSL commands typically fail if no session is open.
        return False

def wait_for_host_ready(engine, timeout: float = 25.0, sleep_time: float = 0.5) -> bool:
    """
    Poll the Pro Tools HostReadyCheck endpoint.
    Returns True if the host is ready, False if the timeout is reached.
    """
    import time
    from ptsl import ops

    start_time = time.time()
    while time.time() - start_time < timeout:
        op = ops.HostReadyCheck()
        try:
            # We run the operation directly through the client so we can inspect the response
            engine.client.run(op)
            if op.response and getattr(op.response, "is_host_ready", False):
                return True
        except Exception:
            # Ignore temporary gRPC errors, timeout, or parsing failures while waking up
            pass

        time.sleep(sleep_time)

    return False

def get_color_palette(engine, target: str = "CPTarget_Tracks") -> list[str]:
    """Fetch the Pro Tools color palette.  Returns ``[]`` on failure."""
    try:
        resp = run_command(
            engine, "CId_GetColorPalette",
            {"color_palette_target": target})
        return (resp or {}).get("color_list", [])
    except Exception:
        return []


def get_selected_track_names(engine) -> list[str]:
    """Return names of explicitly selected tracks in Pro Tools.

    Only returns tracks the user directly selected (``SetExplicitly``),
    not implicit children of selected folders (``SetImplicitly``).
    """
    from ptsl import PTSL_pb2 as pt
    try:
        resp = run_command(
            engine, pt.CommandId.CId_GetTrackList, {})
        tracks = (resp or {}).get("track_list", [])
        selected = []
        for t in tracks:
            attrs = t.get("track_attributes", {})
            if attrs.get("is_selected") == "SetExplicitly":
                selected.append(t["name"])
        return selected
    except Exception:
        return []


def get_session_audio_dir(engine) -> str:
    """Return the session's ``Audio Files`` folder path."""
    session_ptx = engine.session_path()
    return os.path.join(os.path.dirname(session_ptx), "Audio Files")


# ── Session lifecycle ────────────────────────────────────────────────

def create_session_from_template(  # pylint: disable=too-many-positional-arguments
    engine, session_name: str, session_location: str,
    template_group: str, template_name: str,
    sample_rate: str = "SR_48000",
    bit_depth: str = "Bit24",
) -> None:
    """Create a new Pro Tools session from a template.
    
    Paths use native OS separators. CId_CreateSession automatically opens the session.
    """
    from ptsl import PTSL_pb2 as pt

    location = os.path.abspath(session_location)
    os.makedirs(location, exist_ok=True)

    body = {
        "session_name": session_name,
        "session_location": location,
        "create_from_template": True,
        "template_group": template_group,
        "template_name": template_name,
        "file_type": "FT_WAVE",
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
        "input_output_settings": "IO_StereoMix",
        "is_interleaved": True,
        "is_cloud_project": False,
    }

    # 1. Create the session (Pro Tools automatically opens it as well)
    run_command(engine, pt.CommandId.CId_CreateSession, body)

    # Wait until Pro Tools actually loads the template and writes the PTX file.
    # It can take a few seconds for the background creation to finish.
    import time
    session_dir = os.path.join(location, session_name)
    session_path = os.path.join(session_dir, f"{session_name}.ptx")

    success = False
    for _ in range(15):  # Wait up to 7.5 seconds
        if os.path.isfile(session_path):
            success = True
            break
        time.sleep(0.5)

    if not success:
        raise RuntimeError(
            f"Pro Tools failed to create the session. Please check if the "
            f"template '{template_group} / {template_name}' actually exists."
        )

def close_session(engine, save_on_close: bool = False, delay: float = 0.5) -> None:
    """Close the current Pro Tools session."""
    from ptsl import PTSL_pb2 as pt
    import time
    run_command(engine, pt.CommandId.CId_CloseSession, {"save_on_close": save_on_close})
    # Give the host a breather to physically close the document
    time.sleep(delay)


def save_session(engine) -> None:
    """Save the current Pro Tools session without closing it."""
    from ptsl import PTSL_pb2 as pt
    run_command(engine, pt.CommandId.CId_SaveSession, {})


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

def create_track(  # pylint: disable=too-many-positional-arguments
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


# ── IMPORTANT: How PTSL fader control works ──────────────────────────
#
# Pro Tools 2025.10 introduced CId_SetTrackControlBreakpoints (command
# 150) which writes AUTOMATION BREAKPOINTS, not live fader positions.
#
# Key behaviours discovered through empirical testing:
#
#   1. The command writes automation data into the track's volume
#      automation lane.  A single breakpoint at sample 0 effectively
#      sets a flat automation value across the entire timeline.
#
#   2. Faders do NOT visually move when the command is issued.  They
#      only snap to the written value when the TRANSPORT PLAYS and
#      Pro Tools reads the automation.  This is expected behaviour,
#      not a bug.
#
#   3. The value is ACTUAL dB (empirically verified 2025-03-03).
#      Despite the proto documentation claiming a -1.0 to +1.0 range
#      for TCType_Volume, testing confirms that the float value maps
#      directly to dB.  No transfer function is needed.
#        +12.0  →  +12 dB  (fader fully up)
#          0.0  →    0 dB  (unity gain)
#         -6.0  →   -6 dB
#        -18.0  →  -18 dB  (SessionPrep sustained target)
#        -80.0  →  -80 dB  (near silence)
#      The proto's -1.0/+1.0 range likely applies only to pan, mute,
#      LFE, and plugin parameter controls — not volume.
#
#   4. Both track_id (GUID string) and track_name (display name) are
#      accepted for track identification.  Either field can be used,
#      the proto defines them as alternatives.
#
#   5. The command can be wrapped in a batch job (CId_CreateBatchJob /
#      CId_CompleteBatchJob) which shows a modal progress dialog in
#      Pro Tools and blocks user interaction during the operation.
#
#   6. As of Pro Tools 2025.12, only TCType_Volume (and sends) work.
#      TCType_Pan, TCType_Mute, TCType_Lfe, TCType_PluginParameter
#      return "Not yet implemented" from the server.
#
# Reference: PTSL SDK 2025.10 documentation, Chapter 3 (Batch Jobs)
#            and SetTrackControlBreakpointsRequestBody in PTSL.proto.
# ─────────────────────────────────────────────────────────────────────


def set_track_volume(
    engine, track_id: str, volume_db: float,
    batch_job_id: str | None = None, progress: int = 0,
) -> None:
    """Set a track's fader volume via automation breakpoint (by track_id).

    Writes a single automation breakpoint at sample 0 using
    ``CId_SetTrackControlBreakpoints`` with ``TCType_Volume`` on
    ``TSId_MainOut``.

    .. important::

        This writes **automation data**, not a live fader position.
        The fader only visually moves when the transport plays and
        Pro Tools reads the automation.

    Args:
        track_id:     Pro Tools track GUID, e.g.
                      ``"{00000000-2a000000-eead9701-ea871516}"``.
        volume_db:    Fader value in **actual dB**.  Pro Tools range is
                      roughly ``-inf`` to ``+12.0``.  E.g. ``0.0`` = unity,
                      ``-6.0`` = −6 dB, ``+12.0`` = fader fully up.
        batch_job_id: Optional batch job ID (from ``create_batch_job``).
        progress:     Batch job progress percentage (0–100).

    Requires Pro Tools 2025.10+.
    """
    from ptsl import PTSL_pb2 as pt
    dbg(f"set_track_volume: id={track_id}, value={volume_db}")
    try:
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
                }]
            },
            batch_job_id=batch_job_id, progress=progress)
    except Exception as e:
        dbg(f"Error in set_track_volume ({track_id}, {volume_db}): {e}")
        raise


def set_track_volume_by_trackname(
    engine, track_name: str, volume: float,
    batch_job_id: str | None = None, progress: int = 0,
) -> None:
    """Set a track's fader volume via automation breakpoint (by track_name).

    Identical to :func:`set_track_volume` but identifies the track by
    its display name instead of its GUID.  See that function's docstring
    for full details on behaviour, value range, and caveats.

    Args:
        track_name:   Pro Tools track display name, e.g. ``"Audio 1"``.
        volume:       Fader value in **actual dB** (see :func:`set_track_volume`).
        batch_job_id: Optional batch job ID (from ``create_batch_job``).
        progress:     Batch job progress percentage (0–100).

    Requires Pro Tools 2025.10+.
    """
    from ptsl import PTSL_pb2 as pt
    dbg(f"set_track_volume_by_trackname: name={track_name}, value={volume}")
    try:
        run_command(
            engine, pt.CommandId.CId_SetTrackControlBreakpoints,
            {
                "track_name": track_name,
                "control_id": {
                    "section": "TSId_MainOut",
                    "control_type": "TCType_Volume",
                },
                "breakpoints": [{
                    "time": {
                        "location": "0",
                        "time_type": "TLType_Samples",
                    },
                    "value": volume,
                }]
            },
            batch_job_id=batch_job_id, progress=progress)
    except Exception as e:
        dbg(f"Error in set_track_volume_by_trackname ({track_name}, {volume}): {e}")
        raise


# ── Color helpers ────────────────────────────────────────────────────


def parse_argb(argb: str) -> tuple[int, int, int]:
    """Parse '#ffRRGGBB' ARGB hex string to (R, G, B) ints."""
    h = argb.lstrip("#")
    if len(h) == 8:
        return int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)
    if len(h) == 6:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 128, 128, 128


def srgb_to_linear(c: float) -> float:
    """Convert sRGB channel [0..1] to linear."""
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def rgb_to_lab(r: int, g: int, b: int) -> tuple[float, float, float]:
    """Convert sRGB (0-255) to CIE L*a*b* (D65 illuminant)."""
    rl = srgb_to_linear(r / 255.0)
    gl = srgb_to_linear(g / 255.0)
    bl = srgb_to_linear(b / 255.0)
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


def closest_palette_index(
    target_argb: str,
    palette: list[str],
) -> int | None:
    """Find the palette index whose colour is perceptually closest.

    Uses CIE L*a*b* Euclidean distance.  Returns ``None`` if palette
    is empty.
    """
    if not palette:
        return None
    tr, tg, tb = parse_argb(target_argb)
    tL, ta, tb_ = rgb_to_lab(tr, tg, tb)
    best_idx = 0
    best_dist = float("inf")
    for idx, entry in enumerate(palette):
        pr, pg, pb = parse_argb(entry)
        pL, pa, pb2 = rgb_to_lab(pr, pg, pb)
        dist = math.sqrt((tL - pL) ** 2 + (ta - pa) ** 2 + (tb_ - pb2) ** 2)
        if dist < best_dist:
            best_dist = dist
            best_idx = idx
    return best_idx


# ── Track color ──────────────────────────────────────────────────────


def set_track_color(
    engine,
    color_index: int,
    track_names: list[str] | None = None,
    track_ids: list[str] | None = None,
    batch_job_id: str | None = None,
    progress: int = 0,
) -> dict:
    """Set the color of one or more tracks by palette index.

    Either *track_names* or *track_ids* must be provided.
    Returns the raw response dict.
    """
    body: dict[str, Any] = {"color_index": color_index}
    if track_names:
        body["track_names"] = track_names
    if track_ids:
        body["track_ids"] = track_ids
    return run_command(
        engine, "CId_SetTrackColor", body,
        batch_job_id=batch_job_id, progress=progress)
