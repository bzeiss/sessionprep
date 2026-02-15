"""Lightweight debug logging for SessionPrep.

Usage::

    from sessionprepgui.log import dbg

    dbg("Batch job created: {job_id}")
    dbg("Spectrogram cache invalidated")

Output is only emitted when the environment variable ``SP_DEBUG`` is
set to ``1`` or ``true`` (case-insensitive).  Each message is
prefixed with a timestamp and the calling class/module for easy
grep filtering.
"""

from __future__ import annotations

import inspect
import os
import sys
import time

_ENABLED: bool | None = None


def _is_enabled() -> bool:
    global _ENABLED
    if _ENABLED is None:
        val = os.environ.get("SP_DEBUG", "").strip().lower()
        _ENABLED = val in ("1", "true")
    return _ENABLED


def _caller_name() -> str:
    """Return the class name (or module name) of the caller's caller."""
    frame = inspect.currentframe()
    try:
        # Walk up: _caller_name -> dbg -> actual caller
        caller = frame.f_back.f_back if frame and frame.f_back else None
        if caller is None:
            return "?"
        self_obj = caller.f_locals.get("self")
        if self_obj is not None:
            return type(self_obj).__name__
        cls_obj = caller.f_locals.get("cls")
        if cls_obj is not None:
            return getattr(cls_obj, "__name__", str(cls_obj))
        mod = caller.f_globals.get("__name__", "")
        return mod.rsplit(".", 1)[-1] if mod else "?"
    finally:
        del frame


def dbg(msg: str) -> None:
    """Print a timestamped debug line to stderr if ``SP_DEBUG`` is active.

    Automatically detects the calling class or module name.
    Format: ``[HH:MM:SS.mmm ClassName] message``
    """
    if not _is_enabled():
        return
    t = time.strftime("%H:%M:%S")
    ms = int((time.time() % 1) * 1000)
    name = _caller_name()
    print(f"[{t}.{ms:03d} {name}] {msg}", file=sys.stderr, flush=True)
