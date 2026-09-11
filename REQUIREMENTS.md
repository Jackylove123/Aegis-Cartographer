# Aegis Cartographer v2 需求与架构决策记录

日期：2026-09-08  
状态：当前新架构实现基线  
适用范围：Core、Maestro Driver、MapStore、DFS Explorer、Worker、CLI、MCP

---

## 1. 文档定位

本文记录 Aegis Cartographer v2 重构的核心需求、设计决策、实现边界和后续方向。

它不是操作手册。具体安装和使用方式见：

- [USAGE.md](USAGE.md)

本文回答：

```text
为什么这样设计？
哪些是硬约束？
哪些行为是明确要求？
哪些能力已经实现？
哪些能力仍是后续目标？
```

旧文档如 `PRD.md`、`DESIGN_SPEC.md` 保存早期方案和历史讨论。新版开发以本文和 `USAGE.md` 为准。

---

## 2. 问题定义

### 2.1 要解决的问题

移动 App 自动化测试中，AI 经常不知道：

```text
某个页面在哪里？
某个功能入口在哪里？
某个元素在哪个页面？
应该用什么 locator？
从 App 入口如何到达该元素？
该元素是否有风险？
该路径是否可靠？
```

Aegis 的目标是自动探索 App，并生成一个可查询、可复用、可迁移的元素地图。

### 2.2 核心目标

> 在指定 App 版本、平台、语言和测试上下文下，使用 Maestro 尽可能完整地探索安全可达的页面状态和可交互元素，生成项目本地独立地图，并让 AI 测试时能够快速定位元素和生成到达路径。

重点不是理论上的“遍历所有无限状态”，而是：

```text
在安全边界和预算内尽可能提高覆盖率
记录没有覆盖的原因
持续生成可查询、可验证的地图
```

---

## 3. 核心使用场景

### 场景 1：生成某个 App 的元素地图

用户在业务测试项目中执行：

```bash
aegis init
aegis explore start
```

Aegis 使用 Maestro 探索 App，并把地图写入：

```text
业务项目/.aegis/maps/
```

### 场景 2：AI 测试时查询元素

AI 通过 MCP 或 CLI 查询：

```text
修改收货地址
```

Aegis 返回：

```text
元素 ID
页面
页面状态
locator
到达路径
Maestro flow
风险等级
验证信息
```

### 场景 3：测试项目复用地图

地图属于业务测试项目，不属于 Aegis 源码项目。

测试项目可以：

```text
提交地图到 Git
通过 CI artifact 传递地图
导出 map archive
在同一项目中维护多个 App / 平台 / 版本地图
```

### 场景 4：持续增量探索

同一地图可以多次探索：

```text
第一次生成基础地图
后续 run 补充新页面、新元素、新边
旧语义信息不轻易覆盖
```

---

## 4. 产品级硬约束

这些约束是本次重构确认的核心共识，后续开发不应违反。

### R-01 Aegis 引擎与业务地图数据必须分离

Aegis 源码目录只保存工具代码和工具自身测试。

业务 App 的地图数据必须保存在业务测试项目：

```text
业务项目/.aegis/
```

禁止：

```text
把每个 App 的地图写入 Aegis 源码目录
把 Aegis 源码复制到每个测试项目
多个 App 共用一个全局地图数据库
```

原因：

```text
1. 避免 App 之间数据污染
2. 避免地图和工具版本耦合
3. 便于测试项目独立提交和迁移
4. 保持 Aegis 自身干净
```

### R-02 正式地图与运行数据必须分离

业务项目内目录分工固定：

```text
.aegis/
├── config.json
├── maps/
├── runs/
└── logs/
```

| 目录 | 性质 | 内容 |
|---|---|---|
| `.aegis/maps/` | 正式地图 | screens、states、elements、locators、transitions、observations、embeddings |
| `.aegis/runs/` | 运行状态 | checkpoint、task ledger、run metadata |
| `.aegis/logs/` | 日志 | Worker 运行日志 |

任务状态不得写入正式地图数据库。

### R-03 地图身份必须多维隔离

一张地图至少由以下维度唯一标识：

```text
app_id
platform
app_version
build_number
locale
schema_version
```

Map selector 格式：

```text
app_id@platform:app_version+build_number/locale
```

示例：

```text
com.example@android:1.2.0+12000/zh-CN
```

Android 和 iOS 默认分开建图。

### R-04 地图必须是自包含 artifact

每个地图目录必须能独立解释自己：

```text
manifest.json
map.sqlite
exports/
```

地图内部资源必须使用相对路径。

禁止在地图数据库或 manifest 中保存：

```text
Aegis 源码绝对路径
生成机器用户路径
临时遍历 session 路径
其他 App 的数据
```

### R-05 Maestro 是默认执行层，但核心不得写死 Maestro

当前执行层使用 Maestro。

Maestro 负责：

```text
获取 hierarchy
tap
long press
scroll / swipe
back
launch / stop / restart
稳定等待
```

Aegis Core 必须通过 Device Driver 抽象访问设备能力。

原因：

```text
1. 当前开发速度最快
2. Android / iOS 可使用同一工具链
3. 未来可替换为 Appium、UiAutomator2、XCUITest 或其他驱动
```

### R-06 确定性引擎负责遍历，LLM 不参与每一步状态机

禁止让 AI 在每个动作间执行：

```text
get_next_action
手动 tap
report_execution
再 get_next_action
```

新版遍历由确定性 DFS Engine 执行。

AI / LLM 适合承担：

```text
语义命名
查询意图理解
低置信度页面归类辅助
测试策略规划
```

不适合承担：

```text
hash 判断
栈管理
任务去重
动作执行顺序
安全策略判断
```

### R-07 长遍历必须由 Worker 异步执行

MCP tool 不得长时间阻塞等待遍历完成。

正确模式：

```text
MCP / CLI 启动后台 Worker
立即返回 run_id
AI 通过 status 轮询
Worker 持续写 checkpoint 和地图
```

### R-08 安全优先于覆盖率

目标是最大限度遍历，但危险动作默认不执行。

高危元素仍进入地图，任务状态标记为：

```text
BLOCKED
```

这样既保留测试知识，又不自动触发危险行为。

### R-09 查询结果必须可执行

AI 查询元素时，不能只返回“该元素在某个页面”。

必须返回：

```text
element_id
screen
state
locator 列表
到达路径
路径可靠性
Maestro flow
风险等级
最近验证信息
```

### R-10 MCP 是薄适配层

MCP 不承载核心业务逻辑。

MCP 只负责：

```text
输入校验
调用 Core / CLI / Worker
结构化返回
```

核心能力必须可脱离 MCP 使用。

---

## 5. “遍历所有页面和元素”的准确解释

原始诉求是：

> 当前页面提取所有元素，逐个点击；如果进入新页面或弹窗则记录，然后回到当前页面继续；递归探索所有页面。

新版实现将该诉求工程化为：

```text
以 ScreenState 为节点
以 ElementActionTask 为探索单位
执行动作后分类 Transition
保存页面、元素、边和恢复策略
通过 BACK / 路径重放 / App 重启恢复父状态
在预算和安全策略内循环执行
```

不能承诺数学意义上的“所有页面和所有元素”，因为：

```text
1. 页面状态受登录、权限、数据、角色、网络、A/B、语言、版本影响
2. 动态列表和无限 feed 可能产生无限状态
3. 某些元素只有特定业务数据或人工操作后才出现
4. 某些动作不可逆或高风险
```

因此验收目标改为：

```text
尽可能覆盖安全可达状态
输出覆盖率与未覆盖原因
支持重复探索和增量补充
```

---

## 6. 核心领域模型

### 6.1 MapId / AppRelease

描述地图对应的 App 版本上下文：

```text
app_id
platform
app_version
build_number
locale
schema_version
```

### 6.2 Screen

逻辑页面，例如：

```text
订单列表页
订单详情页
设置页
```

### 6.3 ScreenState

同一逻辑页面的具体状态，例如：

```text
订单列表页 - 有订单
订单列表页 - 空态
订单列表页 - 加载失败
```

状态类型：

```text
page
dialog
bottom_sheet
system_dialog
loading
empty
error
external_app
```

### 6.4 Element

页面中的可查询元素。

既包含可交互元素，也包含有语义价值的展示元素。

可交互元素生成探索任务；展示元素只进入地图，不自动执行动作。

### 6.5 Locator

元素定位策略，优先级：

```text
1. id / resource-id
2. accessibility id
3. label / content-desc
4. text
5. point
```

坐标只作为 fallback，不作为长期核心身份。

### 6.6 ExplorationTask

一个任务表示：

```text
在某个 ScreenState
对某个 Element
执行某个 Action
```

状态：

```text
PENDING
RUNNING
DONE
FAILED
SKIPPED
BLOCKED
NEEDS_HUMAN
```

任务保存在：

```text
.aegis/runs/<run_id>/tasks.sqlite
```

### 6.7 Transition

动作执行后的状态变化边。

类型：

```text
NEW_PAGE
OVERLAY
STATE_CHANGE
NO_OP
EXTERNAL_APP
APP_EXIT
CRASH
BLOCKED
ERROR
```

Transition 保存：

```text
from_state
element
action
to_state
maestro_commands
restore_strategy
observed_count
success_count
```

### 6.8 Path

从入口状态到目标页面或元素的路径。

路径规划只使用：

```text
NEW_PAGE
OVERLAY
STATE_CHANGE
```

且要求边有可执行 Maestro 命令和成功记录。

---

## 7. 数据架构

### 7.1 项目工作区

```text
业务项目/
└── .aegis/
    ├── config.json
    ├── maps/
    ├── runs/
    └── logs/
```

### 7.2 正式地图

一张地图一个目录：

```text
.aegis/maps/<app>/<platform>/<version>+<build>/<locale>/
```

包含：

```text
manifest.json
map.sqlite
exports/
```

### 7.3 SQLite 核心表

当前 schema 包含：

```text
screens
screen_states
elements
locators
transitions
observations
paths
embeddings
element_search
screen_search
```

运行任务表不在 `map.sqlite` 中。

---

## 8. 功能需求

### FR-01 Hierarchy 标准化

必须支持：

```text
Maestro tree 格式
Maestro CLI / attributes 格式
Maestro MCP 缩写格式
Android hierarchy
iOS accessibility hierarchy
```

统一输出内部 `NormalizedHierarchy`。

空 hierarchy 必须拒绝，不得生成统一空状态。

### FR-02 页面状态识别

页面身份不得只依赖一个 hash。

必须组合：

```text
结构签名
可交互元素签名
语义 landmark
state type
package / bundle
```

必须避免：

```text
动态数据变化导致同一页面被误拆
不同业务页面因结构相同被误合并
弹窗被当普通页面
外部 App 被写入目标 App 地图
```

### FR-03 元素身份

元素身份必须考虑：

```text
state_id
selector
occurrence_index
```

重复 resource-id 必须生成不同 element key。

### FR-04 元素提取

元素提取分为：

```text
可交互元素：生成任务
展示元素：进入地图，不执行
```

必须识别：

```text
clickable
scrollable
checkable
focusable
input
```

### FR-05 任务账本

每个 run 必须有独立任务账本。

要求：

```text
任务不重复执行
任务状态可恢复
中断 RUNNING 任务可恢复为 PENDING
任务 ID 不因页面元素顺序变化而漂移
```

### FR-06 DFS 探索

核心循环：

```text
观察当前状态
匹配 / 注册 ScreenState
获取下一个任务
安全分类
执行 Maestro 动作
等待稳定
观察执行后状态
分类 Transition
写入地图
进入新状态或恢复父状态
保存 checkpoint
```

### FR-07 滚动探索

scrollable 容器生成 `SCROLL` 任务。

滚动后如果出现新的稳定状态：

```text
记录 STATE_CHANGE
进入新状态
继续提取滚动后元素
```

滚动必须受预算控制。

### FR-08 Overlay 探索

弹窗、bottom sheet、system dialog 必须作为 overlay state 处理。

流程：

```text
入栈探索
安全处理内部元素
探索完成后恢复父状态
```

### FR-09 外部 App 防护

动作后 package / bundle 不属于目标 App 时：

```text
记录 EXTERNAL_APP
不把外部 App 页面写入正式地图
尝试返回目标 App
必要时重启 App 并路径重放
```

### FR-10 父状态恢复

恢复策略分级：

```text
1. BACK
2. 页面返回按钮
3. 有限多次 BACK
4. 已知路径重放
5. 重启 App 后从入口重放
6. 标记不可恢复
```

恢复后必须重新观察并校验状态。

### FR-11 预算与终止

预算包括：

```text
max_actions
max_depth
max_states
max_errors
max_retries
max_restore_attempts
max_consecutive_noops
```

必须有明确状态：

```text
COMPLETED
PAUSED
BUDGET_EXCEEDED
NEEDS_HUMAN
ERROR
STOPPED
```

### FR-12 断点续跑

每个 run 必须保存：

```text
checkpoint.json
tasks.sqlite
run.json
```

支持：

```text
pause
stop
resume
中断后恢复
```

### FR-13 默认危险动作阻断

默认拦截：

```text
删除
支付
购买
下单
退款
转账
注销
登出
卸载
修改密码
绑定银行卡
发送
```

项目自定义危险词必须追加到默认列表后，不得替换默认列表。

### FR-14 输入任务默认人工处理

输入类任务默认：

```text
NEEDS_HUMAN
```

不得自动输入文本，除非项目显式开启。

### FR-15 Maestro flow 安全生成

禁止手写拼接 YAML。

必须使用受限命令模型和安全序列化。

只允许白名单命令，例如：

```text
tapOn
longPressOn
swipe
scroll
pressKey
launchApp
stopApp
inputText
waitForAnimationToEnd
hideKeyboard
back
```

必须拒绝：

```text
未知命令
一个 mapping 多个命令 key
NaN
非法对象
```

### FR-16 元素查询

必须支持按以下内容查询：

```text
语义名
别名
文本
content-desc
accessibility id
resource-id
class
selector
页面名
```

至少支持 FTS 与 substring fallback。

### FR-17 页面查询

必须支持按页面语义、别名、业务域、landmark 查询。

返回页面和所有已知状态。

### FR-18 路径规划

必须基于 transition 图规划路径。

只使用可重放边。

路径可靠性基于：

```text
success_count / observed_count
```

返回每一步：

```text
transition_id
from_state
to_state
element_id
action
result_type
maestro_commands
reliability
```

### FR-19 Maestro flow 生成

查询路径必须能生成 Maestro flow。

默认不自动点击目标元素。

显式请求时才追加目标点击。

### FR-20 路径验证

必须支持路径重放验证。

验证内容：

```text
从入口重启 App
逐步执行 transition
校验实际 state
回写 transition 成功率
验证目标 locator
回写 locator 成功率
```

---

## 9. CLI / Worker / MCP 需求

### FR-21 项目初始化

CLI 必须支持：

```bash
aegis init
```

生成：

```text
.aegis/config.json
```

### FR-22 后台 Worker

CLI 必须支持：

```bash
aegis explore start
aegis explore status
aegis explore resume
aegis explore pause
aegis explore stop
aegis explore report
```

Worker 必须异步执行并写日志。

### FR-23 地图 CLI

必须支持：

```bash
aegis map list
aegis map query
aegis map export
aegis map validate
```

### FR-24 MCP 查询工具

新版 MCP 必须提供：

```text
list_project_maps
get_map_metadata
query_elements
query_screens
locate_element
get_element_locators
get_path_to_element
get_path_to_screen
generate_maestro_flow
export_map
```

### FR-25 MCP 遍历控制

必须提供：

```text
start_exploration
get_exploration_status
resume_exploration
pause_exploration
stop_exploration
get_coverage_report
```

这些工具不得等待遍历完成。

### FR-26 MCP 输入校验

所有 MCP schema 必须：

```text
additionalProperties: false
required 明确
enum 严格
范围校验
```

内部必须再次校验。

错误必须结构化：

```text
VALIDATION_ERROR
NOT_FOUND
CLI_ERROR
INTERNAL_ERROR
```

---

## 10. 阶段改造记录

### 阶段 0：架构收敛

目标：

```text
停止继续扩展旧 server.py
建立 Core / Device / Storage / Query / Worker / MCP 分层
```

状态：已完成。

### 阶段 1：Hierarchy 感知

实现：

```text
NormalizedHierarchy
结构签名
交互签名
稳定 element key
scrollable 识别
空 hierarchy 拒绝
```

关键修复：

```text
修复旧版真实 hierarchy 被计算成空结构的问题
```

状态：已完成。

### 阶段 2：页面状态识别

实现：

```text
Screen / ScreenState 分离
ScreenMatcher
STATE_VARIANT
NEW_SCREEN
OVERLAY
EXTERNAL_APP
AMBIGUOUS
```

状态：已完成。

### 阶段 3：Maestro Driver

实现：

```text
安全 Maestro flow 生成
tap / long press / swipe / scroll / back
launch / stop / restart
稳定等待
结构化 ActionResult
临时 flow 安全管理
```

状态：已完成 Fake Driver 单元验证；真实设备长跑仍待验证。

### 阶段 4：项目本地地图存储

实现：

```text
ProjectWorkspace
MapId
MapManifest
SQLite MapStore
FTS
embedding 存储
只读打开
manifest / database 身份校验
JSON 导出
```

状态：已完成。

### 阶段 5：DFS Explorer

实现：

```text
ExplorationTask
SafetyClassifier
ExplorationRunStore
DFS Engine
Transition 分类
滚动探索
Overlay 探索
外部 App 回退
父状态恢复
checkpoint
resume
覆盖率报告
```

状态：核心已完成；真实设备策略仍需调优。

### 阶段 6：查询与路径

实现：

```text
MapQueryService
元素查询
页面查询
路径规划
Maestro flow 渲染
MapPathExecutor
transition / locator 验证回写
```

状态：已完成。

### 阶段 7：CLI / Worker

实现：

```text
aegis init
aegis map list/query/export/validate
aegis explore start/status/resume/pause/stop/report
后台 Worker
run metadata
日志隔离
地图包导出与校验
```

状态：已完成。

### 阶段 8：新版 MCP

实现：

```text
ModernMCPServerLogic
16 个 MCP 工具
严格 schema
结构化错误
异步遍历控制
默认 MCP 入口切换为新 server
```

状态：已完成。

---

## 11. 当前实现边界

以下能力尚未完成或不承诺当前稳定：

```text
1. 真实 Android / iOS 长时间遍历验证
2. iOS 返回、sheet、权限弹窗专属策略完善
3. 动态列表 item template 归纳
4. embedding 自动生成
5. map archive 一键导入
6. 旧 server.py / app_map.py / traversal.py 清理
7. Worker PID 复用风险增强
```

这些不是架构方向变化，而是后续打磨项。

---

## 12. 验收基线

当前自动化基线：

```text
95 tests passed
ruff passed
compileall passed
git diff --check passed
```

关键验收点：

```text
1. tree / CLI / MCP hierarchy 格式可统一解析
2. 动态文本不会导致页面误拆
3. 重复 resource-id 可区分
4. 正式地图不包含运行任务表
5. 高危任务 BLOCKED 且不触碰设备
6. DFS 可断点恢复
7. 外部 App 不写入目标 App 地图
8. 查询可返回元素、locator、路径和 flow
9. 路径重放可回写验证结果
10. MCP schema 严格拒绝未知字段
```

---

## 13. 后续优先级

### P0 真实设备验收

```text
Android 小规模 DFS
真实 hierarchy 校验
页面识别准确率
点击 / 滚动 / 返回
路径重放
日志与错误分类
```

### P1 稳定性与清理

```text
移除或归档旧 server.py / app_map.py / traversal.py
补充 schema migration 策略
增强 Worker heartbeat
完善 iOS 恢复策略
```

### P2 地图能力增强

```text
map import
版本 diff
embedding 自动生成
动态列表模板
更精细覆盖率统计
```

### P3 团队发布

```text
固定版本
内部包发布
团队统一升级
CI 集成
```

---

## 14. 关键决策总结

| 决策 | 结论 |
|---|---|
| Aegis 是否放业务项目 | 不放，Aegis 是工具 |
| 地图放哪里 | 业务项目 `.aegis/maps/` |
| 是否使用全局地图库 | 不使用 |
| 地图如何隔离 | app/platform/version/build/locale |
| 任务账本放哪里 | `.aegis/runs/<run_id>/tasks.sqlite` |
| 自动探索是否需要 Maestro | 需要 Maestro 或等价 Driver |
| 核心是否绑定 Maestro | 不绑定，使用 Driver 抽象 |
| AI 是否逐步控制遍历 | 不逐步控制 |
| 遍历由谁执行 | 后台 Worker |
| MCP 是否承载长任务 | 不承载 |
| 高危元素是否入库 | 入库但不执行 |
| 查询是否只返回页面名 | 必须返回 locator、路径和 flow |
| 默认 MCP 入口 | 新 `mcp_server.py` |
| 使用文档 | `USAGE.md` |
| 需求与架构记录 | 本文档 |
