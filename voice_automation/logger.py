"""Centralised logging setup with ANSI-colored console output."""

from __future__ import annotations

import logging
import sys

# ── ANSI colour codes ─────────────────────────────────────────────────────────

_RESET = "\033[0m"
_COLORS: dict[int, str] = {
    logging.DEBUG: "\033[36m",      # cyan
    logging.INFO: "\033[32m",       # green
    logging.WARNING: "\033[33m",    # yellow
    logging.ERROR: "\033[31m",      # red
    logging.CRITICAL: "\033[1;31m", # bold red
}


class _ColoredFormatter(logging.Formatter):
    """Logging formatter that prepends ANSI colour codes to the level name."""

    def __init__(self, fmt: str, datefmt: str | None = None) -> None:
        super().__init__(fmt, datefmt=datefmt)

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        colour = _COLORS.get(record.levelno, "")
        record.levelname = f"{colour}{record.levelname:<8}{_RESET}"
        return super().format(record)


_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)-24s | %(message)s"
_DATE_FORMAT = "%H:%M:%S"

_setup_done = False


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root logger with a coloured console handler.

    Calling this function more than once is safe – subsequent calls are
    silently ignored.

    Parameters
    ----------
    level:
        The minimum log level to emit (default ``logging.INFO``).
    """
    global _setup_done  # noqa: PLW0603
    if _setup_done:
        return
    _setup_done = True

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_ColoredFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger for *name*, ensuring logging is initialised.

    This is a convenience wrapper so that every module can call::

        from voice_automation.logger import get_logger
        logger = get_logger(__name__)

    without worrying about whether :func:`setup_logging` has been called yet.
    """
    setup_logging()
    return logging.getLogger(name)
