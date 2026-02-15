"""General-purpose RIFF (WAV) / IFF (AIFF) chunk reader and writer.

Provides functions to enumerate, read, write, and selectively remove
chunks from WAV and AIFF files without external dependencies beyond
the Python standard library.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass
class AudioChunk:
    """A single chunk from a RIFF or IFF container.

    Attributes:
        id: 4-character chunk identifier (e.g. ``"bext"``, ``"iXML"``).
        size: Payload size in bytes (excluding the 8-byte header).
        data: Raw chunk payload.
    """
    id: str
    size: int
    data: bytes


# Chunk IDs that are always present and generally not interesting
# for DAW interoperability diagnostics.
STANDARD_CHUNKS: frozenset[str] = frozenset({
    # WAV
    "fmt ", "data",
    # AIFF / AIFC
    "COMM", "SSND",
})


def chunk_ids(filepath: str) -> list[str]:
    """Return the chunk ID strings found in a WAV or AIFF file.

    This is a lightweight scan that only reads chunk headers (8 bytes
    each) and seeks past the payload data.  No chunk data is loaded
    into memory, making it suitable for bulk scanning during session
    loading.

    Parameters
    ----------
    filepath : str
        Path to a WAV (``.wav``) or AIFF (``.aif`` / ``.aiff``) file.

    Returns
    -------
    list[str]
        Ordered list of chunk IDs as they appear in the file.

    Raises
    ------
    ValueError
        If the file is not a recognised RIFF or IFF container.
    """
    ids: list[str] = []
    with open(filepath, "rb") as f:
        header = f.read(12)
        if len(header) < 12:
            return ids

        container_id = header[:4]
        form_type = header[8:12]

        if container_id == b"RIFF" and form_type == b"WAVE":
            size_fmt = "<I"  # little-endian
        elif container_id == b"FORM" and form_type in (b"AIFF", b"AIFC"):
            size_fmt = ">I"  # big-endian
        else:
            raise ValueError(
                f"Not a recognised WAV/AIFF container: "
                f"{container_id!r} / {form_type!r}"
            )

        container_end = struct.unpack(size_fmt, header[4:8])[0] + 8
        pos = 12

        while pos + 8 <= container_end:
            f.seek(pos)
            chunk_header = f.read(8)
            if len(chunk_header) < 8:
                break
            chunk_id = chunk_header[:4].decode("ascii", errors="replace")
            chunk_size = struct.unpack(size_fmt, chunk_header[4:8])[0]
            ids.append(chunk_id)
            # Advance past chunk data; chunks are padded to even boundaries
            pos += 8 + chunk_size
            if chunk_size % 2:
                pos += 1

    return ids


def read_chunks(filepath: str) -> tuple[str, list[AudioChunk]]:
    """Read all chunks from a WAV or AIFF file, including payload data.

    Parameters
    ----------
    filepath : str
        Path to the audio file.

    Returns
    -------
    tuple[str, list[AudioChunk]]
        ``(container_format, chunks)`` where *container_format* is one
        of ``"WAVE"``, ``"AIFF"``, or ``"AIFC"``.
    """
    chunks: list[AudioChunk] = []
    with open(filepath, "rb") as f:
        header = f.read(12)
        if len(header) < 12:
            return ("WAVE", chunks)

        container_id = header[:4]
        form_type = header[8:12]

        if container_id == b"RIFF" and form_type == b"WAVE":
            size_fmt = "<I"
            container = "WAVE"
        elif container_id == b"FORM" and form_type in (b"AIFF", b"AIFC"):
            size_fmt = ">I"
            container = form_type.decode("ascii")
        else:
            raise ValueError(
                f"Not a recognised WAV/AIFF container: "
                f"{container_id!r} / {form_type!r}"
            )

        container_end = struct.unpack(size_fmt, header[4:8])[0] + 8
        pos = 12

        while pos + 8 <= container_end:
            f.seek(pos)
            chunk_header = f.read(8)
            if len(chunk_header) < 8:
                break
            chunk_id = chunk_header[:4].decode("ascii", errors="replace")
            chunk_size = struct.unpack(size_fmt, chunk_header[4:8])[0]
            data = f.read(chunk_size)
            chunks.append(AudioChunk(id=chunk_id, size=chunk_size, data=data))
            pos += 8 + chunk_size
            if chunk_size % 2:
                pos += 1

    return (container, chunks)


def write_chunks(
    filepath: str,
    container: str,
    chunks: list[AudioChunk],
) -> None:
    """Write chunks to a new WAV or AIFF file.

    Rebuilds the RIFF/FORM container header from the given chunks.

    Parameters
    ----------
    filepath : str
        Destination path.
    container : str
        ``"WAVE"``, ``"AIFF"``, or ``"AIFC"``.
    chunks : list[AudioChunk]
        Chunks to write (order is preserved).
    """
    if container == "WAVE":
        container_id = b"RIFF"
        form_type = b"WAVE"
        size_fmt = "<I"
    elif container in ("AIFF", "AIFC"):
        container_id = b"FORM"
        form_type = container.encode("ascii").ljust(4)
        size_fmt = ">I"
    else:
        raise ValueError(f"Unknown container format: {container!r}")

    # Calculate total data size (form_type + all chunk headers + data + padding)
    data_size = 4  # form_type
    for ch in chunks:
        data_size += 8 + len(ch.data)
        if len(ch.data) % 2:
            data_size += 1

    with open(filepath, "wb") as f:
        f.write(container_id)
        f.write(struct.pack(size_fmt, data_size))
        f.write(form_type)

        for ch in chunks:
            chunk_id_bytes = ch.id.encode("ascii").ljust(4)[:4]
            f.write(chunk_id_bytes)
            f.write(struct.pack(size_fmt, len(ch.data)))
            f.write(ch.data)
            if len(ch.data) % 2:
                f.write(b"\x00")


def remove_chunks(
    src: str,
    dst: str,
    remove_ids: set[str],
) -> None:
    """Copy *src* to *dst*, omitting chunks whose IDs are in *remove_ids*.

    Convenience wrapper around :func:`read_chunks` and
    :func:`write_chunks`.

    Parameters
    ----------
    src : str
        Source audio file path.
    dst : str
        Destination audio file path (may be the same as *src* but this
        is discouraged — write to a temp file first for safety).
    remove_ids : set[str]
        Chunk IDs to strip (e.g. ``{"iXML", "JUNK"}``).
    """
    container, chunks = read_chunks(src)
    filtered = [ch for ch in chunks if ch.id not in remove_ids]
    write_chunks(dst, container, filtered)


def notable_chunks(all_ids: list[str]) -> list[str]:
    """Filter a list of chunk IDs, returning only non-standard ones."""
    return [cid for cid in all_ids if cid not in STANDARD_CHUNKS]


# ---------------------------------------------------------------------------
# DAW origin detection
# ---------------------------------------------------------------------------

# Proprietary chunk IDs → DAW name (tier-1, no I/O needed)
_FINGERPRINT_CHUNKS: dict[str, str] = {
    "DGDA": "Pro Tools",
    "minf": "Pro Tools",
    "elm1": "Pro Tools",
    "regn": "Pro Tools",
    "Fake": "Cubase / Nuendo",
}


def _read_bext_originator(filepath: str) -> str | None:
    """Read the originator field from a bext chunk, if present.

    The bext chunk layout (EBU Tech 3285):
        bytes   0–255: Description (256 bytes)
        bytes 256–287: Originator (32 bytes ASCII)
    """
    with open(filepath, "rb") as f:
        header = f.read(12)
        if len(header) < 12:
            return None

        container_id = header[:4]
        form_type = header[8:12]

        if container_id == b"RIFF" and form_type == b"WAVE":
            size_fmt = "<I"
        elif container_id == b"FORM" and form_type in (b"AIFF", b"AIFC"):
            size_fmt = ">I"
        else:
            return None

        container_end = struct.unpack(size_fmt, header[4:8])[0] + 8
        pos = 12

        while pos + 8 <= container_end:
            f.seek(pos)
            chunk_header = f.read(8)
            if len(chunk_header) < 8:
                break
            chunk_id = chunk_header[:4]
            chunk_size = struct.unpack(size_fmt, chunk_header[4:8])[0]

            if chunk_id == b"bext" and chunk_size >= 288:
                # Skip 256-byte description, read 32-byte originator
                f.seek(pos + 8 + 256)
                originator = f.read(32)
                return originator.decode("ascii", errors="replace").strip("\x00").strip()

            pos += 8 + chunk_size
            if chunk_size % 2:
                pos += 1

    return None


def detect_origin(
    chunk_ids: list[str],
    filepath: str | None = None,
) -> str | None:
    """Identify the DAW or application that created an audio file.

    Tier 1: checks proprietary chunk IDs (no file I/O).
    Tier 2: if *filepath* is provided and a ``bext`` chunk exists,
    reads the 32-byte originator field.

    Returns a human-readable string (e.g. ``"Pro Tools"``,
    ``"Cubase / Nuendo"``) or ``None`` if the origin cannot be
    determined.
    """
    # Tier 1: fingerprint chunks
    for cid in chunk_ids:
        origin = _FINGERPRINT_CHUNKS.get(cid)
        if origin:
            return origin

    # Tier 2: bext originator field
    if filepath and "bext" in chunk_ids:
        try:
            originator = _read_bext_originator(filepath)
            if originator:
                return originator
        except OSError:
            pass

    return None
