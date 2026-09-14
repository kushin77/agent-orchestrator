"""Drill-down — one keystroke from aggregate to raw record (issue #566).

    org → lane → issue → agent → call → tool-call

The breadcrumb is always visible, and each level names the declared RC-10
function it consumes. The level→function map is carried by the fixture (data,
not code), and the renderer refuses an undeclared function BY NAME — which is
exactly the registry-conformance gate's provoked mutant: a fixture that points
a level at a function the registry does not declare renders
``UNDECLARED-FUNCTION``, never a silently healthy pane.

The offline source reads a committed fixture (the same fixture technique
``cockpit_render`` proves); a live source would answer the same levels from the
declared reads. A level with no rows renders ``NO_DATA`` — named, never green.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from . import _paths

#: The closed drill levels (the issue's own path).
LEVELS: tuple[str, ...] = ("org", "lane", "issue", "agent", "call", "tool-call")

DEFAULT_FIXTURE = _paths.FIXTURES / "drill.json"


class DrillError(RuntimeError):
    """The drill cannot be rendered honestly (CANNOT-ASSESS for the caller)."""


@dataclass(frozen=True)
class Row:
    kind: str
    title: str


class DrillSource:
    """The source a drill level reads — rows for a path, and a leaf's detail."""

    def root_title(self) -> str:  # pragma: no cover - interface shape
        raise NotImplementedError

    def rows(self, path: Sequence[tuple[str, str]]) -> list[Row]:  # pragma: no cover
        raise NotImplementedError

    def detail(self, path: Sequence[tuple[str, str]]) -> tuple[str, ...]:  # pragma: no cover
        raise NotImplementedError


@dataclass
class Drill:
    source: DrillSource
    level_functions: Mapping[str, str]
    path: list[tuple[str, str]] = field(default_factory=lambda: [("org", "")])
    selected: int = 0

    @property
    def level(self) -> str:
        return self.path[-1][0]

    def breadcrumb(self) -> str:
        return " \u203a ".join(name for _kind, name in self.path if name)

    def rows(self) -> list[Row]:
        return self.source.rows(self.path)

    def descend(self) -> bool:
        rows = self.rows()
        if not rows:
            return False
        index = min(max(self.selected, 0), len(rows) - 1)
        row = rows[index]
        self.path.append((row.kind, row.title))
        self.selected = 0
        return True

    def ascend(self) -> bool:
        if len(self.path) <= 1:
            return False
        self.path.pop()
        self.selected = 0
        return True

    def function_for(self, level: str) -> str:
        return str(self.level_functions.get(level, ""))

    def render(self, registry: Any, width: int = 96) -> str:
        """The drill frame for the current level (breadcrumb + rows + keys).

        The consumed function is checked against the registry at render time:
        a level whose function is not declared renders a named failure, so a
        mutant fixture cannot smuggle an undeclared function into a frame.
        """
        function_id = self.function_for(self.level)
        if not function_id or function_id not in registry.functions:
            return "\n".join(
                (
                    f"  FAILED  UNDECLARED-FUNCTION: {function_id or '(none)'}",
                    f"  drill level {self.level!r} names a function the registry does not declare",
                )
            )
        lines = [f"  {function_id:<12} drill: {self.breadcrumb()}"]
        rows = self.rows()
        if rows:
            for index, row in enumerate(rows):
                marker = ">" if index == self.selected else " "
                lines.append(f"  {marker} {index + 1:<2} {row.title:<46} [{row.kind}]")
        else:
            detail = self.source.detail(self.path)
            if detail:
                lines.append("  raw record:")
                lines.extend(f"    {line}" for line in detail)
            else:
                lines.append(
                    "  NO_DATA  (this level has no rows — absence, never a green state)"
                )
        lines.append("  d descend · b back up · 1-9 select · q quit")
        return "\n".join(lines)


class FixtureDrillSource(DrillSource):
    """The committed drill fixture, walked level by level.

    The fixture carries the closed ``levels`` map (level → declared function
    id) and the nested record tree. Load time validates the closed level set
    and every node's kind, so a malformed fixture raises rather than rendering
    a guessed row.
    """

    def __init__(
        self, path: Optional[Path | str] = None, document: Optional[Mapping[str, Any]] = None
    ) -> None:
        if document is None:
            target = Path(path) if path is not None else DEFAULT_FIXTURE
            try:
                document = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise DrillError(f"{target} is unreadable: {exc!r}") from exc
        if not isinstance(document, Mapping):
            raise DrillError("the drill fixture is not a mapping")
        levels = document.get("levels")
        if not isinstance(levels, Mapping) or tuple(levels) != LEVELS:
            declared = tuple(levels) if isinstance(levels, Mapping) else ()
            raise DrillError(
                f"the drill fixture declares levels {list(declared)}; "
                f"the closed set is {list(LEVELS)}"
            )
        root = document.get("root")
        if not isinstance(root, Mapping):
            raise DrillError("the drill fixture declares no root node")
        self.document = document
        self.level_functions = {str(key): str(value) for key, value in levels.items()}
        self.root = root
        self._validate_node(self.root, LEVELS[0])

    def _validate_node(self, node: Any, kind: str) -> None:
        if not isinstance(node, Mapping):
            raise DrillError(f"the drill fixture node {node!r} is not a mapping")
        if str(node.get("kind", "")) != kind:
            raise DrillError(
                f"the drill fixture node declares kind {node.get('kind')!r}, expected {kind!r}"
            )
        children = node.get("children")
        if children is None:
            return
        if not isinstance(children, list):
            raise DrillError(f"the drill fixture node {kind!r} has a non-list children")
        if kind not in LEVELS or kind == LEVELS[-1]:
            raise DrillError(f"the drill fixture grows children under the leaf level {kind!r}")
        child_kind = LEVELS[LEVELS.index(kind) + 1]
        for child in children:
            self._validate_node(child, child_kind)

    def root_title(self) -> str:
        return str(self.root.get("title", ""))

    def _node(self, path: Sequence[tuple[str, str]]) -> Optional[Mapping[str, Any]]:
        node: Any = self.root
        for _kind, title in path[1:]:
            children = node.get("children") if isinstance(node, Mapping) else None
            if not isinstance(children, list):
                return None
            node = next(
                (child for child in children if isinstance(child, Mapping) and child.get("title") == title),
                None,
            )
            if node is None:
                return None
        return node if isinstance(node, Mapping) else None

    def rows(self, path: Sequence[tuple[str, str]]) -> list[Row]:
        node = self._node(path)
        if node is None:
            return []
        children = node.get("children")
        if not isinstance(children, list):
            return []
        return [
            Row(kind=str(child["kind"]), title=str(child["title"]))
            for child in children
            if isinstance(child, Mapping)
        ]

    def detail(self, path: Sequence[tuple[str, str]]) -> tuple[str, ...]:
        node = self._node(path)
        if node is None:
            return ()
        raw = node.get("detail")
        if not isinstance(raw, list):
            return ()
        return tuple(str(line) for line in raw)


def load_drill(path: Optional[Path | str] = None) -> Drill:
    """The committed drill fixture, loaded and validated."""
    source = FixtureDrillSource(path)
    drill = Drill(source=source, level_functions=dict(source.level_functions))
    drill.path = [("org", source.root_title())]
    return drill
