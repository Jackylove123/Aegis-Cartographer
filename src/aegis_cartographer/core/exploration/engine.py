"""Deterministic, resumable DFS exploration engine."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from aegis_cartographer.core.exploration.models import (
    ExplorationActionContext,
    ExplorationBudget,
    ExplorationCheckpoint,
    ExplorationStackFrame,
    ExplorationStatus,
    ExplorationTask,
    ExplorationTaskStatus,
    SafetyPolicy,
    TransitionClassification,
    utc_now,
)
from aegis_cartographer.core.exploration.run_store import ExplorationRunStore
from aegis_cartographer.core.exploration.safety import SafetyClassifier
from aegis_cartographer.core.identity import (
    ScreenMatcher,
    ScreenMatchType,
    observe_screen,
)
from aegis_cartographer.core.models import (
    ScreenObservation,
    ScreenStateRecord,
    ScreenStateType,
)
from aegis_cartographer.core.storage.models import (
    ActionResultType,
    ElementRecord,
    ExplorationAction,
)
from aegis_cartographer.core.storage.store import MapStore

logger = logging.getLogger(__name__)


class ExternalAppObservationError(ValueError):
    """Raised when an observation belongs to an application outside the target."""


class DeviceDriverProtocol(Protocol):
    """Minimal device operations required by the exploration engine."""

    def get_hierarchy(self) -> dict[str, Any]:
        """Return the current hierarchy."""

    def wait_until_stable(
        self,
        *,
        timeout: float = 10.0,
        interval: float = 0.3,
        stable_count: int = 2,
    ) -> dict[str, Any]:
        """Wait until the screen structure stops changing."""

    def tap_on(
        self,
        element_id: str | None = None,
        text: str | None = None,
        bounds: str | None = None,
        *,
        label: str | None = None,
        point: tuple[int, int] | None = None,
        occurrence_index: int | None = None,
        timeout: float = 40.0,
    ) -> dict[str, Any]:
        """Tap an element."""

    def long_press_on(
        self,
        element_id: str | None = None,
        text: str | None = None,
        bounds: str | None = None,
        *,
        label: str | None = None,
        point: tuple[int, int] | None = None,
        occurrence_index: int | None = None,
        timeout: float = 40.0,
    ) -> dict[str, Any]:
        """Long-press an element."""

    def swipe(self, direction: str, *, timeout: float = 40.0) -> dict[str, Any]:
        """Swipe the screen."""

    def back(self, *, timeout: float = 15.0) -> dict[str, Any]:
        """Press back."""

    def restart_app(self, *, timeout: float = 30.0) -> dict[str, Any]:
        """Restart the target application."""

    def get_foreground_activity(self) -> Any:
        """Return normalized foreground activity information when available."""


class ExplorationEngine:
    """Bounded DFS engine backed by Maestro and a project-local map store."""

    def __init__(
        self,
        *,
        store: MapStore,
        driver: DeviceDriverProtocol,
        run_id: str,
        target_app_id: str,
        device_id: str = "",
        budget: ExplorationBudget | None = None,
        action_timeout: float = 40.0,
        back_timeout: float = 15.0,
        launch_timeout: float = 30.0,
        stability_timeout: float = 10.0,
        safety_policy: SafetyPolicy | None = None,
        matcher: ScreenMatcher | None = None,
    ) -> None:
        if store.readonly:
            raise ValueError("Exploration requires a writable MapStore")
        if not run_id.strip() or not target_app_id.strip():
            raise ValueError("run_id and target_app_id must be non-empty")
        if Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ValueError("run_id must be a safe single path component")
        if min(action_timeout, back_timeout, launch_timeout, stability_timeout) <= 0:
            raise ValueError("device timeouts must be greater than zero")

        self.store = store
        self.driver = driver
        self.run_id = run_id
        self.target_app_id = target_app_id
        self.device_id = device_id
        self.budget = budget or ExplorationBudget()
        self.action_timeout = action_timeout
        self.back_timeout = back_timeout
        self.launch_timeout = launch_timeout
        self.stability_timeout = stability_timeout
        self.safety = SafetyClassifier(safety_policy)
        self.matcher = matcher or ScreenMatcher()
        self.checkpoint_path = (
            store.workspace.runs_dir / run_id / "checkpoint.json"
        )
        self.task_store = ExplorationRunStore(store.workspace, run_id)
        self._discovered_state_ids: set[str] = set()
        self._uncounted_new_state_ids: set[str] = set()
        self._last_observation_error: str | None = None
        try:
            self.checkpoint = self._load_or_create_checkpoint()
        except BaseException:
            self.task_store.close()
            raise

    def close(self) -> None:
        """Release the run task ledger."""

        self.task_store.close()

    def __enter__(self) -> ExplorationEngine:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def run(self, *, max_steps: int | None = None) -> ExplorationCheckpoint:
        """Run until completion, a budget boundary, or an unrecoverable error."""

        if self.checkpoint.status not in {
            ExplorationStatus.CREATED,
            ExplorationStatus.RUNNING,
            ExplorationStatus.PAUSED,
        }:
            return self.checkpoint

        self.checkpoint.status = ExplorationStatus.RUNNING
        self.checkpoint.updated_at = utc_now()
        self._save_checkpoint()
        steps_allowed = max_steps if max_steps is not None else self.budget.max_actions

        fresh_observation: ScreenStateRecord | None = None
        try:
            while steps_allowed > 0:
                if self.checkpoint.status not in {
                    ExplorationStatus.RUNNING,
                    ExplorationStatus.CREATED,
                    ExplorationStatus.PAUSED,
                }:
                    break
                if not self._within_budget():
                    self._set_status(ExplorationStatus.BUDGET_EXCEEDED)
                    break

                frame = self.checkpoint.stack[-1]
                current_state = self.store.get_screen_state(frame.state_id)
                if current_state is None:
                    self._record_fatal(f"Stack state missing: {frame.state_id}")
                    break

                if (
                    fresh_observation is not None
                    and fresh_observation.state_id == frame.state_id
                ):
                    observed_state = fresh_observation
                    fresh_observation = None
                else:
                    observed_state = self._observe_and_register()
                if observed_state is None:
                    self._record_fatal(
                        self._last_observation_error
                        or "Unable to obtain a valid hierarchy"
                    )
                    break

                if observed_state.state_id != frame.state_id:
                    if not self._reconcile_observed_state(observed_state):
                        continue
                    frame = self.checkpoint.stack[-1]
                    current_state = self.store.get_screen_state(frame.state_id)
                    if current_state is None:
                        self._record_fatal("Stack state disappeared during reconciliation")
                        break

                task = self.task_store.next(state_id=frame.state_id)
                if task is None:
                    self._pop_completed_frame()
                    steps_allowed -= 1
                    continue

                element = self.store.get_element(task.element_id)
                if element is None:
                    task = task.with_update(
                        status=ExplorationTaskStatus.FAILED,
                        error="Element record missing",
                    )
                    self._save_task(task)
                    self.checkpoint.errors += 1
                    self._save_checkpoint()
                    steps_allowed -= 1
                    continue

                risk, blocked_status, reason = self.safety.classify(task, element)
                if risk != task.risk_level or task.status == ExplorationTaskStatus.RUNNING:
                    task = task.with_update(risk_level=risk)
                if blocked_status is not None:
                    task = task.with_update(
                        status=blocked_status,
                        risk_level=risk,
                        error=reason,
                    )
                    self._save_task(task)
                    steps_allowed -= 1
                    continue

                task = task.with_update(
                    status=ExplorationTaskStatus.RUNNING,
                    risk_level=risk,
                    attempt_count=task.attempt_count + 1,
                )
                self._save_task(task)

                before_observation = self._last_observation
                if before_observation is None:
                    before_observation = self._observe(required=True)

                action_result = self._execute_task(task, element)
                after_observation: ScreenObservation | None = None
                if action_result.get("success"):
                    after_observation = self._observe(required=False)

                classification = self._classify_transition(
                    before_state=current_state,
                    before_observation=before_observation,
                    after_observation=after_observation,
                    action_success=bool(action_result.get("success")),
                    action_error=action_result.get("error"),
                )
                if (
                    classification.after_observation is not None
                    and classification.after_state_id is not None
                ):
                    fresh_observation = self.store.get_screen_state(
                        classification.after_state_id
                    )
                transition_id = self._record_transition(
                    task=task,
                    classification=classification,
                    element=element,
                    action_result=action_result,
                )

                failed_result = classification.result_type in {
                    ActionResultType.ERROR,
                    ActionResultType.CRASH,
                }
                next_task_status = ExplorationTaskStatus.DONE
                task_error: str | None = None
                if failed_result:
                    task_error = classification.reason
                    next_task_status = (
                        ExplorationTaskStatus.PENDING
                        if task.attempt_count < task.max_attempts
                        else ExplorationTaskStatus.FAILED
                    )
                task = task.with_update(
                    status=next_task_status,
                    result_transition_id=transition_id,
                    error=task_error,
                )
                self._save_task(task)
                self.checkpoint.actions_executed += 1
                self.checkpoint.last_transition_id = transition_id
                self.checkpoint.updated_at = utc_now()

                if classification.result_type == ActionResultType.NO_OP:
                    self.checkpoint.consecutive_noops += 1
                else:
                    self.checkpoint.consecutive_noops = 0

                if classification.result_type == ActionResultType.ERROR:
                    self.checkpoint.errors += 1

                if classification.should_enter_state:
                    after_state_id = classification.after_state_id
                    assert after_state_id is not None
                    self._enter_state(after_state_id, task.task_id)
                elif classification.result_type in {
                    ActionResultType.EXTERNAL_APP,
                    ActionResultType.APP_EXIT,
                    ActionResultType.CRASH,
                }:
                    self._restore_state(frame.state_id)
                elif classification.after_state_id is not None:
                    self._reconcile_observed_state_id(classification.after_state_id)

                self._save_checkpoint()
                steps_allowed -= 1

                if not self._within_budget():
                    self._set_status(ExplorationStatus.BUDGET_EXCEEDED)
                    break

            if steps_allowed <= 0 and self.checkpoint.status == ExplorationStatus.RUNNING:
                self.checkpoint.status = ExplorationStatus.PAUSED
                self.checkpoint.updated_at = utc_now()
                self._save_checkpoint()
        except Exception as error:  # Engine must not lose checkpoint context.
            logger.exception("Exploration run failed")
            self._record_fatal(str(error))
        return self.checkpoint

    def pause(self) -> ExplorationCheckpoint:
        """Pause after the current synchronous step."""

        return self._set_status(ExplorationStatus.PAUSED)

    def stop(self) -> ExplorationCheckpoint:
        """Mark the run stopped."""

        return self._set_status(ExplorationStatus.STOPPED)

    def get_checkpoint(self) -> ExplorationCheckpoint:
        """Return the current checkpoint."""

        return self.checkpoint

    def coverage_report(self) -> dict[str, Any]:
        """Return deterministic progress and coverage counters."""

        tasks = self.task_store.list()
        statuses: dict[str, int] = {}
        for task in tasks:
            statuses[task.status.value] = statuses.get(task.status.value, 0) + 1
        stats = self.store.statistics()
        return {
            "run_id": self.run_id,
            "status": self.checkpoint.status.value,
            "actions_executed": self.checkpoint.actions_executed,
            "states_created": self.checkpoint.states_created,
            "errors": self.checkpoint.errors,
            "stack_depth": len(self.checkpoint.stack),
            "stack": [frame.state_id for frame in self.checkpoint.stack],
            "task_statuses": statuses,
            "pending_tasks": statuses.get(ExplorationTaskStatus.PENDING.value, 0),
            "blocked_tasks": statuses.get(ExplorationTaskStatus.BLOCKED.value, 0),
            "needs_human_tasks": statuses.get(
                ExplorationTaskStatus.NEEDS_HUMAN.value,
                0,
            ),
            "map_statistics": stats,
        }

    def _load_or_create_checkpoint(self) -> ExplorationCheckpoint:
        if self.checkpoint_path.exists():
            value = self.store.workspace.read_json(self.checkpoint_path)
            checkpoint = ExplorationCheckpoint.from_dict(value)
            self.checkpoint = checkpoint
            self._discovered_state_ids.update(
                state.state_id for state in self.store.list_screen_states()
            )
            self._recover_interrupted_tasks()
            return checkpoint

        observation = self._observe(required=True)
        if (
            observation.package_name
            and observation.target_app_id
            and observation.package_name != observation.target_app_id
        ):
            raise ValueError(
                "Exploration cannot start outside the target app: "
                f"{observation.package_name}"
            )
        state = self._register_observation(observation)
        self.store.set_entry_state(state.state_id)
        self._uncounted_new_state_ids.clear()
        checkpoint = ExplorationCheckpoint(
            run_id=self.run_id,
            status=ExplorationStatus.CREATED,
            stack=[ExplorationStackFrame(state_id=state.state_id, depth=0)],
            states_created=1,
            metadata={
                "target_app_id": self.target_app_id,
                "device_id": self.device_id,
            },
        )
        self.checkpoint = checkpoint
        self._save_checkpoint()
        return checkpoint

    def _observe(self, *, required: bool) -> ScreenObservation:
        stable = self.driver.wait_until_stable(timeout=self.stability_timeout)
        hierarchy = stable.get("hierarchy") or self.driver.get_hierarchy()
        if not hierarchy:
            reason = stable.get("last_hierarchy_error") or "hierarchy_empty"
            message = f"Device returned an empty hierarchy: {reason}"
            if required:
                raise RuntimeError(message)
            raise ValueError(message)
        return observe_screen(
            hierarchy,
            target_app_id=self.target_app_id,
            package_name=self._foreground_package_name(),
        )

    def _foreground_package_name(self) -> str | None:
        reader = getattr(self.driver, "get_foreground_activity", None)
        if not callable(reader):
            return None
        foreground = reader()
        if foreground is None or not getattr(foreground, "success", False):
            return None
        package_name = getattr(foreground, "package_name", "")
        return str(package_name) if package_name else None

    def _observe_and_register(self) -> ScreenStateRecord | None:
        try:
            observation = self._observe(required=False)
        except ValueError as error:
            self._last_observation_error = str(error)
            return None
        self._last_observation_error = None
        return self._register_observation(observation)

    def _register_observation(
        self,
        observation: ScreenObservation,
        *,
        semantic_name: str | None = None,
    ) -> ScreenStateRecord:
        if (
            observation.package_name
            and observation.target_app_id
            and observation.package_name != observation.target_app_id
        ):
            raise ExternalAppObservationError(observation.package_name)
        known = self.store.list_screen_states()
        match = self.matcher.match(observation, known)
        if match.match_type is ScreenMatchType.SAME_STATE:
            state = self.store.record_screen(
                observation,
                screen_id=match.matched_screen_id,
            )
        elif match.match_type is ScreenMatchType.STATE_VARIANT:
            state = self.store.record_screen(
                observation,
                semantic_name=semantic_name,
                screen_id=match.matched_screen_id,
            )
        else:
            fallback_name = semantic_name or (
                observation.landmarks[0]
                if observation.landmarks
                else f"screen-{len(known) + 1}"
            )
            state = self.store.record_screen(
                observation,
                semantic_name=fallback_name,
                screen_id=match.recommended_screen_id
                if match.match_type is ScreenMatchType.NEW_SCREEN
                else None,
            )

        self.store.record_observation(
            observation,
            run_id=self.run_id,
            state_id=state.state_id,
            device_id=self.device_id,
        )
        self._create_tasks_for_state(state, observation)
        if state.state_id not in self._discovered_state_ids:
            self._discovered_state_ids.add(state.state_id)
            self._uncounted_new_state_ids.add(state.state_id)
        self._last_observation = observation
        return state

    _last_observation: ScreenObservation | None = None

    @staticmethod
    def _navigation_rank(element: ElementRecord) -> int:
        """Rank likely page-navigation controls before generic page actions."""

        identity = " ".join(
            (
                element.resource_id,
                element.accessibility_id,
                element.content_desc,
                element.text,
                element.semantic_name or "",
                *element.aliases,
            )
        ).lower()
        navigation_markers = (
            "bottom",
            "tab",
            "nav",
            "menu",
            "home",
            "main",
            "detail",
            "bill",
            "asset",
            "profile",
            "mine",
            "search",
            "setting",
            "首页",
            "主页",
            "明细",
            "账单",
            "资产",
            "我的",
            "个人",
            "搜索",
            "设置",
            "记账",
            "记一笔",
        )
        if any(marker in identity for marker in navigation_markers):
            return 0
        if element.is_actionable:
            return 1
        return 2

    def _create_tasks_for_state(
        self,
        state: ScreenStateRecord,
        observation: ScreenObservation,
    ) -> None:
        from aegis_cartographer.core import extract_ui_elements

        element_records = []
        for element in extract_ui_elements(
            observation.hierarchy,
            include_non_actionable=True,
        ):
            is_dynamic = not (
                element.resource_id
                or element.accessibility_id
                or element.content_desc
                or element.text
            )
            element_records.append(
                self.store.record_element(
                    state,
                    element,
                    role=(
                        "BUTTON"
                        if element.clickable or element.actionable_ancestor
                        else "DISPLAY"
                    ),
                    is_dynamic=is_dynamic,
                    risk_level="unknown",
                )
            )

        action_ranks = {
            ExplorationAction.TAP: 0,
            ExplorationAction.SCROLL: 1,
            ExplorationAction.LONG_PRESS: 2,
            ExplorationAction.INPUT_TEXT: 3,
        }
        candidates: list[tuple[int, int, int, ExplorationAction, Any]] = []
        for document_order, element in enumerate(element_records):
            for action in element.actions:
                candidates.append(
                    (
                        action_ranks.get(action, 4),
                        self._navigation_rank(element),
                        document_order,
                        action,
                        element,
                    )
                )

        sequence = 0
        for _, _, _, action, element in sorted(candidates, key=lambda item: item[:3]):
            sequence += 1
            task_id = self._task_id(element.element_id, action)
            self.task_store.upsert(
                ExplorationTask(
                    task_id=task_id,
                    run_id=self.run_id,
                    state_id=state.state_id,
                    element_id=element.element_id,
                    action=action,
                    sequence=sequence,
                    max_attempts=self.budget.max_retries + 1,
                )
            )
        if candidates:
            self.store.mark_screen_coverage(state.screen_id, "DEEP_PENDING")

    def _refresh_page_coverage(self, state_id: str) -> None:
        """Keep the page card aligned with its current run task ledger."""

        current_state = self.store.get_screen_state(state_id)
        if current_state is None:
            return
        statuses: list[ExplorationTaskStatus] = []
        for state in self.store.list_screen_states(screen_id=current_state.screen_id):
            statuses.extend(task.status for task in self.task_store.list(state_id=state.state_id))
        if not statuses:
            return
        if ExplorationTaskStatus.PENDING in statuses or ExplorationTaskStatus.RUNNING in statuses:
            coverage = "DEEP_PENDING"
        elif ExplorationTaskStatus.NEEDS_HUMAN in statuses:
            coverage = "NEEDS_HUMAN"
        elif ExplorationTaskStatus.BLOCKED in statuses:
            coverage = "BLOCKED"
        else:
            coverage = "DEEP_DONE"
        self.store.mark_screen_coverage(current_state.screen_id, coverage)

    @staticmethod
    def _task_id(element_id: str, action: ExplorationAction) -> str:
        identity = hashlib.sha256(
            f"{element_id}|{action.value}".encode("utf-8")
        ).hexdigest()
        return identity

    def _execute_task(
        self,
        task: ExplorationTask,
        element: ElementRecord,
    ) -> dict[str, Any]:
        context = self._action_context(task, element)
        if task.action == ExplorationAction.BACK:
            result = self.driver.back(timeout=self.back_timeout)
            return {**result, "used_locator": {"strategy": "system", "value": "BACK"}}
        if task.action == ExplorationAction.SCROLL:
            result = self.driver.swipe("DOWN", timeout=self.action_timeout)
            return {**result, "used_locator": {"strategy": "direction", "value": "DOWN"}}
        if task.action in {
            ExplorationAction.TAP,
            ExplorationAction.LONG_PRESS,
        }:
            for locator in sorted(element.locators, key=lambda item: item.priority):
                arguments: dict[str, Any] = {
                    "element_id": None,
                    "text": None,
                    "bounds": None,
                    "label": None,
                    "point": None,
                    "occurrence_index": element.occurrence_index,
                }
                if locator.strategy == "id":
                    arguments["element_id"] = locator.value
                elif locator.strategy == "accessibility_id":
                    arguments["label"] = locator.value
                elif locator.strategy == "label":
                    arguments["label"] = locator.value
                elif locator.strategy == "text":
                    arguments["text"] = locator.value
                elif locator.strategy == "point":
                    arguments["point"] = tuple(int(value) for value in locator.value.split(","))
                else:
                    continue

                method = (
                    self.driver.tap_on
                    if task.action == ExplorationAction.TAP
                    else self.driver.long_press_on
                )
                result = method(**arguments, timeout=self.action_timeout)
                if result.get("success"):
                    return {
                        **result,
                        "used_locator": {
                            "strategy": locator.strategy,
                            "value": locator.value,
                            "occurrence_index": element.occurrence_index,
                        },
                    }
            return {
                "success": False,
                "error": "all locators failed",
                "context": context,
            }
        return {
            "success": False,
            "error": f"unsupported action in engine: {task.action.value}",
            "context": context,
        }

    @staticmethod
    def _action_context(
        task: ExplorationTask,
        element: ElementRecord,
    ) -> ExplorationActionContext:
        left, top, right, bottom = element.bounds
        return ExplorationActionContext(
            task=task,
            element_id=element.element_id,
            selector=element.selector,
            text=element.text,
            resource_id=element.resource_id,
            accessibility_id=element.accessibility_id,
            content_desc=element.content_desc,
            point=((left + right) // 2, (top + bottom) // 2),
            action=task.action,
        )

    def _classify_transition(
        self,
        *,
        before_state: ScreenStateRecord,
        before_observation: ScreenObservation | None,
        after_observation: ScreenObservation | None,
        action_success: bool,
        action_error: Any = None,
    ) -> TransitionClassification:
        if not action_success:
            return TransitionClassification(
                result_type=ActionResultType.ERROR,
                after_state_id=None,
                after_observation=None,
                confidence=1.0,
                reason=str(action_error or "device action failed"),
            )
        if after_observation is None:
            return TransitionClassification(
                result_type=ActionResultType.ERROR,
                after_state_id=None,
                after_observation=None,
                confidence=1.0,
                reason="no valid observation after action",
            )
        if (
            after_observation.target_app_id
            and after_observation.package_name
            and after_observation.package_name != after_observation.target_app_id
        ):
            return TransitionClassification(
                result_type=ActionResultType.EXTERNAL_APP,
                after_state_id=None,
                after_observation=after_observation,
                confidence=1.0,
                reason="current package differs from target app",
            )

        after_state = self._register_observation(after_observation)
        if after_state.state_id == before_state.state_id:
            return TransitionClassification(
                result_type=ActionResultType.NO_OP,
                after_state_id=after_state.state_id,
                after_observation=after_observation,
                confidence=0.99,
                reason="screen state is unchanged",
            )

        is_overlay = after_observation.state_type in {
            ScreenStateType.DIALOG,
            ScreenStateType.BOTTOM_SHEET,
            ScreenStateType.SYSTEM_DIALOG,
        }
        result_type = (
            ActionResultType.OVERLAY
            if is_overlay
            else (
                ActionResultType.STATE_CHANGE
                if after_state.screen_id == before_state.screen_id
                else ActionResultType.NEW_PAGE
            )
        )
        return TransitionClassification(
            result_type=result_type,
            after_state_id=after_state.state_id,
            after_observation=after_observation,
            confidence=0.9,
            reason=f"state changed from {before_state.state_id} to {after_state.state_id}",
            should_enter_state=True,
        )

    def _record_transition(
        self,
        *,
        task: ExplorationTask,
        classification: TransitionClassification,
        element: ElementRecord,
        action_result: Mapping[str, Any],
    ) -> str | None:
        target_required = classification.result_type in {
            ActionResultType.NEW_PAGE,
            ActionResultType.OVERLAY,
            ActionResultType.STATE_CHANGE,
        }
        transition = self.store.record_transition(
            from_state_id=task.state_id,
            element_id=task.element_id,
            action=task.action,
            result_type=classification.result_type,
            to_state_id=(
                classification.after_state_id
                if target_required
                else None
            ),
            maestro_commands=self._maestro_commands(task, element, action_result),
            restore_strategy=({"strategy": "BACK"},),
            success=classification.result_type
            not in {ActionResultType.ERROR, ActionResultType.CRASH},
        )
        return transition.transition_id

    @staticmethod
    def _maestro_commands(
        task: ExplorationTask,
        element: ElementRecord,
        action_result: Mapping[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        if task.action == ExplorationAction.BACK:
            return ({"pressKey": "BACK"},)
        if task.action == ExplorationAction.SCROLL:
            return ({"swipe": "DOWN"},)

        used = action_result.get("used_locator")
        if not isinstance(used, Mapping):
            return ()
        strategy = str(used.get("strategy", ""))
        value = str(used.get("value", ""))
        selector: dict[str, Any] = {}
        if strategy == "id":
            selector["id"] = value
        elif strategy in {"accessibility_id", "label"}:
            selector["label"] = value
        elif strategy == "text":
            selector["text"] = value
        elif strategy == "point":
            selector["point"] = value
        else:
            return ()
        if element.occurrence_index > 1:
            selector["index"] = element.occurrence_index - 1

        command_name = "tapOn" if task.action == ExplorationAction.TAP else "longPressOn"
        return ({command_name: selector},)

    def _enter_state(self, state_id: str, entered_by_task_id: str) -> None:
        if any(frame.state_id == state_id for frame in self.checkpoint.stack):
            self._reconcile_observed_state_id(state_id)
            return
        depth = len(self.checkpoint.stack)
        if depth >= self.budget.max_depth:
            logger.info("Depth budget reached; not entering state %s", state_id)
            self._restore_state(self.checkpoint.stack[-1].state_id)
            return
        self.checkpoint.stack.append(
            ExplorationStackFrame(
                state_id=state_id,
                entered_by_task_id=entered_by_task_id,
                depth=depth,
            )
        )
        if state_id in self._uncounted_new_state_ids:
            self._uncounted_new_state_ids.remove(state_id)
            self.checkpoint.states_created += 1
        self.checkpoint.updated_at = utc_now()

    def _pop_completed_frame(self) -> None:
        if len(self.checkpoint.stack) <= 1:
            current_frame = self.checkpoint.stack[0]
            self._refresh_page_coverage(current_frame.state_id)
            statuses = {
                task.status
                for task in self.task_store.list()
            }
            if ExplorationTaskStatus.NEEDS_HUMAN in statuses:
                self._set_status(ExplorationStatus.NEEDS_HUMAN)
            else:
                self._set_status(ExplorationStatus.COMPLETED)
            self.checkpoint.stack.clear()
            self._save_checkpoint()
            return

        completed_state_id = self.checkpoint.stack[-1].state_id
        self.checkpoint.stack.pop()
        self.checkpoint.updated_at = utc_now()
        self._refresh_page_coverage(completed_state_id)
        parent_state_id = self.checkpoint.stack[-1].state_id
        self._restore_state(parent_state_id)
        self._save_checkpoint()

    def _reconcile_observed_state(
        self,
        observed_state: ScreenStateRecord,
    ) -> bool:
        stack_ids = [frame.state_id for frame in self.checkpoint.stack]
        if observed_state.state_id in stack_ids:
            target_index = stack_ids.index(observed_state.state_id)
            del self.checkpoint.stack[target_index + 1 :]
            self.checkpoint.updated_at = utc_now()
            self._save_checkpoint()
            return True

        desired_state_id = self.checkpoint.stack[-1].state_id
        return self._restore_state(desired_state_id)

    def _reconcile_observed_state_id(self, state_id: str) -> None:
        stack_ids = [frame.state_id for frame in self.checkpoint.stack]
        if state_id in stack_ids:
            target_index = stack_ids.index(state_id)
            del self.checkpoint.stack[target_index + 1 :]
            self.checkpoint.updated_at = utc_now()
            self._save_checkpoint()

    def _restore_state(self, state_id: str) -> bool:
        if self.checkpoint.restore_attempts >= self.budget.max_restore_attempts:
            self._record_fatal("restore attempt budget exceeded")
            return False

        self.checkpoint.restore_attempts += 1
        self.driver.back(timeout=self.back_timeout)
        try:
            observed = self._observe(required=False)
        except (ValueError, ExternalAppObservationError):
            observed = None
        if observed is not None and observed.package_name == self.target_app_id:
            state = self._register_observation(observed)
            if state.state_id == state_id:
                self._save_checkpoint()
                return True

        restart = self.driver.restart_app(timeout=self.launch_timeout)
        if not restart.get("success"):
            self._record_fatal("app restart failed during state restoration")
            return False

        replay_state = self._observe_and_register()
        if replay_state is None:
            self._record_fatal("app did not return to the DFS entry state")
            return False
        stack_ids = [frame.state_id for frame in self.checkpoint.stack]
        if replay_state.state_id != stack_ids[0]:
            # A restart may land on a later persistent state when the original root
            # was a one-time first-run dialog. Preserve the verified prefix.
            if replay_state.state_id not in stack_ids:
                self.checkpoint.last_error = (
                    "app restarted on a state outside the DFS stack; "
                    "start a continuation run from the current state"
                )
                self._set_status(ExplorationStatus.NEEDS_HUMAN)
                return False
            reached_index = stack_ids.index(replay_state.state_id)
            del self.checkpoint.stack[reached_index + 1 :]
            self.checkpoint.updated_at = utc_now()
            self._save_checkpoint()
            return True

        for frame in self.checkpoint.stack[1:]:
            if frame.entered_by_task_id is None:
                self._record_fatal("cannot replay a frame without an entering task")
                return False
            task = self.task_store.get(frame.entered_by_task_id)
            element = self.store.get_element(task.element_id) if task is not None else None
            if task is None or element is None:
                self._record_fatal("cannot replay missing task or element")
                return False
            result = self._execute_task(task, element)
            if not result.get("success"):
                self._record_fatal("task replay failed during restoration")
                return False
            replayed = self._observe_and_register()
            if replayed is None or replayed.state_id != frame.state_id:
                self._record_fatal("replayed task did not reach the expected state")
                return False

        self._save_checkpoint()
        return True

    def _within_budget(self) -> bool:
        state_count = self.store.statistics()["screen_states"]
        return (
            self.checkpoint.actions_executed < self.budget.max_actions
            and state_count <= self.budget.max_states
            and self.checkpoint.errors < self.budget.max_errors
            and self.checkpoint.consecutive_noops
            < self.budget.max_consecutive_noops
            and self.checkpoint.restore_attempts
            < self.budget.max_restore_attempts
        )

    def _set_status(self, status: ExplorationStatus) -> ExplorationCheckpoint:
        self.checkpoint.status = status
        self.checkpoint.updated_at = utc_now()
        self._save_checkpoint()
        return self.checkpoint

    def _record_fatal(self, message: str) -> None:
        self.checkpoint.status = ExplorationStatus.ERROR
        self.checkpoint.last_error = message
        self.checkpoint.errors += 1
        self.checkpoint.updated_at = utc_now()
        self._save_checkpoint()
        logger.error("Exploration fatal error: %s", message)

    def _save_task(self, task: ExplorationTask) -> None:
        self.task_store.upsert(
            task,
            preserve_existing_progress=False,
        )

    def _recover_interrupted_tasks(self) -> None:
        for task in self.task_store.list(
            status=ExplorationTaskStatus.RUNNING,
        ):
            self._save_task(task.with_update(status=ExplorationTaskStatus.PENDING))

    def _save_checkpoint(self) -> None:
        self.checkpoint.updated_at = utc_now()
        self.store.workspace.write_json_atomic(
            self.checkpoint_path,
            self.checkpoint.to_dict(),
        )
