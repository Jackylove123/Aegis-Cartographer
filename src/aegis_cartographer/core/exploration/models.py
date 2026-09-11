"""Typed models for the DFS exploration engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from aegis_cartographer.core.models import ScreenObservation
from aegis_cartographer.core.storage.models import (
    ActionResultType,
    ExplorationAction,
)


def utc_now() -> str:
    """Return a stable UTC timestamp for persisted exploration state."""

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ExplorationStatus(str, Enum):
    """Lifecycle of one exploration run."""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    STOPPED = "STOPPED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    ERROR = "ERROR"


class ExplorationTaskStatus(str, Enum):
    """Lifecycle of one state/element/action exploration task."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    NEEDS_HUMAN = "NEEDS_HUMAN"


class RiskLevel(str, Enum):
    """Safety risk assigned before executing a task."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class ExplorationBudget:
    """Bounded resource consumption for one run."""

    max_actions: int = 1000
    max_depth: int = 15
    max_states: int = 1000
    max_errors: int = 20
    max_retries: int = 2
    max_restore_attempts: int = 3
    max_consecutive_noops: int = 200

    def __post_init__(self) -> None:
        limits = {
            "max_actions": self.max_actions,
            "max_depth": self.max_depth,
            "max_states": self.max_states,
            "max_errors": self.max_errors,
            "max_retries": self.max_retries,
            "max_restore_attempts": self.max_restore_attempts,
            "max_consecutive_noops": self.max_consecutive_noops,
        }
        for name, value in limits.items():
            if value < 1:
                raise ValueError(f"{name} must be at least 1")


@dataclass(frozen=True)
class SafetyPolicy:
    """Conservative action policy used by the DFS engine."""

    blocked_keywords: tuple[str, ...] = (
        "注销",
        "退出登录",
        "登出",
        "删除",
        "移除",
        "支付",
        "购买",
        "下单",
        "提交订单",
        "退款",
        "转账",
        "卸载",
        "修改密码",
        "重置密码",
        "绑定银行卡",
        "发送",
        "delete",
        "remove",
        "pay",
        "payment",
        "purchase",
        "checkout",
        "refund",
        "transfer",
        "logout",
        "sign out",
        "uninstall",
        "reset password",
    )
    needs_human_keywords: tuple[str, ...] = (
        "举报",
        "投诉",
        "客服",
        "授权",
        "权限",
        "report",
        "support",
        "permission",
    )
    allow_input_text: bool = False


@dataclass(frozen=True)
class ExplorationTask:
    """One pending action on one element in one screen state."""

    task_id: str
    run_id: str
    state_id: str
    element_id: str
    action: ExplorationAction
    sequence: int
    status: ExplorationTaskStatus = ExplorationTaskStatus.PENDING
    risk_level: RiskLevel = RiskLevel.LOW
    attempt_count: int = 0
    max_attempts: int = 2
    result_transition_id: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.task_id.strip() or not self.run_id.strip():
            raise ValueError("task_id and run_id must be non-empty")
        if self.attempt_count < 0 or self.max_attempts < 1:
            raise ValueError("task attempt counters are invalid")
        if self.attempt_count > self.max_attempts:
            raise ValueError("attempt_count cannot exceed max_attempts")
        if self.sequence < 1:
            raise ValueError("sequence must be 1-based")

    def with_update(
        self,
        *,
        status: ExplorationTaskStatus | None = None,
        risk_level: RiskLevel | None = None,
        attempt_count: int | None = None,
        result_transition_id: str | None = None,
        error: str | None = None,
    ) -> ExplorationTask:
        """Return a new task with normalized mutable fields."""

        next_attempt = self.attempt_count if attempt_count is None else attempt_count
        if next_attempt < 0 or next_attempt > self.max_attempts:
            raise ValueError("attempt_count cannot exceed max_attempts")
        return ExplorationTask(
            task_id=self.task_id,
            run_id=self.run_id,
            state_id=self.state_id,
            element_id=self.element_id,
            action=self.action,
            sequence=self.sequence,
            status=self.status if status is None else status,
            risk_level=self.risk_level if risk_level is None else risk_level,
            attempt_count=next_attempt,
            max_attempts=self.max_attempts,
            result_transition_id=(
                self.result_transition_id
                if result_transition_id is None
                else result_transition_id
            ),
            error=error,
            created_at=self.created_at,
            updated_at=utc_now(),
        )


@dataclass(frozen=True)
class ExplorationStackFrame:
    """One logical DFS frame and the task that entered it."""

    state_id: str
    entered_by_task_id: str | None = None
    depth: int = 0


@dataclass(frozen=True)
class TransitionClassification:
    """Normalized result of comparing before/after observations."""

    result_type: ActionResultType
    after_state_id: str | None
    after_observation: ScreenObservation | None
    confidence: float
    reason: str
    should_enter_state: bool = False


@dataclass(frozen=True)
class ExplorationActionContext:
    """Context passed into device action execution."""

    task: ExplorationTask
    element_id: str
    selector: str
    text: str
    resource_id: str
    accessibility_id: str
    content_desc: str
    point: tuple[int, int]
    action: ExplorationAction


@dataclass
class ExplorationCheckpoint:
    """Resumable state for one exploration run."""

    run_id: str
    status: ExplorationStatus
    stack: list[ExplorationStackFrame]
    actions_executed: int = 0
    states_created: int = 0
    errors: int = 0
    consecutive_noops: int = 0
    restore_attempts: int = 0
    last_transition_id: str | None = None
    last_error: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize checkpoint data without raw hierarchies."""

        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "stack": [
                {
                    "state_id": frame.state_id,
                    "entered_by_task_id": frame.entered_by_task_id,
                    "depth": frame.depth,
                }
                for frame in self.stack
            ],
            "actions_executed": self.actions_executed,
            "states_created": self.states_created,
            "errors": self.errors,
            "consecutive_noops": self.consecutive_noops,
            "restore_attempts": self.restore_attempts,
            "last_transition_id": self.last_transition_id,
            "last_error": self.last_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ExplorationCheckpoint:
        """Deserialize and validate checkpoint data."""

        required = {
            "run_id", "status", "stack", "actions_executed", "states_created",
            "errors", "consecutive_noops", "restore_attempts",
        }
        missing = sorted(required - set(value))
        if missing:
            raise ValueError(f"Checkpoint is missing fields: {', '.join(missing)}")
        stack = [
            ExplorationStackFrame(
                state_id=str(frame["state_id"]),
                entered_by_task_id=frame.get("entered_by_task_id"),
                depth=int(frame.get("depth", 0)),
            )
            for frame in value["stack"]
        ]
        return cls(
            run_id=str(value["run_id"]),
            status=ExplorationStatus(str(value["status"])),
            stack=stack,
            actions_executed=int(value["actions_executed"]),
            states_created=int(value["states_created"]),
            errors=int(value["errors"]),
            consecutive_noops=int(value["consecutive_noops"]),
            restore_attempts=int(value["restore_attempts"]),
            last_transition_id=value.get("last_transition_id"),
            last_error=value.get("last_error"),
            created_at=str(value.get("created_at", utc_now())),
            updated_at=str(value.get("updated_at", utc_now())),
            metadata=dict(value.get("metadata", {})),
        )
