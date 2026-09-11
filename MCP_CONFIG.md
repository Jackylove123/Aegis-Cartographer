# Aegis Cartographer MCP 配置指南

Aegis MCP 是新架构的薄适配层：

- 长时间遍历由项目本地 Worker 执行；
- 元素地图保存在业务项目 `.aegis/maps/`；
- MCP 查询默认只读；
- 遍历控制工具只启动 / 恢复 / 暂停 / 停止后台 run，不在 MCP 调用中等待遍历完成。

## 方式 1：使用 uv（推荐）

```json
{
  "mcpServers": {
    "aegis": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/Aegis-Cartographer",
        "run",
        "aegis-server"
      ]
    }
  }
}
```

## 方式 2：使用 python3

```json
{
  "mcpServers": {
    "aegis": {
      "command": "python3",
      "args": [
        "-m",
        "aegis_cartographer"
      ],
      "env": {
        "PYTHONPATH": "/path/to/Aegis-Cartographer/src"
      }
    }
  }
}
```

## 地图查询工具

| 工具名 | 功能 |
|---|---|
| `list_project_maps` | 列出业务项目内所有独立地图 |
| `get_map_metadata` | 查看地图 manifest、统计信息和入口状态 |
| `query_elements` | 按语义 / 文案 / ID 查询元素 |
| `query_screens` | 查询页面和页面状态 |
| `locate_element` | 返回最佳元素匹配、locator、路径和 Maestro flow |
| `get_element_locators` | 按 element_id 获取全部 locator 和验证统计 |
| `get_path_to_element` | 规划到指定元素的路径 |
| `get_path_to_screen` | 规划到指定页面的路径 |
| `generate_maestro_flow` | 生成安全 Maestro flow |
| `export_map` | 导出项目本地 `.aegis-map.zip` |

## 遍历控制工具

| 工具名 | 功能 |
|---|---|
| `start_exploration` | 启动后台 DFS Worker，立即返回 |
| `get_exploration_status` | 查看 checkpoint、任务状态、地图统计和进程状态 |
| `resume_exploration` | 恢复暂停或中断的 run |
| `pause_exploration` | 请求 Worker 安全暂停 |
| `stop_exploration` | 请求 Worker 安全停止 |
| `get_coverage_report` | 获取覆盖率报告 |

## 快速开始

先在业务项目初始化配置：

```bash
aegis init \
  --project /path/to/app-project \
  --app-id com.example \
  --platform android \
  --app-version 1.2.0 \
  --build-number 12000 \
  --locale zh-CN
```

然后通过 MCP 查询：

```text
请调用 locate_element：
project_root = /path/to/app-project
query = 修改收货地址
```

启动后台遍历：

```text
请调用 start_exploration：
project_root = /path/to/app-project
max_actions = 500
```

## 注意事项

1. `project_root` 必须是业务项目绝对路径，不是 Aegis 源码目录。
2. 正式地图在业务项目 `.aegis/maps/`。
3. 运行任务和 checkpoint 在业务项目 `.aegis/runs/`。
4. Worker 日志在业务项目 `.aegis/logs/runs/`。
5. 旧版 `start_traversal / get_next_action_v2 / report_execution` 手工遍历工具已从默认 MCP 入口移除。
6. 默认 MCP 入口现在是 `aegis_cartographer.mcp_server.serve`。
