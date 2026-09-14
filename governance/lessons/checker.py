"""Detection logic for RCA + lessons enforcement (issue #141).

``load_ledger`` reads the canonical ledger; ``check_ledger`` decides whether the
records it holds are complete, traceable and evidenced. Every rule below fails
with a named finding and an actionable remediation — the point of the issue is
that a gap is *not* silently ignored.

Scope, stated honestly (the same posture as ``governance/conformance``):

* **Errors** are the records a lane controls: a malformed ledger, an incident
  with no analysis, an RCA with no corrective action, an action that is not
  recorded, a lesson with no commit evidence, an action with no owner. These
  fail the gate.
* **Deviations** are the historical backlog and the open work the process is
  still draining (an incident-labelled issue with no RCA yet, an action that is
  open, a review past its cadence). They are reported with the remediation that
  carries them, counted in the report, and escalated to errors by ``--strict``.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from model import (
    CODE_BOARD_INCIDENT_EXEMPT,
    CODE_BOARD_INCIDENT_PENDING,
    CODE_BOARD_INCIDENT_WITHOUT_RCA,
    CODE_CORRECTIVE_ACTION_OPEN,
    CODE_CORRECTIVE_ACTION_UNLINKED,
    CODE_CORRECTIVE_ACTION_UNRECORDED,
    CODE_CORRECTIVE_ACTION_WITHOUT_EVIDENCE,
    CODE_CORRECTIVE_ACTION_WITHOUT_OWNER,
    CODE_DUPLICATE_ID,
    CODE_EDGE_UNRESOLVED,
    CODE_EVIDENCE_UNRESOLVABLE,
    CODE_INCIDENT_CLOSED_WITHOUT_LESSON,
    CODE_INCIDENT_WITHOUT_RCA,
    CODE_LEDGER_EMPTY,
    CODE_LEDGER_INVALID,
    CODE_LESSON_INVALID_STATUS,
    CODE_LESSON_WITHOUT_COMMIT_EVIDENCE,
    CODE_LESSON_WITHOUT_EVIDENCE,
    CODE_ORIGIN_UNRESOLVED,
    CODE_RCA_ARTIFACT_INCOMPLETE,
    CODE_RCA_ARTIFACT_MISSING,
    CODE_RCA_ARTIFACT_UNTRACKED,
    CODE_RCA_REVIEW_OVERDUE,
    CODE_RCA_WITHOUT_CORRECTIVE_ACTION,
    CODE_RCA_WITHOUT_ORIGIN,
    CODE_SUGGESTION_OPEN,
    CODE_SUGGESTION_WITHOUT_OWNER,
    CODE_SUGGESTION_WITHOUT_REMEDIATION,
    CODE_UNKNOWN_REFERENCE,
    KIND_CORRECTIVE_ACTION,
    KIND_INCIDENT,
    KIND_LESSON,
    KIND_RCA,
    RCA_REQUIRED_SECTIONS,
    REVIEW_CADENCE_DAYS,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    STATUS_CLOSED,
    STATUS_OPEN,
    Entry,
    Finding,
    Report,
    as_list,
    days_since,
    is_lesson,
    is_suggestion,
    now_iso,
    validate_record,
)

# The typed-edge layer (issue #402) is the single place a lessons cross-record
# reference becomes a ticket node id, so no reader re-parses ``origin`` or
# ``remediation_issue`` for itself.
import edges as _edges  # noqa: E402

LEDGER_RELPATH = "governance/lessons/ledger.jsonl"
TEMPLATE_RELPATH = "governance/lessons/rca-template.md"
POLICY_RELPATH = "governance/lessons/policy.yaml"
REPORT_RELPATH = ".verify/lessons-report.json"
SNAPSHOT_RELPATH = ".board/snapshot.json"

#: The board label that marks an issue as an incident or policy failure.
INCIDENT_LABEL = "area:incident-response"

RE_ISSUE_REF = re.compile(r"^#(\d+)$")
RE_SHA = re.compile(r"^[0-9a-f]{7,40}$")


class LedgerUnavailable(Exception):
    """The canonical ledger is absent — the gate cannot assess anything."""


class PolicyUnavailable(Exception):
    """The enforcement policy is present but unusable."""


@dataclass(frozen=True)
class Policy:
    """What counts as an incident-labelled issue, and what is exempt from it.

    ``exemptions`` maps an issue ref to the reason it is exempt. An exemption
    is not a silent bypass: the gate still reports it, with the reason, every
    time it runs.
    """

    incident_label: str = INCIDENT_LABEL
    review_cadence_days: int = REVIEW_CADENCE_DAYS
    exemptions: Mapping[str, str] = field(default_factory=dict)


def default_policy() -> Policy:
    return Policy()


def load_policy(path: Path) -> Policy:
    """Load ``policy.yaml``; a malformed policy is a hard finding, not a pass."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyUnavailable("cannot read %s (%s)" % (path, exc)) from exc
    try:
        import yaml  # noqa: PLC0415 - optional dependency, resolved on demand
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PolicyUnavailable("PyYAML is not installed: %s" % exc) from exc
    try:
        payload = yaml.safe_load(raw)
    except Exception as exc:  # yaml.YAMLError and friends
        raise PolicyUnavailable("%s is not valid YAML (%s)" % (path, exc)) from exc
    if not isinstance(payload, dict):
        raise PolicyUnavailable("%s does not hold a policy object" % path)

    board = payload.get("board") or {}
    if not isinstance(board, dict):
        raise PolicyUnavailable("%s: board must be an object" % path)
    exemptions: Dict[str, str] = {}
    for entry in board.get("exemptions") or []:
        if not isinstance(entry, dict) or not str(entry.get("ref", "")).strip():
            raise PolicyUnavailable(
                "%s: every exemption needs a ref and a reason" % path
            )
        reason = str(entry.get("reason", "")).strip()
        if not reason:
            raise PolicyUnavailable(
                "%s: exemption %s carries no reason" % (path, entry.get("ref"))
            )
        exemptions[str(entry["ref"]).strip()] = reason

    cadence = payload.get("review_cadence_days", REVIEW_CADENCE_DAYS)
    if not isinstance(cadence, int) or isinstance(cadence, bool) or cadence <= 0:
        raise PolicyUnavailable("%s: review_cadence_days must be a positive integer" % path)

    label = str(board.get("incident_label", INCIDENT_LABEL)).strip()
    if not label:
        raise PolicyUnavailable("%s: board.incident_label is empty" % path)

    return Policy(
        incident_label=label,
        review_cadence_days=cadence,
        exemptions=exemptions,
    )


def relpath(value: Any) -> str:
    """Normalise a path a record declares to a repository-relative POSIX path.

    Only a leading ``./`` is stripped: a dot-directory such as ``.board/`` is
    part of the path, not decoration.
    """
    path = str(value).replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


class Ledger:
    """Parsed ledger: entries in file order, plus the index of usable records."""

    def __init__(self, path: Path, entries: Sequence[Entry], findings: Sequence[Finding]):
        self.path = path
        self.entries = list(entries)
        self.findings = list(findings)
        self.records: Dict[str, Dict[str, Any]] = {}
        self.by_kind: Dict[str, List[Dict[str, Any]]] = {
            kind: [] for kind in (KIND_INCIDENT, KIND_RCA, KIND_CORRECTIVE_ACTION, KIND_LESSON)
        }
        self.duplicate_ids: List[Tuple[str, int]] = []
        index_findings: List[Finding] = []
        for entry in self.entries:
            record = entry.record
            if record is None:
                continue
            record_id = str(record.get("id", ""))
            if not record_id:
                continue
            if record_id in self.records:
                self.duplicate_ids.append((record_id, entry.line))
                index_findings.append(
                    Finding(
                        code=CODE_DUPLICATE_ID,
                        message="record id %s is declared more than once" % record_id,
                        subject=record_id,
                        severity=SEVERITY_ERROR,
                        line=entry.line,
                        remediation="keep one authoritative line per id",
                    )
                )
                continue
            self.records[record_id] = record
            if isinstance(record.get("kind"), str) and record["kind"] in self.by_kind:
                self.by_kind[record["kind"]].append(record)
        self.findings.extend(index_findings)

    def of_kind(self, kind: str) -> List[Dict[str, Any]]:
        return self.by_kind.get(kind, [])


def parse_ledger_text(text: str, path: Path) -> Ledger:
    """Parse JSONL text. A malformed line is a hard finding, never a skip."""
    entries: List[Entry] = []
    findings: List[Finding] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            findings.append(
                Finding(
                    code=CODE_LEDGER_INVALID,
                    message="ledger line does not parse as JSON (%s)" % exc,
                    subject="%s:%d" % (path, number),
                    severity=SEVERITY_ERROR,
                    line=number,
                    remediation="repair the line; the ledger is the authoritative record",
                )
            )
            continue
        if not isinstance(payload, dict):
            findings.append(
                Finding(
                    code=CODE_LEDGER_INVALID,
                    message="ledger line is not a JSON object",
                    subject="%s:%d" % (path, number),
                    severity=SEVERITY_ERROR,
                    line=number,
                    remediation="one record object per line",
                )
            )
            continue
        entry = Entry(line=number, raw=raw, record=payload)
        entries.append(entry)
        findings.extend(validate_record(entry))
    return Ledger(path, entries, findings)


def load_ledger(path: Path) -> Ledger:
    """Read the canonical ledger from disk."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LedgerUnavailable("cannot read %s (%s)" % (path, exc)) from exc
    return parse_ledger_text(text, path)


def load_snapshot(path: Path) -> Dict[int, Dict[str, Any]]:
    """Load the committed board snapshot as ``{issue number: issue}``."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    issues = payload.get("issues") or []
    return {int(issue["number"]): issue for issue in issues if "number" in issue}


class GitProbe:
    """The two git facts the gate needs: is that file tracked, does that SHA exist."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.available = self._run("rev-parse", "--git-dir")[0] == 0

    def _run(self, *args: str) -> Tuple[int, str]:
        try:
            done = subprocess.run(
                ["git", "-C", str(self.root), *args],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return (-1, "")
        return (done.returncode, done.stdout)

    @property
    def shallow(self) -> bool:
        if not self.available:
            return False
        code, out = self._run("rev-parse", "--is-shallow-repository")
        return code == 0 and out.strip() == "true"

    def tracked(self, relative_path: str) -> Optional[bool]:
        """``True``/``False`` when git can answer, ``None`` when it cannot."""
        if not self.available:
            return None
        return self._run("ls-files", "--error-unmatch", relative_path)[0] == 0

    def commit_exists(self, sha: str) -> Optional[bool]:
        if not self.available:
            return None
        return self._run("cat-file", "-e", "%s^{commit}" % sha)[0] == 0


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _origin_ref(record: Dict[str, Any]) -> str:
    origin = record.get("origin")
    if not isinstance(origin, dict):
        return ""
    return str(origin.get("ref", "")).strip()


def _origin_kind(record: Dict[str, Any]) -> str:
    origin = record.get("origin")
    if not isinstance(origin, dict):
        return ""
    return str(origin.get("kind", "")).strip()


def check_ledger(
    ledger: Ledger,
    *,
    root: Path,
    snapshot: Optional[Dict[int, Dict[str, Any]]] = None,
    policy: Optional[Policy] = None,
    today: Optional[date] = None,
    strict: bool = False,
    git: Optional[GitProbe] = None,
    generated_at: Optional[str] = None,
) -> Report:
    """Run every enforcement rule and return the report."""
    root = Path(root)
    today = today or date.today()
    active_policy = policy if policy is not None else default_policy()
    probe = git if git is not None else GitProbe(root)
    findings: List[Finding] = list(ledger.findings)

    incidents = ledger.of_kind(KIND_INCIDENT)
    rcas = ledger.of_kind(KIND_RCA)
    actions = ledger.of_kind(KIND_CORRECTIVE_ACTION)
    lessons = ledger.of_kind(KIND_LESSON)

    if not ledger.entries:
        findings.append(
            Finding(
                code=CODE_LEDGER_EMPTY,
                message="the canonical ledger holds no records",
                subject=LEDGER_RELPATH,
                severity=SEVERITY_ERROR,
                remediation="record the incidents and lessons the process is meant to capture",
            )
        )

    findings.extend(_check_edges(ledger.records))
    findings.extend(_check_references(ledger, rcas, actions, lessons))
    findings.extend(_check_rca_artifacts(rcas, root=root, probe=probe))
    findings.extend(_check_origins(rcas, snapshot=snapshot, probe=probe))
    findings.extend(_check_incident_coverage(incidents, rcas, lessons))
    findings.extend(_check_lessons(lessons, root=root, probe=probe))
    findings.extend(_check_actions(actions, rcas, root=root, probe=probe))
    findings.extend(_check_review_cadence(rcas, today=today, policy=active_policy))
    if snapshot is not None:
        findings.extend(_check_board(incidents, snapshot, policy=active_policy))

    counts = {
        "incidents": len(incidents),
        "incidents_open": sum(1 for r in incidents if r.get("status") == STATUS_OPEN),
        "incidents_closed": sum(1 for r in incidents if r.get("status") == STATUS_CLOSED),
        "rcas": len(rcas),
        "corrective_actions": len(actions),
        "corrective_actions_open": sum(
            1 for r in actions if r.get("status") == STATUS_OPEN
        ),
        "lessons": sum(1 for r in lessons if is_lesson(r.get("id"))),
        "suggestions": sum(1 for r in lessons if is_suggestion(r.get("id"))),
        "board_incidents_scanned": (
            0
            if snapshot is None
            else sum(
                1
                for issue in snapshot.values()
                if active_policy.incident_label in (issue.get("labels") or [])
            )
        ),
        "board_incidents_exempt": (
            0
            if snapshot is None
            else sum(
                1
                for issue in snapshot.values()
                if active_policy.incident_label in (issue.get("labels") or [])
                and "#%d" % issue.get("number", 0) in active_policy.exemptions
            )
        ),
        "artifacts_checked": len(rcas),
    }

    if strict:
        findings = [
            Finding(
                code=f.code,
                message=f.message,
                subject=f.subject,
                severity=SEVERITY_ERROR,
                remediation=f.remediation,
                line=f.line,
            )
            for f in findings
        ]

    return Report(
        ledger=str(ledger.path),
        generated_at=generated_at or now_iso(),
        counts=counts,
        findings=findings,
    )


def _check_edges(records: Mapping[str, Dict[str, Any]]) -> List[Finding]:
    """Every cross-record reference must be typable as a ticket edge (#402).

    A reference that cannot be typed is refused rather than passed through as
    free text, so ``origin`` / ``remediation_issue`` resolve in exactly one
    place (``governance/lessons/edges.py``).
    """
    return [
        Finding(
            code=CODE_EDGE_UNRESOLVED,
            message=message,
            subject=message.split(":", 1)[0],
            severity=SEVERITY_ERROR,
            remediation="type the reference as a ticket target (#<n> for an issue)",
        )
        for message in _edges.findings(records.values())
    ]


def _check_references(ledger, rcas, actions, lessons) -> List[Finding]:
    """Every cross-record reference must resolve (AC3, AC4)."""
    findings: List[Finding] = []
    known = set(ledger.records)

    for rca in rcas:
        incident = str(rca.get("incident", ""))
        if incident and incident not in known:
            findings.append(
                Finding(
                    code=CODE_UNKNOWN_REFERENCE,
                    message="%s analyzes incident %s, which is not recorded"
                    % (rca.get("id"), incident),
                    subject=str(rca.get("id")),
                    remediation="record the incident, or point the RCA at the one it analyzes",
                )
            )
        for action in as_list(rca.get("corrective_actions")):
            action_id = str(action)
            if action_id and action_id not in known:
                findings.append(
                    Finding(
                        code=CODE_CORRECTIVE_ACTION_UNRECORDED,
                        message="%s requires corrective action %s, which is not in the ledger"
                        % (rca.get("id"), action_id),
                        subject=action_id,
                        remediation="add the corrective-action record (AC4: every action is recorded)",
                    )
                )

    for action in actions:
        owner = str(action.get("rca", ""))
        if owner and owner not in known:
            findings.append(
                Finding(
                    code=CODE_UNKNOWN_REFERENCE,
                    message="%s names RCA %s, which is not recorded"
                    % (action.get("id"), owner),
                    subject=str(action.get("id")),
                    remediation="point the action at the RCA that produced it",
                )
            )

    for lesson in lessons:
        owner = str(lesson.get("rca", ""))
        if owner and owner not in known:
            findings.append(
                Finding(
                    code=CODE_UNKNOWN_REFERENCE,
                    message="%s cites RCA %s, which is not recorded"
                    % (lesson.get("id"), owner),
                    subject=str(lesson.get("id")),
                    remediation="link the lesson to the RCA that produced it",
                )
            )
    return findings


def _check_rca_artifacts(rcas, *, root: Path, probe: GitProbe) -> List[Finding]:
    """Every RCA names a committed artifact that carries the template sections."""
    findings: List[Finding] = []
    for rca in rcas:
        rca_id = str(rca.get("id"))
        artifact = relpath(rca.get("artifact", ""))
        if not artifact:
            continue
        path = root / artifact
        text = _read_text(path)
        if text is None:
            findings.append(
                Finding(
                    code=CODE_RCA_ARTIFACT_MISSING,
                    message="%s points at %s, which does not exist" % (rca_id, artifact),
                    subject=rca_id,
                    remediation="write the RCA artifact (copy governance/lessons/rca-template.md)",
                )
            )
            continue
        if probe.tracked(artifact) is False:
            findings.append(
                Finding(
                    code=CODE_RCA_ARTIFACT_UNTRACKED,
                    message="%s points at %s, which is not committed" % (rca_id, artifact),
                    subject=rca_id,
                    remediation="commit the artifact (an uncommitted RCA does not exist)",
                )
            )
        missing = [s for s in RCA_REQUIRED_SECTIONS if s not in text]
        if missing:
            findings.append(
                Finding(
                    code=CODE_RCA_ARTIFACT_INCOMPLETE,
                    message="%s: %s omits required section(s): %s"
                    % (rca_id, artifact, ", ".join(missing)),
                    subject=rca_id,
                    remediation="add the missing section(s) from the RCA template",
                )
            )
    return findings


def _check_origins(rcas, *, snapshot, probe: GitProbe) -> List[Finding]:
    """Every RCA traces to the originating issue or event (AC3)."""
    findings: List[Finding] = []
    for rca in rcas:
        rca_id = str(rca.get("id"))
        ref = _origin_ref(rca)
        kind = _origin_kind(rca)
        if not ref:
            findings.append(
                Finding(
                    code=CODE_RCA_WITHOUT_ORIGIN,
                    message="%s names no originating issue or event" % rca_id,
                    subject=rca_id,
                    remediation='set origin to {"kind": "issue", "ref": "#<n>"}',
                )
            )
            continue
        if kind == "issue":
            match = RE_ISSUE_REF.match(ref)
            if match is None:
                findings.append(
                    Finding(
                        code=CODE_ORIGIN_UNRESOLVED,
                        message="%s origin %r is not an issue reference" % (rca_id, ref),
                        subject=rca_id,
                        remediation="write the issue as #<number>",
                    )
                )
                continue
            if snapshot is not None and int(match.group(1)) not in snapshot:
                findings.append(
                    Finding(
                        code=CODE_ORIGIN_UNRESOLVED,
                        message="%s origin %s is not on the committed board snapshot"
                        % (rca_id, ref),
                        subject=rca_id,
                        remediation="refresh .board/snapshot.json, or fix the reference",
                    )
                )
        elif kind == "pr" and RE_ISSUE_REF.match(ref) is None:
            findings.append(
                Finding(
                    code=CODE_ORIGIN_UNRESOLVED,
                    message="%s origin %r is not a pull-request reference" % (rca_id, ref),
                    subject=rca_id,
                    remediation="write the pull request as #<number>",
                )
            )
        elif kind == "commit":
            if RE_SHA.match(ref) is None:
                findings.append(
                    Finding(
                        code=CODE_ORIGIN_UNRESOLVED,
                        message="%s origin %r is not a commit sha" % (rca_id, ref),
                        subject=rca_id,
                        remediation="write the commit as a 7-40 character hex sha",
                    )
                )
                continue
            findings.extend(_unresolvable_evidence(rca_id, ref, probe))
    return findings


def _check_incident_coverage(incidents, rcas, lessons) -> List[Finding]:
    """AC2: every incident has an RCA. AC 'lessons before closure'."""
    findings: List[Finding] = []
    by_incident: Dict[str, List[Dict[str, Any]]] = {}
    for rca in rcas:
        by_incident.setdefault(str(rca.get("incident", "")), []).append(rca)

    for incident in incidents:
        incident_id = str(incident.get("id"))
        analyses = by_incident.get(incident_id, [])
        if not analyses:
            findings.append(
                Finding(
                    code=CODE_INCIDENT_WITHOUT_RCA,
                    message="%s has no RCA artifact" % incident_id,
                    subject=incident_id,
                    remediation="write the RCA and record its rca line in the ledger",
                )
            )
            continue
        for rca in analyses:
            if not as_list(rca.get("corrective_actions")):
                findings.append(
                    Finding(
                        code=CODE_RCA_WITHOUT_CORRECTIVE_ACTION,
                        message="%s produces no corrective action" % rca.get("id"),
                        subject=str(rca.get("id")),
                        remediation="name the action the analysis requires (AC3)",
                    )
                )
        if incident.get("status") == STATUS_CLOSED:
            rca_ids = {str(rca.get("id")) for rca in analyses}
            if not any(str(lesson.get("rca")) in rca_ids for lesson in lessons):
                findings.append(
                    Finding(
                        code=CODE_INCIDENT_CLOSED_WITHOUT_LESSON,
                        message="%s is closed and its RCA records no lesson"
                        % incident_id,
                        subject=incident_id,
                        remediation="record the lesson before closing the incident",
                    )
                )
    return findings


def _check_lessons(lessons, *, root: Path, probe: GitProbe) -> List[Finding]:
    """A LESSON is closed with commit evidence; a SUGGEST is open with an owner."""
    findings: List[Finding] = []
    for lesson in lessons:
        lesson_id = str(lesson.get("id"))
        evidence = as_list(lesson.get("evidence"))
        if is_lesson(lesson_id):
            if lesson.get("status") != STATUS_CLOSED:
                findings.append(
                    Finding(
                        code=CODE_LESSON_INVALID_STATUS,
                        message="%s is not closed" % lesson_id,
                        subject=lesson_id,
                        remediation="close it with commit evidence, or rename it SUGGEST-<n>",
                    )
                )
            if not evidence:
                findings.append(
                    Finding(
                        code=CODE_LESSON_WITHOUT_EVIDENCE,
                        message="%s records no evidence" % lesson_id,
                        subject=lesson_id,
                        remediation="name the commit or PR that proves the lesson landed",
                    )
                )
            elif not any(
                isinstance(item, dict) and item.get("kind") == "commit"
                for item in evidence
            ):
                findings.append(
                    Finding(
                        code=CODE_LESSON_WITHOUT_COMMIT_EVIDENCE,
                        message="%s carries no commit evidence" % lesson_id,
                        subject=lesson_id,
                        remediation="a closed lesson is proven by the commit that ships it",
                    )
                )
        elif is_suggestion(lesson_id):
            if lesson.get("status") != STATUS_OPEN:
                findings.append(
                    Finding(
                        code=CODE_LESSON_INVALID_STATUS,
                        message="%s is closed" % lesson_id,
                        subject=lesson_id,
                        remediation="a closed lesson is a LESSON-<n> with commit evidence",
                    )
                )
            if not str(lesson.get("remediation", "")).strip():
                findings.append(
                    Finding(
                        code=CODE_SUGGESTION_WITHOUT_REMEDIATION,
                        message="%s names no remediation" % lesson_id,
                        subject=lesson_id,
                        remediation="state the change that would close the suggestion",
                    )
                )
            if not str(lesson.get("owner", "")).strip():
                findings.append(
                    Finding(
                        code=CODE_SUGGESTION_WITHOUT_OWNER,
                        message="%s names no owner" % lesson_id,
                        subject=lesson_id,
                        remediation="assign the suggestion to a lane or role",
                    )
                )
            else:
                findings.append(
                    Finding(
                        code=CODE_SUGGESTION_OPEN,
                        message="%s is open (owner: %s); %s"
                        % (
                            lesson_id,
                            lesson.get("owner"),
                            str(lesson.get("remediation", "")).strip()
                            or "no remediation recorded",
                        ),
                        subject=lesson_id,
                        severity=SEVERITY_WARNING,
                        remediation="close it as a LESSON-<n> when the change lands",
                    )
                )
        findings.extend(_check_evidence(lesson_id, evidence, root=root, probe=probe))
    return findings


def _check_actions(actions, rcas, *, root: Path, probe: GitProbe) -> List[Finding]:
    """AC4/AC6: an action is recorded, evidenced, owned and reachable."""
    findings: List[Finding] = []
    claimed: set = set()
    for rca in rcas:
        for action in as_list(rca.get("corrective_actions")):
            claimed.add(str(action))

    for action in actions:
        action_id = str(action.get("id"))
        if action_id and action_id not in claimed:
            findings.append(
                Finding(
                    code=CODE_CORRECTIVE_ACTION_UNLINKED,
                    message="%s is recorded but no RCA claims it" % action_id,
                    subject=action_id,
                    remediation="add the action to the RCA's corrective_actions list",
                )
            )
        evidence = as_list(action.get("evidence"))
        if action.get("status") == STATUS_CLOSED and not evidence:
            findings.append(
                Finding(
                    code=CODE_CORRECTIVE_ACTION_WITHOUT_EVIDENCE,
                    message="%s is closed with no evidence" % action_id,
                    subject=action_id,
                    remediation="name the commit, PR or issue that proves the action landed",
                )
            )
        if action.get("status") == STATUS_OPEN:
            # Resolved through the typed-edge layer, never read as free text.
            target = _edges.remediation_issue(action)
            issue_ref = "#" + target.split("-", 1)[1] if target else ""
            if not issue_ref:
                findings.append(
                    Finding(
                        code=CODE_CORRECTIVE_ACTION_WITHOUT_OWNER,
                        message="%s is open and names no remediation issue" % action_id,
                        subject=action_id,
                        remediation="file the follow-up issue and record its number (AC6)",
                    )
                )
            else:
                findings.append(
                    Finding(
                        code=CODE_CORRECTIVE_ACTION_OPEN,
                        message="%s is open; remediation is tracked in %s"
                        % (action_id, issue_ref),
                        subject=action_id,
                        severity=SEVERITY_WARNING,
                        remediation="close the action when %s lands" % issue_ref,
                    )
                )
        findings.extend(_check_evidence(action_id, evidence, root=root, probe=probe))
    return findings


def _check_evidence(
    record_id: str, evidence: Sequence[Any], *, root: Path, probe: GitProbe
) -> List[Finding]:
    """Evidence must point at something that exists (and be checkable)."""
    findings: List[Finding] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        ref = str(item.get("ref", "")).strip()
        if not ref:
            continue
        if kind == "commit":
            if RE_SHA.match(ref) is None:
                findings.append(
                    Finding(
                        code=CODE_EVIDENCE_UNRESOLVABLE,
                        message="%s cites commit %r, which is not a sha"
                        % (record_id, ref),
                        subject=record_id,
                        remediation="cite the commit as a 7-40 character hex sha",
                    )
                )
            else:
                findings.extend(_unresolvable_evidence(record_id, ref, probe))
        elif kind == "artifact" and not (Path(root) / relpath(ref)).is_file():
            findings.append(
                Finding(
                    code=CODE_EVIDENCE_UNRESOLVABLE,
                    message="%s cites artifact %s, which does not exist"
                    % (record_id, ref),
                    subject=record_id,
                    remediation="point the evidence at a path that exists",
                )
            )
        elif kind in ("issue", "pr") and RE_ISSUE_REF.match(ref) is None:
            findings.append(
                Finding(
                    code=CODE_EVIDENCE_UNRESOLVABLE,
                    message="%s cites %s %r, which is not a number reference"
                    % (record_id, kind, ref),
                    subject=record_id,
                    remediation="cite the %s as #<number>" % kind,
                )
            )
    return findings


def _unresolvable_evidence(record_id: str, sha: str, probe: GitProbe) -> List[Finding]:
    """A named commit must exist in this repository's history."""
    if not probe.available:
        return []
    exists = probe.commit_exists(sha)
    if exists is None or exists:
        return []
    if probe.shallow:
        return [
            Finding(
                code=CODE_EVIDENCE_UNRESOLVABLE,
                message="%s names commit %s, which a shallow clone cannot resolve"
                % (record_id, sha),
                subject=record_id,
                severity=SEVERITY_WARNING,
                remediation="run the gate from a full clone to resolve the evidence",
            )
        ]
    return [
        Finding(
            code=CODE_EVIDENCE_UNRESOLVABLE,
            message="%s names commit %s, which is not in this repository's history"
            % (record_id, sha),
            subject=record_id,
            remediation="cite a commit that exists (evidence must be checkable)",
        )
    ]


def _check_review_cadence(rcas, *, today: date, policy: Policy) -> List[Finding]:
    """An RCA that is never re-read is a document, not a practice."""
    findings: List[Finding] = []
    for rca in rcas:
        rca_id = str(rca.get("id"))
        age = days_since(rca.get("reviewed_at"), today)
        if age is None or age <= policy.review_cadence_days:
            continue
        findings.append(
            Finding(
                code=CODE_RCA_REVIEW_OVERDUE,
                message="%s was last reviewed %d days ago (> %d)"
                % (rca_id, age, policy.review_cadence_days),
                subject=rca_id,
                severity=SEVERITY_WARNING,
                remediation="re-review the RCA and re-stamp reviewed_at",
            )
        )
    return findings


def _check_board(incidents, snapshot, *, policy: Policy) -> List[Finding]:
    """AC2/DoD: an incident-labelled issue needs an RCA (closed) or a plan.

    Scope is declared in ``policy.yaml``. An issue listed as exempt is reported
    with the reason it is exempt, so nothing is silently skipped.
    """
    findings: List[Finding] = []
    traced = {
        _origin_ref(record)
        for record in incidents
        if _origin_ref(record)
    }
    for number in sorted(snapshot):
        issue = snapshot[number]
        if policy.incident_label not in (issue.get("labels") or []):
            continue
        ref = "#%d" % number
        if ref in traced:
            continue
        reason = policy.exemptions.get(ref)
        if reason:
            findings.append(
                Finding(
                    code=CODE_BOARD_INCIDENT_EXEMPT,
                    message="incident-labelled issue %s is exempt: %s" % (ref, reason),
                    subject=ref,
                    severity=SEVERITY_WARNING,
                    remediation="re-review the exemption when the issue closes",
                )
            )
            continue
        if str(issue.get("state", "")).upper() == "CLOSED":
            findings.append(
                Finding(
                    code=CODE_BOARD_INCIDENT_WITHOUT_RCA,
                    message="incident-labelled issue %s was closed with no RCA record"
                    % ref,
                    subject=ref,
                    severity=SEVERITY_ERROR,
                    remediation="record the incident and its RCA, or remove the label",
                )
            )
        else:
            findings.append(
                Finding(
                    code=CODE_BOARD_INCIDENT_PENDING,
                    message="incident-labelled issue %s is open and has no RCA record yet"
                    % ref,
                    subject=ref,
                    severity=SEVERITY_WARNING,
                    remediation="record the incident and RCA when the issue's work completes",
                )
            )
    return findings


def write_report(report: Report, path: Path) -> Path:
    """Write the report as JSON (attested evidence, not a summary)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
