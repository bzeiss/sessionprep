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
    """Parse --group arguments into group spec dicts."""
    groups = []
    for idx, spec in enumerate(group_args or [], start=1):
        parts = [p.strip() for p in str(spec).split(",") if p.strip()]
        if not parts:
            continue
        groups.append({
            "id": f"G{idx}",
            "spec": str(spec),
            "patterns": parts,
            "members": [],
        })
    return groups


def assign_groups_to_files_with_policy(
    filenames: list[str],
    group_specs: list[dict[str, Any]],
    overlap_policy: str = "warn",
) -> tuple[dict[str, str], list]:
    """
    Assign files to groups based on keyword matching.

    Returns:
        (assignments, overlaps) where assignments maps filename -> group_id,
        and overlaps is a list of overlap records.
    """
    assignments: dict[str, str] = {}
    overlaps: list = []

    if not group_specs:
        return assignments, overlaps

    if overlap_policy not in {"warn", "error", "merge"}:
        raise ValueError(f"Unknown group overlap policy: {overlap_policy}")

    def gid_rank(gid: str) -> int:
        try:
            return int(str(gid)[1:])
        except Exception:
            return 10**9

    if overlap_policy in {"warn", "error"}:
        for fname in filenames:
            for g in group_specs:
                if matches_keywords(fname, g["patterns"]):
                    if fname in assignments:
                        overlaps.append((fname, assignments[fname], g["id"]))
                        break
                    assignments[fname] = g["id"]
                    g["members"].append(fname)
                    break

        if overlap_policy == "error" and overlaps:
            msg_lines = ["Grouping overlaps detected:"]
            for fname, keep_gid, drop_gid in overlaps[:50]:
                msg_lines.append(
                    f"- {fname} matched multiple groups ({keep_gid}, {drop_gid})"
                )
            if len(overlaps) > 50:
                msg_lines.append(f"... ({len(overlaps) - 50} more)")
            raise ValueError("\n".join(msg_lines))

        return assignments, overlaps

    # --- merge policy: union-find ---
    parent = {g["id"]: g["id"] for g in group_specs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra = find(a)
        rb = find(b)
        if ra == rb:
            return
        if gid_rank(ra) <= gid_rank(rb):
            parent[rb] = ra
        else:
            parent[ra] = rb

    matched_by_file: dict[str, list[str]] = {}
    for fname in filenames:
        matched = []
        for g in group_specs:
            if matches_keywords(fname, g["patterns"]):
                matched.append(g["id"])
        if not matched:
            continue
        matched_by_file[fname] = matched
        if len(matched) > 1:
            for other in matched[1:]:
                union(matched[0], other)

    for g in group_specs:
        g["members"] = []

    group_by_id = {g["id"]: g for g in group_specs}

    for fname, matched in matched_by_file.items():
        root = find(matched[0])
        assignments[fname] = root
        group_by_id[root]["members"].append(fname)
        if len(matched) > 1:
            overlaps.append((fname, matched))

    return assignments, overlaps
