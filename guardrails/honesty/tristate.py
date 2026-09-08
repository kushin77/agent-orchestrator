"""Tri-state guard status model (issue #28, acceptance criterion 1).

Every guard in this repo reports exactly one of three statuses, and the exit
code is the wire format a caller can trust:

===============  ===========  =============================================
status           exit code    meaning
===============  ===========  =============================================
OK               0            the property the guard checks holds (PASS)
NOT-OK           1            the property is violated (FAIL)
CANNOT-ASSESS    2            the guard could not determine the property;
                              also 124 (timed out) or any non-contract exit
                              code.  NEVER reads as a pass.
===============  ===========  =============================================

The canonical vocabulary is the issue-body vocabulary (OK / NOT-OK /
CANNOT-ASSESS).  PASS / FAIL / UNKNOWN are accepted aliases so the umbrella
wording used across the fleet parses cleanly.  Note: this status axis is the
*honesty* of a guard (can it assess, and what did it find); the gate engine's
enforcement decision (BLOCK / WARN / LOG, AO-GR-19) is a different axis that
consumes this one and is out of scope here.

Aggregation is fail-closed by design: a single NOT-OK fails the whole gate;
otherwise any CANNOT-ASSESS keeps the gate from reading PASS (UNKNOWN is never
a pass); only an all-OK set aggregates to OK.
"""

from __future__ import annotations

import enum
import json
from typing import Any, Dict, Iterable, List, Union

# Exit-code contract: the codes a guard is allowed to emit and what each means.
EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2
# A guard that times out has not attested anything; it is CANNOT-ASSESS.
EXIT_TIMEOUT = 124


class TriState(enum.Enum):
    """The three honest statuses a guard may report."""

    OK = "OK"
    NOT_OK = "NOT-OK"
    CANNOT_ASSESS = "CANNOT-ASSESS"

    # -- query helpers --------------------------------------------------
    @property
    def is_pass(self) -> bool:
        """True only for OK.  CANNOT-ASSESS (UNKNOWN) is never a pass."""
        return self is TriState.OK

    @property
    def is_fail(self) -> bool:
        """True only for NOT-OK."""
        return self is TriState.NOT_OK

    @property
    def is_unknown(self) -> bool:
        """True only for CANNOT-ASSESS."""
        return self is TriState.CANNOT_ASSESS

    # `passes` is the alias-friendly spelling of `is_pass`.
    passes = is_pass

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# Accepted spellings -> canonical member.  The issue-body vocabulary is
# canonical; PASS/FAIL/UNKNOWN are umbrella aliases from the fleet doctrine.
_ALIASES: Dict[str, TriState] = {
    "OK": TriState.OK,
    "PASS": TriState.OK,
    "PASSED": TriState.OK,
    "GOOD": TriState.OK,
    "NOT-OK": TriState.NOT_OK,
    "NOT_OK": TriState.NOT_OK,
    "NOTOK": TriState.NOT_OK,
    "FAIL": TriState.NOT_OK,
    "FAILED": TriState.NOT_OK,
    "BAD": TriState.NOT_OK,
    "CANNOT-ASSESS": TriState.CANNOT_ASSESS,
    "CANNOT_ASSESS": TriState.CANNOT_ASSESS,
    "CANT-ASSESS": TriState.CANNOT_ASSESS,
    "CANT_ASSESS": TriState.CANNOT_ASSESS,
    "UNKNOWN": TriState.CANNOT_ASSESS,
    "INDETERMINATE": TriState.CANNOT_ASSESS,
}


def parse(value: Union[str, TriState]) -> TriState:
    """Parse a status from its canonical name or an alias.

    Raises ``ValueError`` for anything that is not a known status -- an
    unrecognized status must never silently become a pass.
    """
    if isinstance(value, TriState):
        return value
    if not isinstance(value, str):
        raise ValueError(f"cannot parse status from {value!r}")
    key = value.strip().upper().replace(" ", "-").replace("_", "-")
    if key in _ALIASES:
        return _ALIASES[key]
    raise ValueError(
        f"unknown guard status {value!r} (expected OK/NOT-OK/CANNOT-ASSESS "
        "or PASS/FAIL/UNKNOWN)"
    )


def from_exit_code(exit_code: int) -> TriState:
    """Map a guard's exit code to its honest status (fail-closed).

    * 0        -> OK
    * 1        -> NOT-OK
    * 2        -> CANNOT-ASSESS (explicit)
    * 124      -> CANNOT-ASSESS (timed out -- nothing attested)
    * anything else -> CANNOT-ASSESS (a non-contract exit code means the
      guard is broken or was killed; it must never read as a pass)
    """
    if exit_code == EXIT_OK:
        return TriState.OK
    if exit_code == EXIT_NOT_OK:
        return TriState.NOT_OK
    return TriState.CANNOT_ASSESS


def to_exit_code(status: TriState) -> int:
    """The canonical exit code a guard should emit for a status."""
    if status is TriState.OK:
        return EXIT_OK
    if status is TriState.NOT_OK:
        return EXIT_NOT_OK
    return EXIT_CANNOT_ASSESS


def aggregate(verdicts: Iterable[TriState]) -> TriState:
    """Aggregate guard verdicts without ever letting UNKNOWN read as PASS.

    Rule: any NOT-OK fails the gate; otherwise any CANNOT-ASSESS makes the
    aggregate CANNOT-ASSESS; only an all-OK set aggregates to OK.
    """
    saw_unknown = False
    for verdict in verdicts:
        if verdict is TriState.NOT_OK:
            return TriState.NOT_OK
        if verdict is TriState.CANNOT_ASSESS:
            saw_unknown = True
    return TriState.CANNOT_ASSESS if saw_unknown else TriState.OK


def serialize(status: TriState) -> Dict[str, str]:
    """Stable JSON-ready serialization of a status."""
    return {"status": status.value}


def deserialize(payload: Dict[str, Any]) -> TriState:
    """Reverse of :func:`serialize`; unknown payloads raise, never pass."""
    try:
        return parse(str(payload["status"]))
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(f"payload has no 'status' key: {payload!r}") from exc


def to_json(status: TriState) -> str:
    """Serialize a status to a compact JSON document."""
    return json.dumps(serialize(status), sort_keys=True)


def from_json(document: str) -> TriState:
    """Deserialize a status from a JSON document produced by :func:`to_json`."""
    return deserialize(json.loads(document))


def parse_many(values: Iterable[str]) -> List[TriState]:
    """Parse a list of status spellings into TriState members."""
    return [parse(v) for v in values]
