"""Rule-condition evaluation for the gate engine.

A rule condition is a small, dependency-free predicate tree expressed in YAML:

    condition:
      all:
        - path: budget.utilization_ratio
          op: gte
          value: 1.0
        - path: tenant.is_preview
          op: ne
          value: true

Supported node keys:

* ``all`` — every child must hold (short-circuits on the first false child).
* ``any`` — at least one child must hold (short-circuits on the first true).
* ``not`` — a single child whose truth is inverted.
* leaf ``{path, op, value}`` — ``value`` is omitted for the presence
  operators ``exists``/``missing``.

Leaves resolve ``path`` by dotted lookup into the evaluation context and may
index lists numerically (``request.tags[0]``).  Operators:

=============  ==========================================================
``exists``     the path is present in the context
``missing``    the path is absent from the context
``eq``/``ne``  strict equality / inequality (bool never equals 0/1/2...)
``gt``/``gte`` numeric greater-than / greater-or-equal
``lt``/``lte`` numeric less-than / less-or-equal
``in``         the value at ``path`` is a member of the ``value`` list
``not_in``     the value at ``path`` is not a member of the ``value`` list
``glob``       fnmatch the value at ``path`` against the ``value`` pattern
``regex``      search the ``value`` regular expression against the value
=============  ==========================================================

Fail-closed semantics (AO-GR-4 / AO-GR-19, no-false-green): a leaf that
*cannot be evaluated* — an unknown operator, a non-numeric comparison on a
non-numeric value, an ``in``/``not_in`` target that is not a list, an invalid
regular expression, or a **required path absent from the context** — raises
:class:`ConditionError`.  The engine converts that into a BLOCK with the
reason attached to the evidence.  Absence of an attribute is therefore never
a silent pass: a rule that needs a field fails closed when the platform did
not supply it.  Policy authors who mean "the attribute may or may not be
present" use ``exists``/``missing``.
"""

from __future__ import annotations

import fnmatch
import numbers
import re
from typing import Any, List, Mapping, Optional, Sequence

from policy.errors import ConditionError

_PRESENCE_OPS = frozenset({"exists", "missing"})
_COMPARISON_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte"})
_SET_OPS = frozenset({"in", "not_in"})
_PATTERN_OPS = frozenset({"glob", "regex"})
_VALUE_OPS = frozenset(_COMPARISON_OPS | _SET_OPS | _PATTERN_OPS)
KNOWN_OPS = frozenset(_PRESENCE_OPS | _VALUE_OPS)

_LEAF_KEYS = frozenset({"path", "op", "value"})
_NODE_KEYS = frozenset({"all", "any", "not"})
_ALLOWED_KEYS = frozenset(_LEAF_KEYS | _NODE_KEYS)

_MISSING = object()

_SEGMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)((?:\[\d+\])*)$")
_INDEX_RE = re.compile(r"\[(\d+)\]")


def _split_path(path: str) -> List[Any]:
    """Split a dotted path into string/numeric-index tokens.

    Raises :class:`ConditionError` for an unparseable path.
    """
    tokens: List[Any] = []
    for segment in path.split("."):
        match = _SEGMENT_RE.match(segment)
        if not match:
            raise ConditionError(f"invalid condition path segment {segment!r} in {path!r}")
        tokens.append(match.group(1))
        tokens.extend(int(idx) for idx in _INDEX_RE.findall(match.group(2)))
    if not tokens:
        raise ConditionError(f"empty condition path {path!r}")
    return tokens


def resolve_path(context: Any, path: str) -> tuple[bool, Any]:
    """Resolve *path* against *context*; return ``(present, value)``."""
    tokens = _split_path(path)
    node: Any = context
    for token in tokens:
        if isinstance(node, Mapping):
            if isinstance(token, int) or token not in node:
                return False, None
            node = node[token]
        elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
            if not isinstance(token, int) or not -len(node) <= token < len(node):
                return False, None
            node = node[token]
        else:
            return False, None
    return True, node


def _strict_equal(left: Any, right: Any) -> bool:
    """Equality without the Python ``True == 1`` footgun."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    return left == right


def _is_number(value: Any) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def _eval_leaf(leaf: Mapping[str, Any], context: Any) -> bool:
    path = leaf.get("path")
    op = leaf.get("op")
    if not isinstance(path, str) or not path:
        raise ConditionError(f"condition leaf missing a string 'path': {leaf!r}")
    if not isinstance(op, str) or op not in KNOWN_OPS:
        raise ConditionError(f"unknown condition operator {op!r} at path {path!r}")

    if op in _PRESENCE_OPS:
        present, _ = resolve_path(context, path)
        return present if op == "exists" else (not present)

    present, actual = resolve_path(context, path)
    if not present:
        raise ConditionError(
            f"condition path {path!r} (op {op!r}) is absent from the context; "
            "fail closed (use exists/missing for optional attributes)"
        )
    if "value" not in leaf or leaf["value"] is _MISSING:
        raise ConditionError(f"operator {op!r} at path {path!r} requires a 'value'")
    value = leaf["value"]

    if op == "eq":
        return _strict_equal(actual, value)
    if op == "ne":
        return not _strict_equal(actual, value)
    if op in _COMPARISON_OPS:
        if not (_is_number(actual) and _is_number(value)):
            raise ConditionError(
                f"operator {op!r} at path {path!r} requires numeric operands "
                f"(got {type(actual).__name__}, {type(value).__name__})"
            )
        if op == "gt":
            return actual > value
        if op == "gte":
            return actual >= value
        if op == "lt":
            return actual < value
        return actual <= value
    if op in _SET_OPS:
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise ConditionError(f"operator {op!r} at path {path!r} requires a list value")
        return actual in value if op == "in" else actual not in value
    if op == "glob":
        if not isinstance(value, str):
            raise ConditionError(f"operator 'glob' at path {path!r} requires a string pattern")
        return fnmatch.fnmatch(str(actual), value)
    if op == "regex":
        if not isinstance(value, str):
            raise ConditionError(f"operator 'regex' at path {path!r} requires a string pattern")
        try:
            pattern = re.compile(value)
        except re.error as exc:
            raise ConditionError(f"invalid regular expression {value!r}: {exc}") from exc
        return pattern.search(str(actual)) is not None
    raise ConditionError(f"unhandled operator {op!r} at path {path!r}")  # pragma: no cover


def evaluate_condition(node: Any, context: Any) -> bool:
    """Evaluate a condition tree to a boolean.

    Raises :class:`ConditionError` on any leaf that cannot be evaluated
    (see module docstring) so the gate engine can fail closed.
    """
    if not isinstance(node, Mapping):
        raise ConditionError(f"condition node must be a mapping, got {type(node).__name__}")

    if "path" in node:
        return _eval_leaf(node, context)

    if "all" in node:
        children = node["all"]
        if not isinstance(children, list) or not children:
            raise ConditionError("'all' must be a non-empty list of conditions")
        for child in children:
            if not evaluate_condition(child, context):
                return False
        return True

    if "any" in node:
        children = node["any"]
        if not isinstance(children, list) or not children:
            raise ConditionError("'any' must be a non-empty list of conditions")
        for child in children:
            if evaluate_condition(child, context):
                return True
        return False

    if "not" in node:
        child = node["not"]
        return not evaluate_condition(child, context)

    unknown = set(node) - _ALLOWED_KEYS
    if unknown:
        raise ConditionError(f"unknown condition key(s): {sorted(unknown)!r}")
    raise ConditionError(f"condition node has no evaluable key: {sorted(node)!r}")


def validate_condition(node: Any) -> List[str]:
    """Return the structural problems of a condition tree (startup checks).

    Unlike :func:`evaluate_condition` this never needs a runtime context: it
    validates shape, operators, list/string typing and regular-expression
    compilation so a malformed condition fails at deploy time.  A non-empty
    return means the condition is rejected by the startup gate.
    """
    problems: List[str] = []

    def walk(current: Any, where: str) -> None:
        if not isinstance(current, Mapping):
            problems.append(f"{where}: condition node must be a mapping")
            return
        unknown = set(current) - _ALLOWED_KEYS
        if unknown:
            problems.append(f"{where}: unknown condition key(s) {sorted(unknown)!r}")
        if "path" in current:
            if "all" in current or "any" in current or "not" in current:
                problems.append(f"{where}: leaf and tree keys cannot be mixed")
                return
            op = current.get("op")
            if not isinstance(op, str) or op not in KNOWN_OPS:
                problems.append(f"{where}: unknown operator {op!r}")
                return
            path = current.get("path")
            if not isinstance(path, str) or not path:
                problems.append(f"{where}: 'path' must be a non-empty string")
                return
            if op in _PRESENCE_OPS:
                if "value" in current and current["value"] is not None:
                    problems.append(f"{where}: operator {op!r} takes no 'value'")
                return
            if "value" not in current:
                problems.append(f"{where}: operator {op!r} requires a 'value'")
                return
            value = current["value"]
            if op in _SET_OPS and not isinstance(value, (list, tuple, set, frozenset)):
                problems.append(f"{where}: operator {op!r} requires a list 'value'")
            elif op == "glob" and not isinstance(value, str):
                problems.append(f"{where}: operator 'glob' requires a string 'value'")
            elif op == "regex":
                if not isinstance(value, str):
                    problems.append(f"{where}: operator 'regex' requires a string 'value'")
                else:
                    try:
                        re.compile(value)
                    except re.error as exc:
                        problems.append(f"{where}: invalid regular expression {value!r}: {exc}")
            return
        for key in ("all", "any"):
            if key in current:
                children = current[key]
                if not isinstance(children, list) or not children:
                    problems.append(f"{where}.{key}: must be a non-empty list")
                    continue
                for index, child in enumerate(children):
                    walk(child, f"{where}.{key}[{index}]")
        if "not" in current:
            walk(current["not"], f"{where}.not")

    walk(node, "condition")
    return problems
