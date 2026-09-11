from __future__ import annotations

import pytest

from aegis_cartographer import get_skeleton_hash
from aegis_cartographer.core import (
    Platform,
    compute_screen_signature,
    extract_ui_elements,
    normalize_hierarchy,
)
from aegis_cartographer.core.hierarchy import HierarchyValidationError
from aegis_cartographer.hierarchy_parser import (
    compute_state_id,
    extract_clickable_elements,
)


def tree_payload(*, text: str = "订单", button_id: str = "com.example:id/orders") -> dict:
    return {
        "tree": {
            "class": "android.widget.FrameLayout",
            "resource-id": "android:id/content",
            "children": [
                {
                    "class": "android.widget.Button",
                    "resource-id": button_id,
                    "text": text,
                    "clickable": True,
                    "enabled": True,
                    "bounds": "[10,20][110,80]",
                },
                {
                    "class": "android.widget.ScrollView",
                    "resource-id": "com.example:id/scroll",
                    "scrollable": True,
                    "children": [
                        {
                            "class": "android.widget.Button",
                            "resource-id": "com.example:id/repeat",
                            "text": "重复按钮",
                            "clickable": True,
                            "bounds": "[10,100][110,160]",
                        },
                        {
                            "class": "android.widget.Button",
                            "resource-id": "com.example:id/repeat",
                            "text": "重复按钮",
                            "clickable": True,
                            "bounds": "[10,180][110,240]",
                        },
                    ],
                },
            ],
        }
    }


def cli_payload() -> dict:
    return {
        "attributes": {
            "class": "android.widget.FrameLayout",
            "resource-id": "android:id/content",
        },
        "children": [
            {
                "attributes": {
                    "class": "android.widget.Button",
                    "resource-id": "com.example:id/orders",
                    "text": "订单",
                    "bounds": "[10,20][110,80]",
                },
                "clickable": True,
                "enabled": True,
                "children": [],
            },
            {
                "attributes": {
                    "class": "android.widget.ScrollView",
                    "resource-id": "com.example:id/scroll",
                },
                "scrollable": True,
                "children": [
                    {
                        "attributes": {
                            "class": "android.widget.Button",
                            "resource-id": "com.example:id/repeat",
                            "text": "重复按钮",
                            "bounds": "[10,100][110,160]",
                        },
                        "clickable": True,
                        "children": [],
                    },
                    {
                        "attributes": {
                            "class": "android.widget.Button",
                            "resource-id": "com.example:id/repeat",
                            "text": "重复按钮",
                            "bounds": "[10,180][110,240]",
                        },
                        "clickable": True,
                        "children": [],
                    },
                ],
            },
        ],
    }


def mcp_payload() -> dict:
    return {
        "ui_schema": {"version": 1},
        "elements": {
            "cls": "android.widget.FrameLayout",
            "rid": "android:id/content",
            "c": [
                {
                    "cls": "android.widget.Button",
                    "rid": "com.example:id/orders",
                    "txt": "订单",
                    "b": "[10,20][110,80]",
                    "clk": True,
                    "en": True,
                },
                {
                    "cls": "android.widget.ScrollView",
                    "rid": "com.example:id/scroll",
                    "scr": True,
                    "c": [
                        {
                            "cls": "android.widget.Button",
                            "rid": "com.example:id/repeat",
                            "txt": "重复按钮",
                            "b": "[10,100][110,160]",
                            "clk": True,
                        },
                        {
                            "cls": "android.widget.Button",
                            "rid": "com.example:id/repeat",
                            "txt": "重复按钮",
                            "b": "[10,180][110,240]",
                            "clk": True,
                        },
                    ],
                },
            ],
        },
    }


def test_supported_source_formats_produce_equivalent_signatures() -> None:
    signatures = [
        compute_screen_signature(payload)
        for payload in (tree_payload(), cli_payload(), mcp_payload())
    ]

    assert {signature.state_id for signature in signatures} == {
        signatures[0].state_id
    }
    assert signatures[0].interactive_count == 4


def test_dynamic_text_and_bounds_do_not_change_state_identity() -> None:
    baseline = tree_payload()
    changed = tree_payload(text="全部订单", button_id="com.example:id/all_orders")

    # IDs are structural and intentionally change identity; only text/bounds are stable.
    assert compute_state_id(baseline) == compute_state_id(
        tree_payload(text="全部订单")
    )
    assert compute_state_id(baseline) != compute_state_id(changed)


def test_element_extraction_deduplicates_repeated_resource_ids() -> None:
    elements = extract_ui_elements(tree_payload())
    repeated = [element for element in elements if element.resource_id == "com.example:id/repeat"]

    assert [element.occurrence_index for element in repeated] == [1, 2]
    assert [element.element_key for element in repeated] == [
        "id:com.example:id/repeat#1",
        "id:com.example:id/repeat#2",
    ]


def test_scrollable_elements_are_extracted_for_later_exploration() -> None:
    normalized = normalize_hierarchy(tree_payload(), Platform.ANDROID)
    scrollables = [element for element in extract_ui_elements(normalized) if element.scrollable]

    assert len(scrollables) == 1
    assert scrollables[0].resource_id == "com.example:id/scroll"


def test_legacy_extraction_contains_stable_element_key_and_order() -> None:
    elements = extract_clickable_elements(tree_payload())

    assert [element["resource-id"] for element in elements] == [
        "com.example:id/orders",
        "com.example:id/repeat",
        "com.example:id/repeat",
    ]
    assert elements[-1]["element_key"] == "id:com.example:id/repeat#2"


def test_full_element_extraction_can_include_display_nodes() -> None:
    elements = extract_ui_elements(
        tree_payload(),
        include_non_actionable=True,
    )
    resource_ids = [element.resource_id for element in elements]

    assert "android:id/content" in resource_ids
    assert "com.app:id/title" in resource_ids or "com.example:id/orders" in resource_ids
    assert len(elements) > len(extract_ui_elements(tree_payload()))


def test_label_inside_clickable_ancestor_is_actionable() -> None:
    payload = {
        "tree": {
            "class": "Window",
            "children": [
                {
                    "class": "LinearLayout",
                    "clickable": True,
                    "bounds": "[0,100][200,200]",
                    "children": [
                        {
                            "class": "TextView",
                            "text": "记账",
                            "bounds": "[20,120][180,180]",
                        }
                    ],
                }
            ],
        }
    }

    elements = extract_ui_elements(payload)

    assert elements
    label = next(item for item in elements if item.text == "记账")
    assert label.actionable_ancestor is True


def test_invalid_hierarchy_is_rejected_instead_of_hashed_as_empty() -> None:
    with pytest.raises(HierarchyValidationError):
        normalize_hierarchy({})

    with pytest.raises(ValueError):
        get_skeleton_hash({})


def test_structural_hash_is_stable() -> None:
    payload = tree_payload()
    assert get_skeleton_hash(payload) == get_skeleton_hash(payload)
    assert len(get_skeleton_hash(payload)) == 64
