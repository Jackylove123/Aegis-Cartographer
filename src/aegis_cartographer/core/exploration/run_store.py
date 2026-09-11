"""Project-local task ledger for one exploration run."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping

from aegis_cartographer.core.exploration.models import (
    ExplorationTask,
    ExplorationTaskStatus,
    RiskLevel,
)
from aegis_cartographer.core.storage.workspace import ProjectWorkspace

_RUN_SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    state_id TEXT NOT NULL,
    element_id TEXT NOT NULL,
    action TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    status TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 2,
    result_transition_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, state_id, element_id, action)
);

CREATE INDEX IF NOT EXISTS idx_tasks_next
    ON tasks(run_id, state_id, status, created_at, task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_element
    ON tasks(element_id);
"""


class ExplorationRunStore:
    """SQLite task ledger kept under ``.aegis/runs/<run_id>/``."""

    def __init__(self, workspace: ProjectWorkspace, run_id: str) -> None:
        if not run_id.strip() or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ValueError("run_id must be a safe single path component")
        self.workspace = workspace
        self.run_id = run_id
        self.run_directory = workspace.runs_dir / run_id
        self.database_path = self.run_directory / "tasks.sqlite"
        self.run_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = sqlite3.connect(self.database_path, timeout=30.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.executescript(_RUN_SCHEMA)

    def close(self) -> None:
        """Close the task ledger connection."""

        self.connection.close()

    def __enter__(self) -> ExplorationRunStore:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def upsert(
        self,
        task: ExplorationTask,
        *,
        preserve_existing_progress: bool = True,
    ) -> ExplorationTask | None:
        """Insert or update one task without accidentally resetting progress."""

        with self.connection:
            existing = self.connection.execute(
                """
                SELECT task_id FROM tasks
                WHERE run_id = ? AND state_id = ? AND element_id = ? AND action = ?
                """,
                (task.run_id, task.state_id, task.element_id, task.action.value),
            ).fetchone()
            if existing is not None:
                existing_id = str(existing["task_id"])
                if task.task_id != existing_id:
                    raise ValueError("A different task_id already exists for this identity")
                if preserve_existing_progress:
                    self.connection.execute(
                        """
                        UPDATE tasks SET
                            risk_level = ?, max_attempts = ?, updated_at = ?
                        WHERE task_id = ?
                        """,
                        (
                            task.risk_level.value,
                            task.max_attempts,
                            task.updated_at,
                            task.task_id,
                        ),
                    )
                else:
                    self.connection.execute(
                        """
                        UPDATE tasks SET
                            status = ?, risk_level = ?, attempt_count = ?, sequence = ?,
                            max_attempts = ?, result_transition_id = ?,
                            error = ?, updated_at = ?
                        WHERE task_id = ?
                        """,
                        (
                            task.status.value,
                            task.risk_level.value,
                            task.attempt_count,
                            task.sequence,
                            task.max_attempts,
                            task.result_transition_id,
                            task.error,
                            task.updated_at,
                            task.task_id,
                        ),
                    )
                return self.get(task.task_id)

            self.connection.execute(
                """
                INSERT INTO tasks(
                    task_id, run_id, state_id, element_id, action, sequence, status,
                    risk_level, attempt_count, max_attempts, result_transition_id,
                    error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.run_id,
                    task.state_id,
                    task.element_id,
                    task.action.value,
                    task.sequence,
                    task.status.value,
                    task.risk_level.value,
                    task.attempt_count,
                    task.max_attempts,
                    task.result_transition_id,
                    task.error,
                    task.created_at,
                    task.updated_at,
                ),
            )
        return self.get(task.task_id)

    def get(self, task_id: str) -> ExplorationTask | None:
        row = self.connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return self._row_to_task(row) if row is not None else None

    def list(
        self,
        *,
        state_id: str | None = None,
        status: ExplorationTaskStatus | str | None = None,
    ) -> list[ExplorationTask]:
        clauses = ["run_id = ?"]
        parameters: list[Any] = [self.run_id]
        if state_id is not None:
            clauses.append("state_id = ?")
            parameters.append(state_id)
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status.value if hasattr(status, "value") else str(status))
        rows = self.connection.execute(
            f"SELECT * FROM tasks WHERE {' AND '.join(clauses)} "
            "ORDER BY sequence, created_at, task_id",
            parameters,
        ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def next(self, *, state_id: str) -> ExplorationTask | None:
        row = self.connection.execute(
            """
            SELECT * FROM tasks
            WHERE run_id = ? AND state_id = ? AND status = ?
            ORDER BY sequence, created_at, task_id
            LIMIT 1
            """,
            (self.run_id, state_id, ExplorationTaskStatus.PENDING.value),
        ).fetchone()
        return self._row_to_task(row) if row is not None else None

    @staticmethod
    def _row_to_task(row: Mapping[str, Any]) -> ExplorationTask:
        from aegis_cartographer.core.storage.models import ExplorationAction

        return ExplorationTask(
            task_id=str(row["task_id"]),
            run_id=str(row["run_id"]),
            state_id=str(row["state_id"]),
            element_id=str(row["element_id"]),
            action=ExplorationAction(str(row["action"])),
            sequence=int(row["sequence"]),
            status=ExplorationTaskStatus(str(row["status"])),
            risk_level=RiskLevel(str(row["risk_level"])),
            attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]),
            result_transition_id=row["result_transition_id"],
            error=row["error"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
