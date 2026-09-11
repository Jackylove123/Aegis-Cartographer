import asyncio
import json
import logging
import os
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from aegis_cartographer.app_map import AppMapManager
from aegis_cartographer.config_manager import get_config_manager
from aegis_cartographer.hierarchy_parser import (
    compute_state_id,
    extract_clickable_elements,
)
from aegis_cartographer.maestro_driver import MaestroDriver
from aegis_cartographer.models import Edge, MapElement
from aegis_cartographer.traversal_session import SessionManager

# 设置日志，让我们能在控制台看到 ID 截断等警告
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MCPServerLogic:
    def __init__(self):
        self.managers: dict[str, AppMapManager] = {}
        self.session_manager = SessionManager()  # 遍历会话管理器
        self.config_managers: dict[str, Any] = {}  # 配置管理器
        self.drivers: dict[str, MaestroDriver] = {}  # MaestroDriver 缓存

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
                    if "aegis_output/" in f.read():
                        return
                with open(gitignore_path, "a", encoding="utf-8") as f:
                    f.write(entry)
            else:
                with open(gitignore_path, "w", encoding="utf-8") as f:
                    f.write(entry)
        except OSError as error:
            logger.warning("Unable to update .gitignore: %s", error)

    def _get_config_manager(self, project_root: str):
        """获取配置管理器"""
        root = os.path.abspath(project_root)
        if root not in self.config_managers:
            self.config_managers[root] = get_config_manager(root)
        return self.config_managers[root]

    def execute_tool(self, tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
        project_root_raw = parameters.pop("project_root", None)
        if not project_root_raw:
            return {"success": False, "error": "AI must provide project_root"}

        project_root = str(project_root_raw)

        try:
            # ========== 新增：遍历会话工具 ==========
            if tool_name == "start_traversal":
                return self._handle_start_traversal(project_root, parameters)

            if tool_name == "get_next_action_v2":
                return self._handle_get_next_action_v2(project_root, parameters)

            if tool_name == "report_execution":
                return self._handle_report_execution(parameters)

            if tool_name == "get_traversal_status":
                return self._handle_get_traversal_status(parameters)

            if tool_name == "step_traverse":
                return self._handle_step_traverse(project_root, parameters)

            # ========== 新增：配置管理工具 ==========
            if tool_name == "check_project_config":
                return self._handle_check_project_config(project_root)

            if tool_name == "get_project_config":
                return self._handle_get_project_config(project_root)

            if tool_name == "save_project_config":
                return self._handle_save_project_config(project_root, parameters)

            if tool_name == "get_traversal_guide":
                return self._handle_get_traversal_guide(parameters.get("language", "zh"))

            # ========== 原有工具 ==========
            manager = self._get_manager(project_root)

            if tool_name == "upsert_node":
                state_id = parameters.pop("state_id")
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
                manager.add_edge(state_id, edge)
                return {"success": True, "message": f"Link recorded: {trigger_id} -> {target_state}"}

            elif tool_name == "semantic_search":
                query = parameters.get("query")
                if not query:
                    return {"success": False, "error": "query is required"}

                results = manager.vector_index.search_semantic(query)
                return {"success": True, "results": results}

            return {"success": False, "error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            logger.exception("MCP Tool Execution Error")
            return {"success": False, "error": str(e)}

    # ========== 新增方法：遍历会话管理 ==========

    def _handle_start_traversal(self, project_root: str, parameters: dict) -> dict:
        """开始新的遍历会话"""
        target_package = parameters.get("target_package", "")
        mode = parameters.get("mode", "incremental")
        max_depth = parameters.get("max_depth", 10)

        # 检查配置文件是否存在
        config_manager = self._get_config_manager(project_root)
        if not config_manager.config_exists():
            logger.info(f"配置文件不存在: {config_manager.config_path}")
            return {
                "success": False,
                "error": "config_required",
                "session_id": None,
                "status": "config_required",
                "message": "【停止】项目配置文件 aegis_config.json 不存在。请先创建配置文件后再继续遍历。",
                "config_path": str(config_manager.config_path),
                "what_to_do": [
                    "1. 立即停止遍历流程",
                    "2. 向用户询问：项目名称和测试账号（如果需要登录）",
                    "3. 调用 save_project_config 工具保存配置",
                    "4. 重新调用 start_traversal 开始遍历"
                ],
                "example_prompt": "项目配置文件不存在。请问这个应用叫什么名字？如果需要登录，请提供测试账号（用户名和密码）。"
            }

        # 创建新会话
        session = self.session_manager.create_session(
            project_root=project_root,
            target_package=target_package,
        )

        # 增量更新模式：标记现有节点为 DEPRECATED
        if mode == "incremental":
            logger.info("增量更新模式，现有节点将重新验证")

        return {
            "success": True,
            "session_id": session.session_id,
            "status": session.status.value,
            "mode": mode,
            "max_depth": max_depth,
            "message": "遍历会话已创建。请先导航到应用入口页面，然后调用 get_next_action_v2 开始遍历。",
            "critical_instruction": "【重要】遍历必须持续调用 get_next_action_v2，直到返回 action=FINISH。每次执行动作后必须再次调用 get_next_action_v2，不得中断。",
            "usage_guide": {
                "step_1": "导航到应用入口页面",
                "step_2": "获取当前页面元素（Maestro inspect_screen）",
                "step_3": "调用 get_next_action_v2(session_id, current_state_id, current_elements) 获取下一步动作",
                "step_4": "使用 Maestro 执行返回的动作（tap/back）",
                "step_5": "获取新页面元素（Maestro inspect_screen）",
                "step_6": "调用 report_execution(session_id, executed_action, from_state_id, resulting_state_id, success) 上报执行结果",
                "step_7": "【关键】重复步骤 3-6，直到 action=FINISH。只有看到 action=FINISH 才表示遍历完成。",
                "important": [
                    "必须持续调用 get_next_action_v2，不要手动使用 Maestro 操作",
                    "每次执行动作后必须再次调用 get_next_action_v2",
                    "只有返回 action=FINISH 时才停止",
                    "遇到登录页面时，从配置获取测试账号"
                ]
            }
        }

    def _handle_get_next_action_v2(self, project_root: str, parameters: dict) -> dict:
        """获取下一步动作（DFS 版本）"""
        session_id = parameters.get("session_id")
        current_state_id = parameters.get("current_state_id")
        current_elements = parameters.get("current_elements", [])

        if not session_id:
            return {"success": False, "error": "session_id is required"}
        if not current_state_id:
            return {"success": False, "error": "current_state_id is required"}

        session = self.session_manager.get_session(session_id)
        if not session:
            return {"success": False, "error": f"Session not found: {session_id}"}

        # 如果是首次调用，启动遍历
        if session.status.value == "idle":
            elements_list = current_elements if isinstance(current_elements, list) else []
            session.start(current_state_id, elements_list)

        # 获取下一步动作
        result = session.get_next_action(current_state_id, current_elements)

        # 如果有元素信息，同时记录到地图
        if result.get("success") and result.get("action") in ["CLICK", "BACK"]:
            manager = self._get_manager(project_root)

            # 记录当前节点
            map_elements = [
                MapElement(
                    original_id=(
                        elem.get("element_key")
                        or elem.get("element_id")
                        or elem.get("resource_id")
                        or elem.get("resource-id", "")
                    ),
                    text_content=elem.get("text_content") or elem.get("text", ""),
                    semantic_role="BUTTON",
                )
                for elem in current_elements
            ]

            manager.upsert_node(
                state_id=current_state_id,
                semantic_name=result.get("debug_info", {}).get("current_page", "Unknown"),
                elements=map_elements,
            )

        return result

    def _handle_report_execution(self, parameters: dict) -> dict:
        """上报执行结果"""
        session_id = parameters.get("session_id")
        executed_action = parameters.get("executed_action")
        executed_element_id = parameters.get("executed_element_id")
        from_state_id = parameters.get("from_state_id", "")
        resulting_state_id = parameters.get("resulting_state_id")
        success = parameters.get("success", True)

        if not session_id:
            return {"success": False, "error": "session_id is required"}

        session = self.session_manager.get_session(session_id)
        if not session:
            return {"success": False, "error": f"Session not found: {session_id}"}

        result = session.report_execution({
            "executed_action": executed_action,
            "executed_element_id": executed_element_id,
            "from_state_id": from_state_id,
            "resulting_state_id": resulting_state_id,
            "success": success,
        })

        return result

    def _handle_get_traversal_status(self, parameters: dict) -> dict:
        """获取遍历状态"""
        session_id = parameters.get("session_id")

        if not session_id:
            return {"success": False, "error": "session_id is required"}

        session = self.session_manager.get_session(session_id)
        if not session:
            return {"success": False, "error": f"Session not found: {session_id}"}

        stats = session.get_statistics()
        stats["stack_summary"] = session.get_stack_summary()

        return {
            "success": True,
            "statistics": stats,
        }

    def _get_driver(self, session_id: str, device_id: str, app_id: str) -> MaestroDriver:
        """获取或创建 MaestroDriver 实例"""
        if session_id not in self.drivers:
            self.drivers[session_id] = MaestroDriver(
                device_id=device_id, app_id=app_id
            )
        return self.drivers[session_id]

    def _handle_step_traverse(self, project_root: str, parameters: dict) -> dict:
        """
        单步自动遍历：内部自动完成 hierarchy → 元素提取 → DFS 决策 → 执行点击 → 上报结果。

        AI 只需看返回结果确认：
        - action=PAUSE: 需要介入处理（登录页、弹窗等）
        - action=FINISH: 遍历完成
        - 其他: 继续调用 step_traverse

        不需要 AI 手动调用 Maestro 或提取元素。
        """
        session_id = parameters.get("session_id")
        device_id = parameters.get("device_id", "")
        app_id = parameters.get("app_id", "")

        if not session_id:
            return {"success": False, "error": "session_id is required"}

        session = self.session_manager.get_session(session_id)
        if not session:
            return {"success": False, "error": f"Session not found: {session_id}"}

        # 获取 driver
        if not device_id and session.target_package:
            device_id = ""
        driver = self._get_driver(session_id, device_id, app_id or session.target_package)

        # ===== Step 1: 获取当前屏幕 hierarchy =====
        hierarchy = driver.get_hierarchy()
        if not hierarchy:
            return {
                "success": False,
                "error": "无法获取屏幕 hierarchy，请检查设备连接",
                "action": "PAUSE",
                "reason": "设备连接失败",
            }

        # ===== Step 2: 计算 state_id 并提取 clickable 元素 =====
        state_id = compute_state_id(hierarchy)
        clickable_elements = extract_clickable_elements(hierarchy)

        if not clickable_elements:
            # 没有可点击元素，尝试回退
            logger.info(f"页面无可点击元素 (state_id: {state_id[:16]}...)")

        # ===== Step 3: 调用 DFS 决策 =====
        # 第一次调用时初始化 session
        if session.status.value == "idle":
            session.start(state_id, clickable_elements)

        next_action = session.get_next_action(state_id, clickable_elements)

        action = next_action.get("action", "")

        # ===== Step 4: 遍历完成 =====
        if action == "FINISH":
            return next_action

        # ===== Step 5: 记录当前节点到地图 =====
        manager = self._get_manager(project_root)
        map_elements = [
            MapElement(
                original_id=(
                    elem.get("element_key")
                    or elem.get("resource-id")
                    or elem.get("content-desc", "")
                    or f"_pos_{elem.get('bounds', '')}"
                ),
                element_name=elem.get("text", ""),
                bounds=elem.get("bounds", ""),
                semantic_role="BUTTON",
            )
            for elem in clickable_elements
        ]
        # 自动推断页面名称
        page_name = next_action.get("debug_info", {}).get("current_page", "Unknown")
        if page_name == "Unknown" or page_name.startswith("页面_"):
            # 尝试从元素推断
            for elem in clickable_elements:
                txt = elem.get("text", "")
                rid = elem.get("resource-id", "")
                if "title" in rid.lower() or "toolbar" in rid.lower():
                    page_name = txt or page_name
                    break

        manager.upsert_node(
            state_id=state_id,
            semantic_name=page_name,
            elements=map_elements,
        )

        # ===== Step 6: 检测登录页面 → PAUSE =====
        is_login = next_action.get("is_login_page", False)
        if is_login:
            return {
                "success": True,
                "action": "PAUSE",
                "reason": "检测到登录页面，请使用测试账号登录后继续",
                "state_id": state_id,
                "element_count": len(clickable_elements),
                "what_to_do": [
                    "1. 使用 Maestro inputText 输入用户名和密码",
                    "2. 点击登录按钮",
                    "3. 再次调用 step_traverse 继续",
                ],
            }

        # ===== Step 7: 执行动作 =====
        if action == "CLICK":
            element_id = next_action.get("element_id", "")
            resource_id = next_action.get("resource_id", "")
            text_content = next_action.get("text_content", "")

            # 优先用 resource-id，其次用 text，最后用 bounds
            tap_result = None
            if resource_id:
                tap_result = driver.tap_on(element_id=resource_id)
            elif element_id and not element_id.startswith("_"):
                tap_result = driver.tap_on(element_id=element_id)
            elif text_content:
                tap_result = driver.tap_on(text=text_content)
            else:
                # 找到元素的 bounds 用坐标点击
                for elem in clickable_elements:
                    rid = elem.get("resource-id", "")
                    txt = elem.get("text", "")
                    if (
                        elem.get("element_key") == element_id
                        or (rid and rid == resource_id)
                        or txt == text_content
                    ):
                        tap_result = driver.tap_on(bounds=elem.get("bounds", ""))
                        break
                if not tap_result:
                    tap_result = {"success": False, "error": f"无法定位元素: {element_id}"}

            if not tap_result.get("success"):
                logger.warning(f"点击失败: {element_id}, error: {tap_result.get('error', '')}")
                # 点击失败时标记元素为已探索（跳过），继续下一个
                session.report_execution({
                    "executed_action": "CLICK",
                    "executed_element_id": element_id,
                    "from_state_id": state_id,
                    "resulting_state_id": state_id,  # 页面没变
                    "success": True,  # 标记成功以跳过此元素
                })
                return {
                    "success": True,
                    "action": "CONTINUE",
                    "reason": f"点击 {element_id} 失败，已跳过",
                    "element_id": element_id,
                }

            # 点击成功，获取新页面
            import time
            time.sleep(0.5)  # 等待页面动画

            new_hierarchy = driver.get_hierarchy()
            if not new_hierarchy:
                return {
                    "success": False,
                    "action": "PAUSE",
                    "reason": "点击后无法获取屏幕 hierarchy",
                    "element_id": element_id,
                }

            new_state_id = compute_state_id(new_hierarchy)

            # 上报执行结果
            session.report_execution({
                "executed_action": "CLICK",
                "executed_element_id": element_id,
                "from_state_id": state_id,
                "resulting_state_id": new_state_id,
                "success": True,
            })

            # 记录边
            if new_state_id != state_id:
                from aegis_cartographer.models import Edge as MapEdge
                manager.add_edge(state_id, MapEdge(
                    trigger_id=element_id,
                    target_state=new_state_id,
                ))

            # 返回结果给 AI 审核
            page_changed = new_state_id != state_id

            return {
                "success": True,
                "action": "CONTINUE",
                "reason": next_action.get("reason", ""),
                "executed_element": element_id,
                "page_changed": page_changed,
            }

        elif action == "BACK":
            back_result = driver.back()
            if not back_result.get("success"):
                logger.warning(f"back 失败: {back_result.get('error', '')}")

            import time
            time.sleep(0.5)

            # 获取回退后的页面
            new_hierarchy = driver.get_hierarchy()
            new_state_id = compute_state_id(new_hierarchy) if new_hierarchy else state_id

            # 上报
            session.report_execution({
                "executed_action": "BACK",
                "executed_element_id": "",
                "from_state_id": state_id,
                "resulting_state_id": new_state_id,
                "success": True,
            })

            return {
                "success": True,
                "action": "CONTINUE",
                "reason": next_action.get("reason", ""),
                "executed_action": "BACK",
            }

        # 未知动作
        return {
            "success": False,
            "action": "PAUSE",
            "reason": f"未知的 DFS 动作: {action}",
        }

    # ========== 新增方法：配置管理 ==========

    def _handle_check_project_config(self, project_root: str) -> dict:
        """检查项目配置是否存在"""
        config_manager = self._get_config_manager(project_root)
        exists = config_manager.config_exists()
        return {
            "success": True,
            "exists": exists,
            "config_path": str(config_manager.config_path),
        }

    def _handle_get_project_config(self, project_root: str) -> dict:
        """获取项目配置"""
        config_manager = self._get_config_manager(project_root)
        config = config_manager.get_config()

        if config is None:
            return {
                "success": True,
                "exists": False,
                "config": None,
                "message": "项目配置文件不存在，请先创建配置"
            }

        return {
            "success": True,
            "exists": True,
            "config": config,
        }

    def _handle_save_project_config(self, project_root: str, parameters: dict) -> dict:
        """保存项目配置"""
        config = parameters.get("config")

        if not config:
            return {
                "success": False,
                "error": "config 参数不能为空"
            }

        config_manager = self._get_config_manager(project_root)
        success = config_manager.save_config(config)

        if success:
            return {
                "success": True,
                "message": "配置已保存",
                "config_path": str(config_manager.config_path)
            }
        else:
            return {
                "success": False,
                "error": "保存配置失败"
            }

    def _handle_get_traversal_guide(self, language: str = "zh") -> dict:
        """获取遍历指南"""
        guide = {
            "title": "Aegis Cartographer 自动化遍历指南",
            "important": "开始遍历前，AI 必须严格按照此指南执行，不得手动使用 Maestro 操作",
            "workflow": [
                {
                    "step": 1,
                    "action": "检查项目配置",
                    "tool": "check_project_config",
                    "description": "检查项目是否有配置文件。如果不存在，AI 必须先咨询用户获取项目信息和测试账号，然后调用 save_project_config 保存配置。"
                },
                {
                    "step": 2,
                    "action": "创建遍历会话",
                    "tool": "start_traversal",
                    "description": "调用 start_traversal 创建遍历会话，获取 session_id"
                },
                {
                    "step": 3,
                    "action": "导航到应用入口页面",
                    "tool": "maestro",
                    "description": "使用 Maestro 确保应用在入口页面"
                },
                {
                    "step": 4,
                    "action": "获取当前页面元素",
                    "tool": "maestro inspect_screen",
                    "description": "获取当前页面的所有可点击元素"
                },
                {
                    "step": 5,
                    "action": "获取下一步动作",
                    "tool": "get_next_action_v2",
                    "description": "调用 get_next_action_v2(session_id, current_state_id, current_elements) 获取下一步动作",
                    "important": "必须持续调用此工具，直到返回 action=FINISH"
                },
                {
                    "step": 6,
                    "action": "执行动作",
                    "tool": "maestro",
                    "description": "使用 Maestro 执行 get_next_action_v2 返回的动作（tap/back）"
                },
                {
                    "step": 7,
                    "action": "获取新页面",
                    "tool": "maestro inspect_screen",
                    "description": "获取动作执行后的新页面元素"
                },
                {
                    "step": 8,
                    "action": "上报执行结果",
                    "tool": "report_execution",
                    "description": "调用 report_execution(session_id, executed_action, executed_element_id, from_state_id, resulting_state_id, success) 上报执行结果，更新遍历栈。\n关键：from_state_id 是执行前的状态，resulting_state_id 是执行后的状态，系统通过对比两者判断元素是否为导航元素。"
                },
                {
                    "step": 9,
                    "action": "继续遍历",
                    "tool": "get_next_action_v2",
                    "description": "重复步骤 5-8，直到 get_next_action_v2 返回 action=FINISH"
                }
            ],
            "rules": [
                "必须使用 get_next_action_v2 获取每个动作，不得自行决定",
                "必须使用 Maestro 执行动作，不得使用其他方式",
                "必须调用 report_execution 上报每个执行结果",
                "遇到登录页面时，从项目配置获取测试账号",
                "遍历期间不得中断，直到收到 FINISH 指令",
                "元素点击顺序：从左到右，从上到下（已在 get_next_action_v2 中自动处理）"
            ],
            "common_errors": [
                {
                    "error": "AI 直接使用 Maestro 操作",
                    "solution": "必须先调用 get_next_action_v2 获取指令"
                },
                {
                    "error": "AI 只调用一次 get_next_action_v2",
                    "solution": "必须持续循环调用，直到 action=FINISH"
                },
                {
                    "error": "AI 自己决定遍历策略",
                    "solution": "完全遵循 get_next_action_v2 返回的指令"
                }
            ],
            "example_dialog": "好的，我将开始自动化遍历。1. 调用 start_traversal... 2. 调用 get_next_action_v2... 3. 使用 Maestro 执行... 4. 继续调用 get_next_action_v2..."
        }

        return {
            "success": True,
            "guide": guide
        }


async def serve():
    logic = MCPServerLogic()
    server = Server("aegis-cartographer")

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        root_schema = {"type": "string", "description": "项目根目录绝对路径"}
        return [
            # ========== 遍历会话工具 ==========
            types.Tool(
                name="start_traversal",
                description="开始新的遍历会话。初始化 DFS 遍历栈，创建会话。",
                inputSchema={"type": "object", "properties": {
                    "project_root": root_schema,
                    "target_package": {"type": "string", "description": "目标应用包名"},
                    "mode": {"type": "string", "enum": ["full", "incremental"], "description": "遍历模式"},
                    "max_depth": {"type": "integer", "description": "最大深度"}
                }, "required": ["project_root", "target_package"]}
            ),
            types.Tool(
                name="get_next_action_v2",
                description="[核心工具] 获取下一步遍历动作。实现 DFS 深度优先遍历，返回明确的动作指令（CLICK/BACK/FINISH）。\n\n【关键规则】：\n1. 必须持续循环调用此工具，直到返回 action=FINISH\n2. 每次执行动作（tap/back）后，必须再次调用此工具获取下一步\n3. 只有看到 action=FINISH 才表示遍历完成\n4. 返回的 next_step 字段会明确告诉下一步做什么\n5. 不要中断，不要跳过调用，不要自行决定遍历策略",
                inputSchema={"type": "object", "properties": {
                    "project_root": root_schema,
                    "session_id": {"type": "string", "description": "遍历会话 ID"},
                    "current_state_id": {"type": "string", "description": "当前页面状态 ID（需自行计算或使用固定值）"},
                    "current_elements": {"type": "array", "description": "当前页面元素列表（从 Maestro inspect_screen 获取）", "items": {"type": "object"}}
                }, "required": ["project_root", "session_id", "current_state_id", "current_elements"]}
            ),
            types.Tool(
                name="report_execution",
                description="上报执行结果，更新遍历栈。执行动作后必须调用此工具。\n\n重要：from_state_id 用于检测页面是否变化，从而区分导航元素和非导航元素。",
                inputSchema={"type": "object", "properties": {
                    "session_id": {"type": "string", "description": "遍历会话 ID"},
                    "executed_action": {"type": "string", "description": "执行的动作"},
                    "executed_element_id": {"type": "string", "description": "执行的元素 ID"},
                    "from_state_id": {"type": "string", "description": "执行前的页面状态 ID"},
                    "resulting_state_id": {"type": "string", "description": "执行后的页面状态 ID"},
                    "success": {"type": "boolean", "description": "是否成功"}
                }, "required": ["session_id", "executed_action", "from_state_id", "resulting_state_id", "success"]}
            ),
            types.Tool(
                name="get_traversal_status",
                description="获取遍历会话的状态和统计信息",
                inputSchema={"type": "object", "properties": {
                    "session_id": {"type": "string", "description": "遍历会话 ID"}
                }, "required": ["session_id"]}
            ),
            types.Tool(
                name="get_traversal_guide",
                description="获取完整的自动化遍历指南。开始遍历前，AI 必须阅读此指南并严格按照指南执行。",
                inputSchema={"type": "object", "properties": {
                    "language": {"type": "string", "enum": ["zh", "en"], "description": "语言", "default": "zh"}
                }, "required": []}
            ),
            types.Tool(
                name="step_traverse",
                description="[快速遍历] 单步自动遍历。内部自动完成 hierarchy获取→元素提取→DFS决策→Maestro执行→结果上报，AI只需看返回结果。\\n\\n返回值中的 action 字段：\\n- CONTINUE: 一切正常，继续调用 step_traverse\\n- PAUSE: 需要AI介入（登录页、弹窗、设备断连等）\\n- FINISH: 遍历完成\\n\\n典型用法：\\n1. 先调用 start_traversal 创建会话\\n2. 确保应用已启动到入口页面\\n3. 循环调用 step_traverse 直到返回 FINISH\\n4. 遇到 PAUSE 时处理特殊情况后继续调用 step_traverse",
                inputSchema={"type": "object", "properties": {
                    "project_root": root_schema,
                    "session_id": {"type": "string", "description": "遍历会话 ID（由 start_traversal 返回）"},
                    "device_id": {"type": "string", "description": "设备 ID（Android 真机序列号等），首次调用时传入"},
                    "app_id": {"type": "string", "description": "应用包名（如 com.example.app），首次调用时传入"}
                }, "required": ["project_root", "session_id"]}
            ),
            # ========== 配置管理工具 ==========
            types.Tool(
                name="check_project_config",
                description="检查项目配置文件是否存在。用于判断是否需要创建新配置。",
                inputSchema={"type": "object", "properties": {
                    "project_root": root_schema
                }, "required": ["project_root"]}
            ),
            types.Tool(
                name="get_project_config",
                description="获取项目配置信息（包含测试账号等）。如果不存在返回 null。",
                inputSchema={"type": "object", "properties": {
                    "project_root": root_schema
                }, "required": ["project_root"]}
            ),
            types.Tool(
                name="save_project_config",
                description="保存项目配置信息。由 AI 根据用户输入生成配置后保存。",
                inputSchema={"type": "object", "properties": {
                    "project_root": root_schema,
                    "config": {
                        "type": "object",
                        "description": "配置对象，包含 accounts 等信息",
                        "properties": {
                            "project_name": {"type": "string"},
                            "target_package": {"type": "string"},
                            "accounts": {
                                "type": "object",
                                "description": "账号配置，如 {\"default\": {\"username\": \"xxx\", \"password\": \"xxx\"}}"
                            }
                        }
                    }
                }, "required": ["project_root", "config"]}
            ),
            # ========== 原有工具 ==========
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
