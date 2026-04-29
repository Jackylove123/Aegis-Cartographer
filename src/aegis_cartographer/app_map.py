import json
import os
from pathlib import Path
from typing import Any, Optional

from aegis_cartographer.models import MapNode, MapElement, Edge, NodeStatus
from aegis_cartographer.vector_indexer import AegisVectorIndex


class AppMapManager:
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.nodes_dir = os.path.join(output_dir, "nodes")
        self.index_file = os.path.join(output_dir, "index.json")
        os.makedirs(self.nodes_dir, exist_ok=True)
        
        self.index_data = {"nodes_index": {}}
        self.vector_index = AegisVectorIndex(os.path.join(output_dir, "vector_db"))
        self.load_index()

    @property
    def file_path(self) -> Path:
        return Path(self.index_file)

    def load_index(self):
        if os.path.exists(self.index_file):
            with open(self.index_file, 'r', encoding='utf-8') as f:
                self.index_data = json.load(f)

    def save_index(self):
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        with open(self.index_file, 'w', encoding='utf-8') as f:
            json.dump(self.index_data, f, ensure_ascii=False, indent=2)

    def save(self) -> None:
        self.save_index()

    def upsert_node(self, state_id: str, **kwargs) -> MapNode:
        if 'elements' in kwargs:
            for el in kwargs['elements']:
                if isinstance(el, dict):
                    if len(el.get('original_id', '')) > 100:
                        el['original_id'] = el['original_id'][:50] + "...(truncated)"
                elif hasattr(el, 'original_id'):
                    if len(el.original_id) > 100:
                        el.original_id = el.original_id[:50] + "...(truncated)"

        existing_node = self.get_node(state_id)
        if existing_node:
            node_dict = existing_node.model_dump()
            node_dict.update(kwargs)
            updated_node = MapNode(**node_dict)
        else:
            kwargs.setdefault('semantic_name', 'Unnamed')
            kwargs.setdefault('status', NodeStatus.ACTIVE)
            kwargs.setdefault('elements', [])
            kwargs.setdefault('edges', [])
            updated_node = MapNode(state_id=state_id, **kwargs)
        
        node_file = os.path.join(self.nodes_dir, f"{state_id}.json")
        with open(node_file, 'w', encoding='utf-8') as f:
            f.write(updated_node.model_dump_json(indent=2))

        self.index_data["nodes_index"][state_id] = {
            "semantic_name": updated_node.semantic_name,
            "edges": [e.model_dump() for e in updated_node.edges]
        }
        self.save_index()

        if updated_node.business_context:
            self.vector_index.upsert_node_index(
                state_id=state_id,
                context=updated_node.business_context,
                metadata={"semantic_name": updated_node.semantic_name, "state_id": state_id}
            )
        return updated_node

    def get_node(self, state_id: str) -> Optional[MapNode]:
        node_file = os.path.join(self.nodes_dir, f"{state_id}.json")
        if os.path.exists(node_file):
            with open(node_file, 'r', encoding='utf-8') as f:
                return MapNode(**json.load(f))
        return None

    def node_exists(self, state_id: str) -> bool:
        return self.get_node(state_id) is not None

    def add_node(self, node: MapNode) -> bool:
        if self.node_exists(node.state_id):
            return False
        
        node_file = os.path.join(self.nodes_dir, f"{node.state_id}.json")
        with open(node_file, 'w', encoding='utf-8') as f:
            f.write(node.model_dump_json(indent=2))
        
        self.index_data["nodes_index"][node.state_id] = {
            "semantic_name": node.semantic_name,
            "edges": [e.model_dump() for e in node.edges]
        }
        self.save_index()
        return True

    def mark_all_as_deprecated(self) -> None:
        for state_id in self.index_data["nodes_index"]:
            node = self.get_node(state_id)
            if node:
                node.status = NodeStatus.DEPRECATED
                node_file = os.path.join(self.nodes_dir, f"{state_id}.json")
                with open(node_file, 'w', encoding='utf-8') as f:
                    f.write(node.model_dump_json(indent=2))

    def get_active_nodes(self) -> list[MapNode]:
        nodes = []
        for state_id in self.index_data["nodes_index"]:
            node = self.get_node(state_id)
            if node and node.status == NodeStatus.ACTIVE:
                nodes.append(node)
        return nodes

    def get_deprecated_nodes(self) -> list[MapNode]:
        nodes = []
        for state_id in self.index_data["nodes_index"]:
            node = self.get_node(state_id)
            if node and node.status == NodeStatus.DEPRECATED:
                nodes.append(node)
        return nodes

    def add_edge(self, state_id: str, edge: Edge) -> bool:
        node = self.get_node(state_id)
        if not node:
            return False
        
        for existing_edge in node.edges:
            if existing_edge.trigger_id == edge.trigger_id and existing_edge.target_state == edge.target_state:
                return True
        
        node.edges.append(edge)
        node_file = os.path.join(self.nodes_dir, f"{state_id}.json")
        with open(node_file, 'w', encoding='utf-8') as f:
            f.write(node.model_dump_json(indent=2))
        
        self.index_data["nodes_index"][state_id]["edges"] = [e.model_dump() for e in node.edges]
        self.save_index()
        return True

    def set_project_name(self, name: str) -> None:
        self.index_data["project_name"] = name
        self.save_index()

    def get_node_count(self) -> int:
        return len(self.index_data["nodes_index"])

    def clear(self) -> None:
        self.index_data = {"nodes_index": {}}
        self.save_index()

    def export_mermaid(self, title: Optional[str] = None) -> str:
        lines = ["```mermaid", "graph TD"]
        
        if title:
            lines.append(f'    title {title}')
        
        for state_id in self.index_data["nodes_index"]:
            node = self.get_node(state_id)
            if not node:
                continue
            status_icon = "✅" if node.status == NodeStatus.ACTIVE else "❌"
            safe_name = node.semantic_name.replace('"', "'").replace("\n", " ")
            truncated_id = state_id[:12]
            
            lines.append(f'    {truncated_id}["{status_icon} {safe_name}"]')
            
            for edge in node.edges:
                action = edge.action_type.value if edge.action_type else "CLICK"
                lines.append(f'    {truncated_id} -->|{action}| {edge.target_state[:12]}')
        
        lines.append("```")
        
        return "\n".join(lines)

    def export_markdown_with_mermaid(self, output_path: str) -> None:
        project_name = self.index_data.get("project_name", "Aegis Map")
        content = f"""# {project_name} - 语义地图

## 统计信息
- 节点总数: {self.get_node_count()}
- 活跃节点: {len(self.get_active_nodes())}
- 废弃节点: {len(self.get_deprecated_nodes())}

## 地图可视化

{self.export_mermaid()}

## 节点详情

"""
        for state_id in self.index_data["nodes_index"]:
            node = self.get_node(state_id)
            if not node:
                continue
            status = "✅ Active" if node.status == NodeStatus.ACTIVE else "❌ Deprecated"
            content += f"### {node.semantic_name} ({status})\n\n"
            content += f"- **State ID**: `{node.state_id}`\n"
            if node.business_context:
                content += f"- **业务上下文**: {node.business_context}\n"
            content += f"- **元素数量**: {len(node.elements)}\n"
            content += f"- **边数量**: {len(node.edges)}\n\n"
            
            if node.elements:
                content += "**元素**:\n"
                for elem in node.elements:
                    content += f"- `{elem.original_id}`: {elem.text_content}\n"
                content += "\n"
            
            if node.edges:
                content += "**跳转**:\n"
                for edge in node.edges:
                    content += f"- `{edge.trigger_id}` → `{edge.target_state[:12]}...` ({edge.action_type})\n"
                content += "\n"
        
        Path(output_path).write_text(content, encoding="utf-8")

    def compute_state_hash(self, xml_data: dict[str, Any]) -> str:
        from aegis_cartographer.fingerprint import get_skeleton_hash
        return get_skeleton_hash(xml_data)

    def is_new_state(self, xml_data: dict[str, Any]) -> bool:
        state_hash = self.compute_state_hash(xml_data)
        return not self.node_exists(state_hash)

    def register_state(
        self,
        xml_data: dict[str, Any],
        semantic_name: Optional[str] = None,
        business_context: Optional[str] = None,
    ) -> tuple[MapNode, bool]:
        from aegis_cartographer.fingerprint import extract_clickable_elements
        
        state_hash = self.compute_state_hash(xml_data)
        elements = extract_clickable_elements(xml_data)
        
        map_elements = [
            MapElement(
                original_id=elem.get("id", ""),
                text_content=elem.get("text", ""),
                semantic_role=MapElement.semantic_role.default,
            )
            for elem in elements
        ]
        
        existing_node = self.get_node(state_hash)
        if existing_node:
            if semantic_name:
                existing_node.semantic_name = semantic_name
            if business_context:
                existing_node.business_context = business_context
            existing_node.status = NodeStatus.ACTIVE
            self.save()
            return existing_node, False
        
        new_node = MapNode(
            state_id=state_hash,
            semantic_name=semantic_name or "Unnamed",
            business_context=business_context,
            status=NodeStatus.ACTIVE,
            elements=map_elements,
            edges=[],
        )
        
        node_file = os.path.join(self.nodes_dir, f"{state_hash}.json")
        with open(node_file, 'w', encoding='utf-8') as f:
            f.write(new_node.model_dump_json(indent=2))
        
        self.index_data["nodes_index"][state_hash] = {
            "semantic_name": new_node.semantic_name,
            "edges": []
        }
        self.save_index()
        
        return new_node, True

    def search_by_semantic(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        query_lower = query.lower()
        results = []
        
        for state_id in self.index_data["nodes_index"]:
            node = self.get_node(state_id)
            if not node:
                continue
            
            score = 0
            if node.semantic_name and query_lower in node.semantic_name.lower():
                score += 10
            if node.business_context and query_lower in node.business_context.lower():
                score += 5
            for elem in node.elements:
                if elem.text_content and query_lower in elem.text_content.lower():
                    score += 1
            
            if score > 0:
                results.append({
                    "state_id": node.state_id,
                    "semantic_name": node.semantic_name,
                    "score": score
                })
        
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]
