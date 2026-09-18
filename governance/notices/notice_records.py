"""The notice record, the ack record, and the evaluator the gate drives.

THE RULE (issue #1269, EPIC #1268)
----------------------------------
A standing notice is a RULE between runtimes, and it is only a rule when every
registered runtime has acknowledged it. On 2026-09-18 the merge-path rules were
broadcast both ways -- SendMessage to the Claude sessions, a hand-built mailbox
directive to the DeepSeek sister -- and zero acks were recorded, because nothing
in the tree could tell "sent" from "received".

RECORDS
-------
Every record lives under the fleet's runtime state, which is where a transport
writes and is never committed:

    .fleet/notices/<id>/notice.json          the notice (schema notice/v1)
    .fleet/notices/<id>/pending/<runtime>.json   the fan-out copy for one runtime
    .fleet/notices/<id>/ack-<runtime>.json   that runtime's acknowledgement

The notice carries ``requires_ack: ["registered-runtimes"]`` -- a SELECTOR, never
a list of ids. A literal list is REFUSED by name (``hand-maintained-ack-list``),
because a list typed once is exactly how a notice stops covering the runtime
somebody registers next. The effective requirement is computed from
``runtime_registry`` on every read, so a new registration extends every standing
notice without an edit.

REFUSALS, BY NAME (each is provoked by ``scripts/check-notice-acks.sh``)
-----------------------------------------------------------------------
  notice-unacked:<id>:<runtime>            a registered runtime has not acked
  notice-not-fanned-out:<id>:<runtime>     no copy was ever addressed to it
  ack-from-unregistered-runtime:<id>:<r>   an ack that proves nothing (not registered)
  ack-without-evidence:<id>:<runtime>      an ack that carries no evidence
  ack-runtime-mismatch:<file>:<runtime>    the filename and the record disagree
  hand-maintained-ack-list:<id>:<detail>   requires_ack names ids, not the registry
  empty-runtime-registry:<detail>          nothing is registered, so nothing is owed
  transport-surface-missing:<id>:<path>    a runtime's transport is gone from the tree
  malformed-record:<path>:<detail>         a record the schema refuses

A refusal is a FINDING, not an exception: the evaluator reports every one it finds
so a single run names everything outstanding. ``RegistryUnavailable`` is the one
exception -- an input that cannot be read is CANNOT-ASSESS (rc 2), never an empty
registry that would make every notice trivially satisfied.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from governance.notices import ledger as notice_ledger
from governance.notices.finding import Finding
from governance.notices.runtime_registry import (
    RegistryUnavailable,
    Runtime,
    registered_runtimes,
    unregistered_profiles,
)

NOTICE_SCHEMA = "notice/v1"
ACK_SCHEMA = "notice-ack/v1"
FANOUT_SCHEMA = "notice-fanout/v1"
#: The only accepted value of ``requires_ack``: the set is derived, never listed.
REGISTERED_SELECTOR = "registered-runtimes"
STATUS_STANDING = "standing"
STATUS_WITHDRAWN = "withdrawn"
STATUSES = (STATUS_STANDING, STATUS_WITHDRAWN)

NOTICES_DIRNAME = "notices"
NOTICE_FILENAME = "notice.json"
PENDING_DIRNAME = "pending"
ACK_PREFIX = "ack-"
ACK_SUFFIX = ".json"

NOTICE_UNACKED = "notice-unacked"
NOTICE_NOT_FANNED_OUT = "notice-not-fanned-out"
ACK_FROM_UNREGISTERED = "ack-from-unregistered-runtime"
ACK_WITHOUT_EVIDENCE = "ack-without-evidence"
ACK_RUNTIME_MISMATCH = "ack-runtime-mismatch"
HAND_MAINTAINED_ACK_LIST = "hand-maintained-ack-list"
EMPTY_RUNTIME_REGISTRY = "empty-runtime-registry"
TRANSPORT_SURFACE_MISSING = "transport-surface-missing"
MALFORMED_RECORD = "malformed-record"

#: Every refusal this module can report, mirrored by governance/notices/controls.yaml
#: and held to it in BOTH directions by `cli.py controls`: a refusal the code can
#: emit while the declaration omits it is a rule nobody can read, and one the
#: declaration carries while nothing can fire it is a formality (GR-12).
REFUSAL_CODES = (
    NOTICE_UNACKED,
    NOTICE_NOT_FANNED_OUT,
    ACK_FROM_UNREGISTERED,
    ACK_WITHOUT_EVIDENCE,
    ACK_RUNTIME_MISMATCH,
    HAND_MAINTAINED_ACK_LIST,
    EMPTY_RUNTIME_REGISTRY,
    TRANSPORT_SURFACE_MISSING,
    MALFORMED_RECORD,
)

ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class NoticeError(RuntimeError):
    """A write was refused by name. ``code`` is the refusal, ``detail`` its subject."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__("%s:%s" % (code, detail))
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Notice:
    """A standing notice, as read from its record."""

    id: str
    subject: str
    body: str
    issued: str
    issued_by: str
    status: str
    requires_ack: tuple[str, ...]
    refs: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    """The evaluator's verdict: what it read, what it derived, what is outstanding."""

    root: str
    fleet: str
    runtimes: tuple[Runtime, ...] = ()
    roles: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    cannot_assess: str = ""

    def rc(self) -> int:
        if self.cannot_assess:
            return 2
        return 1 if self.findings else 0

    def summary(self) -> str:
        return "runtimes=%d roles=%d notices=%d" % (
            len(self.runtimes),
            len(self.roles),
            len(self.notices),
        )

    def lines(self) -> list[str]:
        """The report's own lines, in a stable order, for a human or a gate."""
        if self.cannot_assess:
            return ["notice-acks: CANNOT-ASSESS -- %s" % self.cannot_assess]
        out = ["notice-acks: %s" % self.summary()]
        for runtime in self.runtimes:
            out.append("  runtime %s (provider %s, transport %s)" % (
                runtime.id, runtime.provider, runtime.transport))
        for role in self.roles:
            out.append("  role %s (registered identity, no transport: owes no ack)" % role)
        for notice_id in self.notices:
            out.append("  notice %s" % notice_id)
        for finding in self.findings:
            out.append("  FAIL    %s" % finding.line())
        if self.findings:
            out.append("notice-acks: FAIL (%d violation(s))" % len(self.findings))
        else:
            out.append(
                "notice-acks: OK -- every registered runtime acked every standing notice"
            )
        return out


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def notices_dir(fleet_dir: Path | str) -> Path:
    """The runtime-state directory notices and acks live under."""
    return Path(fleet_dir) / NOTICES_DIRNAME


def notice_dir(fleet_dir: Path | str, notice_id: str) -> Path:
    return notices_dir(fleet_dir) / notice_id


def notice_path(fleet_dir: Path | str, notice_id: str) -> Path:
    return notice_dir(fleet_dir, notice_id) / NOTICE_FILENAME


def pending_path(fleet_dir: Path | str, notice_id: str, runtime_id: str) -> Path:
    return notice_dir(fleet_dir, notice_id) / PENDING_DIRNAME / ("%s.json" % runtime_id)


def ack_path(fleet_dir: Path | str, notice_id: str, runtime_id: str) -> Path:
    return notice_dir(fleet_dir, notice_id) / ("%s%s%s" % (ACK_PREFIX, runtime_id, ACK_SUFFIX))


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


# -- validation ---------------------------------------------------------------


def _field(document: Mapping[str, Any], name: str) -> str:
    value = document.get(name)
    return value.strip() if isinstance(value, str) else ""


def validate_notice(document: Any, notice_id: str) -> list[Finding]:
    """Every reason this notice record is refused, named. Empty means it holds."""
    if not isinstance(document, Mapping):
        return [Finding(MALFORMED_RECORD, "%s:%s" % (notice_id, NOTICE_FILENAME),
                        "the record is not an object")]
    findings: list[Finding] = []

    schema = _field(document, "schema")
    if schema != NOTICE_SCHEMA:
        findings.append(Finding(
            MALFORMED_RECORD, notice_id,
            "unknown notice schema '%s' (expected %s)" % (schema or "(absent)", NOTICE_SCHEMA),
        ))
    declared_id = _field(document, "id")
    if not ID_RE.match(declared_id or ""):
        findings.append(Finding(
            MALFORMED_RECORD, notice_id,
            "record id '%s' is not a notice id" % (declared_id or "(absent)"),
        ))
    elif declared_id != notice_id:
        findings.append(Finding(
            MALFORMED_RECORD, notice_id,
            "record id '%s' does not match its directory" % declared_id,
        ))
    if not _field(document, "subject").strip():
        findings.append(Finding(MALFORMED_RECORD, notice_id, "no subject"))
    issued = _field(document, "issued")
    if not TIMESTAMP_RE.match(issued or ""):
        findings.append(Finding(
            MALFORMED_RECORD, notice_id,
            "issued '%s' is not <YYYY-MM-DDTHH:MM:SSZ>" % (issued or "(absent)"),
        ))
    status = _field(document, "status")
    if status not in STATUSES:
        findings.append(Finding(
            MALFORMED_RECORD, notice_id,
            "status '%s' is outside %s" % (status or "(absent)", "|".join(STATUSES)),
        ))

    requires = document.get("requires_ack")
    if not isinstance(requires, list) or not requires:
        findings.append(Finding(
            HAND_MAINTAINED_ACK_LIST, notice_id,
            "requires_ack is absent or empty (the requirement is the registry)",
        ))
    elif tuple(str(item) for item in requires) != (REGISTERED_SELECTOR,):
        findings.append(Finding(
            HAND_MAINTAINED_ACK_LIST, notice_id,
            "requires_ack names %s -- it must be the selector '%s', computed from "
            "the registry on every read, never a list typed once"
            % (sorted(str(item) for item in requires), REGISTERED_SELECTOR),
        ))
    return findings


def validate_ack(document: Any, notice_id: str, runtime_id: str) -> list[Finding]:
    """Every reason this ack record is refused, named. Empty means it holds."""
    label = "%s:%s%s%s" % (notice_id, ACK_PREFIX, runtime_id, ACK_SUFFIX)
    if not isinstance(document, Mapping):
        return [Finding(MALFORMED_RECORD, label, "the record is not an object")]
    findings: list[Finding] = []
    if _field(document, "schema") != ACK_SCHEMA:
        findings.append(Finding(
            MALFORMED_RECORD, label, "no %s schema" % ACK_SCHEMA,
        ))
    declared_notice = _field(document, "notice")
    if declared_notice != notice_id:
        findings.append(Finding(
            MALFORMED_RECORD, label,
            "names notice '%s'" % (declared_notice or "(absent)"),
        ))
    declared_runtime = _field(document, "runtime")
    if declared_runtime != runtime_id:
        findings.append(Finding(
            ACK_RUNTIME_MISMATCH, "%s:%s" % (notice_id, declared_runtime or "(absent)"),
            "the record claims runtime '%s' but sits in %s"
            % (declared_runtime or "(absent)", "%s%s%s" % (ACK_PREFIX, runtime_id, ACK_SUFFIX)),
        ))
    if not TIMESTAMP_RE.match(_field(document, "acked_at") or ""):
        findings.append(Finding(
            MALFORMED_RECORD, label, "no <YYYY-MM-DDTHH:MM:SSZ> acked_at",
        ))
    if not _field(document, "evidence").strip():
        findings.append(Finding(
            ACK_WITHOUT_EVIDENCE, "%s:%s" % (notice_id, runtime_id),
            "the ack carries no evidence of receipt",
        ))
    return findings


# -- writing ------------------------------------------------------------------


def publish(
    root: Path | str,
    fleet_dir: Path | str,
    *,
    notice_id: str,
    subject: str,
    body: str,
    issued_by: str = "director",
    status: str = STATUS_STANDING,
    refs: Sequence[str] = (),
    issued: str | None = None,
) -> Path:
    """Write one standing notice and fan a copy out to every registered runtime.

    Refused by name (``NoticeError``) when the record is malformed, when the id is
    already published (a notice is immutable: a correction is a new id), or when
    nothing is registered -- a notice fanned out to nobody would be a record that
    looks like a rule and covers nothing.
    """
    runtimes = registered_runtimes(root)
    if not runtimes:
        raise NoticeError(
            EMPTY_RUNTIME_REGISTRY,
            "refusing to publish '%s': no live AgentPack registers an identity the "
            "gateway carries a transport for, so no runtime could ack it" % notice_id,
        )
    if not ID_RE.match(notice_id or ""):
        raise NoticeError(MALFORMED_RECORD, "%s:id '%s' is not a notice id" % (notice_id, notice_id))
    record = {
        "schema": NOTICE_SCHEMA,
        "id": notice_id,
        "subject": subject,
        "body": body,
        "issued": issued or utc_now(),
        "issued_by": issued_by,
        "status": status,
        "requires_ack": [REGISTERED_SELECTOR],
        "refs": [str(ref) for ref in refs],
    }
    findings = validate_notice(record, notice_id)
    if findings:
        first = findings[0]
        raise NoticeError(first.code, first.detail)
    path = notice_path(fleet_dir, notice_id)
    if path.exists():
        raise NoticeError(
            MALFORMED_RECORD,
            "%s:already published (a notice is immutable -- issue a new id that "
            "supersedes it)" % notice_id,
        )
    _write_json(path, record)
    notice_ledger.append(
        fleet_dir,
        event="publish",
        notice=notice_id,
        record=str(path.relative_to(Path(fleet_dir))),
        digest=notice_ledger.file_digest(path),
        at=record["issued"],
    )
    if status == STATUS_STANDING:
        for runtime in runtimes:
            _write_json(
                pending_path(fleet_dir, notice_id, runtime.id),
                {
                    "schema": FANOUT_SCHEMA,
                    "notice": notice_id,
                    "runtime": runtime.id,
                    "transport": runtime.transport,
                    "fanout": runtime.fanout,
                    "addressed_at": record["issued"],
                    "owed": "ack-%s.json" % runtime.id,
                },
            )
    return path


def acknowledge(
    root: Path | str,
    fleet_dir: Path | str,
    *,
    notice_id: str,
    runtime_id: str,
    evidence: str,
    transport: str | None = None,
    acked_at: str | None = None,
) -> Path:
    """Record one runtime's acknowledgement of one notice.

    Refused by name when the runtime is not registered (an ack from a runtime the
    fleet does not hold proves nothing), when the notice is not published, when the
    ack carries no evidence, or when that runtime has already acked (an ack is a
    record, not a counter).
    """
    runtimes = {runtime.id: runtime for runtime in registered_runtimes(root)}
    if runtime_id not in runtimes:
        raise NoticeError(
            ACK_FROM_UNREGISTERED,
            "%s:%s is not a registered runtime, so its ack proves nothing" % (notice_id, runtime_id),
        )
    if not notice_path(fleet_dir, notice_id).is_file():
        raise NoticeError(
            MALFORMED_RECORD,
            "%s:no such notice under %s" % (notice_id, notices_dir(fleet_dir)),
        )
    if not str(evidence or "").strip():
        raise NoticeError(
            ACK_WITHOUT_EVIDENCE,
            "%s:%s:refusing an ack with no evidence of receipt" % (notice_id, runtime_id),
        )
    path = ack_path(fleet_dir, notice_id, runtime_id)
    if path.exists():
        raise NoticeError(
            MALFORMED_RECORD,
            "%s:%s:already acked (an ack is a record, not a counter -- a re-ack is a "
            "new notice id)" % (notice_id, runtime_id),
        )
    stamp = acked_at or utc_now()
    _write_json(
        path,
        {
            "schema": ACK_SCHEMA,
            "notice": notice_id,
            "runtime": runtime_id,
            "acked_at": stamp,
            "transport": transport or runtimes[runtime_id].transport,
            "evidence": evidence,
        },
    )
    notice_ledger.append(
        fleet_dir,
        event="ack",
        notice=notice_id,
        runtime=runtime_id,
        record=str(path.relative_to(Path(fleet_dir))),
        digest=notice_ledger.file_digest(path),
        at=stamp,
    )
    return path


# -- evaluation ---------------------------------------------------------------


def _read_notices(fleet_dir: Path | str) -> tuple[list[Notice], list[Finding], str, list[Path]]:
    """Read every notice record. Returns (notices, findings, cannot_assess, entries)."""
    directory = notices_dir(fleet_dir)
    if not directory.exists():
        return [], [], "", []
    if not directory.is_dir():
        return [], [], "%s is not a directory" % directory, []
    notices: list[Notice] = []
    findings: list[Finding] = []
    try:
        entries = sorted(entry for entry in directory.iterdir() if entry.is_dir())
    except OSError as exc:
        return [], [], "%s cannot be listed: %s" % (directory, exc), []
    for entry in entries:
        record = entry / NOTICE_FILENAME
        if not record.is_file():
            findings.append(Finding(
                MALFORMED_RECORD, "%s:%s" % (entry.name, NOTICE_FILENAME),
                "the notice record is missing",
            ))
            continue
        try:
            document = _read_json(record)
        except (OSError, ValueError) as exc:
            findings.append(Finding(
                MALFORMED_RECORD, "%s:%s" % (entry.name, NOTICE_FILENAME),
                "cannot be read: %s" % exc,
            ))
            continue
        refused = validate_notice(document, entry.name)
        if refused:
            findings.extend(refused)
            continue
        notices.append(
            Notice(
                id=entry.name,
                subject=_field(document, "subject"),
                body=_field(document, "body"),
                issued=_field(document, "issued"),
                issued_by=_field(document, "issued_by"),
                status=_field(document, "status"),
                requires_ack=tuple(str(item) for item in document.get("requires_ack") or ()),
                refs=tuple(str(item) for item in document.get("refs") or ()),
            )
        )
    return notices, findings, "", entries


def evaluate(root: Path | str, fleet_dir: Path | str) -> Report:
    """Evaluate the tree WITHOUT writing anything: what is owed, and what is outstanding."""
    try:
        runtimes = registered_runtimes(root)
        roles = unregistered_profiles(root)
    except RegistryUnavailable as exc:
        return Report(root=str(root), fleet=str(fleet_dir), cannot_assess=str(exc))

    findings: list[Finding] = []
    if not runtimes:
        findings.append(Finding(
            EMPTY_RUNTIME_REGISTRY, "(none)",
            "no live AgentPack registers an identity the gateway carries a transport "
            "for -- nothing is owed, so the rule would pass by matching nothing",
        ))
    for runtime in runtimes:
        if not (Path(root) / runtime.fanout).is_file():
            findings.append(Finding(
                TRANSPORT_SURFACE_MISSING, "%s:%s" % (runtime.id, runtime.fanout),
                "is declared as this runtime's transport but is not in the tree",
            ))

    notices, read_findings, cannot_assess, entries = _read_notices(fleet_dir)
    findings.extend(read_findings)
    if cannot_assess:
        return Report(root=str(root), fleet=str(fleet_dir), runtimes=runtimes,
                      roles=roles, cannot_assess=cannot_assess)

    registered = {runtime.id for runtime in runtimes}
    by_id = {entry.name: entry for entry in entries}
    for notice in notices:
        entry = by_id.get(notice.id)
        if entry is None:  # pragma: no cover - _read_notices builds both from one pass
            continue
        for ack_file in sorted(entry.glob("%s*%s" % (ACK_PREFIX, ACK_SUFFIX))):
            stem = ack_file.name[len(ACK_PREFIX):-len(ACK_SUFFIX)]
            if stem not in registered:
                findings.append(Finding(
                    ACK_FROM_UNREGISTERED, "%s:%s" % (notice.id, stem),
                    "is not a registered runtime, so its ack proves nothing",
                ))
        if notice.status == STATUS_WITHDRAWN:
            continue
        for runtime in runtimes:
            if not pending_path(fleet_dir, notice.id, runtime.id).is_file():
                findings.append(Finding(
                    NOTICE_NOT_FANNED_OUT, "%s:%s" % (notice.id, runtime.id),
                    "no copy was addressed to this runtime (registered after the "
                    "notice was issued?)",
                ))
            ack = ack_path(fleet_dir, notice.id, runtime.id)
            if not ack.is_file():
                findings.append(Finding(
                    NOTICE_UNACKED, "%s:%s" % (notice.id, runtime.id),
                    "has not acked this standing notice",
                ))
                continue
            try:
                document = _read_json(ack)
            except (OSError, ValueError) as exc:
                findings.append(Finding(
                    MALFORMED_RECORD, "%s:%s%s%s" % (notice.id, ACK_PREFIX, runtime.id, ACK_SUFFIX),
                    "cannot be read: %s" % exc,
                ))
                continue
            findings.extend(validate_ack(document, notice.id, runtime.id))
    return Report(
        root=str(root),
        fleet=str(fleet_dir),
        runtimes=runtimes,
        roles=roles,
        notices=tuple(notice.id for notice in notices),
        findings=tuple(findings),
    )
