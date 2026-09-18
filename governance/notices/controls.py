"""The notice rule's declared vocabulary, held to the code AND to its provocations.

Issue #1269. ``controls.yaml`` is the declaration; this module is the machine that
refuses it when it stops being true. Two questions are asked, and both must be
answerable offline:

  1. **Does the declaration still describe the code?** The selector, the status
     set, the ledger path and the ledger's event kinds are declared in
     ``controls.yaml`` and mirrored as constants in ``notice_records.py`` /
     ``ledger.py``. Drift either way is a finding, so a lane cannot mint a second
     vocabulary by editing one side (the ``governance/vocabulary/fleet.yaml``
     pattern this borrows).
  2. **Is every declared refusal ARMED?** Each ``refusals`` entry names the script
     that provokes it, and the id must appear on a NON-COMMENT line of that file.
     A refusal declared while nothing can make it fire is a formality (GR-12), and
     a refusal the code can emit but the declaration omits is a rule nobody can
     read. The check fails in both directions.

A declaration that cannot be read is CANNOT-ASSESS, never "no findings".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from governance.notices import ledger as notice_ledger
from governance.notices import notice_records
from governance.notices.finding import Finding

CONTROLS_RELPATH = "governance/notices/controls.yaml"
SCHEMA = "notice-controls/v1"

MIRROR_DRIFT = "controls-mirror-drift"
REFUSAL_UNKNOWN = "refusal-unknown"
REFUSAL_UNDECLARED = "refusal-undeclared"
REFUSAL_NOT_PROVOKED = "refusal-not-provoked"

#: This module's own refusals are part of the declared set too: a check whose
#: findings are exempt from its own declaration would be a rule nobody can read.
REFUSAL_CODES = (MIRROR_DRIFT, REFUSAL_UNKNOWN, REFUSAL_UNDECLARED, REFUSAL_NOT_PROVOKED)

#: A shell/python comment line. The provocation must be real code, not a comment
#: that mentions the refusal while nothing can make it fire.
COMMENT_LINE = re.compile(r"^\s*#")


class ControlsUnavailable(RuntimeError):
    """The declaration cannot be read (CANNOT-ASSESS, never "nothing to report")."""


@dataclass(frozen=True)
class Controls:
    """The declaration, as read."""

    path: str
    schema: str
    selector: str
    statuses: tuple[str, ...]
    ledger_path: str
    ledger_events: tuple[str, ...]
    refusals: tuple[Mapping[str, Any], ...]

    @property
    def refusal_ids(self) -> tuple[str, ...]:
        return tuple(str(entry.get("id") or "") for entry in self.refusals)


def load(path: Path | str = CONTROLS_RELPATH) -> Controls:
    """Read the declaration. Raises when it is unreadable or malformed."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - the gate guards this too
        raise ControlsUnavailable("PyYAML is not importable: %s" % exc) from exc
    document_path = Path(path)
    if not document_path.is_file():
        raise ControlsUnavailable("%s is missing" % document_path)
    try:
        document = yaml.safe_load(document_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ControlsUnavailable("%s cannot be read: %s" % (document_path, exc)) from exc
    if not isinstance(document, Mapping):
        raise ControlsUnavailable("%s is not a mapping" % document_path)
    ledger_block = document.get("ledger") or {}
    if not isinstance(ledger_block, Mapping):
        raise ControlsUnavailable("%s: ledger is not a mapping" % document_path)
    refusals = document.get("refusals")
    if not isinstance(refusals, list) or not refusals:
        raise ControlsUnavailable("%s declares no refusals" % document_path)
    return Controls(
        path=str(document_path),
        schema=str(document.get("schema") or ""),
        selector=str(document.get("selector") or ""),
        statuses=tuple(str(item) for item in document.get("statuses") or ()),
        ledger_path=str(ledger_block.get("path") or ""),
        ledger_events=tuple(str(item) for item in ledger_block.get("events") or ()),
        refusals=tuple(entry for entry in refusals if isinstance(entry, Mapping)),
    )


def _provoked_in(text: str, refusal_id: str) -> bool:
    """True when the id appears on a line that is not a comment."""
    for line in text.splitlines():
        if COMMENT_LINE.match(line):
            continue
        if refusal_id in line:
            return True
    return False


def _as_list(value: Any) -> list[str]:
    """A declared value as a list, so a scalar is never compared character by character."""
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def check(controls: Controls, root: Path | str = ".") -> list[Finding]:
    """Every reason the declaration does not hold, named. Empty means it holds."""
    root_path = Path(root)
    findings: list[Finding] = []

    if controls.schema != SCHEMA:
        findings.append(Finding(
            MIRROR_DRIFT, "schema",
            "declared '%s', the machine reads '%s'" % (controls.schema or "(absent)", SCHEMA),
        ))
    mirror = (
        ("selector", _as_list(controls.selector), _as_list(notice_records.REGISTERED_SELECTOR)),
        ("statuses", _as_list(controls.statuses), _as_list(notice_records.STATUSES)),
        ("ledger.path", _as_list(controls.ledger_path), _as_list(notice_ledger.LEDGER_RELPATH)),
        ("ledger.events", _as_list(controls.ledger_events), _as_list(notice_ledger.EVENT_KINDS)),
    )
    for name, declared, mirrored in mirror:
        if declared != mirrored:
            findings.append(Finding(
                MIRROR_DRIFT, name,
                "declared %s, the code mirrors %s" % (declared, mirrored),
            ))

    code_refusals = (
        set(notice_records.REFUSAL_CODES)
        | set(notice_ledger.REFUSAL_CODES)
        | set(REFUSAL_CODES)
    )
    declared_refusals = set(controls.refusal_ids)
    for refusal_id in sorted(code_refusals - declared_refusals):
        findings.append(Finding(
            REFUSAL_UNDECLARED, refusal_id,
            "the code can report it and %s does not declare it" % controls.path,
        ))
    for refusal_id in sorted(declared_refusals - code_refusals):
        findings.append(Finding(
            REFUSAL_UNKNOWN, refusal_id or "(blank)",
            "%s declares it and the code never reports it" % controls.path,
        ))

    for entry in controls.refusals:
        refusal_id = str(entry.get("id") or "")
        if not refusal_id:
            continue
        provoked_by = str(entry.get("provoked_by") or "")
        if not provoked_by:
            findings.append(Finding(
                REFUSAL_NOT_PROVOKED, refusal_id, "declares no provoked_by",
            ))
            continue
        target = root_path / provoked_by
        if not target.is_file():
            findings.append(Finding(
                REFUSAL_NOT_PROVOKED, refusal_id,
                "provoked_by names %s and no such file exists" % provoked_by,
            ))
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            findings.append(Finding(
                REFUSAL_NOT_PROVOKED, refusal_id,
                "provoked_by names %s and it cannot be read: %s" % (provoked_by, exc),
            ))
            continue
        if not _provoked_in(text, refusal_id):
            findings.append(Finding(
                REFUSAL_NOT_PROVOKED, refusal_id,
                "%s never names it outside a comment, so nothing can make it fire"
                % provoked_by,
            ))
    return findings
