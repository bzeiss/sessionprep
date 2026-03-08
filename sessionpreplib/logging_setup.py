"""Centralized logging setup for SessionPrep.

Call :func:`setup_logging` once at application startup (CLI or GUI) to
configure the root logger with:

* A **RotatingFileHandler** writing to ``sessionprep.log`` in the
  OS-specific app-data directory (always active, append mode).
* A **StreamHandler** writing to *stderr* (only when a terminal is
  attached — suppressed in compiled / GUI-only builds).

Log level is controlled via the ``SP_LOG_LEVEL`` environment variable:

* ``DEBUG``, ``INFO`` (default), ``WARNING``, ``ERROR``, ``CRITICAL``
* ``NONE`` — disable logging entirely.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .config import get_app_dir

LOG_FILENAME = "sessionprep.log"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 3
_FORMAT = "%(asctime)s.%(msecs)03d [%(levelname)-5s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_initialized = False


def setup_logging(level: int | None = None) -> None:
    """Configure the root logger for the entire application.

    Safe to call more than once — subsequent calls are no-ops.

    Parameters
    ----------
    level : int or None
        Explicit log level override.  When *None* the level is read
        from the ``SP_LOG_LEVEL`` environment variable.  If not set,
        ``INFO`` is used.  Set to ``NONE`` to disable logging.
    """
    global _initialized  # noqa: PLW0603
    if _initialized:
        return
    _initialized = True

    if level is None:
        level = _level_from_env()

    if level == logging.CRITICAL + 10:  # NONE sentinel
        return

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    # ── File handler (always) ────────────────────────────────────────
    log_dir = get_app_dir()
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, LOG_FILENAME)

    try:
        fh = RotatingFileHandler(
            log_path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setFormatter(formatter)
        root.addHandler(fh)
    except OSError:
        # Cannot write to log file (permissions, etc.) — continue
        # with stderr only.
        pass

    # ── Stderr handler (only when a terminal is attached) ────────────
    if _has_stderr():
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(formatter)
        root.addHandler(sh)


def _level_from_env() -> int:
    """Determine log level from ``SP_LOG_LEVEL``."""
    raw = os.environ.get("SP_LOG_LEVEL", "").strip().upper()
    if raw == "NONE":
        return logging.CRITICAL + 10  # sentinel: skip handler setup
    if raw:
        numeric = getattr(logging, raw, None)
        if isinstance(numeric, int):
            return numeric
    return logging.INFO


def _has_stderr() -> bool:
    """Return True if stderr is connected to a real stream."""
    try:
        return sys.stderr is not None and hasattr(sys.stderr, "write")
    except Exception:
        return False
