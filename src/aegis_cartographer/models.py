from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class NodeStatus(str, Enum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    EXPLORED = "Explored"


class ElementRole(str, Enum):
    BUTTON = "BUTTON"
    INPUT = "INPUT"
    DISPLAY = "DISPLAY"
    NAVIGATION = "NAVIGATION"
    SELECTOR = "SELECTOR"
    ACTION = "ACTION"


class ActionType(str, Enum):
    TAP = "TAP"
    LONG_PRESS = "LONG_PRESS"
    TYPE = "TYPE"


class MapElement(BaseModel):
    original_id: str
    element_name: Optional[str] = None
    ai_description: Optional[str] = None
    bounds: Optional[str] = None
    clickable: bool = True
    enabled: bool = True
    is_explored: bool = False
    target_node_id: Optional[str] = None
    semantic_role: ElementRole = ElementRole.DISPLAY


class Edge(BaseModel):
    trigger_id: str
    target_state: str
    action_type: ActionType = ActionType.TAP


class MapNode(BaseModel):
    state_id: str
    semantic_name: str
    business_context: Optional[str] = None
    status: NodeStatus = NodeStatus.ACTIVE
    elements: List[MapElement] = []
    edges: List[Edge] = []


class AppMap(BaseModel):
    project_name: str
    nodes: List[MapNode] = []
