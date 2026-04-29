需求文档 (PRD)：Aegis Cartographer — 移动端语义测绘智能体 (v1.3 终极版)
1. 项目愿景
开发一个能够自主探索 Android/iOS 应用、识别业务逻辑并生成“自愈式”语义地图的中间体工具 。该工具通过 JSON 结构化存储与向量语义描述相结合，为 AI 自动化测试提供详尽的“产品说明书” 。本版本设定前提为：用户已手动完成应用登录，Agent 在已授权状态下开始测绘 。

2. 系统架构 (Technical Architecture)
Aegis Cartographer 作为一个自定义 MCP Server 运行，采用“三层交互”模式 ：

顶层 (Reasoning Layer)：Trae/Codex。下达高层指令（如“开始测绘”） 。

中间层 (Intelligence Layer)：Aegis Cartographer (本项目)。负责状态识别、逻辑建图、语义压缩、回溯规划 。

底层 (Execution Layer)：Maestro MCP。负责原子的 tap、scroll、get_hierarchy 操作 。

3. 核心算法逻辑规范
3.1 状态指纹精确定义 (State Fingerprinting)

为确保在不同设备及系统版本下的哈希一致性，必须严格执行以下过滤与计算逻辑 ：

排除项：严禁包含坐标 (bounds) 及动态内容 (text, content-desc)。

特征提取：遍历当前页面所有 clickable: true 的元素，仅提取四元组：(id,class,parent_id,depth)。

标准化排序：对提取出的四元组列表按字母顺序进行字典序排序 (Alphabetical Sorting)，以应对 XML 顺序的动态变动。

哈希生成公式：

State_Hash=SHA256(Sort( 
i=1
∑
n
​	
 (id 
i
​	
 ,class 
i
​	
 ,parent_id 
i
​	
 ,depth 
i
​	
 )))
输出：若指纹相同，视为同一页面，即使文案或数据发生变化 。

3.2 启发式多步交互决策 (Action Promotion)

AI 需维护“待探索队列”，并按照以下升级协议执行动作 ：

默认动作：首选执行 TAP 。

动作升级：若执行 TAP 后 State_Hash 未改变，且满足以下条件之一，则尝试 LONG_PRESS：

元素文案包含关键词（如：“更多”、“菜单”、“管理”、“...”）。

LLM 语义分析结果建议该按钮具备隐藏二级功能。

滑动发现：若页面 XML 包含 scrollable: true 属性且当前层级交互元素已遍历完毕，则触发 SWIPE 探索 。

4. 关键功能模块
4.1 LLM 语义分析规约 (Semantic Labeling)

强制格式：要求 LLM 必须以 JSON Mode 返回，严禁任何额外解释性文字 。
+1

标准 Schema：

JSON
{
  "element_intent": "核心业务目的描述",
  "action_weight": 10,
  "is_functional": true,
  "potential_states": ["预测跳转页面名"]
}
4.2 冲突解决与回溯机制 (Backtrack & Resilience)

执行 maestro back 后，通过指纹对比判定回位结果 ：
+1

软匹配 (Soft Match)：

判定：回退后的页面指纹与原指纹的功能元素交集 (Intersection) > 70%。

处理：视为回退成功，仅更新本地节点指纹 。
+1

硬重置 (Hard Reset)：

判定：匹配度 < 30% 或检测到非目标应用包名 。
+1

处理：执行 maestro stop -> 重启应用 -> 利用地图记录的最短路径重新导航至目标节点 。
+1

4.3 增量更新与生命周期 (Incremental Persistence)

持久化原则：若 State_Hash 已存在，禁止重写核心结构 。
+1

状态标记：

Active：本次遍历确认存在的元素。

Deprecated：旧地图存在但本次未扫描到的元素（不删除，可视化标记为虚线）。

版本管理：若同一页面指纹变化超过 50%，判定为版本大改，触发新建地图分支逻辑 。
+1

4.4 可视化导出 (Visual Mapping)

功能：实时将 JSON 拓扑转换为 Mermaid.js 代码 。
+1

目的：提供直观的“树干与树枝”拓扑图，方便人工校验功能路径 。
+1

5. 地图数据结构 (Map Schema)
JSON
{
  "project_name": "My_App",
  "nodes": [
    {
      "state_id": "SHA256_HASH",
      "semantic_name": "页面语义名称",
      "business_context": "页面业务逻辑详述",
      "status": "Active/Deprecated",
      "elements": [
        {
          "original_id": "com.app:id/btn_ok",
          "text_content": "确认支付",
          "ai_description": "描述",
          "semantic_role": "PRIMARY_ACTION"
        }
      ],
      "edges": [
        {
          "trigger_id": "btn_ok",
          "target_state": "SUCCESS_PAGE_HASH",
          "action_type": "TAP/LONG_PRESS/SWIPE"
        }
      ]
    }
  ]
}
6. 开发任务清单 (Tasks for Codex)
Task 1 (基础架构)：创建 Python MCP Server，定义 AppMap 类处理符合 Schema 的 JSON 读写与 status 标记 。
+1

Task 2 (指纹计算)：实现 get_skeleton_hash，严格执行提取四元组、排序、SHA256 计算的逻辑 。
+1

Task 3 (遍历与回溯)：实现 BFS 遍历循环及 Hard Reset 逻辑，集成“软匹配”判定机制 。
+1

Task 4 (安全与过滤)：集成 Action_Blacklist（注销、删除等）过滤，并确保遍历不超出目标应用包名 。
+1

7. 验收标准
数据免疫：动态数据更新后不产生重复 State，且不覆盖现有正确节点 。

安全可靠：遍历不触发高危动作，崩溃后能根据路径记录自愈 。

拓扑可视化：能够输出清晰的 Mermaid 路径图 。