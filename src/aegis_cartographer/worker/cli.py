"""Argument parser for the detached exploration worker."""

from __future__ import annotations

import argparse
import json

from aegis_cartographer.core.models import Platform
from aegis_cartographer.core.storage import ProjectWorkspace
from aegis_cartographer.worker.config import load_project_config
from aegis_cartographer.worker.runner import run_exploration
from aegis_cartographer.worker.selector import MapSelection, resolve_map_id


def build_parser() -> argparse.ArgumentParser:
    """Build the worker CLI parser."""

    parser = argparse.ArgumentParser(
        prog="python -m aegis_cartographer.worker",
        description="Run one Aegis exploration job",
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--map", dest="map_selector")
    parser.add_argument("--app-id")
    parser.add_argument("--platform", choices=[item.value for item in Platform])
    parser.add_argument("--app-version")
    parser.add_argument("--build-number")
    parser.add_argument("--locale")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-actions", type=int)
    parser.add_argument("--chunk-steps", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the worker CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    workspace = ProjectWorkspace(args.project)
    config = load_project_config(workspace)
    map_id = resolve_map_id(
        config,
        MapSelection(
            selector=args.map_selector,
            app_id=args.app_id,
            platform=Platform(args.platform) if args.platform else None,
            app_version=args.app_version,
            build_number=args.build_number,
            locale=args.locale,
        ),
    )
    if config is None:
        raise ValueError("Worker requires a project config")
    result = run_exploration(
        workspace=workspace,
        map_id=map_id,
        run_id=args.run_id,
        config=config,
        max_actions=args.max_actions,
        chunk_steps=args.chunk_steps,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] != "ERROR" else 1


if __name__ == "__main__":
    raise SystemExit(main())
