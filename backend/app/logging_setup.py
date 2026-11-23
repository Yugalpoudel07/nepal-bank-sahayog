"""
logging_setup.py
================
Structured, Windows-safe logging.

The reference project used emoji `print()` calls, which crash on a Windows
cp1252 console. Here we (1) reconfigure stdout/stderr to UTF-8 where the
stream supports it and (2) route everything through `logging` with a
key=value format that is easy to grep and parse.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def _force_utf8_streams() -> None:
    """Make stdout/stderr UTF-8 so Nepali text never raises UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


class KeyValueFormatter(logging.Formatter):
    """`time level logger msg key=value ...` — extra fields passed via `extra={"kv": {...}}`."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        kv = getattr(record, "kv", None)
        if kv:
            base += " " + " ".join(f"{k}={v!r}" for k, v in kv.items())
        return base


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _force_utf8_streams()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        KeyValueFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "chromadb", "urllib3", "sentence_transformers", "pdfminer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True
