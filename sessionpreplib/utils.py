from __future__ import annotations

import fnmatch
from typing import Any


def protools_sort_key(filename: str) -> str:
    """
    Generate a sort key that matches Pro Tools' sorting behavior.
    Pro Tools appears to sort as if spaces don't exist.
    """
    return filename.lower().replace(' ', '')


def matches_keywords(filename: str, keywords: list[str]) -> bool:
    """
    Check if filename matches any of the keywords.
    Supports:
      - Substring matching (default): "bass" matches "Bass_01.wav"
      - Glob patterns (if * or ? present): "Bass_??.wav" matches "Bass_01.wav"
      - Exact matching (if keyword ends with $): "Bass_01.wav$" matches only "Bass_01.wav"
    """
    fname_lower = filename.lower()

    for keyword in keywords:
        kw_lower = keyword.lower()

        # Exact match mode: keyword ending with '$'
        if kw_lower.endswith('$'):
            exact_pattern = kw_lower[:-1]  # Remove the '$'
            if fname_lower == exact_pattern or fname_lower == exact_pattern + '.wav':
                return True
        # Glob pattern mode: contains * or ?
        elif '*' in kw_lower or '?' in kw_lower:
            if fnmatch.fnmatch(fname_lower, kw_lower):
                return True
        # Default: substring matching
        else:
            if kw_lower in fname_lower:
                return True

    return False


def parse_group_specs(group_args: list[str] | None) -> list[dict[str, Any]]:
    """Parse ``--group Name:pattern1,pattern2`` arguments into group specs.

    The ``Name:`` prefix is mandatory.  Raises :class:`ValueError` if a
    spec is missing the prefix or has no patterns after the colon.

    Returns a list of dicts with keys ``name``, ``patterns``, ``members``.
    """
    groups: list[dict[str, Any]] = []
    for spec in (group_args or []):
        raw = str(spec).strip()
        if ":" not in raw:
            raise ValueError(
                f"Invalid --group syntax: '{raw}'. "
                f"Expected Name:pattern1,pattern2  (e.g. --group Kick:kick,kick_sub)"
            )
        name, _, pattern_str = raw.partition(":")
        name = name.strip()
        if not name:
            raise ValueError(
                f"Invalid --group syntax: '{raw}'. Group name before ':' must not be empty."
            )
        patterns = [p.strip() for p in pattern_str.split(",") if p.strip()]
        if not patterns:
            raise ValueError(
                f"Invalid --group syntax: '{raw}'. "
                f"At least one pattern is required after '{name}:'."
            )
        groups.append({
            "name": name,
            "patterns": patterns,
            "members": [],
        })
    return groups


def assign_groups(
    filenames: list[str],
    group_specs: list[dict[str, Any]],
) -> tuple[dict[str, str], list[str]]:
    """Assign files to named groups using first-match-wins.

    Returns:
        ``(assignments, warnings)`` where *assignments* maps
        ``filename → group_name`` and *warnings* is a list of
        human-readable overlap warning strings.
    """
    assignments: dict[str, str] = {}
    warnings: list[str] = []

    if not group_specs:
        return assignments, warnings

    for fname in filenames:
        matched_group: str | None = None
        for g in group_specs:
            if matches_keywords(fname, g["patterns"]):
                if matched_group is None:
                    matched_group = g["name"]
                    g["members"].append(fname)
                else:
                    warnings.append(
                        f"Group overlap: {fname} matches '{g['name']}' "
                        f"but is already assigned to '{matched_group}'"
                    )
        if matched_group is not None:
            assignments[fname] = matched_group

    return assignments, warnings
