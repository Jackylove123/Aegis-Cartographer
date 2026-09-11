from __future__ import annotations

from typing import Any

import pytest

from aegis_cartographer.core import (
    Platform,
    ScreenMatcher,
    ScreenMatchType,
    ScreenStateType,
    create_state_record,
    observe_screen,
)
from aegis_cartographer.core.hierarchy import HierarchyValidationError

APP_ID = "com.example.app"


def page_payload(
    *,
    title: str = "订单列表",
    dynamic_value: str = "订单 A10001",
    extra_switch: bool = False,
) -> dict[str, Any]:
    children: list[dict[str, Any]] = [
        {
            "class": "android.widget.TextView",
            "resource-id": "com.example:id/page_title",
            "text": title,
        },
        {
            "class": "android.widget.TextView",
            "resource-id": "com.example:id/first_value",
            "text": dynamic_value,
        },
        {
            "class": "android.widget.Button",
            "resource-id": "com.example:id/detail",
            "text": "查看详情",
            "clickable": True,
        },
    ]
    if extra_switch:
        children.append(
            {
                "class": "android.widget.Switch",
                "resource-id": "com.example:id/only_pending",
                "text": "只看待支付",
                "clickable": True,
                "checkable": True,
            }
        )
    return {
        "package": APP_ID,
        "tree": {
            "class": "android.widget.FrameLayout",
            "resource-id": "android:id/content",
            "children": children,
        },
    }


def dialog_payload() -> dict[str, Any]:
    return {
        "package": APP_ID,
        "tree": {
            "class": "android.widget.FrameLayout",
            "resource-id": "android:id/content",
            "children": [
                {
                    "class": "androidx.appcompat.widget.AppCompatAlertDialog",
                    "resource-id": "com.example:id/dialog",
                    "children": [
                        {
                            "class": "android.widget.Button",
                            "resource-id": "com.example:id/confirm",
                            "text": "确认",
                            "clickable": True,
                        }
                    ],
                }
            ],
        },
    }


def ios_sheet_payload() -> dict[str, Any]:
    return {
        "bundleId": "com.example.ios",
        "tree": {
            "class": "XCUIElementTypeWindow",
            "children": [
                {
                    "class": "XCUIElementTypeActionSheet",
                    "children": [
                        {
                            "class": "XCUIElementTypeButton",
                            "identifier": "cancel",
                            "label": "取消",
                            "clickable": True,
                        }
                    ],
                }
            ],
        },
    }


def test_same_page_with_dynamic_data_is_same_state() -> None:
    baseline = observe_screen(page_payload(), target_app_id=APP_ID)
    changed_data = observe_screen(
        page_payload(dynamic_value="订单 B99999"),
        target_app_id=APP_ID,
    )
    matcher = ScreenMatcher()
    known = (create_state_record(baseline),)

    result = matcher.match(changed_data, known)

    assert result.match_type is ScreenMatchType.SAME_STATE
    assert result.matched_state_id == known[0].state_id
    assert changed_data.signature.state_id == baseline.signature.state_id


def test_additional_control_is_a_state_variant_of_same_screen() -> None:
    baseline = observe_screen(page_payload(), target_app_id=APP_ID)
    variant = observe_screen(page_payload(extra_switch=True), target_app_id=APP_ID)
    matcher = ScreenMatcher()
    known = (create_state_record(baseline),)

    result = matcher.match(variant, known)

    assert result.match_type is ScreenMatchType.STATE_VARIANT
    assert result.recommended_screen_id == known[0].screen_id
    assert result.recommended_state_id != known[0].state_id
    assert result.candidate_scores[0].structural_similarity >= 0.7


def test_same_structure_with_different_semantic_title_is_new_screen() -> None:
    orders = observe_screen(page_payload(title="订单列表"), target_app_id=APP_ID)
    coupons = observe_screen(page_payload(title="优惠券列表"), target_app_id=APP_ID)
    matcher = ScreenMatcher()

    result = matcher.match(coupons, (create_state_record(orders),))

    assert result.match_type is ScreenMatchType.NEW_SCREEN
    assert result.recommended_screen_id != result.matched_screen_id
    assert result.recommended_state_id != orders.signature.state_id


def test_android_dialog_is_classified_as_overlay() -> None:
    observation = observe_screen(dialog_payload(), target_app_id=APP_ID)
    result = ScreenMatcher().match(observation, ())

    assert observation.state_type is ScreenStateType.DIALOG
    assert result.match_type is ScreenMatchType.OVERLAY


def test_ios_action_sheet_is_classified_as_overlay() -> None:
    observation = observe_screen(
        ios_sheet_payload(),
        target_app_id="com.example.ios",
        platform=Platform.IOS,
    )
    result = ScreenMatcher().match(observation, ())

    assert observation.state_type is ScreenStateType.BOTTOM_SHEET
    assert result.match_type is ScreenMatchType.OVERLAY


def test_external_package_is_detected_before_screen_matching() -> None:
    observation = observe_screen(
        page_payload(),
        target_app_id=APP_ID,
        package_name="com.android.chrome",
    )
    known = (create_state_record(observe_screen(page_payload(), target_app_id=APP_ID)),)

    result = ScreenMatcher().match(observation, known)

    assert result.match_type is ScreenMatchType.EXTERNAL_APP
    assert result.matched_state_id is None


def test_empty_and_error_states_are_classified() -> None:
    empty = observe_screen(
        page_payload(title="暂无订单", dynamic_value="先去逛逛吧"),
        target_app_id=APP_ID,
    )
    error = observe_screen(
        page_payload(title="网络错误", dynamic_value="请稍后重试"),
        target_app_id=APP_ID,
    )

    assert empty.state_type is ScreenStateType.EMPTY
    assert error.state_type is ScreenStateType.ERROR


def test_overlay_classification_takes_precedence_over_error_text() -> None:
    payload = dialog_payload()
    dialog_node = payload["tree"]["children"][0]
    dialog_node["children"].insert(
        0,
        {
            "class": "android.widget.TextView",
            "resource-id": "com.example:id/dialog_title",
            "text": "网络错误",
        },
    )

    observation = observe_screen(payload, target_app_id=APP_ID)

    assert observation.state_type is ScreenStateType.DIALOG


def test_dynamically_named_value_fields_do_not_become_landmarks() -> None:
    baseline = observe_screen(
        page_payload(dynamic_value="已完成"),
        target_app_id=APP_ID,
    )
    changed = observe_screen(
        page_payload(dynamic_value="已取消"),
        target_app_id=APP_ID,
    )

    assert baseline.landmarks == changed.landmarks == ("订单列表",)


def test_invalid_observation_is_rejected() -> None:
    with pytest.raises(HierarchyValidationError):
        observe_screen({}, target_app_id=APP_ID)
