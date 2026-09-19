"""The lane record's declaration, held to the code it describes (issue #1270).

``controls.yaml`` is the DECLARATION; ``lane_record.py`` is the machine. This
module is the mirror between them, and it refuses by name when they stop
agreeing:

    controls-mirror-drift:<field>   the declared field and the code's constant
                                    have stopped agreeing
    refusal-unknown:<id>            the declaration carries a refusal this module
                                    can never report
    refusal-undeclared:<id>         this module can report a refusal the
                                    declaration omits
    refusal-not-provoked:<id>       the declared ``provoked_by`` never names it on
                                    a non-comment line, so nothing can make it fire
    limit-file-missing:<id>         a declared bound names a file that is not there

That is the ``governance/vocabulary/fleet.yaml`` pattern, borrowed twice over:
declared once, mirrored in code, and a gate that names the drift -- so a lane
cannot mint a second vocabulary by editing one side, and a refusal cannot be
declared while nothing can make it fire (GR-29 / AO-GR-4).

A missing or malformed declaration is CANNOT-ASSESS, never a pass: a mirror with
nothing to mirror against reports agreement it never checked.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

import lane_record  # noqa: E402

DEFAULT_CONTROLS = _PKG_DIR / "controls.yaml"

DECLARATION_SCHEMA = "lane-record-controls/v1"

#: The declared key -> the module constant it must equal. The mirror is
#: exhaustive: every constant that decides what a record is has a row here, so a
#: field cannot be added to one side alone.
_MIRROR: Tuple[Tuple[str, str], ...] = (
    ("version", "SCHEMA_VERSION"),
    ("kinds", "KINDS"),
    ("record_required", "RECORD_REQUIRED"),
    ("record_optional", "RECORD_OPTIONAL"),
    ("brief_required", "BRIEF_REQUIRED"),
    ("result_required", "RESULT_REQUIRED"),
    ("records_subdir", "RECORDS_SUBDIR"),
)


class ControlsUnavailable(lane_record.CannotAssess):
    """The declaration is missing, not YAML, or names a shape this module cannot read."""


def load(path: Path | str | None = None) -> Mapping[str, Any]:
    """Read, parse and self-check the declaration. Never cached."""
    target = Path(path) if path else DEFAULT_CONTROLS
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ControlsUnavailable(
            "controls-unreadable:%s -- the declaration cannot be read: %s" % (target, exc)
        ) from exc
    try:
        import yaml
    except Exception as exc:  # pragma: no cover - PyYAML is a repository dependency
        raise ControlsUnavailable(
            "controls-unavailable -- PyYAML is not installed, so the declaration "
            "cannot be read at all: %s" % exc
        ) from exc
    try:
        document = yaml.safe_load(text)
    except Exception as exc:
        raise ControlsUnavailable(
            "controls-not-yaml:%s -- %s" % (target, exc)
        ) from exc
    if not isinstance(document, Mapping):
        raise ControlsUnavailable("controls-not-an-object:%s" % target)
    if document.get("schema") != DECLARATION_SCHEMA:
        raise ControlsUnavailable(
            "controls-schema:%s -- the declaration declares %r, expected %r"
            % (target, document.get("schema"), DECLARATION_SCHEMA)
        )
    return document


def _script_lines(path: Path) -> Tuple[str, ...] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return tuple(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def problems(declaration: Mapping[str, Any], root: Path | str | None = None) -> Tuple[lane_record.Finding, ...]:
    """Every place the declaration and the code have stopped agreeing."""
    base = Path(root) if root else lane_record.ROOT
    found: List[lane_record.Finding] = []
    Finding = lane_record.Finding

    for key, constant in _MIRROR:
        declared = declaration.get(key)
        actual = getattr(lane_record, constant)
        if key in ("kinds", "record_required", "record_optional", "brief_required", "result_required"):
            if list(declared or ()) != list(actual):
                found.append(
                    Finding(
                        "controls-mirror-drift",
                        key,
                        "the declaration says %r and %s says %r"
                        % (list(declared or ()), constant, list(actual)),
                    )
                )
        elif declared != actual:
            found.append(
                Finding(
                    "controls-mirror-drift",
                    key,
                    "the declaration says %r and %s says %r" % (declared, constant, actual),
                )
            )

    declared_refusals = declaration.get("refusals") or ()
    declared_ids = [str(entry.get("id") or "") for entry in declared_refusals
                    if isinstance(entry, Mapping)]
    known = set(lane_record.REFUSAL_CODES)

    for entry in declared_refusals:
        if not isinstance(entry, Mapping):
            found.append(
                Finding("controls-mirror-drift", "refusals", "a refusal row is not an object")
            )
            continue
        rid = str(entry.get("id") or "")
        if rid not in known:
            found.append(
                Finding(
                    "refusal-unknown",
                    rid,
                    "the declaration carries a refusal this module can never report",
                )
            )
            continue
        provoked_by = str(entry.get("provoked_by") or "")
        if not provoked_by:
            found.append(
                Finding("refusal-not-provoked", rid, "the declaration names no gate")
            )
            continue
        lines = _script_lines(base / provoked_by)
        if lines is None:
            found.append(
                Finding(
                    "refusal-not-provoked",
                    rid,
                    "the gate %s named by the declaration is not readable" % provoked_by,
                )
            )
            continue
        if not any(rid in line for line in lines):
            found.append(
                Finding(
                    "refusal-not-provoked",
                    rid,
                    "nothing on a non-comment line of %s can make this fire, so the "
                    "refusal is declared and unarmed" % provoked_by,
                )
            )

    for code in lane_record.REFUSAL_CODES:
        if code not in declared_ids:
            found.append(
                Finding(
                    "refusal-undeclared",
                    code,
                    "this module can report a refusal the declaration omits",
                )
            )

    for limit in declaration.get("limits") or ():
        if not isinstance(limit, Mapping):
            found.append(
                Finding("controls-mirror-drift", "limits", "a limit row is not an object")
            )
            continue
        lid = str(limit.get("id") or "")
        enforced_by = str(limit.get("enforced_by") or "")
        if not enforced_by or not (base / enforced_by).exists():
            found.append(
                Finding(
                    "limit-file-missing",
                    lid,
                    "the declared bound names %r, which is not a file here" % enforced_by,
                )
            )
    return tuple(found)


def describe(declaration: Mapping[str, Any]) -> Dict[str, Any]:
    """A stable, printable view: the declaration as the code reads it."""
    return {
        "declaration": DECLARATION_SCHEMA,
        "version": declaration.get("version"),
        "kinds": list(declaration.get("kinds") or ()),
        "records_subdir": declaration.get("records_subdir"),
        "limits": len(declaration.get("limits") or ()),
        "refusals": len(declaration.get("refusals") or ()),
    }
