from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import uuid4
import random


@dataclass
class Task:
    id: str
    name: str
    due_at: str | None
    created_at: str
    sort_key: int

    @property
    def due_datetime(self) -> datetime | None:
        return datetime.fromisoformat(self.due_at) if self.due_at else None

    @classmethod
    def create(cls, name: str, due_at: datetime | None) -> "Task":
        now = datetime.now().replace(second=0, microsecond=0)
        return cls(str(uuid4()), name.strip(), due_at.isoformat() if due_at else None,
                   now.isoformat(), random.SystemRandom().randint(0, 2**63 - 1))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Task":
        return cls(str(raw["id"]), str(raw["name"]), raw.get("due_at"),
                   str(raw["created_at"]), int(raw["sort_key"]))


def task_sort_key(task: Task) -> tuple:
    """Due tasks first, then time; same time uses shorter name then stable key."""
    return (task.due_at is None, task.due_at or "", len(task.name), task.sort_key)


def sorted_tasks(tasks: list[Task]) -> list[Task]:
    return sorted(tasks, key=task_sort_key)


def current_minute(now: datetime | None = None) -> datetime:
    return (now or datetime.now()).replace(second=0, microsecond=0)
