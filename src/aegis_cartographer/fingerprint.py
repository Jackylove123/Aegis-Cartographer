import hashlib
import json
from typing import Any, Optional


def get_skeleton_hash(xml_data: dict[str, Any]) -> str:
    """
    根据 PRD 规范计算页面骨架哈希
    
    过滤：剔除 bounds, text, content-desc
    特征提取：仅针对 clickable: true 的元素，提取 (id, class, parent_id, depth)
    标准化排序：按字母顺序字典序排序
    哈希生成：SHA256
    """
    features = []
    
    def traverse(element: dict[str, Any], parent_id: Optional[str], depth: int) -> None:
        if not isinstance(element, dict):
            return
        
        is_clickable = element.get("clickable", "").lower() == "true"
        
        if is_clickable:
            resource_id = element.get("resource-id", "") or element.get("id", "")
            class_name = element.get("class", "") or element.get("className", "")
            
            feature_tuple = (
                resource_id,
                class_name,
                parent_id or "",
                str(depth),
            )
            features.append(feature_tuple)
        
        children = element.get("children", [])
        if isinstance(children, list):
            current_id = element.get("resource-id", "") or element.get("id", "")
            for child in children:
                traverse(child, current_id, depth + 1)
    
    tree = xml_data.get("tree", xml_data)
    if isinstance(tree, dict):
        traverse(tree, None, 0)
    
    features.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    
    serialized = json.dumps(features, separators=(",", ":"))
    
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def extract_clickable_elements(xml_data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    提取所有 clickable: true 的元素（用于后续语义分析）
    """
    elements = []
    
    def traverse(element: dict[str, Any], depth: int = 0) -> None:
        if not isinstance(element, dict):
            return
        
        is_clickable = element.get("clickable", "").lower() == "true"
        
        if is_clickable:
            elements.append({
                "id": element.get("resource-id", "") or element.get("id", ""),
                "class": element.get("class", "") or element.get("className", ""),
                "text": element.get("text", ""),
                "content_desc": element.get("content-desc", "") or element.get("contentDescription", ""),
                "depth": depth,
            })
        
        children = element.get("children", [])
        if isinstance(children, list):
            for child in children:
                traverse(child, depth + 1)
    
    tree = xml_data.get("tree", xml_data)
    if isinstance(tree, dict):
        traverse(tree, 0)
    
    return elements


def calculate_similarity(hash1: str, hash2: str) -> float:
    """
    计算两个哈希值的相似度（用于软匹配判定）
    返回 0.0 - 1.0 之间的相似度
    """
    if hash1 == hash2:
        return 1.0
    
    matching_bits = sum(c1 == c2 for c1, c2 in zip(hash1, hash2))
    total_bits = len(hash1) * 4
    
    return matching_bits / total_bits
