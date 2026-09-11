"""Typed domain models used by the Aegis core engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Platform(str, Enum):
    """Mobile platform supported by a hierarchy source."""

    ANDROID = "android"
    IOS = "ios"


class ScreenStateType(str, Enum):
    """The interaction role of an observed screen state."""

    PAGE = "page"
    DIALOG = "dialog"
    BOTTOM_SHEET = "bottom_sheet"
    SYSTEM_DIALOG = "system_dialog"
    LOADING = "loading"
    EMPTY = "empty"
    ERROR = "error"
    EXTERNAL_APP = "external_app"


class ScreenMatchType(str, Enum):
    """Decision produced by the screen identity matcher."""

    SAME_STATE = "same_state"
    STATE_VARIANT = "state_variant"
    NEW_SCREEN = "new_screen"
    OVERLAY = "overlay"
    EXTERNAL_APP = "external_app"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class Bounds:
    """Integer bounds in the ``[left, top, right, bottom]`` order."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def center_x(self) -> int:
        return (self.left + self.right) // 2

    @property
    def center_y(self) -> int:
        return (self.top + self.bottom) // 2

    def to_list(self) -> list[int]:
        return [self.left, self.top, self.right, self.bottom]


@dataclass
class NormalizedNode:
    """A platform-independent node in a normalized UI hierarchy."""

    class_name: str = ""
    resource_id: str = ""
    accessibility_id: str = ""
    text: str = ""
    content_desc: str = ""
    value: str = ""
    hint: str = ""
    bounds: Bounds | None = None
    clickable: bool = False
    enabled: bool = True
    scrollable: bool = False
    checkable: bool = False
    checked: bool = False
    focusable: bool = False
    focused: bool = False
    selected: bool = False
    children: list[NormalizedNode] = field(default_factory=list)
    raw_attributes: dict[str, Any] = field(default_factory=dict)

    def walk(self, parent: NormalizedNode | None = None, depth: int = 0):
        """Yield ``(node, parent, depth)`` in deterministic document order."""

        yield self, parent, depth
        for child in self.children:
            yield from child.walk(self, depth + 1)


@dataclass(frozen=True)
class NormalizedHierarchy:
    """A normalized hierarchy and the platform it was observed on."""

    platform: Platform
    root: NormalizedNode


@dataclass(frozen=True)
class ScreenSignature:
    """Composite signatures used to identify and compare screen states."""

    state_id: str
    structural_hash: str
    interactive_hash: str
    landmark_hash: str
    platform: Platform
    node_count: int
    interactive_count: int
    landmark_count: int


@dataclass(frozen=True)
class ScreenObservation:
    """One classified observation of a running application screen."""

    signature: ScreenSignature
    hierarchy: NormalizedHierarchy
    state_type: ScreenStateType
    landmarks: tuple[str, ...]
    semantic_hash: str
    package_name: str
    target_app_id: str


@dataclass(frozen=True)
class ScreenStateRecord:
    """A persistent screen-state identity derived from one or more observations."""

    state_id: str
    screen_id: str
    state_type: ScreenStateType
    signature: ScreenSignature
    landmarks: tuple[str, ...]
    semantic_hash: str
    package_name: str
    structural_features: tuple[str, ...] = ()
    interactive_features: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateScore:
    """Comparable similarity components for one known screen state."""

    state_id: str
    screen_id: str
    structural_similarity: float
    interactive_similarity: float
    semantic_similarity: float | None
    overall_score: float


@dataclass(frozen=True)
class ScreenMatchResult:
    """The matcher decision and enough context to persist a new state."""

    match_type: ScreenMatchType
    confidence: float
    reason: str
    observation: ScreenObservation
    matched_state_id: str | None
    matched_screen_id: str | None
    recommended_state_id: str
    recommended_screen_id: str | None
    candidate_scores: tuple[CandidateScore, ...]


@dataclass(frozen=True)
class UiElement:
    """A stable, queryable element extracted from one screen observation."""

    element_key: str
    selector: str
    occurrence_index: int
    class_name: str
    resource_id: str
    accessibility_id: str
    text: str
    content_desc: str
    value: str
    hint: str
    bounds: Bounds
    clickable: bool
    enabled: bool
    scrollable: bool
    checkable: bool
    checked: bool
    selected: bool
    focused: bool
    depth: int
    parent_path: tuple[str, ...]
    actionable_ancestor: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["parent_path"] = list(self.parent_path)
        return result
