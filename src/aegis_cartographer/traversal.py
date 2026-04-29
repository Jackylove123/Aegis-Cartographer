import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from aegis_cartographer.app_map import AppMapManager
from aegis_cartographer.fingerprint import get_skeleton_hash, extract_clickable_elements
from aegis_cartographer.models import ActionType, Edge, MapElement, MapNode, NodeStatus
from aegis_cartographer.security import SecurityFilter, PackageNameGuard


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


class ExplorationStatus(Enum):
    IDLE = "IDLE"
    EXPLORING = "EXPLORING"
    BACKTRACKING = "BACKTRACKING"
    HARD_RESET = "HARD_RESET"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


@dataclass
class TraversalAction:
    element_id: str
    element_text: str
    action_type: ActionType
    target_state: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class BacktrackRecord:
    from_state: str
    to_state: str
    action: TraversalAction
    success: bool
    timestamp: float = field(default_factory=time.time)


class TraversalEngine:
    SOFT_MATCH_THRESHOLD = 0.70
    HARD_RESET_THRESHOLD = 0.30
    
    def __init__(
        self,
        map_manager: AppMapManager,
        execute_action_fn: Callable[[str, ActionType], dict[str, Any]],
        get_page_xml_fn: Callable[[], dict[str, Any]],
        back_fn: Callable[[], dict[str, Any]],
        stop_app_fn: Callable[[], dict[str, Any]],
        start_app_fn: Callable[[], dict[str, Any]],
        get_current_package_fn: Optional[Callable[[], str]] = None,
        target_package: Optional[str] = None,
    ):
        self.map_manager = map_manager
        self.execute_action = execute_action_fn
        self.get_page_xml = get_page_xml_fn
        self.back = back_fn
        self.stop_app = stop_app_fn
        self.start_app = start_app_fn
        self.get_current_package = get_current_package_fn or (lambda: "")
        
        self.security_filter = SecurityFilter(target_package=target_package or "") if target_package else None
        self.package_guard = PackageNameGuard(target_package=target_package) if target_package else None
        
        self.exploration_queue: deque[tuple[str, list[str]]] = deque()
        self.backtrack_stack: list[BacktrackRecord] = []
        self.visited_states: set[str] = set()
        self.current_state: Optional[str] = None
        self.status = ExplorationStatus.IDLE
        self.max_depth = 10
        self.action_promotion_keywords = ["更多", "菜单", "管理", "...", "settings", "menu", "more"]
        self._blocked_actions = 0
    
    def start(self, initial_xml: dict[str, Any], project_name: str = "App") -> None:
        self.map_manager.set_project_name(project_name)
        
        self.current_state = get_skeleton_hash(initial_xml)
        self.visited_states.add(self.current_state)
        
        logger.info(f"🚀 开始探索，初始状态: {self.current_state[:16]}...")
        
        self.map_manager.upsert_node(
            state_id=self.current_state,
            semantic_name="入口页面",
        )
        self.map_manager.save()
        
        self.exploration_queue.append((self.current_state, []))
        
        self._run_exploration_loop()
    
    def _run_exploration_loop(self) -> None:
        iteration = 0
        while self.exploration_queue:
            iteration += 1
            logger.info(f"\n{'='*50}")
            logger.info(f"📍 迭代 #{iteration} - 队列长度: {len(self.exploration_queue)}")
            logger.info(f"{'='*50}")
            
            if not self.exploration_queue:
                break
            
            current_state, path = self.exploration_queue.popleft()
            self.current_state = current_state
            
            logger.info(f"▶ 进入状态: {current_state[:16]}... (深度: {len(path)})")
            
            xml = self.get_page_xml()
            elements = extract_clickable_elements(xml)
            
            logger.info(f"   发现 {len(elements)} 个可交互元素")
            
            sorted_elements = self._sort_elements_by_priority(elements)
            
            for elem in sorted_elements:
                logger.info(f"   🔍 检查元素: {elem['id']} | {elem.get('text', '')}")
                
                if self.security_filter:
                    current_package = self.get_current_package()
                    should_explore, block_reason = self.security_filter.should_explore(
                        element_text=elem.get("text", ""),
                        element_id=elem["id"],
                        current_package=current_package,
                    )
                    if not should_explore:
                        logger.warning(f"      🛡️ 安全过滤拦截: {block_reason}")
                        self._blocked_actions += 1
                        continue
                
                if self.package_guard:
                    logger.info(f"      🛡️ 包名边界检查...")
                
                if elem["id"] in self.action_promotion_keywords or \
                   any(kw in elem.get("text", "") for kw in self.action_promotion_keywords):
                    action_type = ActionType.TAP
                    logger.info(f"      → 优先级按钮，使用 TAP")
                else:
                    action_type = ActionType.TAP
                    logger.info(f"      → 普通按钮，使用 TAP")
                
                result = self.execute_action(elem["id"], action_type)
                logger.info(f"      执行动作结果: {result.get('success', False)}")
                
                time.sleep(0.5)
                
                new_xml = self.get_page_xml()
                new_state = get_skeleton_hash(new_xml)
                
                logger.info(f"      新状态指纹: {new_state[:16]}...")
                
                if new_state == current_state:
                    logger.info(f"      ⚠ 状态未变化，尝试 LONG_PRESS")
                    
                    result_lp = self.execute_action(elem["id"], ActionType.LONG_PRESS)
                    logger.info(f"      LONG_PRESS 结果: {result_lp.get('success', False)}")
                    
                    time.sleep(0.5)
                    
                    new_xml = self.get_page_xml()
                    new_state = get_skeleton_hash(new_xml)
                    
                    if new_state == current_state:
                        logger.info(f"      ⏭ LONG_PRESS 也无变化，跳过此元素")
                        continue
                
                edge = Edge(
                    trigger_id=elem["id"],
                    target_state=new_state,
                    action_type=action_type,
                )
                self.map_manager.add_edge(current_state, edge)
                
                if new_state not in self.visited_states:
                    self.visited_states.add(new_state)
                    
                    map_elements = [
                        MapElement(
                            original_id=e.get("id", ""),
                            text_content=e.get("text", ""),
                        )
                        for e in extract_clickable_elements(new_xml)
                    ]
                    
                    self.map_manager.upsert_node(
                        state_id=new_state,
                        elements=map_elements,
                    )
                    
                    new_path = path + [elem["id"]]
                    if len(new_path) < self.max_depth:
                        self.exploration_queue.append((new_state, new_path))
                        logger.info(f"      ✅ 发现新状态，加入队列: {new_state[:16]}...")
                    else:
                        logger.info(f"      ⏭ 达到最大深度限制")
                else:
                    logger.info(f"      ⚡ 状态已存在，跳过")
                
                self.map_manager.save()
                
                logger.info(f"      🔙 执行回退...")
                backtrack_success = self._try_backtrack(current_state)
                
                if not backtrack_success:
                    logger.info(f"      ⚠ 回退失败，尝试硬重置")
                    if not self._hard_reset(current_state, path):
                        logger.error(f"      ❌ 硬重置失败，终止探索")
                        self.status = ExplorationStatus.ERROR
                        return
    
    def _sort_elements_by_priority(self, elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def priority_score(elem: dict[str, Any]) -> int:
            text = elem.get("text", "").lower()
            resource_id = elem.get("id", "").lower()
            
            if any(kw in text or kw in resource_id for kw in ["nav", "tab", "menu", "更多", "settings"]):
                return 0
            if any(kw in text or kw in resource_id for kw in ["submit", "confirm", "ok", "save", "确定", "保存"]):
                return 1
            if any(kw in text or kw in resource_id for kw in ["back", "返回", "close", "关闭"]):
                return 2
            return 3
        
        return sorted(elements, key=priority_score)
    
    def _try_backtrack(self, original_state: str) -> bool:
        logger.info(f"   执行 maestro back...")
        
        self.back()
        time.sleep(1.0)
        
        current_xml = self.get_page_xml()
        current_state = get_skeleton_hash(current_xml)
        
        logger.info(f"   回退后状态: {current_state[:16]}...")
        
        if current_state == original_state:
            logger.info(f"   ✅ 完全回退成功")
            return True
        
        original_node = self.map_manager.get_node(original_state)
        current_node = self.map_manager.get_node(current_state)
        
        if original_node and current_node:
            original_ids = {e.original_id for e in original_node.elements if e.original_id}
            current_ids = {e.original_id for e in current_node.elements if e.original_id}
            
            if original_ids and current_ids:
                intersection = len(original_ids & current_ids)
                union = len(original_ids | current_ids)
                similarity = intersection / union if union > 0 else 0
                
                logger.info(f"   元素交集: {intersection}, 并集: {union}, 相似度: {similarity:.2%}")
                
                if similarity >= self.SOFT_MATCH_THRESHOLD:
                    logger.info(f"   ✅ 软匹配成功 (>{self.SOFT_MATCH_THRESHOLD:.0%})")
                    
                    record = BacktrackRecord(
                        from_state=original_state,
                        to_state=current_state,
                        action=TraversalAction(
                            element_id="back",
                            element_text="system_back",
                            action_type=ActionType.TAP,
                        ),
                        success=True,
                    )
                    self.backtrack_stack.append(record)
                    
                    self.current_state = current_state
                    return True
        
        logger.info(f"   ❌ 软匹配失败 (<{self.SOFT_MATCH_THRESHOLD:.0%})")
        
        record = BacktrackRecord(
            from_state=original_state,
            to_state=current_state,
            action=TraversalAction(
                element_id="back",
                element_text="system_back",
                action_type=ActionType.TAP,
            ),
            success=False,
        )
        self.backtrack_stack.append(record)
        
        return False
    
    def _hard_reset(self, target_state: str, path: list[str]) -> bool:
        self.status = ExplorationStatus.HARD_RESET
        logger.info(f"   🚨 执行硬重置...")
        
        logger.info(f"   🔴 stop_app()")
        self.stop_app()
        time.sleep(1.0)
        
        logger.info(f"   🟢 start_app()")
        self.start_app()
        time.sleep(2.0)
        
        current_xml = self.get_page_xml()
        current_state = get_skeleton_hash(current_xml)
        
        logger.info(f"   重启后状态: {current_state[:16]}...")
        
        if current_state == target_state:
            logger.info(f"   ✅ 硬重置成功，已回到目标状态")
            self.status = ExplorationStatus.EXPLORING
            return True
        
        logger.info(f"   ⚠ 重新导航到目标状态: {target_state[:16]}...")
        
        target_node = self.map_manager.get_node(target_state)
        if not target_node:
            logger.error(f"   ❌ 目标节点不存在于地图中")
            self.status = ExplorationStatus.ERROR
            return False
        
        for edge in target_node.edges:
            logger.info(f"   尝试导航边: {edge.trigger_id} -> {edge.target_state[:16]}...")
            
            result = self.execute_action(edge.trigger_id, edge.action_type)
            time.sleep(1.0)
            
            new_xml = self.get_page_xml()
            new_state = get_skeleton_hash(new_xml)
            
            if new_state == target_state:
                logger.info(f"   ✅ 导航成功")
                self.current_state = target_state
                self.status = ExplorationStatus.EXPLORING
                return True
        
        logger.error(f"   ❌ 无法回到目标状态")
        self.status = ExplorationStatus.ERROR
        return False
    
    def get_statistics(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "visited_states": len(self.visited_states),
            "queue_length": len(self.exploration_queue),
            "backtrack_count": len(self.backtrack_stack),
            "successful_backtracks": sum(1 for r in self.backtrack_stack if r.success),
            "total_nodes": self.map_manager.get_node_count(),
            "blocked_actions": self._blocked_actions,
        }
    
    def export_mermaid_graph(self) -> str:
        lines = ["graph TD"]
        
        for node in self.map_manager.app_map.nodes:
            status_indicator = "💚" if node.status == NodeStatus.ACTIVE else "💔"
            lines.append(f'    {node.state_id[:12]}["{status_indicator} {node.semantic_name}"]')
            
            for edge in node.edges:
                lines.append(f'    {node.state_id[:12]} -->|{edge.action_type.value}| {edge.target_state[:12]}')
        
        return "\n".join(lines)


def create_traversal_engine(
    map_file_path: str,
    execute_action_fn: Callable[[str, ActionType], dict[str, Any]],
    get_page_xml_fn: Callable[[], dict[str, Any]],
    back_fn: Callable[[], dict[str, Any]] = lambda: {"success": True},
    stop_app_fn: Callable[[], dict[str, Any]] = lambda: {"success": True},
    start_app_fn: Callable[[], dict[str, Any]] = lambda: {"success": True},
) -> TraversalEngine:
    map_manager = AppMapManager(map_file_path)
    return TraversalEngine(
        map_manager=map_manager,
        execute_action_fn=execute_action_fn,
        get_page_xml_fn=get_page_xml_fn,
        back_fn=back_fn,
        stop_app_fn=stop_app_fn,
        start_app_fn=start_app_fn,
    )
