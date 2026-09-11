# Aegis Cartographer 完整需求文档

## 文档说明

**项目名称**: Aegis Cartographer - 移动端语义测绘智能体
**文档版本**: v3.0 Final
**创建日期**: 2026-05-28
**文档类型**: 完整需求文档（包含需求定义和技术实现规范）

---

## 目录

1. [项目概述](#1-项目概述)
2. [业务需求](#2-业务需求)
3. [系统架构](#3-系统架构)
4. [功能需求](#4-功能需求)
5. [技术方案](#5-技术方案)
6. [接口规范](#6-接口规范)
7. [数据模型](#7-数据模型)
8. [安全机制](#8-安全机制)
9. [性能要求](#9-性能要求)
10. [开发计划](#10-开发计划)

---

## 1. 项目概述

### 1.1 项目愿景

开发一个能够自主探索 Android/iOS 应用、识别业务逻辑并生成"自愈式"语义地图的 MCP 服务。该服务通过 JSON 结构化存储与向量语义描述相结合，为 AI 自动化测试提供详尽的"产品说明书"。

### 1.2 核心价值

| 价值点 | 描述 |
|--------|------|
| **完整遍历** | 按树状图深度优先遍历，确保覆盖所有可达页面 |
| **精确识别** | 通过状态指纹技术，准确识别页面和元素 |
| **快速查询** | 元素级向量索引，支持语义搜索定位 |
| **增量更新** | 支持重复遍历，识别新增/修改/删除的功能 |
| **安全可控** | 危险操作过滤，防止误操作导致数据损失 |
| **操作约束** | 仅通过 Maestro MCP 操作，确保可追溯和可控 |

### 1.3 应用场景

```
场景1：新应用地图构建
    ├─ 首次遍历应用，生成完整元素地图
    └─ 用于后续测试用例设计参考

场景2：应用迭代更新
    ├─ 应用发版后，重新遍历
    ├─ 对比新旧地图，识别新增功能
    └─ 更新测试用例覆盖

场景3：测试执行辅助
    ├─ 测试用例："执行支付功能"
    ├─ 查询地图：支付按钮在"订单确认页"
    ├─ 获取路径：首页 → 订单列表 → 订单详情 → 订单确认页
    └─ 执行测试：按路径逐步执行
```

### 1.4 项目边界

| 职责 | Aegis Cartographer | Maestro MCP | 测试系统 |
|------|---------------------|-------------|----------|
| 遍历策略制定 | ✅ 负责 | ❌ | ❌ |
| 执行 tap/scroll | ❌ | ✅ 负责 | ❌ |
| 元素地图存储 | ✅ 负责 | ❌ | ❌ |
| 快速查询接口 | ✅ 负责 | ❌ | ❌ |
| 测试用例执行 | ❌ | ❌ | ✅ 负责 |
| 路径规划执行 | ❌ | ❌ | ✅ 负责 |

---

## 2. 业务需求

### 2.1 核心业务需求

#### BR-001: 完整遍历应用

**需求描述**: 系统必须能够深度优先遍历应用的所有可达页面，确保不遗漏任何功能入口。

**验收标准**:
- 能遍历到至少 5 层深度的所有页面
- 每个页面的所有 clickable 元素都被记录
- 不出现循环导致的死遍历
- 遍历可中断和恢复

**优先级**: P0（最高）

#### BR-002: 精确识别页面状态

**需求描述**: 系统必须能够准确识别不同的页面状态，忽略动态内容变化。

**验收标准**:
- 同一页面多次访问返回相同的状态指纹
- 页面内容变化（如数据更新）不影响指纹识别
- 支持弹窗、对话框等特殊状态的识别

**优先级**: P0

#### BR-003: 元素级快速查询

**需求描述**: 系统必须支持通过语义搜索快速定位到具体的功能按钮或元素。

**验收标准**:
- 能通过"支付按钮"查询到具体的 element_id
- 返回结果包含元素所在页面信息
- 查询响应时间 < 1 秒
- 支持模糊语义搜索

**优先级**: P1

#### BR-004: 增量更新地图

**需求描述**: 系统必须支持重复遍历同一应用，识别新增、修改、删除的功能。

**验收标准**:
- 重复遍历不创建重复节点
- 能正确识别新增的功能页面
- 能标记废弃的旧功能
- 支持版本历史查询

**优先级**: P1

#### BR-005: 安全操作过滤

**需求描述**: 系统必须过滤危险操作，防止遍历过程中造成数据损失。

**验收标准**:
- 自动识别并跳过"注销"、"删除"等危险操作
- 防止跨应用跳转
- 防止跳转到浏览器或系统应用
- 可配置危险关键词列表

**优先级**: P0

#### BR-006: 执行方式约束

**需求描述**: 遍历过程中必须仅通过 Maestro MCP 执行手机操作，确保操作可追溯和可控。

**验收标准**:
- 所有 tap/scroll/get_hierarchy 操作必须通过 Maestro MCP
- 配置文件中明确指定 `execution_method: "maestro"`
- AI 在遍历时严格遵守此约束
- 不允许使用其他方式（如直接系统命令）操作应用

**优先级**: P0（强制要求）

### 2.2 业务流程

#### 流程1：首次遍历构建地图

```
┌─────────────────────────────────────────────────────────────┐
│  阶段1：初始化                                                 │
├─────────────────────────────────────────────────────────────┤
│  1. 用户在 Trae 中启动遍历会话                                 │
│  2. 大模型调用 start_traversal(project_root)                 │
│  3. Aegis 初始化遍历栈，返回 session_id                       │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  阶段2：遍历执行                                               │
├─────────────────────────────────────────────────────────────┤
│  4. 大模型获取当前页面 (Maestro get_hierarchy)                │
│  5. 大模型调用 get_next_action(session_id, state_id, elements)│
│  6. Aegis 返回动作指令 (CLICK element_id)                    │
│  7. 大模型执行动作 (Maestro tap)                             │
│  8. 大模型获取新页面 (Maestro get_hierarchy)                  │
│  9. 大模型调用 report_execution(...)                          │
│  10. Aegis 更新遍历栈，返回下一个动作                         │
│  11. 重复 4-10，直到遍历完成                                  │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  阶段3：地图保存                                               │
├─────────────────────────────────────────────────────────────┤
│  12. Aegis 保存完整的元素地图到 aegis_output/                │
│  13. 生成 Mermaid 可视化图表                                  │
│  14. 建立元素级向量索引                                        │
└─────────────────────────────────────────────────────────────┘
```

#### 流程2：增量更新地图

```
┌─────────────────────────────────────────────────────────────┐
│  阶段1：加载旧地图                                             │
├─────────────────────────────────────────────────────────────┤
│  1. 用户指定项目根目录                                         │
│  2. Aegis 加载上次遍历的地图                                  │
│  3. 将所有现有节点标记为 DEPRECATED                            │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  阶段2：重新遍历                                               │
├─────────────────────────────────────────────────────────────┤
│  4. 执行 DFS 遍历                                             │
│  5. 遇到已访问节点：                                          │
│     ├─ 状态指纹相同 → 标记 ACTIVE，更新元素列表                │
│     └─ 状态指纹不同 → 视为新版本，创建新节点                   │
│  6. 记录新增节点                                              │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  阶段3：版本对比                                               │
├─────────────────────────────────────────────────────────────┤
│  7. 生成版本对比报告                                          │
│     ├─ 新增页面数                                             │
│     ├─ 修改页面数                                             │
│     └─ 废弃页面数                                             │
│  8. 保存版本摘要                                              │
└─────────────────────────────────────────────────────────────┘
```

#### 流程3：查询使用地图

```
┌─────────────────────────────────────────────────────────────┐
│  场景：测试用例"执行支付功能"                                  │
├─────────────────────────────────────────────────────────────┤
│  1. 测试系统调用 locate_element(query="支付按钮")             │
│  2. Aegis 返回：                                             │
│     {                                                        │
│       "element_id": "btn_pay",                              │
│       "page_name": "订单确认页",                              │
│       "page_state_id": "abc123..."                           │
│     }                                                        │
│  3. 测试系统调用 get_map_summary() 获取入口信息              │
│  4. 测试系统根据地图设计执行路径                              │
│  5. 测试系统通过 Maestro 逐步执行                            │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 系统架构

### 3.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                      Trae / Agent 框架                         │
│                   （大模型决策与执行层）                        │
│                                                               │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              大模型（Claude/GPT）                       │  │
│  │  • 决策"下一步点哪"                                     │  │
│  │  • 理解业务语义                                         │  │
│  │  • 处理异常情况                                         │  │
│  └───────────────────────────────────────────────────────┘  │
│           │                              │                    │
│           ↓                              ↓                    │
│  ┌──────────────────┐        ┌──────────────────┐            │
│  │   Maestro MCP    │        │  Aegis           │            │
│  │                  │        │  Cartographer     │            │
│  │  • tap           │◄──────►│  MCP              │            │
│  │  • scroll        │ 调用   │  • 遍历引导        │            │
│  │  • get_hierarchy │       │  • 地图存储        │            │
│  │  • back          │       │  • 快速查询        │            │
│  └──────────────────┘        └──────────────────┘            │
└─────────────────────────────────────────────────────────────┘
           │                              │
           ↓                              ↓
    ┌──────────────┐              ┌──────────────┐
    │  移动设备     │              │  本地存储     │
    │  (Android/   │              │  (JSON文件)   │
    │   iOS)       │              │              │
    └──────────────┘              └──────────────┘
```

### 3.2 MCP 通信模式

Aegis Cartographer 采用 **stdio 模式**与 MCP 客户端通信：

```
┌─────────────────┐         ┌─────────────────┐
│  MCP Client     │         │  Aegis Server   │
│  (Trae/Claude)  │         │                 │
└────────┬────────┘         └────────┬────────┘
         │                           │
         │ stdio (stdin/stdout)       │
         │                           │
         │ ←────────────────────→    │
         │    list_tools()           │
         │ ←────────────────────→    │
         │    call_tool()            │
         │ ←────────────────────→    │
         │    result                 │
```

### 3.3 三层交互模式

```
┌─────────────────────────────────────────────────────────────┐
│  顶层：Reasoning Layer（推理层）                               │
│  • Claude/GPT 等大模型                                        │
│  • 负责高层决策和语义理解                                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  中间层：Intelligence Layer（智能层）                          │
│  • Aegis Cartographer（本项目）                               │
│  • 负责状态识别、逻辑建图、语义压缩、遍历引导                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  底层：Execution Layer（执行层）                               │
│  • Maestro MCP                                                │
│  • 负责原子的 tap、scroll、get_hierarchy 操作                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. 功能需求

### 4.1 功能清单

| 功能ID | 功能名称 | 优先级 | 状态 |
|--------|----------|--------|------|
| F-001 | DFS 深度优先遍历 | P0 | 待改造 |
| F-002 | 状态指纹计算 | P0 | ✅ 已实现 |
| F-003 | 元素提取与过滤 | P0 | ✅ 已实现 |
| F-004 | 地图存储与管理 | P0 | ✅ 已实现 |
| F-005 | 安全操作过滤 | P0 | ✅ 已实现 |
| F-006 | 遍历栈管理 | P0 | 待改造 |
| F-007 | 执行闭环机制 | P0 | 待实现 |
| F-008 | 元素级向量索引 | P1 | 待实现 |
| F-009 | 快速查询接口 | P1 | 待实现 |
| F-010 | 增量更新机制 | P1 | ✅ 部分实现 |
| F-011 | 回溯与恢复 | P1 | ✅ 已实现 |
| F-012 | Mermaid 可视化 | P2 | ✅ 已实现 |

### 4.2 功能详细描述

#### F-001: DFS 深度优先遍历

**功能描述**:
系统必须采用深度优先搜索策略遍历应用，确保探索到所有深层页面。

**业务规则**:
1. 遇到新页面时，必须先探索完该页面的所有分支，再返回上一级
2. 按页面中元素的出现顺序依次探索（确定性）
3. 维护遍历栈，记录访问路径
4. 检测循环，避免死遍历

**技术方案**: [见 5.1 节]

#### F-002: 状态指纹计算

**功能描述**:
计算页面的唯一指纹，用于识别页面状态。

**业务规则**:
1. 排除动态内容（bounds, text, content-desc）
2. 仅提取 clickable 元素的四元组 (id, class, parent_id, depth)
3. 字母顺序排序后计算 SHA256

**实现状态**: ✅ 已实现 (`fingerprint.py`)

#### F-003: 元素提取与过滤

**功能描述**:
从页面 XML 中提取所有可交互元素。

**业务规则**:
1. 仅提取 clickable: true 的元素
2. 记录元素 id、text、class 等信息
3. 过滤系统元素和危险元素

**实现状态**: ✅ 已实现 (`fingerprint.py`, `security.py`)

#### F-004: 地图存储与管理

**功能描述**:
将遍历结果持久化存储，支持查询和更新。

**业务规则**:
1. 分片存储，每个节点一个文件
2. 维护全局索引
3. 支持节点状态标记（ACTIVE/DEPRECATED）

**实现状态**: ✅ 已实现 (`app_map.py`)

#### F-005: 安全操作过滤

**功能描述**:
识别并过滤危险操作，防止遍历造成数据损失。

**业务规则**:
1. 检测危险关键词：注销、删除、支付等
2. 检测包名边界，防止跨应用跳转
3. 检测浏览器跳转

**实现状态**: ✅ 已实现 (`security.py`)

#### F-006: 遍历栈管理

**功能描述**:
维护 DFS 遍历的状态栈，记录访问路径和探索进度。

**业务规则**:
1. 每个页面维护当前探索到的元素索引
2. 入栈时记录元素列表
3. 出栈时返回上一级继续探索

**技术方案**: [见 5.2 节]

#### F-007: 执行闭环机制

**功能描述**:
大模型执行动作后必须上报结果，系统更新遍历状态。

**业务规则**:
1. 大模型执行 action 后调用 report_execution
2. 系统根据执行结果更新栈状态
3. 返回下一个动作指令

**技术方案**: [见 5.3 节]

#### F-008: 元素级向量索引

**功能描述**:
为每个元素建立向量索引，支持语义搜索。

**业务规则**:
1. 索引元素文案、业务上下文、语义角色
2. 支持 TF-IDF 或 Embedding 向量
3. 返回匹配元素及其所在页面

**技术方案**: [见 5.4 节]

#### F-009: 快速查询接口

**功能描述**:
提供快速查询工具，支持按功能名定位元素。

**业务规则**:
1. locate_element: 查询元素位置
2. 返回 element_id 和所在页面信息
3. 支持模糊语义搜索

**接口规范**: [见 6.2 节]

#### F-010: 增量更新机制

**功能描述**:
支持重复遍历，识别新增/修改/删除的功能。

**业务规则**:
1. 新遍历开始时将旧节点标记 DEPRECATED
2. 访问到旧节点时更新为 ACTIVE
3. 未访问到的保持 DEPRECATED

**实现状态**: ✅ 部分实现 (`app_map.py` 需要完善)

#### F-011: 回溯与恢复

**功能描述**:
探索完成后自动返回上一级页面，继续探索其他分支。

**业务规则**:
1. 尝试软回溯（maestro back）
2. 失败时尝试硬重置（重启应用）
3. 根据地图记录的路径重新导航

**实现状态**: ✅ 已实现 (`traversal.py` 中的 `_try_backtrack`, `_hard_reset`)

#### F-012: Mermaid 可视化

**功能描述**:
将地图导出为 Mermaid 图表，方便人工查看。

**实现状态**: ✅ 已实现 (`app_map.py` 中的 `export_mermaid`)

---

## 5. 技术方案

### 5.1 DFS 状态机设计

#### 方案概述

将遍历逻辑从大模型转移到 MCP 内部，通过状态机确保遍历的确定性和完整性。

#### 核心数据结构

```python
@dataclass
class TraversalFrame:
    """遍历栈中的一帧，代表一个页面的遍历状态"""
    state_id: str                    # 当前页面 ID
    semantic_name: str                # 页面语义名称
    elements: List[ElementInfo]      # 页面可点击元素列表
    current_index: int                # 当前探索到的元素索引
    visited: bool                     # 是否已访问过

@dataclass
class ElementInfo:
    """元素信息"""
    element_id: str                   # 元素唯一标识
    text_content: str                 # 元素文案
    is_explored: bool                 # 是否已探索过
    semantic_role: str                # 元素角色
```

#### 状态机流程

```
状态：IDLE → VISITING → EXPLORING → BACKTRACKING → COMPLETED
                    ↑                              ↓
                    └────────── 异常恢复 ──────────┘
```

#### 算法伪代码

```
function get_next_action(current_state_id, current_elements):
    # 1. 检查是否新页面
    if current_state_id not in visited:
        frame = create_frame(current_state_id, current_elements)
        stack.push(frame)
        visited.add(current_state_id)

    # 2. 获取当前帧
    frame = stack.top()

    # 3. 查找未探索元素
    while frame.current_index < len(frame.elements):
        element = frame.elements[frame.current_index]
        if not element.is_explored:
            return CLICK(element.element_id)
        frame.current_index += 1

    # 4. 所有元素已探索，回溯
    if stack.size() > 1:
        stack.pop()
        return BACK
    else:
        return FINISH
```

### 5.2 遍历栈管理

#### 栈操作

| 操作 | 说明 |
|------|------|
| `push(frame)` | 新页面入栈 |
| `pop()` | 返回上一级 |
| `top()` | 获取当前页面 |
| `depth()` | 获取当前深度 |

#### 循环检测

```
维护 visited_states: Set[str]

遇到新状态:
    if state_id in visited_states:
        return "已访问，跳过"
    else:
        visited_states.add(state_id)
        # 正常处理
```

### 5.3 执行闭环机制

#### 流程设计

```
┌─────────────────────────────────────────────────────────────┐
│  MCP: get_next_action() → {action: "CLICK", element_id: "x"}│
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  大模型: 调用 Maestro tap(element_id)                        │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  大模型: 获取新页面 get_hierarchy()                           │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  大模型: report_execution({                                   │
│           executed_action: "CLICK",                          │
│           resulting_state_id: "new_state",                   │
│           resulting_elements: [...]                          │
│         })                                                   │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  MCP: 更新栈状态 → 返回下一个动作                             │
└─────────────────────────────────────────────────────────────┘
```

### 5.4 元素级向量索引

#### 当前方案

TF-IDF + 余弦相似度（`vector_indexer.py`）

**问题**:
- 仅索引页面级别
- 无法定位具体元素

#### 改进方案

**方案 A: TF-IDF 元素级索引（短期）**
```python
class ElementIndex:
    element_id: str
    text_content: str
    page_context: str           # 所属页面的业务上下文
    semantic_role: str

# 索引内容: f"{text_content} {page_context} {semantic_role}"
```

**方案 B: Embedding 向量索引（长期）**
```python
# 使用 OpenAI Embedding API
embedding = openai.Embedding.create(
    input=f"{element_text} {page_context}",
    model="text-embedding-3-small"
)
```

### 5.5 安全机制

#### 已实现的安全策略

| 安全策略 | 实现位置 | 说明 |
|----------|----------|------|
| 危险关键词过滤 | `SecurityFilter` | 过滤注销、删除、支付等 |
| 包名边界检查 | `PackageNameGuard` | 防止跨应用跳转 |
| 浏览器跳转检测 | `PackageNameGuard` | 防止跳转到浏览器 |
| 系统应用检测 | `PackageNameGuard` | 防止跳转到系统设置 |

---

## 6. 接口规范

### 6.1 MCP 工具列表

| 工具名 | 功能 | 状态 |
|--------|------|------|
| `start_traversal` | 开始新的遍历会话 | ✅ 已实现（需改造） |
| `get_next_action` | 获取下一步动作（核心） | ✅ 已实现（需改造） |
| `report_execution` | 上报执行结果 | ❌ 待实现 |
| `locate_element` | 查找元素位置 | ❌ 待实现 |
| `get_traversal_status` | 获取遍历进度 | ✅ 已实现 |
| `semantic_search` | 语义搜索 | ✅ 已实现 |
| `upsert_node` | 记录页面节点 | ✅ 已实现 |
| `add_edge` | 记录页面跳转 | ✅ 已实现 |

### 6.2 核心接口详细设计

#### get_next_action（核心改造）

```python
Tool(
    name="get_next_action",
    description="""
    获取下一步遍历动作。MCP 内部维护 DFS 栈，大模型只需执行返回的动作。

    此工具是遍历的核心，实现了深度优先搜索逻辑：
    - 遇到新页面时，返回第一个可点击元素
    - 页面有未探索元素时，返回下一个元素
    - 页面所有元素已探索时，返回 BACK 指令
    - 所有分支探索完成时，返回 FINISH

    大模型必须严格按照返回的动作执行，并在执行后调用 report_execution。
    """,
    inputSchema={
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "遍历会话 ID，由 start_traversal 返回"
            },
            "current_state_id": {
                "type": "string",
                "description": "当前页面的状态指纹（SHA256）"
            },
            "current_elements": {
                "type": "array",
                "description": "当前页面可点击元素列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "element_id": {"type": "string"},
                        "text_content": {"type": "string"},
                        "clickable": {"type": "boolean"},
                        "resource_id": {"type": "string"}
                    }
                }
            },
            "project_root": {
                "type": "string",
                "description": "项目根目录"
            }
        },
        "required": ["session_id", "current_state_id", "current_elements", "project_root"]
    }
)

# 返回示例
{
    "success": true,
    "action": "CLICK",              # CLICK | BACK | SWIPE | FINISH | WAIT
    "element_id": "btn_settings",  # 当 action=CLICK 时
    "reason": "继续探索设置分支，当前深度 3",
    "debug_info": {
        "stack_depth": 3,
        "remaining_in_page": 2,
        "total_unexplored": 15,
        "current_page": "设置页"
    }
}
```

#### report_execution（新增）

```python
Tool(
    name="report_execution",
    description="""
    上报执行结果，更新遍历栈。

    大模型执行 get_next_action 返回的动作后，必须调用此工具上报结果。
    系统会根据执行结果更新 DFS 栈状态，并决定下一步动作。

    执行闭环的关键：确保遍历状态与实际执行同步。
    """,
    inputSchema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "executed_action": {
                "type": "string",
                "description": "实际执行的动作"
            },
            "executed_element_id": {
                "type": "string",
                "description": "实际点击的元素 ID"
            },
            "resulting_state_id": {
                "type": "string",
                "description": "动作后的页面状态 ID"
            },
            "resulting_elements": {
                "type": "array",
                "description": "新页面的可点击元素列表"
            },
            "success": {
                "type": "boolean",
                "description": "动作是否成功执行"
            },
            "error_message": {
                "type": "string",
                "description": "失败时的错误信息"
            }
        },
        "required": ["session_id", "executed_action", "resulting_state_id", "success"]
    }
)

# 返回示例
{
    "success": true,
    "state_updated": true,
    "next_action_hint": "等待下一次 get_next_action 调用",
    "debug_info": {
        "stack_size": 3,
        "visited_nodes": 15
    }
}
```

#### locate_element（新增）

```python
Tool(
    name="locate_element",
    description="""
    通过语义搜索定位元素。返回元素详情及其所在页面。

    用途：
    - 测试用例执行前，查找目标按钮位置
    - 确认元素是否存在
    - 获取元素所在页面信息

    支持模糊语义搜索，如"支付按钮"、"登录输入框"。
    """,
    inputSchema={
        "type": "object",
        "properties": {
            "project_root": {"type": "string"},
            "query": {
                "type": "string",
                "description": "查询文本，如'支付按钮'、'登录'、'提交表单'"
            },
            "limit": {
                "type": "integer",
                "description": "返回结果数量限制",
                "default": 5
            }
        },
        "required": ["project_root", "query"]
    }
)

# 返回示例
{
    "success": true,
    "query": "支付按钮",
    "results": [
        {
            "element_id": "btn_pay",
            "text_content": "确认支付",
            "page_state_id": "abc123...",
            "page_name": "订单确认页",
            "semantic_role": "BUTTON",
            "confidence": 0.95,
            "element_info": {
                "bounds": "[100,500][300,550]",
                "enabled": true,
                "clickable": true
            }
        }
    ],
    "total_results": 1
}
```

#### start_traversal（改造）

```python
Tool(
    name="start_traversal",
    description="""
    开始新的遍历会话。

    初始化 DFS 遍历栈，创建新的遍历会话。
    如果项目已有旧地图，会将旧节点标记为 DEPRECATED，准备增量更新。
    """,
    inputSchema={
        "type": "object",
        "properties": {
            "project_root": {"type": "string"},
            "target_package": {
                "type": "string",
                "description": "目标应用包名，用于安全过滤"
            },
            "max_depth": {
                "type": "integer",
                "description": "最大遍历深度",
                "default": 10
            },
            "mode": {
                "type": "string",
                "enum": ["full", "incremental"],
                "description": "遍历模式：full=完全重新遍历, incremental=增量更新",
                "default": "incremental"
            }
        },
        "required": ["project_root", "target_package"]
    }
)

# 返回示例
{
    "success": true,
    "session_id": "traversal_20250528_153000",
    "status": "IDLE",
    "existing_nodes": 45,          # 旧地图节点数
    "mode": "incremental",
    "message": "遍历会话已创建，请使用 get_next_action 开始遍历"
}
```

---

## 7. 数据模型

### 7.1 核心数据结构

#### MapNode（节点）

```python
class MapNode(BaseModel):
    """应用地图的节点，代表一个页面状态"""
    state_id: str                    # 页面状态指纹（SHA256）
    semantic_name: str               # 页面语义名称
    business_context: Optional[str]  # 业务上下文描述
    status: NodeStatus               # 节点状态：ACTIVE/DEPRECATED
    elements: List[MapElement]       # 页面元素列表
    edges: List[Edge]                # 出边列表
    metadata: Optional[dict]         # 元数据
```

#### MapElement（元素）

```python
class MapElement(BaseModel):
    """页面元素"""
    original_id: str                 # 元素唯一标识
    element_name: Optional[str]      # 元素名称
    text_content: Optional[str]      # 元素文案
    ai_description: Optional[str]    # AI 描述
    bounds: Optional[str]            # 元素位置
    clickable: bool                  # 是否可点击
    is_explored: bool                # 是否已探索
    target_node_id: Optional[str]    # 点击后跳转的目标节点
    semantic_role: ElementRole       # 元素角色
```

#### Edge（边）

```python
class Edge(BaseModel):
    """页面跳转边"""
    trigger_id: str                  # 触发元素 ID
    target_state: str                # 目标页面状态 ID
    action_type: ActionType          # 动作类型：TAP/LONG_PRESS/SWIPE
```

### 7.2 存储结构

#### 目录结构

```
aegis_output/
├── index.json              # 全局索引
├── traversal_state.json    # 遍历会话状态
├── nodes/                  # 节点分片存储
│   ├── abc123...json      # 节点文件
│   └── def456...json
├── vector_db/              # 向量索引
│   ├── vector_index.json  # 页面级索引
│   └── elements_index.json # 元素级索引（新增）
├── versions/               # 版本历史
│   ├── v1_summary.json
│   └── v2_summary.json
└── app_map.mmd            # Mermaid 可视化
```

#### 节点文件格式

```json
{
  "state_id": "sha256_hash",
  "semantic_name": "订单确认页",
  "business_context": "显示订单详情和支付按钮的确认页面",
  "status": "Active",
  "elements": [
    {
      "original_id": "btn_pay",
      "element_name": "支付按钮",
      "text_content": "确认支付",
      "semantic_role": "BUTTON",
      "is_explored": true,
      "target_node_id": "支付成功页hash"
    }
  ],
  "edges": [
    {
      "trigger_id": "btn_pay",
      "target_state": "支付成功页hash",
      "action_type": "TAP"
    }
  ],
  "metadata": {
    "first_seen": "2026-05-28T10:00:00",
    "last_seen": "2026-05-28T15:30:00",
    "version": 2,
    "depth": 3
  }
}
```

---

## 8. 安全机制

### 8.1 安全策略

| 安全策略 | 说明 | 实现 |
|----------|------|------|
| **危险关键词过滤** | 识别并跳过危险操作 | ✅ `SecurityFilter` |
| **包名边界检查** | 防止跨应用跳转 | ✅ `PackageNameGuard` |
| **浏览器跳转检测** | 防止跳转到外部浏览器 | ✅ `PackageNameGuard` |
| **系统应用检测** | 防止跳转到系统设置 | ✅ `PackageNameGuard` |
| **循环检测** | 防止死循环 | ✅ `visited_states` |
| **深度限制** | 防止无限深入 | ✅ `max_depth` |

### 8.2 危险关键词列表

```python
DANGEROUS_KEYWORDS = [
    # 账户操作
    "注销", "退出登录", "sign out", "log out", "logout",
    "删除账号", "delete account",

    # 数据操作
    "删除", "delete", "remove",
    "清除", "clear", "erase",

    # 支付操作
    "支付", "payment", "pay", "转账", "transfer",
    "购买", "buy", "purchase",

    # 敏感操作
    "绑定银行卡", "bank card", "credit card",
    "修改密码", "change password", "reset password",
    "开通会员", "subscribe", "vip",
]
```

### 8.3 安全过滤器使用

```python
# 在遍历过程中检查每个元素
should_explore, reason = security_filter.should_explore(
    element_text=element.get("text", ""),
    element_id=element["id"],
    current_package=current_package
)

if not should_explore:
    logger.warning(f"安全过滤拦截: {reason}")
    continue  # 跳过此元素
```

---

## 9. 性能要求

### 9.1 性能指标

| 指标 | 目标 | 说明 |
|------|------|------|
| 遍历速度 | 100 页面 / 30 分钟 | 包含动作执行和等待 |
| 查询响应 | < 1 秒 | 元素级查询 |
| 地图大小 | < 10 MB | 100 页面的地图文件 |
| 内存占用 | < 500 MB | 遍历过程中的内存 |

### 9.2 优化策略

| 策略 | 说明 | 优先级 |
|------|------|--------|
| 分片存储 | 每个节点独立文件，减少内存 | P0 |
| 懒加载 | 按需加载节点数据 | P1 |
| 索引优化 | 维护全局索引，快速查找 | P1 |
| 向量缓存 | 缓存向量计算结果 | P2 |

---

## 10. 开发计划

### 10.1 开发阶段

#### Phase 1: DFS 核心改造（P0）

**目标**: 改造遍历逻辑，实现 DFS 深度优先遍历

| 任务 | 工作量 | 负责模块 |
|------|--------|----------|
| 实现 `TraversalSession` 类 | 2-3h | 新增 |
| 实现遍历栈管理 | 1-2h | 新增 |
| 重构 `get_next_action` | 2-3h | 改造 `server.py` |
| 实现 `report_execution` | 2-3h | 新增 |
| 循环检测机制 | 1h | 集成到 Session |
| 单元测试 | 2-3h | `tests/` |

**总计**: 约 10-15 小时

#### Phase 2: 元素级索引（P1）

**目标**: 实现元素级别的向量索引

| 任务 | 工作量 | 负责模块 |
|------|--------|----------|
| 扩展 `VectorIndexer` | 2-3h | 改造 `vector_indexer.py` |
| 实现 `locate_element` | 1-2h | 新增工具 |
| 索引性能优化 | 2-3h | 优化 |
| 单元测试 | 1-2h | `tests/` |

**总计**: 约 6-10 小时

#### Phase 3: 边界情况处理（P1）

**目标**: 处理各种异常和边界情况

| 任务 | 工作量 | 负责模块 |
|------|--------|----------|
| 弹窗识别与处理 | 3-4h | 集成到遍历逻辑 |
| 回溯失败恢复 | 2-3h | 完善 `traversal.py` |
| 崩溃节点标记 | 1-2h | 异常处理 |
| 加载状态检测 | 2-3h | 状态识别 |

**总计**: 约 8-12 小时

#### Phase 4: 增量更新完善（P2）

**目标**: 完善增量更新和版本管理

| 任务 | 工作量 | 负责模块 |
|------|--------|----------|
| 版本管理机制 | 2-3h | 新增 |
| 差异对比报告 | 2-3h | 新增 |
| 历史版本查询 | 1-2h | 新增工具 |

**总计**: 约 5-8 小时

### 10.2 总体工作量估算

| 阶段 | 工作量 | 优先级 |
|------|--------|--------|
| Phase 1 | 10-15h | P0 |
| Phase 2 | 6-10h | P1 |
| Phase 3 | 8-12h | P1 |
| Phase 4 | 5-8h | P2 |
| **总计** | **29-45h** | - |

### 10.3 里程碑

| 里程碑 | 交付物 | 预计时间 |
|--------|--------|----------|
| M1: DFS 核心完成 | 完整的 DFS 遍历能力 | 1-2 天 |
| M2: 元素级索引完成 | 快速查询元素位置 | +1 天 |
| M3: 边界情况处理完成 | 稳定的遍历执行 | +1-2 天 |
| M4: 增量更新完成 | 完整的版本管理 | +1 天 |

---

## 11. 验收标准

### 11.1 遍历完整性

- [ ] 能遍历到所有可达页面（至少 5 层深度）
- [ ] 每个页面的所有 clickable 元素都被记录
- [ ] 不出现循环导致的死遍历
- [ ] 遍历可中断和恢复
- [ ] 遍历过程符合 DFS 深度优先策略

### 11.2 数据准确性

- [ ] 状态指纹稳定（同一页面多次访问指纹一致）
- [ ] 元素信息完整（id, text, role 都有）
- [ ] 边关系正确（trigger → target 对应准确）
- [ ] 动态内容不影响指纹识别

### 11.3 查询能力

- [ ] 能通过"支付按钮"查询到具体元素
- [ ] 返回结果包含 element_id 和所在页面
- [ ] 查询响应时间 < 1s
- [ ] 支持模糊语义搜索

### 11.4 安全性

- [ ] 危险操作被正确过滤
- [ ] 不发生跨应用跳转
- [ ] 不跳转到浏览器
- [ ] 循环被正确检测

### 11.5 增量更新

- [ ] 重复遍历不创建重复节点
- [ ] 能正确识别新增的功能
- [ ] 废弃功能被正确标记
- [ ] 版本历史可查询

---

## 12. 附录

### 12.1 术语表

| 术语 | 定义 |
|------|------|
| **状态指纹** | 通过页面结构计算的 SHA256 哈希，用于唯一标识页面状态 |
| **DFS 栈** | 深度优先遍历过程中维护的页面访问栈 |
| **元素级索引** | 针对每个元素建立的向量索引，支持精确元素定位 |
| **MCP** | Model Context Protocol，模型上下文协议 |
| **Maestro MCP** | 移动端自动化 MCP 服务，负责执行 tap/scroll 等动作 |
| **软匹配** | 通过元素集合相似度判定页面是否相同 |
| **硬重置** | 重启应用并重新导航到目标状态 |
| **增量更新** | 在已有地图基础上，识别新增/修改/删除的功能 |

### 12.2 参考资料

| 文档 | 说明 |
|------|------|
| `PRD.md` | 原始产品需求文档 |
| `map_schema.json` | 地图数据结构定义 |
| `src/aegis_cartographer/models.py` | 数据模型实现 |
| `src/aegis_cartographer/server.py` | MCP 服务器实现 |
| `src/aegis_cartographer/app_map.py` | 地图管理实现 |
| `src/aegis_cartographer/fingerprint.py` | 状态指纹实现 |
| `src/aegis_cartographer/security.py` | 安全机制实现 |
| `src/aegis_cartographer/traversal.py` | 遍历引擎实现（需改造） |
| `src/aegis_cartographer/vector_indexer.py` | 向量索引实现（需改造） |

### 12.3 变更记录

| 版本 | 日期 | 变更说明 | 作者 |
|------|------|----------|------|
| v3.0 | 2026-05-28 | 完整需求文档，整合讨论方案和现有实现 | - |
| v2.0 | 2026-05-28 | 初版设计规范，基于讨论结果 | - |
| v1.0 | - | 原始 PRD | - |

---

**文档结束**
