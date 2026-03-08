"""Lightweight debug logging for SessionPrep GUI.

Usage::

    from sessionprepgui.log import dbg

    dbg("Batch job created: {job_id}")
    dbg("Spectrogram cache invalidated")

This is a thin convenience wrapper around Python's standard
:mod:`logging` module.  Each call resolves the calling class or
module automatically and delegates to ``logging.getLogger(name).debug()``.

The log level and handlers are configured once at startup via
:func:`sessionpreplib.logging_setup.setup_logging`.
"""

from __future__ import annotations

import inspect
import logging


def _caller_logger() -> logging.Logger:
    """Return a logger named after the calling module/class."""
    frame = inspect.currentframe()
    try:
        # Walk up: _caller_logger -> dbg -> actual caller
        caller = frame.f_back.f_back if frame and frame.f_back else None
        if caller is None:
            return logging.getLogger("sessionprepgui")
        mod = caller.f_globals.get("__name__", "sessionprepgui")
        return logging.getLogger(mod)
    finally:
        del frame


def dbg(msg: str) -> None:
    """Log a debug message, automatically detecting the caller.

    This is a backward-compatible convenience wrapper.  New code should
    prefer ``log = logging.getLogger(__name__)`` at module level and
    call ``log.debug(...)`` directly.
    """
    _caller_logger().debug(msg)
