"""Screen-state identity, classification, and similarity matching."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Mapping, Sequence

from aegis_cartographer.core.hierarchy import (
    _is_interactive,
    _node_identity,
    normalize_hierarchy,
)
from aegis_cartographer.core.models import (
    CandidateScore,
    NormalizedHierarchy,
    NormalizedNode,
    Platform,
    ScreenMatchResult,
    ScreenMatchType,
    ScreenObservation,
    ScreenSignature,
    ScreenStateRecord,
    ScreenStateType,
)

_DYNAMIC_TEXT_RE = re.compile(r"[\d¥$€£%，,.:/\-]+")
_PACKAGE_KEYS = (
    "package",
    "packageName",
    "package_name",
    "bundleId",
    "bundleID",
    "bundle_id",
    "appId",
    "app_id",
)
_OVERLAY_MARKERS = (
    "alertdialog",
    "dialogfragment",
    "modal",
    "popupwindow",
    "uiactionsheet",
    "actionsheet",
    "alertcontroller",
    "action.sheet",
    "bottomsheet",
    "bottom.sheet",
    "sheetdialog",
)
_LANDMARK_ID_MARKERS = (
    "title",
    "header",
    "toolbar",
    "navbar",
    "navigationbar",
    "tabbar",
    "tablayout",
    "screen_title",
    "page_title",
)
_LANDMARK_CLASS_MARKERS = (
    "toolbar",
    "actionbar",
    "navbar",
    "navigationbar",
    "tabbar",
    "tablayout",
    "header",
    "title",
)
_EMPTY_MARKERS = (
    "暂无",
    "没有数据",
    "空空如也",
    "no data",
    "empty",
    "nothing here",
)
_ERROR_MARKERS = (
    "加载失败",
    "网络错误",
    "请求失败",
    "出错了",
    "load failed",
    "loading failed",
    "network error",
    "something went wrong",
)


def _stable_hash(value: object) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _clean_text(value: str) -> str:
    return " ".join(value.strip().split())


def _normalized_text(value: str) -> str:
    return _clean_text(value).casefold()


def _looks_like_dynamic_text(value: str) -> bool:
    text = _clean_text(value)
    if not text or len(text) > 80:
        return True
    stripped = _DYNAMIC_TEXT_RE.sub("", text)
    return len(stripped) / max(len(text), 1) < 0.45


def _node_markers(node: NormalizedNode) -> str:
    return " ".join(
        [
            node.resource_id,
            node.accessibility_id,
            node.content_desc,
            node.class_name,
        ]
    ).casefold()


def _has_marker(node: NormalizedNode, markers: Sequence[str]) -> bool:
    value = _node_markers(node)
    return any(marker in value for marker in markers)


def _is_loading_hierarchy(hierarchy: NormalizedHierarchy) -> bool:
    for node, _parent, _depth in hierarchy.root.walk():
        marker = node.class_name.casefold()
        if any(value in marker for value in ("progressbar", "activityindicator", "spinner")):
            return True
    return False


def _landmark_texts(hierarchy: NormalizedHierarchy) -> tuple[str, ...]:
    """Extract conservative, semantic page landmarks.

    Dynamic list values, prices, dates, and long content are deliberately ignored.
    A text node is considered semantic when its identifier/class marks it as a
    title, toolbar, tab, header, or a short high-level label near the tree root.
    """

    landmarks: set[str] = set()
    for node, _parent, depth in hierarchy.root.walk():
        text = _clean_text(node.text or node.content_desc or node.accessibility_id)
        if not text or _looks_like_dynamic_text(text):
            continue

        is_structural_label = _has_marker(node, _LANDMARK_ID_MARKERS) or _has_marker(
            node, _LANDMARK_CLASS_MARKERS
        )
        has_specific_identifier = bool(node.resource_id or node.accessibility_id)
        if has_specific_identifier and not is_structural_label:
            # IDs such as first_value, price, and order_status often carry dynamic
            # data. They must not become page-level semantic landmarks.
            continue
        is_short_label = (
            depth <= 3
            and not node.clickable
            and not node.scrollable
            and any(
                marker in node.class_name.casefold()
                for marker in ("textview", "text", "label", "statictext")
            )
        )
        if is_structural_label or is_short_label:
            landmarks.add(text)

    return tuple(sorted(landmarks, key=lambda item: (item.casefold(), item)))


def _detect_state_type(
    hierarchy: NormalizedHierarchy,
    signature: ScreenSignature,
    landmarks: Sequence[str],
) -> ScreenStateType:
    landmark_text = " ".join(landmarks).casefold()

    for node, _parent, _depth in hierarchy.root.walk():
        markers = _node_markers(node)
        if any(marker in markers for marker in _OVERLAY_MARKERS):
            if "bottom" in markers or "sheet" in markers:
                return ScreenStateType.BOTTOM_SHEET
            if "permission" in markers or "system" in markers:
                return ScreenStateType.SYSTEM_DIALOG
            return ScreenStateType.DIALOG

    if any(marker in landmark_text for marker in _EMPTY_MARKERS):
        return ScreenStateType.EMPTY
    if any(marker in landmark_text for marker in _ERROR_MARKERS):
        return ScreenStateType.ERROR
    if _is_loading_hierarchy(hierarchy):
        return ScreenStateType.LOADING

    _ = signature
    return ScreenStateType.PAGE


def _extract_package(raw: Mapping[str, object]) -> str:
    for key in _PACKAGE_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    ui_schema = raw.get("ui_schema")
    if isinstance(ui_schema, Mapping):
        for key in _PACKAGE_KEYS:
            value = ui_schema.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    attributes = raw.get("attributes")
    if isinstance(attributes, Mapping):
        for key in _PACKAGE_KEYS:
            value = attributes.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _feature_set(hierarchy: NormalizedHierarchy, interactive_only: bool) -> Counter[str]:
    features: list[str] = []
    for node, parent, depth in hierarchy.root.walk():
        if interactive_only and not _is_interactive(node):
            continue
        parent_identity = _node_identity(parent) if parent is not None else ""
        features.append(
            json.dumps(
                [
                    node.resource_id,
                    node.accessibility_id,
                    node.class_name,
                    parent_identity,
                    depth,
                    node.clickable,
                    node.scrollable,
                    node.checkable,
                    node.focusable,
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    return Counter(features)


def _counter_similarity(left: Counter[str], right: Counter[str]) -> float:
    if not left and not right:
        return 1.0
    intersection = sum((left & right).values())
    union = sum((left | right).values())
    return intersection / union if union else 0.0


def _set_similarity(left: Sequence[str], right: Sequence[str]) -> float | None:
    if not left or not right:
        return None
    left_set = {_normalized_text(item) for item in left}
    right_set = {_normalized_text(item) for item in right}
    intersection = len(left_set & right_set)
    union = len(left_set | right_set)
    return intersection / union if union else 0.0


def observe_screen(
    source: NormalizedHierarchy | Mapping[str, object],
    *,
    target_app_id: str = "",
    package_name: str | None = None,
    platform: Platform = Platform.ANDROID,
) -> ScreenObservation:
    """Normalize and classify one hierarchy observation."""

    hierarchy = source if isinstance(source, NormalizedHierarchy) else normalize_hierarchy(source, platform)
    raw_package = package_name if package_name is not None else (
        _extract_package(source) if isinstance(source, Mapping) else ""
    )
    from aegis_cartographer.core.hierarchy import compute_screen_signature

    signature = compute_screen_signature(hierarchy, hierarchy.platform)
    landmarks = _landmark_texts(hierarchy)
    state_type = _detect_state_type(hierarchy, signature, landmarks)
    semantic_hash = _stable_hash(
        {
            "landmarks": landmarks,
            "interactive": signature.interactive_hash,
        }
    )

    return ScreenObservation(
        signature=signature,
        hierarchy=hierarchy,
        state_type=state_type,
        landmarks=landmarks,
        semantic_hash=semantic_hash,
        package_name=raw_package,
        target_app_id=target_app_id,
    )


def derive_screen_id(observation: ScreenObservation) -> str:
    identity_source = observation.semantic_hash if observation.landmarks else observation.signature.interactive_hash
    return _stable_hash(
        {
            "kind": "screen",
            "app": observation.target_app_id or observation.package_name,
            "semantic": identity_source,
        }
    )[:32]


def derive_state_id(observation: ScreenObservation) -> str:
    return _stable_hash(
        {
            "kind": "state",
            "app": observation.target_app_id or observation.package_name,
            "type": observation.state_type.value,
            "structural": observation.signature.structural_hash,
            "interactive": observation.signature.interactive_hash,
            "semantic": observation.semantic_hash,
        }
    )


def create_state_record(
    observation: ScreenObservation,
    *,
    screen_id: str | None = None,
    state_id: str | None = None,
) -> ScreenStateRecord:
    """Create a persistent record from an observation."""

    return ScreenStateRecord(
        state_id=state_id or derive_state_id(observation),
        screen_id=screen_id or derive_screen_id(observation),
        state_type=observation.state_type,
        signature=observation.signature,
        landmarks=observation.landmarks,
        semantic_hash=observation.semantic_hash,
        package_name=observation.package_name,
        structural_features=tuple(sorted(_feature_set(observation.hierarchy, False).elements())),
        interactive_features=tuple(sorted(_feature_set(observation.hierarchy, True).elements())),
    )


class ScreenMatcher:
    """Match observations against known screen states using composite similarity."""

    def __init__(
        self,
        *,
        exact_threshold: float = 0.98,
        variant_structural_threshold: float = 0.72,
        variant_interactive_threshold: float = 0.48,
        strong_interactive_threshold: float = 0.84,
        semantic_conflict_threshold: float = 0.32,
        semantic_agreement_threshold: float = 0.64,
        ambiguity_window: float = 0.03,
    ) -> None:
        self.exact_threshold = exact_threshold
        self.variant_structural_threshold = variant_structural_threshold
        self.variant_interactive_threshold = variant_interactive_threshold
        self.strong_interactive_threshold = strong_interactive_threshold
        self.semantic_conflict_threshold = semantic_conflict_threshold
        self.semantic_agreement_threshold = semantic_agreement_threshold
        self.ambiguity_window = ambiguity_window

        if not 0.0 < exact_threshold <= 1.0:
            raise ValueError("exact_threshold must be in (0, 1]")
        if not 0.0 <= variant_structural_threshold <= 1.0:
            raise ValueError("variant_structural_threshold must be in [0, 1]")
        if not 0.0 <= variant_interactive_threshold <= 1.0:
            raise ValueError("variant_interactive_threshold must be in [0, 1]")

    def _score(
        self,
        observation: ScreenObservation,
        record: ScreenStateRecord,
        structural_features: Counter[str],
        interactive_features: Counter[str],
    ) -> CandidateScore:
        structural = _counter_similarity(
            structural_features,
            Counter(record.structural_features),
        )
        interactive = _counter_similarity(
            interactive_features,
            Counter(record.interactive_features),
        )
        semantic = _set_similarity(observation.landmarks, record.landmarks)
        if semantic is None:
            overall = structural * 0.5625 + interactive * 0.4375
        else:
            overall = structural * 0.45 + interactive * 0.35 + semantic * 0.20
        return CandidateScore(
            state_id=record.state_id,
            screen_id=record.screen_id,
            structural_similarity=round(structural, 6),
            interactive_similarity=round(interactive, 6),
            semantic_similarity=None if semantic is None else round(semantic, 6),
            overall_score=round(overall, 6),
        )

    def match(
        self,
        observation: ScreenObservation,
        known_states: Sequence[ScreenStateRecord],
    ) -> ScreenMatchResult:
        if (
            observation.target_app_id
            and observation.package_name
            and observation.package_name != observation.target_app_id
        ):
            return ScreenMatchResult(
                match_type=ScreenMatchType.EXTERNAL_APP,
                confidence=1.0,
                reason=(
                    f"Current package {observation.package_name!r} differs from "
                    f"target app {observation.target_app_id!r}"
                ),
                observation=observation,
                matched_state_id=None,
                matched_screen_id=None,
                recommended_state_id=derive_state_id(observation),
                recommended_screen_id=None,
                candidate_scores=(),
            )

        compatible = [
            record
            for record in known_states
            if not observation.package_name
            or not record.package_name
            or record.package_name == observation.package_name
        ]
        is_overlay = observation.state_type in {
            ScreenStateType.DIALOG,
            ScreenStateType.BOTTOM_SHEET,
            ScreenStateType.SYSTEM_DIALOG,
        }
        compatible = [
            record
            for record in compatible
            if (
                record.state_type
                in {ScreenStateType.DIALOG, ScreenStateType.BOTTOM_SHEET, ScreenStateType.SYSTEM_DIALOG}
                if is_overlay
                else record.state_type
                not in {ScreenStateType.DIALOG, ScreenStateType.BOTTOM_SHEET, ScreenStateType.SYSTEM_DIALOG}
            )
        ]

        if not compatible:
            match_type = ScreenMatchType.OVERLAY if is_overlay else ScreenMatchType.NEW_SCREEN
            return ScreenMatchResult(
                match_type=match_type,
                confidence=1.0,
                reason="No compatible known state exists",
                observation=observation,
                matched_state_id=None,
                matched_screen_id=None,
                recommended_state_id=derive_state_id(observation),
                recommended_screen_id=derive_screen_id(observation),
                candidate_scores=(),
            )

        structural_features = _feature_set(observation.hierarchy, interactive_only=False)
        interactive_features = _feature_set(observation.hierarchy, interactive_only=True)
        scores = tuple(
            sorted(
                (
                    self._score(observation, record, structural_features, interactive_features)
                    for record in compatible
                ),
                key=lambda item: item.overall_score,
                reverse=True,
            )
        )
        top = scores[0]
        top_record = next(record for record in compatible if record.state_id == top.state_id)

        if len(scores) > 1:
            runner_up = scores[1]
            if (
                runner_up.screen_id != top.screen_id
                and 0.55 <= top.overall_score <= 0.9
                and top.overall_score - runner_up.overall_score <= self.ambiguity_window
            ):
                return ScreenMatchResult(
                    match_type=ScreenMatchType.AMBIGUOUS,
                    confidence=top.overall_score,
                    reason="Top candidates are too close to safely merge",
                    observation=observation,
                    matched_state_id=None,
                    matched_screen_id=None,
                    recommended_state_id=derive_state_id(observation),
                    recommended_screen_id=derive_screen_id(observation),
                    candidate_scores=scores,
                )

        semantic = _set_similarity(observation.landmarks, top_record.landmarks)
        exact_structure = (
            observation.signature.structural_hash == top_record.signature.structural_hash
            and observation.signature.interactive_hash == top_record.signature.interactive_hash
        )
        semantic_agrees = semantic is None or semantic >= self.semantic_agreement_threshold
        if exact_structure and semantic_agrees:
            return ScreenMatchResult(
                match_type=ScreenMatchType.SAME_STATE,
                confidence=max(0.98, top.overall_score),
                reason="Structural and interactive signatures match",
                observation=observation,
                matched_state_id=top_record.state_id,
                matched_screen_id=top_record.screen_id,
                recommended_state_id=top_record.state_id,
                recommended_screen_id=top_record.screen_id,
                candidate_scores=scores,
            )

        semantic_conflict = (
            semantic is not None and semantic <= self.semantic_conflict_threshold
        )
        variant_by_structure = (
            top.structural_similarity >= self.variant_structural_threshold
            and top.interactive_similarity >= self.variant_interactive_threshold
        )
        variant_by_interactive = (
            top.structural_similarity >= self.variant_interactive_threshold
            and top.interactive_similarity >= self.strong_interactive_threshold
        )

        if not semantic_conflict and (variant_by_structure or variant_by_interactive):
            return ScreenMatchResult(
                match_type=ScreenMatchType.STATE_VARIANT,
                confidence=min(0.94, max(0.60, top.overall_score)),
                reason="Structure is close enough to classify as another state of the same screen",
                observation=observation,
                matched_state_id=top_record.state_id,
                matched_screen_id=top_record.screen_id,
                recommended_state_id=derive_state_id(observation),
                recommended_screen_id=top_record.screen_id,
                candidate_scores=scores,
            )

        match_type = ScreenMatchType.OVERLAY if is_overlay else ScreenMatchType.NEW_SCREEN
        reason = (
            "Overlay structure differs from known overlays"
            if is_overlay
            else "Structure and interactive anchors differ from known screens"
        )
        if semantic_conflict:
            reason = "Semantic landmarks conflict with the closest structural candidate"

        return ScreenMatchResult(
            match_type=match_type,
            confidence=min(0.95, max(0.55, top.overall_score)),
            reason=reason,
            observation=observation,
            matched_state_id=None,
            matched_screen_id=None,
            recommended_state_id=derive_state_id(observation),
            recommended_screen_id=derive_screen_id(observation),
            candidate_scores=scores,
        )
