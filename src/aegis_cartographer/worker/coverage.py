"""Project-level task aggregation across historical exploration runs."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from aegis_cartographer.core.storage.store import MapStore
from aegis_cartographer.core.storage.workspace import ProjectWorkspace


@dataclass(frozen=True)
class TaskSummary:
    """One task identity merged across runs."""

    task_id: str
    status: str
    state_id: str
    element_id: str
    action: str
    screen_id: str
    page_name: str
    element_text: str
    resource_id: str
    last_updated_at: str
    last_run_id: str
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the task for CLI and MCP consumers."""

        return {
            "task_id": self.task_id,
            "status": self.status,
            "state_id": self.state_id,
            "element_id": self.element_id,
            "action": self.action,
            "screen_id": self.screen_id,
            "page_name": self.page_name,
            "element_text": self.element_text,
            "resource_id": self.resource_id,
            "last_updated_at": self.last_updated_at,
            "last_run_id": self.last_run_id,
            "error": self.error,
        }


def _merged_status(statuses: set[str]) -> str:
    if "DONE" in statuses:
        return "DONE"
    if "NEEDS_HUMAN" in statuses:
        return "NEEDS_HUMAN"
    if "BLOCKED" in statuses:
        return "BLOCKED"
    if "RUNNING" in statuses:
        return "RUNNING"
    if "PENDING" in statuses:
        return "PENDING"
    if "FAILED" in statuses:
        return "FAILED"
    return "SKIPPED"


def collect_task_summaries(
    workspace: ProjectWorkspace,
    store: MapStore,
) -> tuple[TaskSummary, ...]:
    """Merge run-local task ledgers into map-level task identities."""

    states = {state.state_id: state for state in store.list_screen_states()}
    elements = {element.element_id: element for element in store.list_elements()}
    rows: dict[str, list[dict[str, Any]]] = {}

    for database_path in sorted(workspace.runs_dir.glob("*/tasks.sqlite")):
        if database_path.is_symlink() or not database_path.is_file():
            continue
        run_id = database_path.parent.name
        uri = database_path.resolve().as_uri() + "?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True)
            connection.row_factory = sqlite3.Row
            try:
                run_rows = connection.execute(
                    """
                    SELECT task_id, state_id, element_id, action, status, error, updated_at
                    FROM tasks
                    ORDER BY updated_at, task_id
                    """
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.Error:
            continue
        for row in run_rows:
            element = elements.get(str(row["element_id"]))
            state = states.get(str(row["state_id"]))
            if element is None or state is None:
                continue
            rows.setdefault(str(row["task_id"]), []).append(
                {
                    "task_id": str(row["task_id"]),
                    "status": str(row["status"]),
                    "state_id": str(row["state_id"]),
                    "element_id": str(row["element_id"]),
                    "action": str(row["action"]),
                    "screen_id": state.screen_id,
                    "updated_at": str(row["updated_at"]),
                    "run_id": run_id,
                    "error": row["error"],
                }
            )

    summaries: list[TaskSummary] = []
    for task_id, appearances in rows.items():
        latest = max(
            appearances,
            key=lambda item: (item["updated_at"], item["run_id"]),
        )
        element = elements[latest["element_id"]]
        screen = store.get_screen(latest["screen_id"])
        summaries.append(
            TaskSummary(
                task_id=task_id,
                status=_merged_status({item["status"] for item in appearances}),
                state_id=latest["state_id"],
                element_id=latest["element_id"],
                action=latest["action"],
                screen_id=latest["screen_id"],
                page_name=screen.semantic_name if screen is not None else latest["screen_id"],
                element_text=element.text,
                resource_id=element.resource_id,
                last_updated_at=latest["updated_at"],
                last_run_id=latest["run_id"],
                error=latest["error"],
            )
        )
    return tuple(
        sorted(
            summaries,
            key=lambda item: (
                item.screen_id,
                item.status,
                item.last_updated_at,
                item.task_id,
            ),
        )
    )


def task_report(workspace: ProjectWorkspace, store: MapStore) -> dict[str, Any]:
    """Return page-grouped task status across every run in this project."""

    summaries = collect_task_summaries(workspace, store)
    counts: dict[str, int] = {}
    pages: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        counts[summary.status] = counts.get(summary.status, 0) + 1
        page = pages.setdefault(
            summary.screen_id,
            {
                "screen_id": summary.screen_id,
                "name": summary.page_name,
                "task_counts": {},
                "unfinished": 0,
                "tasks": [],
            },
        )
        page["task_counts"][summary.status] = (
            page["task_counts"].get(summary.status, 0) + 1
        )
        if summary.status in {"PENDING", "RUNNING", "FAILED"}:
            page["unfinished"] += 1
            page["tasks"].append(summary.to_dict())
    return {
        "task_counts": counts,
        "unfinished_tasks": sum(
            counts.get(status, 0) for status in ("PENDING", "RUNNING", "FAILED")
        ),
        "page_count": len(pages),
        "pages": sorted(pages.values(), key=lambda item: (-item["unfinished"], item["name"])),
    }
