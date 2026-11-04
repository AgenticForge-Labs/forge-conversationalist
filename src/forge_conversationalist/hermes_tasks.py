"""Per-room Hermes task registry.

Delegation is asynchronous: admission returns a run id, the result arrives
later, and a live conversation may ask about it several turns afterwards. The
voice model must be able to answer "what about the time?" instead of guessing
from the most recent run, so tasks and their results are kept for the room.

This is bookkeeping for one room, not a task authority. Hermes remains the
owner of the run; the stored status is only what the API last reported.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
_MAX_FIELD = 2000


@dataclass(frozen=True)
class HermesTask:
    run_id: str
    task: str
    status: str
    answer: str
    updated_at: float

    @property
    def finished(self) -> bool:
        return self.status in _TERMINAL


class HermesTaskStore:
    """Small durable registry of this room's delegated questions."""

    def __init__(self, path: str | Path | None = None, *, room: str) -> None:
        configured = path or os.environ.get("CONVERSATIONALIST_HERMES_STORE")
        if configured:
            self.path = Path(configured).expanduser()
        else:
            self.path = Path.home() / ".config/forge-conversationalist/hermes-tasks.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.room = room
        self._migrate()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            yield db
        finally:
            db.close()

    def _migrate(self) -> None:
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS hermes_tasks ("
                "room TEXT NOT NULL, run_id TEXT NOT NULL, task TEXT NOT NULL, "
                "status TEXT NOT NULL, answer TEXT NOT NULL, updated_at REAL NOT NULL, "
                "PRIMARY KEY (room, run_id))"
            )

    def track(self, run_id: str, task: str, *, status: str = "started") -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO hermes_tasks VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(room, run_id) DO UPDATE SET "
                "task=excluded.task, status=excluded.status, updated_at=excluded.updated_at",
                (self.room, run_id, task[:1000], status, "", time.time()),
            )

    def finish(self, run_id: str, status: str, answer: str = "") -> None:
        with self._db() as db:
            db.execute(
                "UPDATE hermes_tasks SET status=?, answer=?, updated_at=? "
                "WHERE room=? AND run_id=?",
                (status, answer[:_MAX_FIELD], time.time(), self.room, run_id),
            )

    def _rows(self, limit: int) -> list[HermesTask]:
        with self._db() as db:
            rows = db.execute(
                "SELECT run_id, task, status, answer, updated_at FROM hermes_tasks "
                "WHERE room=? ORDER BY updated_at DESC LIMIT ?",
                (self.room, limit),
            ).fetchall()
        return [HermesTask(*row) for row in rows]

    def recent(self, limit: int = 10) -> list[HermesTask]:
        return self._rows(limit)

    def outstanding(self) -> list[HermesTask]:
        return [task for task in self._rows(50) if not task.finished]

    def find(self, query: str, limit: int = 3) -> list[HermesTask]:
        """Best text match for a follow-up question, newest first."""
        words = {w for w in _normalise(query).split() if len(w) > 2}
        scored: list[tuple[int, HermesTask]] = []
        for task in self._rows(50):
            haystack = _normalise(task.task)
            score = sum(1 for word in words if word in haystack)
            if score:
                scored.append((score, task))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return [task for _score, task in scored[:limit]]


def _normalise(text: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else " " for ch in text)
