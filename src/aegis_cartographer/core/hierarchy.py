"""Normalization and identity extraction for mobile UI hierarchies."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any, Mapping

from aegis_cartographer.core.models import (
    Bounds,
    NormalizedHierarchy,
    NormalizedNode,
    Platform,
    ScreenSignature,
    UiElement,
)


class HierarchyValidationError(ValueError):
    """Raised when a hierarchy cannot be safely interpreted."""


_ABBREVIATIONS = {
    "b": "bounds",
    "rid": "resource-id",
    "txt": "text",
    "a11y": "content-desc",
    "cls": "class",
    "val": "value",
    "hint": "hintText",
    "c": "children",
    "clk": "clickable",
    "en": "enabled",
    "scr": "scrollable",
    "chk": "checkable",
    "sel": "selected",
    "foc": "focusable",
}
_BOUNDS_RE = re.compile(r"-?\d+")


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def _as_str(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple)):
        return ""
    return str(value)


def parse_bounds(value: Any) -> Bounds | None:
    """Parse Android/Maestro bounds without treating coordinates as identity."""

    text = _as_str(value)
    numbers = [int(item) for item in _BOUNDS_RE.findall(text)]
    if len(numbers) < 4:
        return None
    left, top, right, bottom = numbers[:4]
    return Bounds(left=left, top=top, right=right, bottom=bottom)


def _expand_abbreviated(value: Mapping[str, Any]) -> dict[str, Any]:
    expanded: dict[str, Any] = {}
    for key, item in value.items():
        full_key = _ABBREVIATIONS.get(key, key)
        if full_key == "children" and isinstance(item, list):
            expanded[full_key] = [
                _expand_abbreviated(child) if isinstance(child, Mapping) else child
                for child in item
            ]
        else:
            expanded[full_key] = item
    return expanded


def _source_mapping(raw: Mapping[str, Any]) -> tuple[Mapping[str, Any], bool]:
    """Return the root mapping and whether the MCP abbreviated format was used."""

    if "ui_schema" in raw:
        elements = raw.get("elements")
        if isinstance(elements, Mapping):
            return elements, True
        if not isinstance(elements, list) or not elements:
            raise HierarchyValidationError("MCP hierarchy has no elements")
        # Maestro MCP can return multiple top-level windows (app content, system
        # bars, and overlays). Preserve them under one synthetic root so map
        # signatures remain deterministic without losing visible siblings.
        if len(elements) == 1 and isinstance(elements[0], Mapping):
            return elements[0], True
        return {"cls": "MaestroMcpRoot", "c": elements}, True

    for key in ("tree", "elements", "root"):
        candidate = raw.get(key)
        if isinstance(candidate, Mapping):
            return candidate, bool(set(candidate) & set(_ABBREVIATIONS))
    return raw, bool(set(raw) & set(_ABBREVIATIONS))


def _normalize_mapping(value: Mapping[str, Any], abbreviated: bool) -> dict[str, Any]:
    source = _expand_abbreviated(value) if abbreviated else dict(value)

    attributes = source.get("attributes")
    if isinstance(attributes, Mapping):
        flattened = dict(attributes)
        for key in ("children", "clickable", "enabled", "scrollable", "checked", "selected", "focused"):
            if key in source:
                flattened[key] = source[key]
        source = flattened

    children = source.get("children", source.get("c", []))
    if not isinstance(children, list):
        children = []

    normalized_children: list[Mapping[str, Any]] = []
    for child in children:
        if isinstance(child, Mapping):
            normalized_children.append(child)

    source["children"] = normalized_children
    return source


def _normalize_node(value: Mapping[str, Any], abbreviated: bool) -> NormalizedNode:
    source = _normalize_mapping(value, abbreviated)

    resource_id = (
        _as_str(source.get("resource-id"))
        or _as_str(source.get("resourceId"))
        or _as_str(source.get("id"))
    )
    accessibility_id = (
        _as_str(source.get("accessibility-id"))
        or _as_str(source.get("accessibilityId"))
        or _as_str(source.get("identifier"))
    )
    content_desc = _as_str(source.get("content-desc")) or _as_str(
        source.get("contentDescription")
    )
    raw_attributes = {
        key: item
        for key, item in source.items()
        if key not in {"children"}
    }

    return NormalizedNode(
        class_name=_as_str(source.get("class") or source.get("className")),
        resource_id=resource_id,
        accessibility_id=accessibility_id,
        text=_as_str(source.get("text")),
        content_desc=content_desc,
        value=_as_str(source.get("value")),
        hint=_as_str(source.get("hintText") or source.get("hint")),
        bounds=parse_bounds(source.get("bounds")),
        clickable=_as_bool(source.get("clickable")),
        enabled=_as_bool(source.get("enabled"), True),
        scrollable=_as_bool(source.get("scrollable")),
        checkable=_as_bool(source.get("checkable")),
        checked=_as_bool(source.get("checked")),
        focusable=_as_bool(source.get("focusable")),
        focused=_as_bool(source.get("focused")),
        selected=_as_bool(source.get("selected")),
        children=[
            _normalize_node(child, abbreviated)
            for child in source.get("children", [])
            if isinstance(child, Mapping)
        ],
        raw_attributes=raw_attributes,
    )


def normalize_hierarchy(raw: Mapping[str, Any], platform: Platform = Platform.ANDROID) -> NormalizedHierarchy:
    """Normalize Maestro CLI/MCP, tree-wrapper, or raw CLI hierarchies."""

    if not isinstance(raw, Mapping):
        raise HierarchyValidationError("Hierarchy must be a JSON object")

    root_mapping, abbreviated = _source_mapping(raw)
    root = _normalize_node(root_mapping, abbreviated)
    has_meaningful_root = bool(
        root.class_name
        or root.resource_id
        or root.accessibility_id
        or root.text
        or root.content_desc
        or root.bounds
        or root.children
        or root.raw_attributes
    )
    if not has_meaningful_root:
        raise HierarchyValidationError("Hierarchy root has no usable attributes or children")
    return NormalizedHierarchy(platform=platform, root=root)


def _node_identity(node: NormalizedNode) -> str:
    if node.resource_id:
        return f"id:{node.resource_id}"
    if node.accessibility_id:
        return f"a11y:{node.accessibility_id}"
    return f"class:{node.class_name or 'unknown'}"


def _node_structural_feature(node: NormalizedNode, parent_identity: str, depth: int) -> list[Any]:
    # Text, content description, value, and bounds intentionally remain excluded:
    # they are frequently dynamic and would fragment the same logical page.
    return [
        node.resource_id,
        node.class_name,
        parent_identity,
        depth,
        node.clickable,
        node.scrollable,
        node.checkable,
        node.focusable,
    ]


def _is_interactive(node: NormalizedNode) -> bool:
    editable_class = node.class_name.endswith(
        ("EditText", "TextInputLayout", "TextField", "TextView")
    ) and bool(node.hint or node.focusable)
    return (
        node.clickable
        or node.scrollable
        or node.checkable
        or node.focusable
        or editable_class
    )


def _stable_text(node: NormalizedNode) -> str:
    return node.text.strip() or node.content_desc.strip() or node.accessibility_id.strip()


def _hash_features(features: list[Any]) -> str:
    serialized = json.dumps(features, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_screen_signature(
    hierarchy: NormalizedHierarchy | Mapping[str, Any],
    platform: Platform = Platform.ANDROID,
) -> ScreenSignature:
    """Compute deterministic structural, interactive, and landmark signatures."""

    normalized = (
        hierarchy
        if isinstance(hierarchy, NormalizedHierarchy)
        else normalize_hierarchy(hierarchy, platform)
    )

    structural_features: list[Any] = []
    interactive_features: list[Any] = []
    landmarks: list[str] = []
    node_count = 0
    interactive_count = 0

    for node, parent, depth in normalized.root.walk():
        node_count += 1
        parent_identity = _node_identity(parent) if parent is not None else ""
        structural_features.append(_node_structural_feature(node, parent_identity, depth))

        if _is_interactive(node):
            interactive_count += 1
            interactive_features.append(_node_structural_feature(node, parent_identity, depth))

        text = _stable_text(node)
        if text and (node.resource_id or node.accessibility_id or depth <= 3):
            landmarks.append(text)

    structural_hash = _hash_features(sorted(map(json.dumps, structural_features)))
    interactive_hash = _hash_features(sorted(map(json.dumps, interactive_features)))
    landmark_hash = _hash_features(sorted(landmarks))
    state_source = {
        "platform": normalized.platform.value,
        "structural": structural_hash,
        "interactive": interactive_hash,
    }
    state_id = _hash_features([state_source])

    return ScreenSignature(
        state_id=state_id,
        structural_hash=structural_hash,
        interactive_hash=interactive_hash,
        landmark_hash=landmark_hash,
        platform=normalized.platform,
        node_count=node_count,
        interactive_count=interactive_count,
        landmark_count=len(landmarks),
    )


def _element_selector(node: NormalizedNode, bounds: Bounds) -> str:
    if node.resource_id:
        return f"id:{node.resource_id}"
    if node.accessibility_id:
        return f"a11y:{node.accessibility_id}"
    if node.content_desc:
        return f"desc:{node.content_desc}"
    if node.text:
        return f"text:{node.text}"
    return f"class:{node.class_name or 'unknown'}@{bounds.left},{bounds.top}"


def _walk_with_ancestors(
    node: NormalizedNode,
    parent: NormalizedNode | None = None,
    depth: int = 0,
    ancestors: tuple[NormalizedNode, ...] = (),
):
    """Yield document-order nodes with their complete ancestor chain."""

    yield node, parent, depth, ancestors
    child_ancestors = (*ancestors, node)
    for child in node.children:
        yield from _walk_with_ancestors(child, node, depth + 1, child_ancestors)


def _has_actionable_ancestor(
    node: NormalizedNode,
    ancestors: tuple[NormalizedNode, ...],
) -> bool:
    """Infer actionability for a semantic label in a clickable container."""

    if not _stable_text(node) or node.bounds is None:
        return False
    for ancestor in reversed(ancestors):
        if not ancestor.clickable or not ancestor.enabled or ancestor.bounds is None:
            continue
        contains_label = (
            ancestor.bounds.left <= node.bounds.left
            and ancestor.bounds.top <= node.bounds.top
            and node.bounds.right <= ancestor.bounds.right
            and node.bounds.bottom <= ancestor.bounds.bottom
        )
        if contains_label:
            return True
    return False


def extract_ui_elements(
    hierarchy: NormalizedHierarchy | Mapping[str, Any],
    *,
    include_disabled: bool = False,
    include_non_actionable: bool = False,
    platform: Platform = Platform.ANDROID,
) -> list[UiElement]:
    """Extract actionable elements with stable occurrence-aware identities."""

    normalized = (
        hierarchy
        if isinstance(hierarchy, NormalizedHierarchy)
        else normalize_hierarchy(hierarchy, platform)
    )
    occurrences: Counter[str] = Counter()
    elements: list[UiElement] = []

    for node, parent, depth, ancestors in _walk_with_ancestors(normalized.root):
        actionable_ancestor = _has_actionable_ancestor(node, ancestors)
        if (
            not (_is_interactive(node) or actionable_ancestor)
            and not include_non_actionable
        ):
            continue
        if not include_disabled and not node.enabled:
            continue

        bounds = node.bounds or Bounds(0, 0, 0, 0)
        selector = _element_selector(node, bounds)
        occurrences[selector] += 1
        occurrence_index = occurrences[selector]
        element_key = f"{selector}#{occurrence_index}"
        parent_path = [_node_identity(ancestor) for ancestor in ancestors]

        elements.append(
            UiElement(
                element_key=element_key,
                selector=selector,
                occurrence_index=occurrence_index,
                class_name=node.class_name,
                resource_id=node.resource_id,
                accessibility_id=node.accessibility_id,
                text=node.text,
                content_desc=node.content_desc,
                value=node.value,
                hint=node.hint,
                bounds=bounds,
                clickable=node.clickable,
                enabled=node.enabled,
                scrollable=node.scrollable,
                checkable=node.checkable,
                checked=node.checked,
                selected=node.selected,
                focused=node.focused,
                depth=depth,
                parent_path=tuple(parent_path),
                actionable_ancestor=actionable_ancestor,
            )
        )

    return elements
