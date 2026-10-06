"""Append-only log so a silent failure stops being invisible.

A windowed PyInstaller build has no console: a crash before the window appears, a
duplicate launch that exits by design, or an unexpected startup error all look
identical from the outside ("double-clicked, nothing happened"). Every such event
lands in `%APPDATA%\\深海待办桌宠\\pet-error.log`, next to the user data — a stable
place that survives a rebuild or a move of the project folder.
"""

from __future__ import annotations

import os
import time


LOG_NAME = "pet-error.log"


def log_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "深海待办桌宠", LOG_NAME)


def log_line(message: str) -> None:
    """Append one log entry; logging must never raise into its caller."""
    try:
        path = log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message.rstrip("\n") + "\n")
    except OSError:
        pass
