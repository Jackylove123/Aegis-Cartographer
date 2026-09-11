"""
DFS 遍历会话管理

维护深度优先遍历的状态栈，确保遍历的确定性和完整性。
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class TraversalStatus(str, Enum):
    """遍历状态"""
    IDLE = "idle"
    VISITING = "visiting"
    EXPLORING = "exploring"
    BACKTRACKING = "backtracking"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class ElementInfo:
    """元素信息"""
    element_id: str
    text_content: str
    is_explored: bool = False
    semantic_role: str = "UNKNOWN"
    resource_id: str = ""
    bounds: list = None  # [left, top, right, bottom] 或 [x1, y1, x2, y2]

    def get_center_y(self) -> int:
        """获取元素中心 Y 坐标（用于从上到下排序）"""
        if self.bounds and len(self.bounds) >= 4:
            return (self.bounds[1] + self.bounds[3]) // 2
        return 0

    def get_center_x(self) -> int:
        """获取元素中心 X 坐标（用于从左到右排序）"""
        if self.bounds and len(self.bounds) >= 4:
            return (self.bounds[0] + self.bounds[2]) // 2
        return 0

    def to_dict(self) -> dict:
        return {
            "element_id": self.element_id,
            "text_content": self.text_content,
            "is_explored": self.is_explored,
            "semantic_role": self.semantic_role,
            "resource_id": self.resource_id,
            "bounds": self.bounds,
        }


@dataclass
class TraversalFrame:
    """遍历栈中的一帧，代表一个页面的遍历状态"""
    state_id: str                    # 当前页面状态 ID
    semantic_name: str                # 页面语义名称
    elements: List[ElementInfo]       # 页面可点击元素列表
    current_index: int = 0            # 当前探索到的元素索引
    visited: bool = False             # 是否已访问过

    def get_current_element(self) -> Optional[ElementInfo]:
        """获取当前待探索的元素"""
        if self.current_index < len(self.elements):
            return self.elements[self.current_index]
        return None

    def advance(self) -> Optional[ElementInfo]:
        """前进到下一个元素，返回当前元素"""
        elem = self.get_current_element()
        if elem:
            self.current_index += 1
        return elem

    def mark_current_explored(self) -> bool:
        """标记当前元素已探索"""
        elem = self.get_current_element()
        if elem and not elem.is_explored:
            # 这里需要通过 index 找到实际元素并标记
            # current_index 指向下一个未探索的，所以 current_index - 1 是刚探索的
            if self.current_index > 0 and self.current_index - 1 < len(self.elements):
                self.elements[self.current_index - 1].is_explored = True
            return True
        return False

    def refresh_elements(self, elements: List["ElementInfo"]) -> None:
        """Refresh observations without losing already-explored element state.

        Element identity uses both the observed ID and bounds. Bounds are only a
        disambiguator here; they are not promoted to the permanent map identity.
        """

        def identity(element: ElementInfo) -> tuple[str, tuple[int, int, int, int]]:
            bounds = element.bounds or [0, 0, 0, 0]
            return element.element_id, tuple(bounds)

        previous = {identity(element): element for element in self.elements}
        refreshed: List[ElementInfo] = []
        for element in elements:
            original = previous.get(identity(element))
            if original is not None:
                original.text_content = element.text_content
                original.resource_id = element.resource_id
                original.bounds = element.bounds
                refreshed.append(original)
            else:
                refreshed.append(element)

        self.elements = refreshed
        self.current_index = min(self.current_index, len(self.elements))

    def has_unexplored_elements(self) -> bool:
        """是否还有未探索的元素"""
        for elem in self.elements[self.current_index:]:
            if not elem.is_explored:
                return True
        return False

    def get_next_unexplored_element(self) -> Optional[ElementInfo]:
        """获取下一个未探索的元素"""
        for i in range(self.current_index, len(self.elements)):
            if not self.elements[i].is_explored:
                self.current_index = i
                return self.elements[i]
        return None

    def to_dict(self) -> dict:
        return {
            "state_id": self.state_id,
            "semantic_name": self.semantic_name,
            "elements_count": len(self.elements),
            "current_index": self.current_index,
            "visited": self.visited,
            "unexplored_count": sum(1 for e in self.elements if not e.is_explored),
        }


@dataclass
class ExecutionRecord:
    """执行记录"""
    timestamp: float = field(default_factory=time.time)
    action: str = ""
    element_id: str = ""
    from_state: str = ""
    to_state: str = ""
    success: bool = True
    error_message: str = ""


class TraversalSession:
    """
    DFS 遍历会话

    维护遍历状态栈，实现深度优先搜索策略。
    """

    def __init__(self, session_id: str, project_root: str, target_package: str = ""):
        self.session_id = session_id
        self.project_root = project_root
        self.target_package = target_package

        # 遍历状态
        self.status = TraversalStatus.IDLE

        # DFS 栈
        self.stack: List[TraversalFrame] = []

        # 已访问状态集合（防循环）
        self.visited_states: Set[str] = set()

        # 执行记录
        self.execution_history: List[ExecutionRecord] = []

        # 统计信息
        self.total_nodes_visited = 0
        self.total_elements_explored = 0
        self.max_depth_reached = 0

        # 时间戳
        self.created_at = datetime.now()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None

        logger.info(f"创建遍历会话: {self.session_id}")

    def start(self, initial_state_id: str, initial_elements: List[dict]) -> dict:
        """
        开始遍历

        Args:
            initial_state_id: 初始页面状态 ID
            initial_elements: 初始页面元素列表

        Returns:
            开始结果
        """
        self.status = TraversalStatus.VISITING
        self.started_at = datetime.now()

        # 创建初始帧
        elements_info = self._parse_elements(initial_elements)
        initial_frame = TraversalFrame(
            state_id=initial_state_id,
            semantic_name="入口页面",
            elements=elements_info,
        )

        self.stack.append(initial_frame)
        self.visited_states.add(initial_state_id)
        self.total_nodes_visited += 1

        logger.info(f"遍历开始，初始状态: {initial_state_id[:16]}...，元素数: {len(elements_info)}")

        return {
            "success": True,
            "session_id": self.session_id,
            "status": self.status.value,
            "message": "遍历已开始，请调用 get_next_action 获取下一步",
        }

    def _parse_elements(self, elements: List[dict]) -> List[ElementInfo]:
        """解析元素列表，并按位置排序（从上到下，从左到右）"""
        parsed = []
        for idx, elem in enumerate(elements):
            resource_id = (
                elem.get("resource_id")
                or elem.get("resource-id")
                or elem.get("id", "")
            )
            element_id = (
                elem.get("element_key")
                or elem.get("element_id")
                or resource_id
            )
            text_content = elem.get("text_content") or elem.get("text", "")

            # 解析 bounds
            bounds = self._parse_bounds(elem.get("bounds", ""))

            # 如果元素没有 ID，生成一个基于位置的临时 ID
            if not element_id:
                element_id = f"_no_id_{idx}_{bounds[0]}_{bounds[1]}"

            parsed.append(ElementInfo(
                element_id=element_id,
                text_content=text_content,
                resource_id=resource_id,
                bounds=bounds,
            ))

        # 按位置排序：先按 Y 坐标（行），再按 X 坐标（列）
        parsed.sort(key=lambda e: (e.get_center_y(), e.get_center_x()))

        return parsed

    def _parse_bounds(self, bounds_str: str) -> list:
        """解析 bounds 字符串为坐标列表

        支持格式：
        - "[100,200][300,400]" → [100, 200, 300, 400]
        - "100,200,300,400" → [100, 200, 300, 400]
        """
        if not bounds_str:
            return [0, 0, 0, 0]

        import re
        # 提取所有数字
        numbers = re.findall(r'\d+', bounds_str)
        if len(numbers) >= 4:
            return [int(numbers[0]), int(numbers[1]), int(numbers[2]), int(numbers[3])]
        return [0, 0, 0, 0]

    def _detect_login_page(self, elements: List[ElementInfo]) -> bool:
        """检测是否为登录页面"""
        login_keywords = ["登录", "login", "账号", "account", "密码", "password", "用户名", "username", "微信", "wechat"]
        for elem in elements:
            text_lower = elem.text_content.lower() if elem.text_content else ""
            id_lower = elem.element_id.lower() if elem.element_id else ""
            for keyword in login_keywords:
                if keyword in text_lower or keyword in id_lower:
                    return True
        return False

    def get_next_action(self, current_state_id: str, current_elements: List[dict]) -> dict:
        """
        获取下一步动作

        核心遍历逻辑：
        1. 如果是新页面，创建新帧入栈
        2. 如果当前页有未探索元素，返回 CLICK
        3. 如果当前页所有元素已探索，返回 BACK
        4. 如果栈为空，返回 FINISH

        Args:
            current_state_id: 当前页面状态 ID
            current_elements: 当前页面元素列表

        Returns:
            下一步动作指令
        """
        self.status = TraversalStatus.EXPLORING

        # 更新最大深度
        self.max_depth_reached = max(self.max_depth_reached, len(self.stack))

        # 检查是否是新页面
        if current_state_id not in self.visited_states:
            return self._handle_new_state(current_state_id, current_elements)
        elif current_state_id in self.visited_states and (not self.stack or self.stack[-1].state_id != current_state_id):
            # 已访问但不在栈顶，可能已经回溯了
            return self._handle_revisit_state(current_state_id, current_elements)

        # 获取当前帧
        if not self.stack:
            return self._finish("栈为空，遍历完成")

        current_frame = self.stack[-1]

        # 确保当前帧的元素列表是最新的
        if current_frame.state_id == current_state_id:
            current_frame.refresh_elements(self._parse_elements(current_elements))

        # 查找下一个未探索的元素
        next_element = current_frame.get_next_unexplored_element()

        if next_element:
            # 标记当前元素为正在探索（不立即标记已探索，等执行结果）
            logger.info(
                f"待探索元素: {next_element.element_id} ({next_element.text_content}) "
                f"深度: {len(self.stack)}, 页面: {current_frame.semantic_name}"
            )
            return {
                "success": True,
                "action": "CLICK",
                "element_id": next_element.element_id,
                "resource_id": next_element.resource_id,
                "text_content": next_element.text_content,
                "reason": f"探索元素: {next_element.text_content or next_element.element_id}",
                "instruction": "请使用 Maestro 点击此元素，然后获取新页面后再次调用 get_next_action_v2 继续遍历",
                "must_continue": True,
                "next_step": "执行此动作后，必须再次调用 get_next_action_v2",
                "debug_info": self._get_debug_info(),
            }
        else:
            # 当前页面所有元素已探索，回溯
            if len(self.stack) > 1:
                self.status = TraversalStatus.BACKTRACKING
                return {
                    "success": True,
                    "action": "BACK",
                    "element_id": None,
                    "reason": f"页面 '{current_frame.semantic_name}' 所有元素已探索，返回上一级",
                    "instruction": "请使用 Maestro 执行 back 返回上一级页面，然后再次调用 get_next_action_v2 继续遍历",
                    "must_continue": True,
                    "next_step": "执行 back 后，必须再次调用 get_next_action_v2",
                    "debug_info": self._get_debug_info(),
                }
            else:
                # 栈底，遍历完成
                return self._finish("所有分支已探索完成")

    def _handle_new_state(self, state_id: str, elements: List[dict]) -> dict:
        """处理新发现的页面"""
        elements_info = self._parse_elements(elements)

        # 创建新帧
        new_frame = TraversalFrame(
            state_id=state_id,
            semantic_name=f"页面_{len(self.visited_states)}",
            elements=elements_info,
        )

        self.stack.append(new_frame)
        self.visited_states.add(state_id)
        self.total_nodes_visited += 1

        logger.info(
            f"发现新页面: {state_id[:16]}..., 深度: {len(self.stack)}, 元素数: {len(elements_info)}"
        )

        # 检测是否为登录页面
        is_login_page = self._detect_login_page(elements_info)
        login_hint = ""
        if is_login_page:
            login_hint = " 【检测到登录页面】请从项目配置获取测试账号，完成登录后再继续遍历。"

        # 返回第一个元素
        first_element = new_frame.get_next_unexplored_element()
        if first_element:
            return {
                "success": True,
                "action": "CLICK",
                "element_id": first_element.element_id,
                "resource_id": first_element.resource_id,
                "text_content": first_element.text_content,
                "reason": f"新页面，探索第一个元素{login_hint}",
                "instruction": f"请使用 Maestro 点击此元素进入新页面，然后再次调用 get_next_action_v2 继续遍历。{login_hint}",
                "must_continue": True,
                "is_login_page": is_login_page,
                "debug_info": self._get_debug_info(),
            }
        else:
            # 没有可点击元素，直接回溯
            return {
                "success": True,
                "action": "BACK",
                "element_id": None,
                "reason": "新页面无可用元素，返回",
                "instruction": "请使用 Maestro 执行 back 返回上一级，然后再次调用 get_next_action_v2 继续遍历",
                "must_continue": True,
                "debug_info": self._get_debug_info(),
            }

    def _handle_revisit_state(self, state_id: str, elements: List[dict]) -> dict:
        """处理重新访问的页面（回溯后）"""
        # 查找该状态是否在栈中
        frame_index = -1
        for i, frame in enumerate(self.stack):
            if frame.state_id == state_id:
                frame_index = i
                break

        if frame_index >= 0:
            # 在栈中，说明回溯成功
            # 栈在这里保持不变：真实回退尚未通过 report_execution 上报。
            # 观察到父页面只说明设备可能已经回退，栈的提交仍以执行结果为准。

            current_frame = self.stack[frame_index]
            current_frame.refresh_elements(self._parse_elements(elements))

            logger.info(f"回溯到: {state_id[:16]}..., 深度: {len(self.stack)}")

            # 继续探索
            next_element = current_frame.get_next_unexplored_element()
            if next_element:
                return {
                    "success": True,
                    "action": "CLICK",
                    "element_id": next_element.element_id,
                    "resource_id": next_element.resource_id,
                    "text_content": next_element.text_content,
                    "reason": f"回溯后继续探索: {next_element.text_content or next_element.element_id}",
                    "instruction": "请使用 Maestro 点击此元素，然后再次调用 get_next_action_v2 继续遍历",
                    "must_continue": True,
                    "debug_info": self._get_debug_info(),
                }
            else:
                # 这个页面也探索完了，继续回溯
                if len(self.stack) > 1:
                    return {
                        "success": True,
                        "action": "BACK",
                        "element_id": None,
                        "reason": "回溯后页面已完成，继续返回",
                        "instruction": "请使用 Maestro 执行 back 返回上一级，然后再次调用 get_next_action_v2 继续遍历",
                        "must_continue": True,
                        "debug_info": self._get_debug_info(),
                    }
                else:
                    return self._finish("回溯后所有分支已完成")
        else:
            # 不在栈中，可能是回溯到了更早的页面
            # 重新入栈
            return self._handle_new_state(state_id, elements)

    def _finish(self, reason: str) -> dict:
        """完成遍历"""
        self.status = TraversalStatus.COMPLETED
        self.completed_at = datetime.now()

        logger.info(f"遍历完成: {reason}")

        return {
            "success": True,
            "action": "FINISH",
            "element_id": None,
            "reason": reason,
            "instruction": "遍历已完成，所有页面和元素都已探索",
            "must_continue": False,
            "debug_info": self._get_debug_info(),
            "statistics": self.get_statistics(),
        }

    def report_execution(self, execution_result: dict) -> dict:
        """
        上报执行结果，更新遍历状态

        Args:
            execution_result: 执行结果
                - executed_action: 执行的动作
                - executed_element_id: 执行的元素 ID
                - from_state_id: 执行前的页面状态 ID（用于检测页面是否变化）
                - resulting_state_id: 结果状态 ID
                - success: 是否成功

        Returns:
            更新后的状态
        """
        executed_action = execution_result.get("executed_action", "")
        executed_element_id = execution_result.get("executed_element_id", "")
        from_state_id = execution_result.get("from_state_id", "")
        resulting_state_id = execution_result.get("resulting_state_id", "")
        success = execution_result.get("success", True)

        # 记录执行
        record = ExecutionRecord(
            action=executed_action,
            element_id=executed_element_id,
            from_state=from_state_id or (self.stack[-1].state_id if self.stack else ""),
            to_state=resulting_state_id,
            success=success,
        )
        self.execution_history.append(record)

        if not success:
            logger.warning(f"执行失败: {executed_action} on {executed_element_id}")
            return {
                "success": True,
                "state_updated": False,
                "message": "执行失败，请重试或跳过此元素",
            }

        # 处理 CLICK 动作：检测页面是否变化
        if executed_action == "CLICK" and executed_element_id:
            current_frame = self.stack[-1] if self.stack else None
            if current_frame:
                # 检查页面是否变化
                page_changed = (from_state_id and resulting_state_id and
                               from_state_id != resulting_state_id)

                for elem in current_frame.elements:
                    if elem.element_id == executed_element_id and not elem.is_explored:
                        elem.is_explored = True
                        if page_changed:
                            # 页面有变化：有效导航元素
                            self.total_elements_explored += 1
                            logger.info(f"有效导航元素: {executed_element_id} ({elem.text_content}) → {resulting_state_id[:16]}...")
                        else:
                            # 页面无变化：非导航元素，标记跳过
                            logger.info(f"非导航元素（跳过）: {executed_element_id} ({elem.text_content})")
                        break

        # 如果是 BACK，弹出栈顶
        if executed_action == "BACK" and len(self.stack) > 1:
            popped = self.stack.pop()
            logger.info(f"回溯弹出: {popped.state_id[:16]}...")

        return {
            "success": True,
            "state_updated": True,
            "message": "执行结果已记录",
            "debug_info": self._get_debug_info(),
        }

    def _get_debug_info(self) -> dict:
        """获取调试信息"""
        current_frame = self.stack[-1] if self.stack else None

        return {
            "stack_depth": len(self.stack),
            "visited_nodes": self.total_nodes_visited,
            "explored_elements": self.total_elements_explored,
            "max_depth": self.max_depth_reached,
            "current_page": current_frame.semantic_name if current_frame else None,
            "remaining_in_page": sum(1 for e in current_frame.elements if not e.is_explored) if current_frame else 0,
            "status": self.status.value,
        }

    def get_statistics(self) -> dict:
        """获取统计信息"""
        duration = None
        if self.completed_at:
            duration = (self.completed_at - self.started_at).total_seconds() if self.started_at else None
        elif self.started_at:
            duration = (datetime.now() - self.started_at).total_seconds()

        return {
            "session_id": self.session_id,
            "status": self.status.value,
            "stack_depth": len(self.stack),
            "visited_nodes": self.total_nodes_visited,
            "explored_elements": self.total_elements_explored,
            "max_depth_reached": self.max_depth_reached,
            "execution_count": len(self.execution_history),
            "duration_seconds": duration,
        }

    def get_stack_summary(self) -> List[dict]:
        """获取栈摘要（用于调试）"""
        return [frame.to_dict() for frame in self.stack]


class SessionManager:
    """会话管理器，管理多个遍历会话"""

    def __init__(self):
        self.sessions: Dict[str, TraversalSession] = {}

    def create_session(
        self, project_root: str, target_package: str = "", session_id: Optional[str] = None
    ) -> TraversalSession:
        """创建新会话"""
        if not session_id:
            import uuid
            session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        session = TraversalSession(
            session_id=session_id,
            project_root=project_root,
            target_package=target_package,
        )
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[TraversalSession]:
        """获取会话"""
        return self.sessions.get(session_id)

    def remove_session(self, session_id: str) -> bool:
        """移除会话"""
        if session_id in self.sessions:
            del self.sessions[session_id]
            return True
        return False

    def list_sessions(self) -> List[str]:
        """列出所有会话 ID"""
        return list(self.sessions.keys())
