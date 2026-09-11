from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aegis_cartographer.core import (
    Platform,
    extract_ui_elements,
    observe_screen,
)
from aegis_cartographer.core.storage import (
    ActionResultType,
    ElementLocator,
    ExplorationAction,
    MapId,
    MapManifest,
    MapStore,
    ProjectWorkspace,
)
from tests.test_core_identity import APP_ID, page_payload


@pytest.fixture()
def workspace(tmp_path: Path) -> ProjectWorkspace:
    return ProjectWorkspace(tmp_path)


@pytest.fixture()
def map_id() -> MapId:
    return MapId(
        app_id=APP_ID,
        platform=Platform.ANDROID,
        app_version="8.2.0",
        build_number="12000",
        locale="zh-CN",
    )


def populated_store(workspace: ProjectWorkspace, map_id: MapId) -> MapStore:
    store = MapStore.create(workspace, map_id)
    observation = observe_screen(page_payload(), target_app_id=APP_ID)
    state = store.record_screen(
        observation,
        semantic_name="订单列表页",
        business_domain="订单",
    )
    element = extract_ui_elements(observation.hierarchy)[0]
    store.record_element(
        state,
        element,
        semantic_name="查看订单详情",
        aliases=["订单详情", "查看详情"],
        canonical_element_id="order_list.view_detail",
        risk_level="low",
    )
    store.record_observation(
        observation,
        run_id="run-001",
        state_id=state.state_id,
        device_id="emulator-5554",
        account_state="logged_in",
        network_state="online",
    )
    return store


def test_project_workspace_separates_maps_runs_and_logs(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)
    store.close()

    assert workspace.aegis_dir.is_dir()
    assert workspace.maps_dir.is_dir()
    assert workspace.runs_dir.is_dir()
    assert workspace.logs_dir.is_dir()
    map_dir = workspace.map_directory(map_id)
    assert (map_dir / "manifest.json").is_file()
    assert (map_dir / "map.sqlite").is_file()
    assert not (map_dir / ".write.lock").exists()


def test_page_cards_and_human_metadata_are_queryable(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)

    pages = store.list_page_summaries()
    assert len(pages) == 1
    assert pages[0].name == "订单列表页"
    assert "订单列表" in pages[0].description
    assert pages[0].coverage_status == "OBSERVED"
    assert pages[0].element_count == 1

    updated = store.update_screen_card(
        pages[0].screen_id,
        name="订单明细",
        description="查看全部订单，并进入订单详情",
        aliases=["订单", "流水"],
        coverage_status="BASELINE_DONE",
    )
    assert updated.semantic_name == "订单明细"
    assert updated.description == "查看全部订单，并进入订单详情"
    assert updated.aliases == ("订单", "流水")
    assert updated.coverage_status == "BASELINE_DONE"
    assert store.list_page_summaries()[0].name == "订单明细"
    store.close()


def test_default_locators_ignore_whitespace_only_identifiers(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)
    observation = observe_screen(page_payload(), target_app_id=APP_ID)
    state = store.list_screen_states()[0]
    element = extract_ui_elements(observation.hierarchy)[0]
    element = type(element)(
        **{
            **element.to_dict(),
            "text": "   ",
            "resource_id": "   ",
            "accessibility_id": "   ",
            "content_desc": "   ",
            "bounds": element.bounds,
            "parent_path": list(element.parent_path),
        }
    )

    locators = store.default_locators(element, state.signature.platform)

    assert [locator.strategy for locator in locators] == ["point"]
    store.close()


def test_map_identity_is_isolated_by_platform_and_version(
    workspace: ProjectWorkspace,
) -> None:
    android = MapId(APP_ID, Platform.ANDROID, "8.2.0", "12000", "zh-CN")
    ios = MapId(APP_ID, Platform.IOS, "8.2.0", "12000", "zh-CN")
    upgrade = MapId(APP_ID, Platform.ANDROID, "8.3.0", "13000", "zh-CN")

    for identity in (android, ios, upgrade):
        store = populated_store(workspace, identity)
        store.close()

    maps = MapStore.list_maps(workspace)
    assert set(maps) == {android, ios, upgrade}
    assert len(list(workspace.maps_dir.glob("**/map.sqlite"))) == 3

    android_store = MapStore.open(workspace, android, readonly=True)
    assert android_store.statistics()["elements"] == 1
    android_store.close()


def test_map_creation_never_overwrites_existing_artifact(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)
    store.close()

    with pytest.raises(FileExistsError):
        MapStore.create(workspace, map_id)


def test_manifest_and_database_identity_are_verified(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)
    store.close()

    manifest_data = workspace.read_json(store.manifest_path)
    assert MapManifest.from_dict(manifest_data).map_id == map_id

    other = MapId(APP_ID, Platform.ANDROID, "8.3.0", "13000", "zh-CN")
    with pytest.raises(FileNotFoundError):
        MapStore.open(workspace, other, readonly=True)

    manifest_data["map_id"]["app_version"] = "tampered"
    workspace.write_json_atomic(store.manifest_path, manifest_data)
    with pytest.raises(ValueError, match="Manifest map identity does not match"):
        MapStore.open(workspace, map_id, readonly=True)


def test_semantic_names_are_not_overwritten_by_refresh(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)
    observation = observe_screen(page_payload(), target_app_id=APP_ID)
    state = store.list_screen_states()[0]

    refreshed_state = store.record_screen(observation)
    refreshed_element = store.record_element(
        state,
        extract_ui_elements(observation.hierarchy)[0],
    )

    screen_row = store.connection.execute(
        "SELECT semantic_name FROM screens WHERE screen_id = ?",
        (refreshed_state.screen_id,),
    ).fetchone()
    assert screen_row["semantic_name"] == "订单列表页"
    assert refreshed_element.semantic_name == "查看订单详情"
    assert refreshed_element.canonical_element_id == "order_list.view_detail"
    store.close()


def test_element_search_matches_semantics_aliases_and_ids(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)

    for query in ("查看订单详情", "订单详情", "com.example:id/detail"):
        results = store.search_elements(query)
        assert results
        assert results[0].element.semantic_name == "查看订单详情"
        assert results[0].matched_source in {"fts", "substring"}

    assert store.search_elements("") == []
    store.close()


def test_default_locators_include_stable_and_fallback_strategies(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)
    element = store.list_elements()[0]

    strategies = [(locator.strategy, locator.priority) for locator in element.locators]
    assert ("id", 10) in strategies
    assert ("text", 40) in strategies
    assert ("point", 900) in strategies
    assert element.actions == (ExplorationAction.TAP, ExplorationAction.LONG_PRESS)
    assert element.risk_level == "low"
    store.close()


def test_back_navigation_does_not_generate_long_press(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)
    state = store.list_screen_states()[0]
    payload = {
        "package": APP_ID,
        "tree": {
            "class": "android.widget.FrameLayout",
            "children": [
                {
                    "class": "android.widget.ImageButton",
                    "content-desc": "转到上一层级",
                    "clickable": True,
                    "bounds": "[0,138][168,264]",
                }
            ],
        },
    }
    observation = observe_screen(payload, target_app_id=APP_ID)
    element = extract_ui_elements(observation.hierarchy)[0]

    stored = store.record_element(state, element)

    assert stored.actions == (ExplorationAction.TAP,)
    store.close()


def test_cross_platform_locator_is_rejected(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)
    state = store.list_screen_states()[0]
    element = extract_ui_elements(
        observe_screen(page_payload(), target_app_id=APP_ID).hierarchy
    )[0]

    with pytest.raises(ValueError, match="does not match map platform"):
        store.record_element(
            state,
            element,
            locators=(
                ElementLocator(
                    platform=Platform.IOS,
                    strategy="accessibility_id",
                    value="bad",
                ),
            ),
        )
    store.close()


def test_observations_reject_absolute_project_paths(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)
    observation = observe_screen(page_payload(), target_app_id=APP_ID)
    state = store.list_screen_states()[0]

    with pytest.raises(ValueError, match="relative to the map artifact"):
        store.record_observation(
            observation,
            run_id="run-002",
            state_id=state.state_id,
            screenshot_path="/tmp/screen.png",
        )
    with pytest.raises(ValueError, match="screenshots directory"):
        store.record_observation(
            observation,
            run_id="run-003",
            state_id=state.state_id,
            screenshot_path="../screen.png",
        )
    store.close()


def test_transition_upsert_and_validation(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)
    source = store.list_screen_states()[0]
    target_observation = observe_screen(
        page_payload(extra_switch=True),
        target_app_id=APP_ID,
    )
    target = store.record_screen(
        target_observation,
        semantic_name="订单列表页-筛选",
        screen_id=source.screen_id,
    )
    element = store.list_elements(state_id=source.state_id)[0]

    transition = store.record_transition(
        from_state_id=source.state_id,
        element_id=element.element_id,
        action=ExplorationAction.TAP,
        result_type=ActionResultType.STATE_CHANGE,
        to_state_id=target.state_id,
        maestro_commands=({"tapOn": {"id": element.resource_id}},),
        restore_strategy=({"pressKey": "BACK"},),
    )
    repeated = store.record_transition(
        from_state_id=source.state_id,
        element_id=element.element_id,
        action=ExplorationAction.TAP,
        result_type=ActionResultType.STATE_CHANGE,
        to_state_id=target.state_id,
    )

    assert repeated.transition_id == transition.transition_id
    assert repeated.observed_count == 2
    assert repeated.success_count == 2
    assert store.statistics()["paths"] == 1
    assert store.materialize_paths() == 0


def test_materialize_paths_caches_multi_step_entry_routes(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)
    source = store.list_screen_states()[0]
    middle = store.record_screen(
        observe_screen(page_payload(extra_switch=True), target_app_id=APP_ID),
        semantic_name="订单列表页-筛选",
        screen_id=source.screen_id,
    )
    target = store.record_screen(
        observe_screen(
            page_payload(title="订单详情", dynamic_value="订单 A10002"),
            target_app_id=APP_ID,
        ),
        semantic_name="订单详情页",
    )
    element = store.list_elements(state_id=source.state_id)[0]
    store.set_entry_state(source.state_id)
    store.record_transition(
        from_state_id=source.state_id,
        element_id=element.element_id,
        action=ExplorationAction.TAP,
        result_type=ActionResultType.STATE_CHANGE,
        to_state_id=middle.state_id,
        maestro_commands=({"tapOn": {"id": "com.example:id/detail"}},),
    )
    store.record_transition(
        from_state_id=middle.state_id,
        action=ExplorationAction.TAP,
        result_type=ActionResultType.NEW_PAGE,
        to_state_id=target.state_id,
        maestro_commands=({"tapOn": {"text": "订单 A10001"}},),
    )

    created = store.materialize_paths()
    route = store.connection.execute(
        """
        SELECT steps_json FROM paths
        WHERE from_id = ? AND target_id = ?
        """,
        (source.state_id, target.state_id),
    ).fetchone()

    assert created >= 1
    assert route is not None
    steps = json.loads(route["steps_json"])
    assert len(steps) == 2
    assert steps[0]["to_state_id"] == middle.state_id
    assert steps[1]["to_state_id"] == target.state_id
    store.close()

    with pytest.raises(ValueError, match="requires to_state_id"):
        store.record_transition(
            from_state_id=source.state_id,
            element_id=element.element_id,
            action=ExplorationAction.TAP,
            result_type=ActionResultType.NEW_PAGE,
        )
    with pytest.raises(ValueError, match="must not have to_state_id"):
        store.record_transition(
            from_state_id=source.state_id,
            action=ExplorationAction.BACK,
            result_type=ActionResultType.EXTERNAL_APP,
            to_state_id=target.state_id,
        )
    store.close()


def test_null_identity_transitions_are_deduplicated(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)
    state = store.list_screen_states()[0]

    first = store.record_transition(
        from_state_id=state.state_id,
        action=ExplorationAction.BACK,
        result_type=ActionResultType.NO_OP,
    )
    second = store.record_transition(
        from_state_id=state.state_id,
        action=ExplorationAction.BACK,
        result_type=ActionResultType.NO_OP,
        success=False,
    )

    assert second.transition_id == first.transition_id
    assert second.observed_count == 2
    assert second.success_count == 1
    store.close()


def test_readonly_store_can_query_but_not_write(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    writer = populated_store(workspace, map_id)
    writer.close()

    store = MapStore.open(workspace, map_id, readonly=True)
    assert store.statistics()["elements"] == 1
    assert store.search_elements("查看订单详情")

    observation = observe_screen(page_payload(), target_app_id=APP_ID)
    with pytest.raises(sqlite3.OperationalError, match="read-only"):
        store.record_screen(observation)
    store.close()


def test_json_export_is_self_contained_and_relative(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)
    element = store.list_elements()[0]
    store.record_element_embedding(
        element.element_id,
        [1.0, 0.0],
        model_name="test-model",
        model_version="1",
        searchable_text="查看订单详情",
    )
    export_path = store.export_json()

    assert export_path == store.map_directory / "exports" / "map.json"
    data = json.loads(export_path.read_text(encoding="utf-8"))
    assert data["manifest"]["map_id"] == map_id.to_dict()
    assert data["statistics"]["elements"] == 1
    assert str(workspace.root) not in json.dumps(data)
    store.close()


def test_map_local_embeddings_support_vector_search(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)
    element = store.list_elements()[0]
    store.record_element_embedding(
        element.element_id,
        [0.8, 0.2],
        model_name="semantic-model",
        model_version="1.2",
        searchable_text="查看订单详情",
    )

    results = store.search_elements_by_vector(
        [1.0, 0.0],
        model_name="semantic-model",
        model_version="1.2",
    )
    assert results[0].element.element_id == element.element_id
    assert results[0].matched_source == "embedding:semantic-model:1.2"
    assert results[0].score > 0.95

    assert store.search_elements_by_vector(
        [1.0, 0.0],
        model_name="other-model",
        model_version="1.2",
    ) == []
    with pytest.raises(ValueError, match="finite"):
        store.search_elements_by_vector(
            [math.nan, 1.0],
            model_name="semantic-model",
            model_version="1.2",
        )
    store.close()


def test_screen_search_returns_screen_and_states(
    workspace: ProjectWorkspace,
    map_id: MapId,
) -> None:
    store = populated_store(workspace, map_id)
    results = store.search_screens("订单列表页")

    assert results
    assert results[0].semantic_name == "订单列表页"
    assert len(results[0].state_ids) == 1
    store.close()


def test_map_id_validations_and_safe_component_hashing() -> None:
    with pytest.raises(ValueError):
        MapId("../evil", Platform.ANDROID, "1.0", "1")
    with pytest.raises(ValueError):
        MapId("", Platform.ANDROID, "1.0", "1")

    unsafe = MapId("应用 名称", Platform.ANDROID, "版本 8.2", "12000", "中文")
    relative = unsafe.relative_directory
    assert not any(part in {"", ".", ".."} for part in relative.parts)
    assert relative.parts[-4].startswith("_____--")


def test_workspace_rejects_absolute_and_escaping_resources(
    workspace: ProjectWorkspace,
) -> None:
    with pytest.raises(ValueError, match="project-relative"):
        workspace.resolve_resource("/tmp/screen.png")
    with pytest.raises(ValueError, match="escapes"):
        workspace.resolve_resource("../outside/screen.png")


def test_workspace_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / ".aegis").mkdir()
    (project / ".aegis" / "maps").symlink_to(outside, target_is_directory=True)

    workspace = ProjectWorkspace(project)
    with pytest.raises(ValueError, match="symlink"):
        workspace.ensure_layout()


def test_failed_initialization_does_not_leave_partial_map(
    workspace: ProjectWorkspace,
    map_id: MapId,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_manifest(*args: Any, **kwargs: Any) -> None:
        raise OSError("manifest write failed")

    monkeypatch.setattr(ProjectWorkspace, "write_json_atomic", fail_manifest)
    with pytest.raises(OSError, match="manifest write failed"):
        MapStore.create(workspace, map_id)

    assert not workspace.map_directory(map_id).exists()
