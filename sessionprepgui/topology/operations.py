"""Pure topology-mutation functions.

Every function takes a ``TopologyMapping`` (and optional helpers) and mutates
it in-place.  No Qt, no UI — only data transforms.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from sessionpreplib.topology import (
    ChannelRoute,
    TopologyEntry,
    TopologyMapping,
    TopologySource,
    extract_channel as _extract_channel_routes,
    passthrough_routes,
    sum_to_mono as _sum_to_mono_routes,
)

if TYPE_CHECKING:
    from sessionpreplib.models import TrackContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def output_names(topo: TopologyMapping) -> set[str]:
    """Return set of current output filenames."""
    return {e.output_filename for e in topo.entries}


def unique_output_name(topo: TopologyMapping, base: str, ext: str) -> str:
    """Generate a unique output filename by appending ``_N`` if needed."""
    existing = output_names(topo)
    candidate = f"{base}{ext}"
    if candidate not in existing:
        return candidate
    n = 2
    while f"{base}_{n}{ext}" in existing:
        n += 1
    return f"{base}_{n}{ext}"


def _find_entry(topo: TopologyMapping, output_filename: str) -> TopologyEntry | None:
    for e in topo.entries:
        if e.output_filename == output_filename:
            return e
    return None


def channel_label(ch_index: int, total_channels: int) -> str:
    """Human-readable channel label: ``L``/``R`` for stereo, numeric otherwise."""
    if total_channels == 2:
        return {0: "L", 1: "R"}.get(ch_index, str(ch_index))
    return str(ch_index)


# ---------------------------------------------------------------------------
# Existing operations (moved from mixin)
# ---------------------------------------------------------------------------

def split_stereo(
    topo: TopologyMapping,
    track_map: dict[str, TrackContext],
    input_filename: str,
) -> None:
    """Replace a stereo passthrough with two mono extract entries."""
    track = track_map.get(input_filename)
    if not track or track.channels < 2:
        return

    stem, ext = os.path.splitext(input_filename)

    # Remove existing entries for this input
    topo.entries = [
        e for e in topo.entries
        if not (len(e.sources) == 1
                and e.sources[0].input_filename == input_filename)
    ]

    for ch, suffix in enumerate(["_L", "_R"]):
        if ch >= track.channels:
            break
        out_name = unique_output_name(topo, f"{stem}{suffix}", ext)
        topo.entries.append(TopologyEntry(
            output_filename=out_name,
            output_channels=1,
            sources=[TopologySource(
                input_filename=input_filename,
                routes=_extract_channel_routes(ch),
            )],
        ))


def extract_channel(
    topo: TopologyMapping,
    track_map: dict[str, TrackContext],
    input_filename: str,
    channel: int,
) -> None:
    """Replace entry with a mono extract of a single channel."""
    track = track_map.get(input_filename)
    if not track or channel >= track.channels:
        return

    stem, ext = os.path.splitext(input_filename)
    suffix = {0: "_L", 1: "_R"}.get(channel, f"_ch{channel}")

    topo.entries = [
        e for e in topo.entries
        if not (len(e.sources) == 1
                and e.sources[0].input_filename == input_filename)
    ]

    out_name = unique_output_name(topo, f"{stem}{suffix}", ext)
    topo.entries.append(TopologyEntry(
        output_filename=out_name,
        output_channels=1,
        sources=[TopologySource(
            input_filename=input_filename,
            routes=_extract_channel_routes(channel),
        )],
    ))


def sum_to_mono(
    topo: TopologyMapping,
    track_map: dict[str, TrackContext],
    input_filename: str,
) -> None:
    """Replace entry with a mono sum of all channels."""
    track = track_map.get(input_filename)
    if not track:
        return

    stem, ext = os.path.splitext(input_filename)

    topo.entries = [
        e for e in topo.entries
        if not (len(e.sources) == 1
                and e.sources[0].input_filename == input_filename)
    ]

    out_name = unique_output_name(topo, f"{stem}_mono", ext)
    topo.entries.append(TopologyEntry(
        output_filename=out_name,
        output_channels=1,
        sources=[TopologySource(
            input_filename=input_filename,
            routes=_sum_to_mono_routes(track.channels),
        )],
    ))


def merge_stereo(
    topo: TopologyMapping,
    track_map: dict[str, TrackContext],
    left_filename: str,
    right_filename: str,
) -> None:
    """Merge two mono inputs into one stereo output."""
    t_l = track_map.get(left_filename)
    t_r = track_map.get(right_filename)
    if not t_l or not t_r or t_l.channels != 1 or t_r.channels != 1:
        return

    stem_l, ext = os.path.splitext(left_filename)

    remove_fns = {left_filename, right_filename}
    topo.entries = [
        e for e in topo.entries
        if not (len(e.sources) == 1
                and e.sources[0].input_filename in remove_fns)
    ]

    out_name = unique_output_name(topo, f"{stem_l}_stereo", ext)
    topo.entries.append(TopologyEntry(
        output_filename=out_name,
        output_channels=2,
        sources=[
            TopologySource(
                input_filename=left_filename,
                routes=[ChannelRoute(0, 0)],
            ),
            TopologySource(
                input_filename=right_filename,
                routes=[ChannelRoute(0, 1)],
            ),
        ],
    ))


def include_input(
    topo: TopologyMapping,
    track_map: dict[str, TrackContext],
    input_filename: str,
) -> None:
    """Re-include an excluded input track as a passthrough entry."""
    track = track_map.get(input_filename)
    if not track:
        return

    out_name = unique_output_name(topo, *os.path.splitext(input_filename))
    topo.entries.append(TopologyEntry(
        output_filename=out_name,
        output_channels=track.channels,
        sources=[TopologySource(
            input_filename=input_filename,
            routes=passthrough_routes(track.channels),
        )],
    ))


def reset_to_passthrough(
    topo: TopologyMapping,
    track_map: dict[str, TrackContext],
    input_filename: str,
) -> None:
    """Reset an input track's routing back to default passthrough."""
    track = track_map.get(input_filename)
    if not track:
        return

    # Remove all entries referencing this input
    topo.entries = [
        e for e in topo.entries
        if not any(s.input_filename == input_filename for s in e.sources)
    ]

    out_name = unique_output_name(topo, *os.path.splitext(input_filename))
    topo.entries.append(TopologyEntry(
        output_filename=out_name,
        output_channels=track.channels,
        sources=[TopologySource(
            input_filename=input_filename,
            routes=passthrough_routes(track.channels),
        )],
    ))


def exclude_input(topo: TopologyMapping, input_filename: str) -> None:
    """Remove all topology entries that reference the given input."""
    topo.entries = [
        e for e in topo.entries
        if not any(s.input_filename == input_filename for s in e.sources)
    ]


def rename_output(
    topo: TopologyMapping,
    old_name: str,
    new_name: str,
) -> bool:
    """Rename an output entry.  Returns False if *new_name* already exists."""
    existing = output_names(topo) - {old_name}
    if new_name in existing:
        return False
    entry = _find_entry(topo, old_name)
    if entry is not None:
        entry.output_filename = new_name
    return True


def remove_output(topo: TopologyMapping, output_filename: str) -> None:
    """Remove an output entry entirely."""
    topo.entries = [e for e in topo.entries
                    if e.output_filename != output_filename]


# ---------------------------------------------------------------------------
# New channel-level operations
# ---------------------------------------------------------------------------

def add_channel(topo: TopologyMapping, output_filename: str) -> None:
    """Append an empty (silent) channel slot to an output entry."""
    entry = _find_entry(topo, output_filename)
    if entry is None:
        return
    entry.output_channels += 1


def remove_channel(
    topo: TopologyMapping,
    output_filename: str,
    target_ch: int,
) -> None:
    """Remove a channel from an output entry, renumbering higher channels."""
    entry = _find_entry(topo, output_filename)
    if entry is None or target_ch >= entry.output_channels:
        return

    # Remove routes targeting this channel, renumber higher ones
    for src in entry.sources:
        src.routes = [
            ChannelRoute(r.source_channel,
                         r.target_channel - 1 if r.target_channel > target_ch
                         else r.target_channel,
                         r.gain)
            for r in src.routes
            if r.target_channel != target_ch
        ]
    entry.output_channels -= 1

    # Clean up sources with no remaining routes
    entry.sources = [s for s in entry.sources if s.routes]


def clear_channel(
    topo: TopologyMapping,
    output_filename: str,
    target_ch: int,
) -> None:
    """Remove all routes targeting a channel without removing the slot."""
    entry = _find_entry(topo, output_filename)
    if entry is None:
        return

    for src in entry.sources:
        src.routes = [r for r in src.routes if r.target_channel != target_ch]
    entry.sources = [s for s in entry.sources if s.routes]


def wire_channel(
    topo: TopologyMapping,
    output_filename: str,
    target_ch: int,
    input_filename: str,
    source_ch: int,
) -> None:
    """Wire a single source channel to a target channel (replacing existing)."""
    entry = _find_entry(topo, output_filename)
    if entry is None:
        return

    # Remove existing routes to this target channel
    for src in entry.sources:
        src.routes = [r for r in src.routes if r.target_channel != target_ch]
    entry.sources = [s for s in entry.sources if s.routes]

    # Add the new route
    existing_src = None
    for src in entry.sources:
        if src.input_filename == input_filename:
            existing_src = src
            break

    if existing_src:
        existing_src.routes.append(ChannelRoute(source_ch, target_ch))
    else:
        entry.sources.append(TopologySource(
            input_filename=input_filename,
            routes=[ChannelRoute(source_ch, target_ch)],
        ))


def sum_channel(
    topo: TopologyMapping,
    output_filename: str,
    target_ch: int,
    input_filename: str,
    source_ch: int,
) -> None:
    """Add a source to a target channel (summing with existing routes)."""
    entry = _find_entry(topo, output_filename)
    if entry is None:
        return

    existing_src = None
    for src in entry.sources:
        if src.input_filename == input_filename:
            existing_src = src
            break

    if existing_src:
        existing_src.routes.append(ChannelRoute(source_ch, target_ch))
    else:
        entry.sources.append(TopologySource(
            input_filename=input_filename,
            routes=[ChannelRoute(source_ch, target_ch)],
        ))


def remove_source(
    topo: TopologyMapping,
    output_filename: str,
    target_ch: int,
    input_filename: str,
    source_ch: int,
) -> None:
    """Remove one specific route from a channel."""
    entry = _find_entry(topo, output_filename)
    if entry is None:
        return

    for src in entry.sources:
        if src.input_filename == input_filename:
            src.routes = [
                r for r in src.routes
                if not (r.source_channel == source_ch
                        and r.target_channel == target_ch)
            ]
    entry.sources = [s for s in entry.sources if s.routes]


def reorder_channel(
    topo: TopologyMapping,
    output_filename: str,
    from_ch: int,
    to_ch: int,
) -> None:
    """Move channel *from_ch* to position *to_ch*, shifting others."""
    entry = _find_entry(topo, output_filename)
    if entry is None:
        return
    if from_ch == to_ch:
        return
    n = entry.output_channels
    if from_ch >= n or to_ch >= n:
        return

    # Build a permutation map: old index → new index
    order = list(range(n))
    order.pop(from_ch)
    order.insert(to_ch, from_ch)
    # order[new_pos] = old_index  →  invert to old_index → new_pos
    new_index = {old: new for new, old in enumerate(order)}

    for src in entry.sources:
        src.routes = [
            ChannelRoute(r.source_channel, new_index[r.target_channel], r.gain)
            for r in src.routes
        ]


def new_output_file(
    topo: TopologyMapping,
    filename: str,
    channels: int,
) -> None:
    """Create a new empty output entry."""
    topo.entries.append(TopologyEntry(
        output_filename=filename,
        output_channels=channels,
        sources=[],
    ))


# ---------------------------------------------------------------------------
# Bulk wire (file-to-file 1:1)
# ---------------------------------------------------------------------------

def wire_file(
    topo: TopologyMapping,
    output_filename: str,
    input_filename: str,
    input_channels: int,
) -> None:
    """Auto-wire input channels 1:1 into an output file's channels."""
    entry = _find_entry(topo, output_filename)
    if entry is None:
        return

    n = min(input_channels, entry.output_channels)
    # Clear existing routes for target channels 0..n-1
    for ch in range(n):
        for src in entry.sources:
            src.routes = [r for r in src.routes
                          if r.target_channel != ch]
    entry.sources = [s for s in entry.sources if s.routes]

    # Add 1:1 routes
    entry.sources.append(TopologySource(
        input_filename=input_filename,
        routes=[ChannelRoute(i, i) for i in range(n)],
    ))


def move_channel(
    topo: TopologyMapping,
    from_filename: str,
    from_ch: int,
    to_filename: str,
    to_ch: int,
) -> None:
    """Move a channel from one output entry to another (or within the same).

    Extracts the routes targeting *from_ch* in *from_filename*, removes that
    channel slot (renumbering), then inserts a new channel at *to_ch* in
    *to_filename* and wires the extracted routes there.
    """
    from_entry = _find_entry(topo, from_filename)
    to_entry = _find_entry(topo, to_filename)
    if from_entry is None or to_entry is None:
        return
    if from_ch >= from_entry.output_channels:
        return

    # 1) Extract routes targeting from_ch
    extracted: list[tuple[str, int]] = []  # (input_filename, source_ch)
    for src in from_entry.sources:
        for route in src.routes:
            if route.target_channel == from_ch:
                extracted.append((src.input_filename, route.source_channel))

    # 2) Remove the channel from the source entry
    remove_channel(topo, from_filename, from_ch)

    # 3) Insert a new channel slot at to_ch in the target entry
    #    Renumber existing routes >= to_ch upward
    to_ch = min(to_ch, to_entry.output_channels)
    for src in to_entry.sources:
        src.routes = [
            ChannelRoute(r.source_channel,
                         r.target_channel + 1 if r.target_channel >= to_ch
                         else r.target_channel,
                         r.gain)
            for r in src.routes
        ]
    to_entry.output_channels += 1

    # 4) Wire extracted routes to the new slot
    for inp_fn, src_ch in extracted:
        existing_src = None
        for src in to_entry.sources:
            if src.input_filename == inp_fn:
                existing_src = src
                break
        if existing_src:
            existing_src.routes.append(ChannelRoute(src_ch, to_ch))
        else:
            to_entry.sources.append(TopologySource(
                input_filename=inp_fn,
                routes=[ChannelRoute(src_ch, to_ch)],
            ))


def append_channels(
    topo: TopologyMapping,
    output_filename: str,
    channels: list[tuple[str, int]],
) -> None:
    """Append dragged channels as new slots at the end of an output entry.

    *channels* is a list of ``(input_filename, source_channel)`` tuples.
    Each one becomes a new target channel wired to that source.
    """
    entry = _find_entry(topo, output_filename)
    if entry is None:
        return

    for input_filename, source_ch in channels:
        target_ch = entry.output_channels
        entry.output_channels += 1

        # Find or create TopologySource for this input file
        existing_src = None
        for src in entry.sources:
            if src.input_filename == input_filename:
                existing_src = src
                break
        if existing_src:
            existing_src.routes.append(ChannelRoute(source_ch, target_ch))
        else:
            entry.sources.append(TopologySource(
                input_filename=input_filename,
                routes=[ChannelRoute(source_ch, target_ch)],
            ))


def remove_empty_outputs(topo: TopologyMapping) -> int:
    """Remove output entries that have no wired source channels.

    Returns the number of entries removed.
    """
    before = len(topo.entries)
    topo.entries = [
        e for e in topo.entries
        if e.sources and any(s.routes for s in e.sources)
    ]
    return before - len(topo.entries)


def used_channels(topo: TopologyMapping) -> set[tuple[str, int]]:
    """Return set of (input_filename, source_channel) pairs used in the topology."""
    used: set[tuple[str, int]] = set()
    if not topo:
        return used
    for entry in topo.entries:
        for src in entry.sources:
            for route in src.routes:
                used.add((src.input_filename, route.source_channel))
    return used
