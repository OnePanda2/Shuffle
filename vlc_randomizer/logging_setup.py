"""
Centralised logging configuration.

A rotating file handler keeps a bounded on-disk history for debugging; a console
handler surfaces warnings during development. All modules obtain their logger via
``logging.getLogger(__name__)`` after :func:`configure_logging` has run once.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_configured = False


def configure_logging(log_path: Path, level: int = logging.INFO) -> None:
    """Install file + console handlers on the root logger (idempotent)."""
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        file_handler = RotatingFileHandler(
            log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    except OSError:
        # Never let logging setup crash the app (e.g. locked file / no perms).
        pass

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(logging.WARNING)
    root.addHandler(console)

    _configured = True
