import asyncio
import os
import json
import logging
from typing import Any, Optional

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from aegis_cartographer.app_map import AppMapManager
from aegis_cartographer.models import Edge

# 设置日志，让我们能在控制台看到 ID 截断等警告
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MCPServerLogic:
    def __init__(self):
        self.managers: dict[str, AppMapManager] = {}

    def _get_manager(self, project_root: str) -> AppMapManager:
        root = os.path.abspath(project_root)
        
        if root not in self.managers:
            output_dir = os.path.join(root, "aegis_output")
            
            # 确保分片存储的根目录存在
            if not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)
            
            self._ensure_gitignore(root)
            
            # 🚀 重点：初始化 Manager，现在它管理的是整个文件夹
            self.managers[root] = AppMapManager(output_dir)
            
        return self.managers[root]

    def _ensure_gitignore(self, root: str):
        gitignore_path = os.path.join(root, ".gitignore")
        entry = "\n# Aegis Cartographer Output\naegis_output/\n"
        try:
            if os.path.exists(gitignore_path):
                with open(gitignore_path, "r", encoding="utf-8") as f:
                    if "aegis_output/" in f.read(): return
                with open(gitignore_path, "a", encoding="utf-8") as f:
                    f.write(entry)
            else:
                with open(gitignore_path, "w", encoding="utf-8") as f:
                    f.write(entry)
        except: pass

    def execute_tool(self, tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
        project_root_raw = parameters.pop("project_root", None)
        if not project_root_raw:
            return {"success": False, "error": "AI must provide project_root"}
        
        project_root = str(project_root_raw)

        try:
            manager = self._get_manager(project_root)
            
            if tool_name == "upsert_node":
                state_id = parameters.pop("state_id")
                # 🚀 内部现在会自动处理 ID 清洗和单文件保存
                node = manager.upsert_node(state_id=state_id, **parameters)
                return {
                    "success": True, 
                    "storage_dir": manager.nodes_dir,
                    "node": node.model_dump(mode="json")
                }
            
            elif tool_name == "get_status":
                return {
                    "success": True,
                    "project_root": project_root,
                    "nodes_count": manager.get_node_count(),
                    "mode": "Sharded (Index + Nodes)"
                }
            
            elif tool_name == "get_next_action":
                state_id = parameters.get("state_id")
                if not state_id:
                    return {"success": False, "error": "state_id is required"}
                
                node = manager.get_node(state_id)
                if not node:
                    return {
                        "success": True,
                        "action": "CLICK",
                        "element_id": None,
                        "reason": "新页面发现，请先执行 upsert_node 记录"
                    }
                
                # 寻找未探索的元素
                unexplored = [e for e in node.elements if not getattr(e, 'is_explored', False)]
                if unexplored:
                    elem = unexplored[0]
                    return {
                        "success": True,
                        "action": "CLICK",
                        "element_id": elem.original_id,
                        "reason": f"自主探索目标: {elem.element_name or elem.original_id}"
                    }
                
                return {
                    "success": True,
                    "action": "BACK",
                    "reason": "当前页面所有路径已探索完毕，执行回退"
                }
            
            elif tool_name == "add_edge":
                state_id = parameters.get("state_id")
                trigger_id = parameters.get("trigger_id")
                target_state = parameters.get("target_state")
                
                if not all([state_id, trigger_id, target_state]):
                    return {"success": False, "error": "Missing edge info"}
                
                edge = Edge(trigger_id=trigger_id, target_state=target_state)
                # 🚀 add_edge 内部会自动更新节点并持久化
                manager.add_edge(state_id, edge)
                return {"success": True, "message": f"Link recorded: {trigger_id} -> {target_state}"}
            
            elif tool_name == "semantic_search":
                query = parameters.get("query")
                if not query:
                    return {"success": False, "error": "query is required"}
                
                # 🚀 直接调用向量索引器
                results = manager.vector_index.search_semantic(query)
                return {"success": True, "results": results}
            
            return {"success": False, "error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            logger.exception("MCP Tool Execution Error")
            return {"success": False, "error": str(e)}


async def serve():
    logic = MCPServerLogic()
    server = Server("aegis-cartographer")

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        root_schema = {"type": "string", "description": "项目根目录绝对路径"}
        return [
            types.Tool(
                name="get_status",
                description="查看当前项目的分片测绘进度",
                inputSchema={"type": "object", "properties": {"project_root": root_schema}, "required": ["project_root"]}
            ),
            types.Tool(
                name="upsert_node",
                description="记录页面节点。详情会自动存入 nodes/ 文件夹以保持性能。",
                inputSchema={"type": "object", "properties": {**{"project_root": root_schema}, "state_id": {"type": "string"}, "semantic_name": {"type": "string"}, "business_context": {"type": "string"}, "elements": {"type": "array", "items": {"type": "object"}}}, "required": ["project_root", "state_id"]}
            ),
            types.Tool(
                name="get_next_action",
                description="[核心决策] 询问下一步该点哪里。返回 CLICK 或 BACK 指令。",
                inputSchema={"type": "object", "properties": {"project_root": root_schema, "state_id": {"type": "string"}}, "required": ["project_root", "state_id"]}
            ),
            types.Tool(
                name="add_edge",
                description="[连线] 记录页面跳转。会自动更新节点的探索状态。",
                inputSchema={"type": "object", "properties": {
                    "project_root": root_schema,
                    "state_id": {"type": "string", "description": "源页面ID"},
                    "trigger_id": {"type": "string", "description": "点击的ID"},
                    "target_state": {"type": "string", "description": "目标页面ID"}
                }, "required": ["project_root", "state_id", "trigger_id", "target_state"]}
            ),
            types.Tool(
                name="semantic_search",
                description="通过语义模糊搜索定位功能所在页面。",
                inputSchema={"type": "object", "properties": {"project_root": root_schema, "query": {"type": "string"}}, "required": ["project_root", "query"]}
            )
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        result = logic.execute_tool(name, arguments or {})
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(serve())