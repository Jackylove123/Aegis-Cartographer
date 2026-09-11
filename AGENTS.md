# AGENTS.md — Aegis Cartographer AI 使用说明

这份文档写给 AI 测试代理（例如 Codex）和自动化工具。执行 Aegis 相关任务前，先完整理解本文。

## 1. Aegis 是什么

Aegis Cartographer 是一个通用的移动 App 元素地图工具：

```text
读取 Maestro/ADB 页面层级
        ↓
识别页面和页面状态
        ↓
提取元素与 locator
        ↓
执行安全遍历并记录 transition
        ↓
生成可查询、可导出的项目本地元素地图
```

Aegis 的核心职责：

- 页面识别与页面状态管理
- 元素提取和 locator 记录
- 页面路径规划
- 遍历任务执行
- 中断状态和任务账本记录
- 覆盖率与待办任务查询
- 地图导出和校验
- 通过 CLI/MCP 给 AI 提供查询能力

Aegis 不是业务测试框架，不理解特定 App 的账号、登录、支付或业务数据。

## 2. 目录和职责边界

Aegis 源码只安装一份：

```text
/Users/mini/Dev/Lobster/Aegis-Cartographer
```

业务 App 测试项目拥有自己的 `.aegis/` 数据，例如当前鲨鱼记账项目：

```text
/Users/mini/Dev/Lobster/aijizhang/android
```

不要把某个 App 的地图写进 Aegis 源码目录。

业务项目中的典型布局：

```text
<app-project>/
└── .aegis/
    ├── config.json
    ├── maps/
    ├── runs/
    └── logs/
```

## 3. 登录和业务准备在哪里

登录、账号、安装、清数据、退出登录、造测试数据，都不属于 Aegis 引擎职责。

这些信息必须放在业务测试项目或外层测试编排中。

当前鲨鱼记账项目的账号文档位于：

```text
/Users/mini/Dev/Lobster/aijizhang/android/.aegis/accounts.md
```

规则：

- Aegis 不读取这个文件。
- Aegis 配置不引用这个文件。
- Aegis 地图包不导出这个文件。
- AI 或外层测试脚本负责在启动遍历前完成登录准备。
- 不要把账号内容复制到 Aegis 源码、地图数据库或地图归档中。

标准做法：

```text
外层流程安装/启动/登录 App
        ↓
确认 App 停在预期起始页面
        ↓
再启动 Aegis 遍历
```

如果遍历过程中需要重新登录，应暂停 Aegis，由外层流程处理登录，再恢复或重新启动合适的 run。

## 4. 初始化和检查项目

初始化业务项目：

```bash
aegis init \
  --project /path/to/app-project \
  --app-id com.example \
  --platform android \
  --app-version 1.2.0 \
  --build-number 12000 \
  --locale zh-CN
```

常用检查：

```bash
aegis map list --project /path/to/app-project
aegis map pages --project /path/to/app-project
aegis map current --project /path/to/app-project
```

说明：

- `map list`：列出项目里的地图。
- `map pages`：输出中文页面卡片和覆盖状态。
- `map current`：只读取当前页面并尝试匹配地图，不点击、不输入。

`map current` 返回 `new_screen` 时不是命令失败，而是表示当前真实页面还没有被地图覆盖。

## 5. 启动遍历

启动前检查清单：

```text
1. 业务项目路径正确
2. 设备在线且是预期设备
3. 被测 App 版本、build、locale 与地图选择器一致
4. 外层流程已处理安装、权限、登录或起始状态
5. App 停在预期起始页面
6. 同一张地图没有其他 worker 正在运行
```

推荐使用后台 worker：

```bash
aegis explore start \
  --project /path/to/app-project \
  --run-id page-map-001 \
  --max-actions 1000 \
  --chunk-steps 50
```

查看状态：

```bash
aegis explore status \
  --project /path/to/app-project \
  --run-id page-map-001
```

控制命令：

```bash
aegis explore pause --project /path/to/app-project --run-id page-map-001
aegis explore resume --project /path/to/app-project --run-id page-map-001
aegis explore stop --project /path/to/app-project --run-id page-map-001
aegis explore report --project /path/to/app-project --run-id page-map-001
```

`BUDGET_EXCEEDED` 表示本次预算用完，不表示 App 已经完整遍历。

## 6. 中断后如何继续

按以下顺序处理，不要直接重复启动新 run。

### 6.1 先确认 worker 状态

```bash
aegis explore status \
  --project /path/to/app-project \
  --run-id <run-id>
```

判断：

- `process_alive: true`：worker 还在跑，不要重复启动。
- `PAUSED`：可以先恢复。
- `BUDGET_EXCEEDED`：预算用完，需要提高预算或开启后续 run。
- `ERROR`：先看日志和 `last_error`，不要盲目重试。
- `COMPLETED`：该 run 完成，但仍需看 pending 页面和地图覆盖。

### 6.2 识别当前真实页面

```bash
aegis map current --project /path/to/app-project
```

如果返回已知页面，记录：

```text
state_id
page.name
page.description
```

如果返回未知页面，说明这是地图缺口；可以在外层流程确保 App 回到已知稳定页面后继续，或让遍历引擎观察并注册该页面。

### 6.3 查看未完成任务

```bash
aegis map pending \
  --project /path/to/app-project \
  --limit 20
```

该命令会跨历史 run 聚合任务，按页面显示：

```text
未完成任务数量
任务状态
页面名
元素
动作
最近一次 run
```

### 6.4 查询页面路径

从指定页面到目标页面：

```bash
aegis map route \
  --project /path/to/app-project \
  --from 主页 \
  --to 明细
```

从具体状态出发：

```bash
aegis map route \
  --project /path/to/app-project \
  --from-state <state_id> \
  --to 明细
```

返回内容包含：

- 起始页面卡片
- 目标页面卡片
- 路径步骤
- transition
- 可靠性
- Maestro flow

当前 Aegis 可以生成路径和 flow；完整自动重放与逐步断言仍在后续版本中增强。

### 6.5 恢复 run

确认是可恢复状态后：

```bash
aegis explore resume \
  --project /path/to/app-project \
  --run-id <run-id> \
  --chunk-steps 50
```

如果 run 已经终态或无法安全恢复，应先修复原因，再开启新 run。新 run 会写入同一张地图，但应避免重复执行已经完成的任务。

## 7. 查询元素地图

查询元素：

```bash
aegis map query \
  --project /path/to/app-project \
  "记账" \
  --include-target-tap
```

返回：

- 元素所在页面
- 推荐 locator
- 元素是否可操作
- 页面路径
- Maestro flow

物化路径缓存：

```bash
aegis map build-paths --project /path/to/app-project
```

人工修正页面说明：

```bash
aegis map describe-page \
  --project /path/to/app-project \
  <screen_id或页面名> \
  --name "明细" \
  --description "查看账单流水和筛选账单" \
  --alias 账单 \
  --coverage-status BASELINE_DONE
```

## 8. 导出和校验

导出地图：

```bash
aegis map export --project /path/to/app-project
```

校验地图：

```bash
aegis map validate \
  --project /path/to/app-project \
  --archive /path/to/map.aegis-map.zip
```

地图包是通用结构工件，只包含：

```text
manifest.json
map.sqlite
pages.json
```

不要把账号、业务数据、项目私密配置放进地图包。

## 9. MCP 使用要点

AI 可通过 MCP 查询：

- `list_project_maps`
- `get_map_metadata`
- `list_pages`
- `list_pending_tasks`
- `get_page_route`
- `identify_current_page`
- `query_elements`
- `query_screens`
- `locate_element`
- `get_element_locators`
- `get_path_to_element`
- `get_path_to_screen`
- `generate_maestro_flow`

MCP 中的探索控制是异步的。启动后应使用状态工具或 CLI 检查 `run_id`，不要阻塞等待整个遍历完成。

MCP 不负责登录，也不会读取业务项目账号文档。如果目标页面需要登录，先由外层流程完成登录，再调用 Aegis 查询或遍历。

## 10. 常见问题判断

| 现象 | 判断 | 处理 |
|---|---|---|
| `map current` 返回 `new_screen` | 当前页面尚未进入地图 | 作为覆盖缺口处理，或回到已知页面后继续 |
| 前台包名不是目标 App | 当前在外部 App / 系统页 | 外层流程先回到目标 App |
| hierarchy 为空 | 设备、前台 App 或 Maestro 状态异常 | 先检查设备与前台页面，再重试 |
| `BUDGET_EXCEEDED` | 本次预算耗尽 | 查看 pending 后继续或开新 run |
| `ERROR` | run 遇到异常 | 先看日志和 `last_error`，修复后恢复或新 run |
| 路径不可达 | transition 缺失或页面未覆盖 | 继续页面发现，或人工修正地图 |
| 页面名不清楚 | 自动命名不准 | 用 `map describe-page` 修正 |

## 11. AI 执行纪律

1. 先确认业务项目路径，不要把数据写进 Aegis 源码目录。
2. 遍历前确认外层流程已处理登录或起始状态。
3. 同一时间只允许一个 worker 操作同一地图。
4. 中断后先看状态，再决定 resume、修复或新 run。
5. `BUDGET_EXCEEDED` 不是完整覆盖。
6. `new_screen` 表示地图缺口，不是页面识别器损坏。
7. 账号和业务测试数据只放在业务项目。
8. 对删除、支付、退出登录、清数据等动作必须遵守业务项目安全策略。
9. 地图导出后必须执行 validate。
10. 若页面名不准确，优先使用 `map describe-page` 修正中文页面卡片。
11. 尽量直接执行单条 `aegis` 命令，并显式传 `--project`，避免 `cd ... && ...`、管道和环境变量前缀。
12. 不要为了绕过工具边界把账号、登录或项目专属逻辑写入 Aegis 源码。
