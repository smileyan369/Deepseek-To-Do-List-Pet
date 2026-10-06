from __future__ import annotations

import json
import os
from pathlib import Path
from .models import Task

DEFAULT_SETTINGS = {"pet_position": None, "hidden": False, "autostart": False}


class DataStore:
    """JSON store with a previous-good backup and atomic replacement."""
    def __init__(self, data_dir: Path | None = None):
        base = data_dir or Path(os.environ.get("APPDATA", Path.home())) / "深海待办桌宠"
        self.data_dir = Path(base)
        self.path = self.data_dir / "data.json"
        self.backup = self.data_dir / "data.backup.json"

    def load(self) -> tuple[list[Task], dict]:
        for path in (self.path, self.backup):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                tasks = [Task.from_dict(x) for x in raw.get("tasks", [])]
                settings = DEFAULT_SETTINGS | raw.get("settings", {})
                return tasks, settings
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return [], DEFAULT_SETTINGS.copy()

    def save(self, tasks: list[Task], settings: dict) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "tasks": [t.to_dict() for t in tasks],
                              "settings": DEFAULT_SETTINGS | settings}, ensure_ascii=False, indent=2)
        if self.path.exists():
            # Never replace a known-good backup with a malformed interrupted file.
            try:
                json.loads(self.path.read_text(encoding="utf-8"))
                self.backup.write_bytes(self.path.read_bytes())
            except (OSError, ValueError):
                pass
        # A deterministic temp name, NOT tempfile.NamedTemporaryFile: on Windows
        # tempfile retries a permission failure up to 2**31-1 times (TMP_MAX is
        # os.TMP_MAX there), so an unwritable data directory turned a startup save
        # into an invisible infinite hang with no window and no error. Writing a
        # fixed name raises immediately instead, and os.replace stays atomic.
        temp_path = self.path.with_name(self.path.name + ".tmp")
        temp_path.write_text(payload, encoding="utf-8")
        os.replace(temp_path, self.path)
        # Establish an initial recovery point on the first successful write.
        if not self.backup.exists():
            self.backup.write_bytes(self.path.read_bytes())
