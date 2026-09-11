"""Compatibility façade for stable screen fingerprinting.

New code should use :mod:`aegis_cartographer.core.hierarchy` directly. This
module remains because earlier tooling and tests import these functions.
"""

from __future__ import annotations

from typing import Any, Mapping

from aegis_cartographer.core.hierarchy import (
    HierarchyValidationError,
    compute_screen_signature,
    extract_ui_elements,
)


def _is_true(value: Any) -> bool:
    """兼容 bool、字符串和数字形式的布尔值。"""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def get_skeleton_hash(xml_data: Mapping[str, Any]) -> str:
    """Return a stable structural hash for a supported hierarchy payload."""

    try:
        return compute_screen_signature(xml_data).structural_hash
    except HierarchyValidationError as error:
        raise ValueError(str(error)) from error


def extract_clickable_elements(xml_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract clickable/enabled elements in the legacy dictionary shape."""

    return [
        {
            "id": element.resource_id or element.accessibility_id,
            "resource-id": element.resource_id,
            "accessibility-id": element.accessibility_id,
            "class": element.class_name,
            "text": element.text,
            "content-desc": element.content_desc,
            "value": element.value,
            "hintText": element.hint,
            "bounds": (
                f"[{element.bounds.left},{element.bounds.top}]"
                f"[{element.bounds.right},{element.bounds.bottom}]"
            ),
            "clickable": element.clickable,
            "enabled": element.enabled,
            "scrollable": element.scrollable,
            "selected": element.selected,
            "checked": element.checked,
            "element_key": element.element_key,
        }
        for element in extract_ui_elements(xml_data)
        if element.clickable
    ]


def calculate_similarity(hash1: str, hash2: str) -> float:
    """Warn callers that hash-text similarity is not a state similarity metric.

    The function is retained for compatibility, but page similarity should compare
    normalized feature sets instead of hexadecimal digest characters.
    """

    if hash1 == hash2:
        return 1.0
    matching_characters = sum(a == b for a, b in zip(hash1, hash2))
    return matching_characters / (max(len(hash1), len(hash2)) * 4)
