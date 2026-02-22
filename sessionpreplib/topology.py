"""Channel topology data model and helpers.

Defines the mapping from input tracks to output tracks via channel routing.
Each ``TopologyEntry`` describes one physical output file; its ``sources``
list references input files and specifies per-channel routing with gain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import TrackContext


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ChannelRoute:
    """Route one source channel to one output channel, with optional gain."""
    source_channel: int   # 0-based index in the source file
    target_channel: int   # 0-based index in the output file
    gain: float = 1.0     # scaling factor (1.0 = unity; 0.5 for equal-power sum, etc.)


@dataclass
class TopologySource:
    """One input file's contribution to an output file."""
    input_filename: str
    routes: list[ChannelRoute] = field(default_factory=list)


@dataclass
class TopologyEntry:
    """One output file in the resolved topology."""
    output_filename: str
    output_channels: int       # target channel count (1, 2, 6, …)
    sources: list[TopologySource] = field(default_factory=list)


@dataclass
class TopologyMapping:
    """Complete channel topology: how input tracks map to output files."""
    entries: list[TopologyEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Convenience factories for common channel operations
# ---------------------------------------------------------------------------

def passthrough_routes(channels: int) -> list[ChannelRoute]:
    """1:1 mapping for all channels."""
    return [ChannelRoute(i, i) for i in range(channels)]


def extract_channel(source_ch: int) -> list[ChannelRoute]:
    """Extract a single channel to mono output (target channel 0)."""
    return [ChannelRoute(source_ch, 0)]


def sum_to_mono(source_channels: int) -> list[ChannelRoute]:
    """Equal-gain sum of all channels to mono."""
    gain = 1.0 / source_channels
    return [ChannelRoute(i, 0, gain) for i in range(source_channels)]


# ---------------------------------------------------------------------------
# Default topology builder
# ---------------------------------------------------------------------------

def build_default_topology(tracks: list[TrackContext]) -> TopologyMapping:
    """All-passthrough: each OK input track maps 1:1, preserving all channels."""
    entries: list[TopologyEntry] = []
    for track in tracks:
        if track.status != "OK":
            continue
        entries.append(TopologyEntry(
            output_filename=track.filename,
            output_channels=track.channels,
            sources=[TopologySource(
                input_filename=track.filename,
                routes=passthrough_routes(track.channels),
            )],
        ))
    return TopologyMapping(entries=entries)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def resolve_entry_audio(
    entry: TopologyEntry,
    track_audio: dict[str, tuple[Any, int]],
) -> Any:
    """Produce the output audio array for a single TopologyEntry.

    Parameters
    ----------
    entry : TopologyEntry
        The topology entry to resolve.
    track_audio : dict
        Mapping ``input_filename → (audio_ndarray, samplerate)``.
        Audio arrays are float64, shape ``(samples,)`` for mono or
        ``(samples, channels)`` for multi-channel.

    Returns
    -------
    numpy.ndarray
        The resolved audio array with shape ``(samples, output_channels)``
        or ``(samples,)`` if ``output_channels == 1``.
    """
    import numpy as np

    # Determine output length from the longest source
    max_samples = 0
    for src in entry.sources:
        audio, _sr = track_audio[src.input_filename]
        n = audio.shape[0]
        if n > max_samples:
            max_samples = n

    if max_samples == 0:
        if entry.output_channels == 1:
            return np.zeros(0, dtype=np.float64)
        return np.zeros((0, entry.output_channels), dtype=np.float64)

    # Allocate output buffer
    out = np.zeros((max_samples, entry.output_channels), dtype=np.float64)

    for src in entry.sources:
        audio, _sr = track_audio[src.input_filename]
        # Normalise to 2-D: (samples, channels)
        if audio.ndim == 1:
            audio_2d = audio[:, np.newaxis]
        else:
            audio_2d = audio

        for route in src.routes:
            src_ch = route.source_channel
            tgt_ch = route.target_channel
            if src_ch < audio_2d.shape[1]:
                n = audio_2d.shape[0]
                out[:n, tgt_ch] += audio_2d[:, src_ch] * route.gain

    # Squeeze mono to 1-D
    if entry.output_channels == 1:
        return out[:, 0]
    return out


def build_transfer_manifest(
    mapping: TopologyMapping,
    tracks: list[TrackContext],
    existing_manifest: list | None = None,
) -> list:
    """Build a transfer manifest from topology + output tracks.

    For each topology entry, creates a ``TransferEntry`` (imported locally
    to avoid circular imports).  If *existing_manifest* contains user-added
    duplicates (entries whose ``output_filename`` still exists), they are
    preserved.

    Returns a list of ``TransferEntry`` objects.
    """
    from .models import TransferEntry

    # Collect existing user-added duplicates (entry_id != output_filename)
    preserved: list = []
    valid_outputs = {e.output_filename for e in mapping.entries}
    if existing_manifest:
        # Count how many entries exist per output_filename in the old manifest
        from collections import Counter
        old_counts: Counter = Counter(
            e.output_filename for e in existing_manifest)
        seen: Counter = Counter()
        for e in existing_manifest:
            seen[e.output_filename] += 1
            # The first entry per output_filename is the "primary" — we
            # recreate it.  Extra entries (duplicates) are preserved.
            if seen[e.output_filename] > 1 and e.output_filename in valid_outputs:
                preserved.append(e)

    # Build group lookup from input tracks
    group_map = {t.filename: t.group for t in tracks}

    import os
    result: list = []
    for entry in mapping.entries:
        # Determine group: take from first source's input track
        grp = None
        if entry.sources:
            grp = group_map.get(entry.sources[0].input_filename)
        stem = os.path.splitext(entry.output_filename)[0]
        result.append(TransferEntry(
            entry_id=entry.output_filename,
            output_filename=entry.output_filename,
            daw_track_name=stem,
            group=grp,
        ))

    # Re-add preserved user duplicates
    result.extend(preserved)
    return result


def validate_topology(
    mapping: TopologyMapping,
    tracks: list[TrackContext],
) -> list[str]:
    """Return a list of error messages (empty = valid)."""
    errors: list[str] = []
    track_map = {t.filename: t for t in tracks if t.status == "OK"}

    seen_outputs: set[str] = set()
    for entry in mapping.entries:
        # Duplicate output filenames
        if entry.output_filename in seen_outputs:
            errors.append(
                f"Duplicate output filename: {entry.output_filename}")
        seen_outputs.add(entry.output_filename)

        if entry.output_channels < 1:
            errors.append(
                f"{entry.output_filename}: output_channels must be >= 1")

        if not entry.sources:
            errors.append(f"{entry.output_filename}: no sources defined")

        for src in entry.sources:
            tc = track_map.get(src.input_filename)
            if tc is None:
                errors.append(
                    f"{entry.output_filename}: source '{src.input_filename}' "
                    f"not found in input tracks")
                continue

            for route in src.routes:
                if route.source_channel < 0 or route.source_channel >= tc.channels:
                    errors.append(
                        f"{entry.output_filename}: source '{src.input_filename}' "
                        f"channel {route.source_channel} out of range "
                        f"(0..{tc.channels - 1})")
                if route.target_channel < 0 or route.target_channel >= entry.output_channels:
                    errors.append(
                        f"{entry.output_filename}: target channel "
                        f"{route.target_channel} out of range "
                        f"(0..{entry.output_channels - 1})")

    return errors
