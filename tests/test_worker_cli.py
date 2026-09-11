from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

from aegis_cartographer.aegis_cli import main as cli_main
from aegis_cartographer.core import Platform
from aegis_cartographer.core.storage import (
    MapId,
    MapStore,
    ProjectWorkspace,
)
from aegis_cartographer.worker.artifacts import export_map_archive, validate_map_archive
from aegis_cartographer.worker.config import (
    MaestroConfig,
    ProjectConfig,
    load_project_config,
    save_project_config,
)
from aegis_cartographer.worker.coverage import task_report
from aegis_cartographer.worker.current import identify_current_page
from aegis_cartographer.worker.runner import _safety_from_config, run_exploration
from aegis_cartographer.worker.selector import parse_map_selector
from tests.test_exploration_engine import APP_ID, SCREEN_A, FakeDriver

MAP_ID = MapId(APP_ID, Platform.ANDROID, "11.0.0", "1100", "zh-CN")


def project_config() -> ProjectConfig:
    return ProjectConfig(
        app_id=MAP_ID.app_id,
        platform=MAP_ID.platform,
        app_version=MAP_ID.app_version,
        build_number=MAP_ID.build_number,
        locale=MAP_ID.locale,
        maestro=MaestroConfig(command_timeout=1.0),
        exploration=ProjectConfig().exploration,
    )


def create_explored_workspace(tmp_path: Path) -> ProjectWorkspace:
    workspace = ProjectWorkspace(tmp_path)
    save_project_config(workspace, project_config())
    with MapStore.create(workspace, MAP_ID):
        pass
    result = run_exploration(
        workspace=workspace,
        map_id=MAP_ID,
        run_id="run-worker",
        config=project_config(),
        chunk_steps=1,
        driver_factory=lambda **kwargs: FakeDriver(),
    )
    assert result["status"] == "COMPLETED"
    return workspace


def test_project_config_round_trip_and_validation(tmp_path: Path) -> None:
    workspace = ProjectWorkspace(tmp_path)
    config = project_config()
    config.safety.blocked_keywords.append("项目专属危险词")

    path = save_project_config(workspace, config)
    loaded = load_project_config(workspace)

    assert loaded is not None
    assert loaded.default_map_id() == MAP_ID
    assert loaded.safety.blocked_keywords == ["项目专属危险词"]
    assert loaded.maestro.action_timeout == 40.0
    assert loaded.maestro.hierarchy_timeout == 20.0
    assert loaded.maestro.hierarchy_retries == 3
    assert loaded.maestro.hierarchy_retry_backoff_ms == 500
    assert loaded.maestro.animation_wait_timeout_ms == 3000
    assert loaded.maestro.adb_path is None
    assert path == workspace.config_path

    loaded.exploration.max_actions = 0
    with pytest.raises(ValueError, match="max_actions"):
        loaded.validate()
    loaded.exploration.max_actions = 1
    loaded.maestro.hierarchy_retries = 0
    with pytest.raises(ValueError, match="hierarchy_retries"):
        loaded.validate()


def test_map_selector_round_trip() -> None:
    selector = "com.example.app@android:11.0.0+1100/zh-CN"
    assert parse_map_selector(selector) == MAP_ID
    with pytest.raises(ValueError, match="Map selector"):
        parse_map_selector("invalid-selector")


def test_project_safety_keywords_extend_builtin_defaults() -> None:
    config = project_config()
    config.safety.blocked_keywords.append("项目专属危险词")

    policy = _safety_from_config(config)

    assert "项目专属危险词" in policy.blocked_keywords
    assert "删除" in policy.blocked_keywords
    assert "支付" in policy.blocked_keywords
    assert policy.allow_input_text is False


def test_run_exploration_uses_chunked_resume_and_clean_map(
    tmp_path: Path,
) -> None:
    workspace = create_explored_workspace(tmp_path)

    checkpoint = json.loads(
        (workspace.runs_dir / "run-worker" / "checkpoint.json").read_text(encoding="utf-8")
    )
    with MapStore.open(workspace, MAP_ID, readonly=True) as store:
        tables = {
            str(row["name"])
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert store.statistics()["screen_states"] == 2

    assert checkpoint["status"] == "COMPLETED"
    assert "exploration_tasks" not in tables
    assert (workspace.runs_dir / "run-worker" / "tasks.sqlite").is_file()


def test_cli_init_map_list_and_query(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = create_explored_workspace(tmp_path)
    default_config = workspace.config_path.read_text(encoding="utf-8")
    workspace.config_path.unlink()

    assert (
        cli_main(
            [
                "init",
                "--project",
                str(tmp_path),
                "--app-id",
                APP_ID,
                "--platform",
                "android",
                "--app-version",
                "11.0.0",
                "--build-number",
                "1100",
                "--locale",
                "zh-CN",
                "--command-timeout",
                "1",
                "--blocked-keyword",
                "项目专属危险词",
            ]
        )
        == 0
    )
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["success"] is True
    assert "项目专属危险词" in workspace.config_path.read_text(encoding="utf-8")
    _ = default_config

    assert cli_main(["map", "list", "--project", str(tmp_path)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["maps"] == [MAP_ID.cache_key]

    assert (
        cli_main(
            [
                "map",
                "query",
                "--project",
                str(tmp_path),
                "详情按钮",
                "--include-target-tap",
            ]
        )
        == 0
    )
    query = json.loads(capsys.readouterr().out)
    assert query["count"] >= 1
    assert query["results"][0]["path"] is not None
    assert "tapOn" in query["target_tap_flow"]


def test_cli_map_route_plans_between_named_pages(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = create_explored_workspace(tmp_path)

    assert (
        cli_main(
            [
                "map",
                "route",
                "--project",
                str(workspace.root),
                "--from",
                "首页",
                "--to",
                "详情页",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)

    assert result["success"] is True
    assert result["reachable"] is True
    assert result["from_page"]["name"] == "首页"
    assert result["to_page"]["name"] == "详情页"
    assert result["path"]["maestro_flow"]


def test_map_archive_export_and_validation(tmp_path: Path) -> None:
    workspace = create_explored_workspace(tmp_path)

    archive = export_map_archive(workspace, MAP_ID)
    result = validate_map_archive(archive)

    assert archive.is_file()
    assert result["valid"] is True
    assert result["map_id"] == MAP_ID.cache_key
    assert result["screen_states"] == 2
    assert result["elements"] > 0


def test_map_archive_contains_page_cards_without_project_fixtures(tmp_path: Path) -> None:
    workspace = create_explored_workspace(tmp_path)
    accounts = workspace.root / ".aegis" / "accounts.md"
    accounts.parent.mkdir(parents=True, exist_ok=True)
    accounts.write_text(
        """
# 测试账号

## 正确账号

- id: valid_main
- 用户名: 190-0000-0068
- 密码: 999000
""",
        encoding="utf-8",
    )

    archive = export_map_archive(
        workspace,
        MAP_ID,
    )

    with zipfile.ZipFile(archive, "r") as bundle:
        pages = json.loads(bundle.read("aegis-map/pages.json"))
        names = set(bundle.namelist())

    assert pages["page_count"] >= 1
    assert all(page["name"] for page in pages["pages"])
    assert "aegis-map/accounts.md" not in names


def test_pending_task_report_groups_historical_tasks_by_page(
    tmp_path: Path,
) -> None:
    workspace = create_explored_workspace(tmp_path)
    store = MapStore.open(workspace, MAP_ID, readonly=True)
    try:
        report = task_report(workspace, store)
    finally:
        store.close()

    assert report["unfinished_tasks"] == 0
    assert report["task_counts"]["DONE"] > 0
    assert report["pages"]
    assert all(page["name"] for page in report["pages"])


def test_current_page_can_be_matched_without_mutating_map(tmp_path: Path) -> None:
    workspace = create_explored_workspace(tmp_path)

    class CurrentDriver:
        def wait_until_stable(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "success": True,
                "stable": True,
                "hierarchy": SCREEN_A,
                "last_hierarchy_error": None,
            }

        def get_hierarchy(self) -> dict[str, Any]:
            return SCREEN_A

    result = identify_current_page(
        workspace=workspace,
        map_id=MAP_ID,
        config=project_config(),
        driver_factory=lambda **kwargs: CurrentDriver(),  # type: ignore[return-value]
    )

    assert result["success"] is True
    assert result["match_type"] == "same_state"
    assert result["state_id"]
    assert result["page"]["name"]


def test_cli_start_spawns_detached_worker_and_records_metadata(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = create_explored_workspace(tmp_path)

    class FakeProcess:
        pid = 999999

    def fake_popen(*args: Any, **kwargs: Any) -> FakeProcess:
        captured_command.extend(args[0])
        return FakeProcess()

    captured_command: list[str] = []
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    assert (
        cli_main(
            [
                "explore",
                "start",
                "--project",
                str(tmp_path),
                "--run-id",
                "run-detached",
                "--chunk-steps",
                "2",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["pid"] == 999999
    assert output["status"] == "RUNNING"
    assert "aegis_cartographer.worker" in captured_command
    assert "--run-id" in captured_command
    assert workspace.runs_dir.joinpath("run-detached", "run.json").is_file()
    assert workspace.logs_dir.joinpath("runs", "run-detached.log").is_file()


def test_cli_status_reports_checkpoint_and_task_statuses(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    create_explored_workspace(tmp_path)

    assert cli_main(["explore", "status", "--project", str(tmp_path), "--run-id", "run-worker"]) == 0
    status = json.loads(capsys.readouterr().out)

    assert status["checkpoint"]["status"] == "COMPLETED"
    assert status["task_statuses"]["DONE"] > 0
    assert status["map_statistics"]["screen_states"] == 2
    assert status["process_alive"] is False
