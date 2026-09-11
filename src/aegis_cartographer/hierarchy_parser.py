"""Compatibility façade around the normalized core hierarchy parser."""

from __future__ import annotations

from typing import Any, Mapping

from aegis_cartographer.core.hierarchy import (
    compute_screen_signature as _compute_screen_signature,
)
from aegis_cartographer.core.hierarchy import (
    extract_ui_elements as _extract_ui_elements,
)
from aegis_cartographer.core.hierarchy import (
    normalize_hierarchy as _normalize_hierarchy,
)
from aegis_cartographer.core.hierarchy import (
    parse_bounds as _parse_bounds,
)
from aegis_cartographer.core.models import NormalizedHierarchy, NormalizedNode


def _node_to_dict(node: NormalizedNode) -> dict[str, Any]:
    return {
        "class": node.class_name,
        "className": node.class_name,
        "resource-id": node.resource_id,
        "id": node.resource_id,
        "accessibility-id": node.accessibility_id,
        "text": node.text,
        "content-desc": node.content_desc,
        "value": node.value,
        "hintText": node.hint,
        "bounds": (
            None
            if node.bounds is None
            else (
                f"[{node.bounds.left},{node.bounds.top}]"
                f"[{node.bounds.right},{node.bounds.bottom}]"
            )
        ),
        "clickable": node.clickable,
        "enabled": node.enabled,
        "scrollable": node.scrollable,
        "checked": node.checked,
        "selected": node.selected,
        "children": [_node_to_dict(child) for child in node.children],
    }


def normalize_hierarchy(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize any supported hierarchy payload to the legacy wrapper shape."""

    normalized: NormalizedHierarchy = _normalize_hierarchy(raw)
    return {"elements": _node_to_dict(normalized.root)}


def parse_bounds(bounds_str: str) -> list[int]:
    """Parse bounds into ``[left, top, right, bottom]``."""

    bounds = _parse_bounds(bounds_str)
    return bounds.to_list() if bounds else [0, 0, 0, 0]


def compute_state_id(hierarchy_json: Mapping[str, Any]) -> str:
    """Compute a deterministic composite state ID for a hierarchy payload."""

    return _compute_screen_signature(hierarchy_json).state_id


def extract_clickable_elements(hierarchy_json: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract clickable/enabled elements in current server-compatible shape."""

    elements: list[dict[str, Any]] = []
    for element in _extract_ui_elements(hierarchy_json):
        if not element.clickable:
            continue
        elements.append(
            {
                "bounds": (
                    f"[{element.bounds.left},{element.bounds.top}]"
                    f"[{element.bounds.right},{element.bounds.bottom}]"
                ),
                "class": element.class_name,
                "resource-id": element.resource_id,
                "id": element.resource_id or element.accessibility_id,
                "text": element.text,
                "content-desc": element.content_desc,
                "accessibilityText": element.accessibility_id,
                "value": element.value,
                "hintText": element.hint,
                "selected": element.selected,
                "checked": element.checked,
                "clickable": True,
                "enabled": element.enabled,
                "element_key": element.element_key,
            }
        )

    def sort_key(item: dict[str, Any]) -> tuple[int, int]:
        values = parse_bounds(item.get("bounds", ""))
        return ((values[1] + values[3]) // 2, (values[0] + values[2]) // 2)

    return sorted(elements, key=sort_key)


__all__ = [
    "compute_state_id",
    "extract_clickable_elements",
    "normalize_hierarchy",
    "parse_bounds",
]
