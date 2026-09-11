# Aegis Cartographer 使用手册

这份文档面向两类使用者：

- **人类测试工程师**：准备项目、启动遍历、查看状态、查询元素地图。
- **AI 测试代理**：通过 MCP 或 CLI 获取元素、locator、页面路径和 Maestro flow。

本文只描述新版 Aegis 工作流。新版工具的数据都在业务测试项目的 `.aegis/` 目录中，不再使用旧版 `aegis_output/`。

---

## 1. 一句话理解 Aegis

Aegis Cartographer 是一个移动 App 元素地图生成和查询工具：

```text
Maestro 自动探索 App
        ↓
生成项目本地元素地图
        ↓
AI 测试时快速查询：
某个元素在哪里？
用什么 locator？
怎么到达？
生成什么 Maestro flow？
```

核心原则：

```text
Aegis 引擎只有一份，作为工具安装。
每个 App 测试项目拥有自己的 .aegis/ 地图数据。
```

不要把 Aegis 源码复制到每个测试项目中。

---

## 2. 核心概念

| 概念 | 含义 |
|---|---|
| App 测试项目 | 你的业务测试仓库，例如 `AppTestProject/` |
| `.aegis/` | 测试项目中的 Aegis 数据目录 |
| Map | 一份独立元素地图，按 App、平台、版本、build、locale 隔离 |
| Run | 一次遍历任务，包含 checkpoint 和任务账本 |
| Screen | 逻辑页面，例如“订单列表页” |
| ScreenState | 页面状态，例如“订单列表页 - 空态” |
| Element | 可查询的 UI 元素 |
| Locator | 定位策略，例如 resource-id、accessibility id、text、point |
| Transition | 从一个页面状态到另一个页面状态的边 |
| Path | 从入口状态到目标页面或元素的路径 |
| Maestro flow | 可执行或可重放的 Maestro YAML |

---

## 3. 推荐目录关系

假设：

```text
Aegis 源码：
/Users/mini/Dev/Lobster/Aegis-Cartographer

App 测试项目：
/Users/mini/Dev/AppTestProject
```

推荐结构：

```text
/Users/mini/Dev/Lobster/Aegis-Cartographer
└── src/aegis_cartographer/...

/Users/mini/Dev/AppTestProject
├── src/
├── tests/
└── .aegis/
    ├── config.json
    ├── maps/
    ├── runs/
    └── logs/
```

Aegis 源码目录不保存业务 App 的地图。

---

## 4. 环境要求

### 4.1 基础要求

- Python 3.10+
- uv
- Maestro CLI
- Android / iOS 设备或模拟器
- 被测 App 已安装

### 4.2 确认 Maestro 可用

```bash
maestro --version
```

如果 `maestro` 不在 PATH，可以在项目初始化时指定：

```bash
--maestro-path /path/to/maestro
```

也可以使用环境变量：

```bash
AEGIS_MAESTRO_PATH=/path/to/maestro
```

### 4.3 多设备注意

如果只连接一台设备，通常无需特殊配置。

如果有多台设备，建议在初始化时写入 `device_id`：

```bash
--device-id emulator-5554
```

注意：部分 Maestro 版本的 `hierarchy` 命令不支持显式 `--device`。多设备环境下请确认 Maestro 默认设备是否正确。

---

## 5. 安装 Aegis

## 5.1 推荐方案：editable 工具安装

editable 安装后，`aegis` 和 `aegis-server` 会成为全局可用命令，但代码仍然直接引用 Aegis 源码目录。

安装：

```bash
cd /Users/mini/Dev/Lobster/Aegis-Cartographer
uv tool install -e .
```

如果之前已经安装过，需要刷新工具环境：

```bash
cd /Users/mini/Dev/Lobster/Aegis-Cartographer
uv tool install -e . --force
```

验证：

```bash
which aegis
which aegis-server

aegis --help
aegis --version
```

如果终端提示工具路径不在 PATH，按 uv 的提示处理，或重新打开终端。

### editable 安装的优点

```text
1. Aegis 源码只保留一份
2. 修改源码后，新启动的 aegis 命令立即使用新代码
3. 不需要每次修改后重新安装
4. 多个 App 测试项目共享同一个 Aegis 引擎
5. 每个测试项目的 .aegis/ 数据仍然互相隔离
```

---

## 5.2 临时使用：不安装

如果只是临时尝试，可以不安装：

```bash
uv --directory /Users/mini/Dev/Lobster/Aegis-Cartographer \
  run aegis \
  --help
```

例如查询某个测试项目的地图：

```bash
uv --directory /Users/mini/Dev/Lobster/Aegis-Cartographer \
  run aegis \
  map query \
  --project /Users/mini/Dev/AppTestProject \
  "修改收货地址"
```

日常长期使用更推荐 editable 安装。

---

## 5.3 不推荐：复制 Aegis 源码到每个测试项目

不要这样做：

```text
AppTestProject/Aegis-Cartographer/
```

原因：

```text
1. 引擎升级困难
2. bug 修复容易漏同步
3. 测试项目中混入工具源码
4. MCP 配置容易混乱
5. 多项目无法共享同一个稳定工具版本
```

---

## 6. 初始化一个 App 测试项目

进入你的业务测试项目：

```bash
cd /Users/mini/Dev/AppTestProject
```

初始化：

```bash
aegis init \
  --project . \
  --app-id com.example \
  --platform android \
  --app-version 1.2.0 \
  --build-number 12000 \
  --locale zh-CN
```

参数说明：

| 参数 | 含义 |
|---|---|
| `--project` | App 测试项目根目录，推荐用 `.` |
| `--app-id` | Android package 或 iOS bundle ID |
| `--platform` | `android` 或 `ios` |
| `--app-version` | App 版本 |
| `--build-number` | build 号 |
| `--locale` | 语言 / 地区，例如 `zh-CN` |

初始化后会生成：

```text
.aegis/config.json
```

示例配置结构：

```json
{
  "schema_version": 1,
  "app_id": "com.example",
  "platform": "android",
  "app_version": "1.2.0",
  "build_number": "12000",
  "locale": "zh-CN",
  "maestro": {
    "device_id": "",
    "command_timeout": 15.0,
    "maestro_path": null
  },
  "exploration": {
    "max_actions": 1000,
    "max_depth": 15,
    "max_states": 1000,
    "max_errors": 20,
    "max_retries": 2,
    "max_restore_attempts": 3,
    "max_consecutive_noops": 200
  },
  "safety": {
    "allow_input_text": false,
    "blocked_keywords": [],
    "needs_human_keywords": []
  }
}
```

### 常用初始化选项

指定设备：

```bash
aegis init ... --device-id emulator-5554
```

指定 Maestro 路径：

```bash
aegis init ... --maestro-path /path/to/maestro
```

限制首轮遍历规模：

```bash
aegis init ... \
  --max-actions 100 \
  --max-depth 8 \
  --max-errors 10
```

追加项目专属危险词：

```bash
aegis init ... \
  --blocked-keyword "清空账户" \
  --blocked-keyword "解绑设备"
```

默认安全词不会被覆盖，只会在默认列表后追加。

覆盖已有配置：

```bash
aegis init ... --force
```

注意：`--force` 只覆盖 `.aegis/config.json`，不会删除已有地图。

---

## 7. 第一次遍历前的检查清单

第一次跑之前，建议确认：

```text
1. 设备或模拟器已连接
2. App 已安装
3. App 已手动登录
4. 权限弹窗已按你的测试策略处理
5. App 停在稳定入口页面
6. `.aegis/config.json` 中 app_id / platform / version / build 正确
7. Maestro 可用
```

重要：Aegis Worker 不会替你登录，也不会自动选择登录账号。请在启动遍历前手动进入合适的入口状态。

---

## 8. 启动一次小规模遍历

第一次建议先跑小规模 smoke：

```bash
cd /Users/mini/Dev/AppTestProject

aegis explore start \
  --project . \
  --run-id smoke-001 \
  --max-actions 10 \
  --chunk-steps 1
```

说明：

| 参数 | 含义 |
|---|---|
| `--run-id` | 本次 run 的 ID，可自定义 |
| `--max-actions` | 本次最多执行多少动作 |
| `--chunk-steps` | Worker 每个检查点执行多少步 |

命令会立即返回，不会等待遍历完成。

返回中重点关注：

```text
run_id
pid
log
status
```

日志位置：

```text
.aegis/logs/runs/<run-id>.log
```

---

## 9. 查看遍历状态

```bash
aegis explore status \
  --project . \
  --run-id smoke-001
```

输出包含：

```text
1. Worker 是否仍在运行
2. checkpoint 状态
3. 已执行动作数
4. 已发现状态数
5. 错误数
6. 当前 DFS 栈
7. 任务状态统计
8. 地图统计
```

常见状态：

| 状态 | 含义 |
|---|---|
| `RUNNING` | 正在执行 |
| `PAUSED` | 已暂停，可恢复 |
| `COMPLETED` | 所有安全任务已完成 |
| `BUDGET_EXCEEDED` | 达到预算限制 |
| `NEEDS_HUMAN` | 存在需要人工处理的任务 |
| `ERROR` | 出现不可恢复错误 |
| `STOPPED` | 已被停止 |

覆盖率报告：

```bash
aegis explore report \
  --project . \
  --run-id smoke-001
```

当前 `report` 与 `status` 输出等价。

---

## 10. 暂停、恢复、停止

### 请求暂停

```bash
aegis explore pause \
  --project . \
  --run-id smoke-001
```

暂停不是立即强杀，而是：

```text
等待当前动作完成
保存 checkpoint
标记 PAUSED
```

### 恢复

```bash
aegis explore resume \
  --project . \
  --run-id smoke-001
```

可以追加新的动作预算：

```bash
aegis explore resume \
  --project . \
  --run-id smoke-001 \
  --max-actions 100
```

### 请求停止

```bash
aegis explore stop \
  --project . \
  --run-id smoke-001
```

同样是优雅停止。

---

## 11. 查看和查询地图

### 11.1 列出项目内地图

```bash
aegis map list \
  --project .
```

输出示例：

```json
{
  "count": 1,
  "maps": [
    "com.example@android:1.2.0+12000/zh-CN"
  ]
}
```

这个字符串就是 map selector。

---

### 11.2 Map selector 格式

格式：

```text
app_id@platform:app_version+build_number/locale
```

示例：

```text
com.example@android:1.2.0+12000/zh-CN
```

如果不传 map selector，Aegis 使用 `.aegis/config.json` 中的默认地图。

如果要查询非默认地图：

```bash
aegis map query \
  --project . \
  --map 'com.example@android:1.2.0+12000/zh-CN' \
  "修改收货地址"
```

也可以逐项指定：

```bash
aegis map query \
  --project . \
  --app-id com.example \
  --platform android \
  --app-version 1.2.0 \
  --build-number 12000 \
  --locale zh-CN \
  "修改收货地址"
```

---

### 11.3 查询元素

```bash
aegis map query \
  --project . \
  "修改收货地址"
```

返回内容包括：

```text
元素 ID
语义名
页面
页面状态
文本
resource-id
accessibility id
locator 列表
到达路径
路径可靠性
Maestro flow
```

限制返回数量：

```bash
aegis map query \
  --project . \
  "确认" \
  --limit 20
```

---

### 11.4 生成包含目标点击的 Maestro flow

默认查询只生成“到达目标元素所在页面”的 flow，不会自动点击目标元素。

如果明确要生成目标点击：

```bash
aegis map query \
  --project . \
  "修改收货地址" \
  --include-target-tap
```

输出中的 `target_tap_flow` 会包含最后的：

```yaml
{"tapOn":{"id":"..."}}
```

建议先确认目标元素风险后再使用该选项。

---

## 12. 导出和校验地图包

### 12.1 导出

```bash
aegis map export \
  --project .
```

默认导出到：

```text
.aegis/maps/.archives/<map>.aegis-map.zip
```

也可以指定输出路径：

```bash
aegis map export \
  --project . \
  --output exports/app-map.aegis-map.zip
```

导出包内包含：

```text
manifest.json
map.sqlite 快照
exports/
screenshots/
maestro_paths/
```

### 12.2 校验

```bash
aegis map validate \
  --archive /path/to/map.aegis-map.zip
```

会校验：

```text
zip 结构
manifest
地图身份
SQLite integrity
foreign key
路径安全
symlink
重复成员
解压大小
```

当前还没有实现 `aegis map import`。

---

## 13. 项目内数据结构

初始化和遍历后，测试项目会出现：

```text
.aegis/
├── config.json
├── maps/
│   ├── com.example/
│   │   └── android/
│   │       └── 1.2.0+12000/
│   │           └── zh-CN/
│   │               ├── manifest.json
│   │               ├── map.sqlite
│   │               └── exports/
│   └── .archives/
├── runs/
│   └── smoke-001/
│       ├── run.json
│       ├── checkpoint.json
│       └── tasks.sqlite
└── logs/
    └── runs/
        └── smoke-001.log
```

### 13.1 正式地图

位置：

```text
.aegis/maps/
```

包含可复用知识：

```text
页面
页面状态
元素
locator
transition
observation
embedding
```

不要手工编辑 `map.sqlite`。

### 13.2 运行数据

位置：

```text
.aegis/runs/
```

包含：

```text
任务账本
checkpoint
run 元数据
```

任务账本不会写入正式地图数据库。

### 13.3 日志

位置：

```text
.aegis/logs/
```

排查问题时优先查看：

```text
.aegis/logs/runs/<run-id>.log
```

---

## 14. Git 建议

建议 App 测试项目的 `.gitignore` 包含：

```gitignore
.aegis/runs/
.aegis/logs/
.aegis/cache/
```

是否提交地图由团队决定：

```text
提交 .aegis/maps/
    优点：团队共享、CI 可直接查询
    缺点：仓库体积可能增加

不提交 .aegis/maps/
    优点：仓库干净
    缺点：每次需要重新生成或从 artifact 获取
```

如果地图用于 CI 测试，推荐提交或通过 CI artifact 传递。

---

## 15. 在同一测试项目中管理多个地图

同一个项目可以有多个地图，例如：

```text
Android 1.2.0
iOS 1.2.0
Android 1.3.0
zh-CN
en-US
```

查询非默认地图时使用 `--map`。

启动遍历时也可以临时指定另一张地图：

```bash
aegis explore start \
  --project . \
  --run-id ios-smoke-001 \
  --app-id com.example.ios \
  --platform ios \
  --app-version 1.2.0 \
  --build-number 12000 \
  --locale zh-CN
```

---

## 16. MCP 配置

Aegis 默认 MCP 入口是：

```text
aegis-server
```

### 16.1 editable 安装后

如果 `aegis-server` 在 PATH 中，可以配置：

```json
{
  "mcpServers": {
    "aegis": {
      "command": "aegis-server"
    }
  }
}
```

### 16.2 不依赖 PATH 的配置

更稳妥的配置：

```json
{
  "mcpServers": {
    "aegis": {
      "command": "uv",
      "args": [
        "--directory",
        "/Users/mini/Dev/Lobster/Aegis-Cartographer",
        "run",
        "aegis-server"
      ]
    }
  }
}
```

### 16.3 重要：project_root 指向哪里？

MCP 工具中的：

```text
project_root
```

永远传 App 测试项目路径。

正确：

```text
/Users/mini/Dev/AppTestProject
```

错误：

```text
/Users/mini/Dev/Lobster/Aegis-Cartographer
```

除非你是在给 Aegis 自己做测试。

---

## 17. MCP 工具清单

## 17.1 地图查询工具

| 工具 | 用途 |
|---|---|
| `list_project_maps` | 列出项目内地图 |
| `get_map_metadata` | 获取地图 manifest、统计、入口状态 |
| `query_elements` | 查询多个匹配元素 |
| `query_screens` | 查询页面 |
| `locate_element` | 返回最佳元素匹配、locator、路径、flow |
| `get_element_locators` | 按 element_id 获取全部 locator |
| `get_path_to_element` | 获取到元素的路径 |
| `get_path_to_screen` | 获取到页面的路径 |
| `generate_maestro_flow` | 生成 Maestro flow |
| `export_map` | 导出地图包 |

## 17.2 遍历控制工具

| 工具 | 用途 |
|---|---|
| `start_exploration` | 启动后台遍历 Worker |
| `get_exploration_status` | 查看 run 状态 |
| `resume_exploration` | 恢复 run |
| `pause_exploration` | 请求暂停 |
| `stop_exploration` | 请求停止 |
| `get_coverage_report` | 获取覆盖率报告 |

所有 MCP schema 都是严格 schema：

```text
additionalProperties: false
```

传入未知字段会返回结构化校验错误。

---

## 18. AI 使用 MCP 的推荐方式

### 18.1 查询元素

告诉 AI：

```text
请调用 locate_element：
project_root = /Users/mini/Dev/AppTestProject
query = 修改收货地址
```

返回中最重要的是：

```text
element_id
screen
locators
path
maestro_flow
risk_level
```

### 18.2 启动遍历

```text
请调用 start_exploration：
project_root = /Users/mini/Dev/AppTestProject
run_id = smoke-002
max_actions = 20
```

该调用是异步的，会立即返回。

### 18.3 查看状态

```text
请调用 get_exploration_status：
project_root = /Users/mini/Dev/AppTestProject
run_id = smoke-002
```

### 18.4 生成到目标元素的 Maestro flow

先用 `locate_element` 拿到：

```text
element_id
```

再调用：

```text
generate_maestro_flow：
project_root = /Users/mini/Dev/AppTestProject
element_id = <上一步返回的 element_id>
include_target_tap = true
```

只有确认目标元素安全时才建议 `include_target_tap = true`。

---

## 19. 安全策略

Aegis 会在执行动作前做安全分类。

默认会拦截类似动作：

```text
删除
支付
购买
下单
退款
转账
注销
退出登录
卸载
修改密码
绑定银行卡
发送
```

被拦截的元素仍然会进入地图，但任务状态为：

```text
BLOCKED
```

不会自动点击。

输入类任务默认标记为：

```text
NEEDS_HUMAN
```

除非明确开启：

```bash
aegis init ... --allow-input-text
```

不建议在真实账号或生产环境开启。

---

## 20. editable 安装后的开发迭代

## 20.1 普通源码修改

修改 Aegis Python 源码后，不需要重新安装。

新启动的进程会自动使用新代码：

```text
aegis map query       新进程，自动使用新代码
aegis explore resume  新 Worker，自动使用新代码
```

但已经运行的进程不会热更新：

```text
正在运行的 Worker：pause/stop 后 resume
正在运行的 MCP Server：重启 MCP 连接
```

## 20.2 什么时候需要刷新 editable 安装？

以下情况需要重新执行：

```bash
uv tool install -e . --force
```

情况包括：

```text
1. 修改 pyproject.toml 依赖
2. 新增或修改 console script
3. 修改包结构
4. 命令找不到或环境异常
```

普通业务逻辑修改不需要。

## 20.3 卸载

```bash
uv tool uninstall aegis-cartographer
```

卸载不会删除测试项目里的：

```text
.aegis/
```

---

## 21. 如何排查问题

## 21.1 `aegis: command not found`

检查：

```bash
which aegis
uv tool list
```

如果没有安装：

```bash
cd /path/to/Aegis-Cartographer
uv tool install -e .
```

如果只是临时使用：

```bash
uv --directory /path/to/Aegis-Cartographer run aegis --help
```

## 21.2 `Project is not initialized`

说明测试项目中没有：

```text
.aegis/config.json
```

解决：

```bash
cd /path/to/AppTestProject
aegis init ...
```

## 21.3 `Map has no entry state`

可能原因：

```text
1. Worker 还没有成功观察过页面
2. 遍历启动时 App 不在前台
3. hierarchy 获取失败
4. map selector 选错
```

先查看：

```bash
aegis explore status ...
cat .aegis/logs/runs/<run-id>.log
```

## 21.4 查询不到元素

依次检查：

```text
1. 地图是否已生成：aegis map list
2. 是否选错地图：检查 --map
3. 遍历是否覆盖过该页面
4. run 是否完成：aegis explore status
5. 换更具体的查询词
```

例如：

```bash
aegis map query --project . "收货地址"
aegis map query --project . "修改"
aegis map query --project . "com.example:id/edit_address"
```

## 21.5 Worker 状态是 ERROR

查看：

```text
1. checkpoint.last_error
2. .aegis/logs/runs/<run-id>.log
3. 任务状态统计
```

常见原因：

```text
设备断连
hierarchy 为空
无法恢复父页面
App 崩溃
Maestro 命令失败
```

## 21.6 目标动作被 BLOCKED

这是安全策略生效，不是 bug。

如确认为安全测试环境，可以：

```text
1. 人工执行该动作
2. 调整测试数据或页面状态
3. 评估是否真的要放开该关键词
```

不要轻易移除默认危险词。

## 21.7 页面识别效果不好

可能现象：

```text
不同页面被合并
同一页面被拆成多个状态
弹窗被当普通页面
```

这属于 Aegis 核心识别策略调优问题。保留以下数据有助于修复：

```text
hierarchy 样例
run_id
日志
被误识别的 map
```

---

## 22. 重新遍历与增量探索

同一张地图可以多次探索。

例如：

```bash
aegis explore start \
  --project . \
  --run-id second-pass \
  --max-actions 100
```

地图中已有语义、元素和边会保留，新 run 会继续补充。

如果核心识别算法发生大改，建议：

```text
1. 备份旧地图
2. 删除对应地图目录，或
3. 使用新的 map identity 重新生成
```

不要在算法大改后盲目混用旧地图数据。

---

## 23. 当前已知限制

截至本文编写时：

```text
1. 还需要更多真实 Android / iOS 设备长跑验证
2. iOS 返回 / sheet 策略仍需完善
3. 动态列表模板归纳还未完全实现
4. embedding 自动生成还未接入
5. map import 还未实现
6. 旧版 server.py / app_map.py 仍保留为遗留代码
```

这些不影响当前使用新版 CLI / MCP 工作流。

---

## 24. 常用命令速查

```bash
# 安装 / 刷新
cd /path/to/Aegis-Cartographer
uv tool install -e .
uv tool install -e . --force

# 初始化 App 测试项目
cd /path/to/AppTestProject
aegis init \
  --project . \
  --app-id com.example \
  --platform android \
  --app-version 1.2.0 \
  --build-number 12000 \
  --locale zh-CN

# 启动小规模遍历
aegis explore start \
  --project . \
  --run-id smoke-001 \
  --max-actions 10 \
  --chunk-steps 1

# 查看状态
aegis explore status \
  --project . \
  --run-id smoke-001

# 暂停 / 恢复 / 停止
aegis explore pause --project . --run-id smoke-001
aegis explore resume --project . --run-id smoke-001
aegis explore stop --project . --run-id smoke-001

# 列出地图
aegis map list --project .

# 查询元素
aegis map query \
  --project . \
  "修改收货地址"

# 查询并生成目标点击 flow
aegis map query \
  --project . \
  "修改收货地址" \
  --include-target-tap

# 导出地图
aegis map export --project .

# 校验地图包
aegis map validate \
  --archive /path/to/map.aegis-map.zip
```

