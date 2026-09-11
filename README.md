# Aegis Cartographer

Aegis Cartographer is a mobile App mapping agent. It explores Android and iOS
applications through Maestro and builds reusable, project-local element maps for
AI-driven testing.

For a complete end-to-end guide, see [USAGE.md](USAGE.md).

AI agents should also read [AGENTS.md](AGENTS.md) for operational boundaries,
login ownership, exploration resume procedures, and safe CLI usage.

For the v2 requirements and architecture decisions, see
[REQUIREMENTS.md](REQUIREMENTS.md).

The project is organized into separated layers:

- **Core engine**: hierarchy normalization, screen identity, element identity,
  exploration policy, safety, and map storage.
- **Device adapters**: Maestro execution and structured action results.
- **MCP/CLI interfaces**: project-local map control and read-only queries.

Maps are stored as project-local artifacts under `.aegis/maps/`, isolated by
application, platform, version, build, and locale. Temporary exploration state is
kept under `.aegis/runs/` and logs under `.aegis/logs/`, so generated maps remain
clean and portable.

Example business-project layout:

```text
.aegis/
├── config.json
├── maps/
│   └── com.example/android/8.2.0+12000/zh-CN/
│       ├── manifest.json
│       ├── map.sqlite
│       └── exports/map.json
├── runs/
└── logs/
```

## Development

```bash
python -m pip install -e '.[dev]'
PYTHONPATH=src python -m pytest
```

## CLI

Initialize a business project:

```bash
aegis init \
  --project /path/to/app-project \
  --app-id com.example \
  --platform android \
  --app-version 1.2.0 \
  --build-number 12000 \
  --locale zh-CN
```

Start a background exploration worker:

```bash
aegis explore start --project /path/to/app-project
aegis explore status --project /path/to/app-project --run-id <run-id>
aegis explore resume --project /path/to/app-project --run-id <run-id>
```

Query the project-local element map:

```bash
aegis map list --project /path/to/app-project
aegis map query --project /path/to/app-project "修改收货地址"
```

Export and validate a portable map archive:

```bash
aegis map export --project /path/to/app-project
aegis map validate --archive /path/to/map.aegis-map.zip
```

## MCP

The default `aegis-server` entry point runs the modern MCP facade in
`aegis_cartographer.mcp_server`. It exposes read-only project map queries and
asynchronous exploration-worker controls. See `MCP_CONFIG.md` for configuration.
