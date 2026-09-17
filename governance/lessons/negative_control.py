#!/usr/bin/env python3
"""The provoked negative control for the board/incident rule (issue #766).

An AREA label was used as an incident marker: ``area:incident-response`` says
where work *lives*, and the gate read it as "this issue *records* an incident,
so it must carry an RCA". Four issues carried it and all four are work items, so
the rule was wrong about every holder — and by the time each was exempted by
hand, it could no longer fail at all (AO-GR-4).

This control exists because the *fix* (a record label) must not be believed: it
is provoked. Every probe below plants one fact and asserts the named verdict,
and the last one is the control's own control — a mutated copy of the checker
with the refusal removed, which must stop refusing, or the probe above proves
nothing (GR-12: a check that cannot fail is a formality).

What it provokes:

* ``AREA-LABEL-IS-NOT-AN-INCIDENT`` — #494's exact shape: a closed issue in the
  incident-response AREA produces no incident finding.
* ``RECORD-LABEL-WITHOUT-A-RECORD-IS-REFUSED`` — a genuine incident record with
  no ledger record is still an error, named.
* ``LEDGER-INCIDENT-WITHOUT-RCA-IS-REFUSED`` — and a genuine incident *record*
  with no RCA is still an error too: the re-keying did not trade one inert
  check for another.
* ``RECORD-LABEL-WITH-A-RECORD-IS-ACCEPTED`` — the detector reads the RECORD,
  not the label: the same issue with a matching ``INC-*`` is accepted.
* ``OPEN-RECORD-LABEL-IS-A-DEVIATION`` — an open one is a tracked deviation,
  not an error, and it is still reported.
* ``EXEMPTIONS-CANNOT-BE-DECLARED`` — the four retired by-issue exemptions
  cannot come back as a YAML edit: ``load_policy`` refuses the key by name.
* ``AREA-LABEL-CANNOT-BE-THE-RECORD-LABEL`` — the defect itself cannot be
  reinstated: an ``area:`` label in that position is refused by name.
* ``SHIPPED-POLICY-DECLARES-THE-RECORD-LABEL`` — the policy in this repository
  names the record label and carries no exemption path at all.
* ``REAL-BOARD-HAS-NO-UNRECORDED-RECORD-LABEL`` — the same verdict against the
  REAL ledger, policy and committed board snapshot: green with no exemption, and
  the four area-label holders measured rather than assumed.
* ``MUTANT-DROPS-THE-REFUSAL`` — the derived control: a scratch copy of the
  checker with the record-label selector forced off must STOP refusing the
  unrecorded issue, and the harness reports that by name.

Issue #1028 added a second rule to the same gate and provokes it here for the
same reason: an open ``corrective-action`` whose tracked issue the committed
board snapshot reports **CLOSED** states a closure condition it has already met,
so it is an error rather than the benign “still in flight” deviation — and while
nothing observed the landing, a stale record was indistinguishable from work in
progress (eight of this ledger's actions were in exactly that state).

* ``CA-REMEDIATION-LANDED-IS-REFUSED`` — exactly one error, named, subject
  ``CA-0001``, quoting the issue and the board's own verdict, and **not** also
  emitted as the mild deviation.
* ``CA-REMEDIATION-OPEN-IS-A-DEVIATION`` — the accepted half: while that issue
  is open the action is a deviation, so the rule did not become “any open
  action”.
* ``CA-REMEDIATION-ABSENT-IS-NOT-EVIDENCE`` — an issue the point-in-time
  snapshot does not carry is evidence of nothing, so its absence must not fire.
* ``MUTANT-DROPS-CA-REMEDIATION-REFUSAL`` — the derived control: a scratch copy
  whose ``_check_actions`` call drops the snapshot must stop refusing.

Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

No network. Writes only inside one scratch directory, which it removes.

Usage::

    python3 governance/lessons/negative_control.py [--repo ROOT]
"""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
DEFAULT_REPO = HERE.parent.parent

#: Every probe answers with (held, detail). ``held`` False is a failing probe.
Probe = Callable[["Case"], Tuple[bool, str]]

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

#: A closed incident ledger that is complete on its own: every other rule passes,
#: so the only finding a probe can observe is the board one it planted.
EVIDENCE_SHA = "abc1234"
ARTIFACT_RELPATH = "governance/lessons/rca/RCA-0001-probe.md"
TODAY = date(2026, 9, 15)

#: The four issues that held the AREA label and were exempted by hand. The
#: record label is what makes those exemptions unnecessary (#766).
RETIRED_EXEMPTIONS = ("#141", "#494", "#495", "#497")
AREA_LABEL = "area:incident-response"
RECORD_LABEL = "incident"

#: The single line the derived control flips. Anchored exactly (once) so a
#: rename in the checker fails this control loudly instead of silently.
MUTANT_ANCHOR = '    return label in (issue.get("labels") or [])\n'
MUTANT_REPLACEMENT = "    return False\n"

DRIVER_SOURCE = '''"""Run ONE probe against a mutated copy of the checker (derived control).

The mutant directory goes FIRST on sys.path, so the probe module's own
``import checker`` resolves to the mutated copy — while the probe module itself
is imported from the real tree.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
probe_dir = Path(sys.argv[1]).resolve()
mutant_dir = Path(sys.argv[2]).resolve()
repo = Path(sys.argv[3]).resolve()
scratch = Path(sys.argv[4]).resolve()
sys.path.insert(0, str(probe_dir))
sys.path.insert(0, str(mutant_dir))

import checker  # noqa: E402

resolved = Path(checker.__file__).resolve()
if not str(resolved).startswith(str(mutant_dir)):
    print("MUTANT-VERDICT=UNPROVEN checker resolved to %s" % resolved)
    raise SystemExit(3)
if checker.INCIDENT_LABEL != "incident":
    print("MUTANT-VERDICT=UNPROVEN the mutant does not carry the record label")
    raise SystemExit(3)

import negative_control  # noqa: E402

case = negative_control.Case(
    checker=checker, repo=repo, scratch=scratch, module_dir=mutant_dir
)
held, detail = negative_control.PROBES["RECORD-LABEL-WITHOUT-A-RECORD-IS-REFUSED"](case)
print("MUTANT-DETAIL=%s" % detail)
if held:
    print("MUTANT-VERDICT=STILL-REFUSED board-incident-without-rca")
    raise SystemExit(1)
print("MUTANT-VERDICT=NOT-REFUSED board-incident-without-rca")
'''


class Case:
    """One probe's context: the checker under test, the repo, and scratch space."""

    def __init__(
        self,
        *,
        checker: Any,
        repo: Path,
        scratch: Path,
        module_dir: Path,
    ) -> None:
        self.c = checker
        self.repo = Path(repo)
        self.scratch = Path(scratch)
        self.module_dir = Path(module_dir)
        self._fixture: Optional[Path] = None

    @property
    def fixture_root(self) -> Path:
        """A repository-shaped root holding the RCA artifact a record points at."""
        if self._fixture is None:
            root = self.scratch / "fixture"
            artifact = root / ARTIFACT_RELPATH
            artifact.parent.mkdir(parents=True, exist_ok=True)
            blocks = ["# RCA-0001 — probe"] + [
                "%s\n\nText." % section for section in self.c.RCA_REQUIRED_SECTIONS
            ]
            artifact.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
            self._fixture = root
        return self._fixture

    def report(self, records: List[Dict[str, Any]], snapshot, *, policy=None):
        root = self.fixture_root
        text = "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n"
        ledger = self.c.parse_ledger_text(text, root / "probe-ledger.jsonl")
        return self.c.check_ledger(
            ledger,
            root=root,
            snapshot=snapshot,
            policy=policy,
            today=TODAY,
            git=_StubProbe(),
            generated_at="2026-09-15T00:00:00Z",
        )


class _StubProbe:
    """A ``GitProbe`` stand-in: explicit answers, no subprocess, no repository."""

    available = True
    shallow = False

    def tracked(self, relative_path: str) -> bool:
        return True

    def commit_exists(self, sha: str) -> bool:
        return sha == EVIDENCE_SHA


def _issue(number: int, *, state: str = "CLOSED", labels=()) -> Dict[str, Any]:
    return {
        "number": number,
        "title": "a probe issue",
        "state": state,
        "milestone": "M24 - Enterprise Knowledge Index",
        "labels": list(labels),
        "parent": None,
        "blocked_by": [],
    }


#: The issue the fixture ledger traces, always on the fixture board: a record
#: whose origin is not on the board is a different finding (`origin-unresolved`),
#: and every probe here must observe the board rule alone.
TRACED_ISSUE = 901


def _snapshot(subject: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """The board under test: the probe's subject, plus the traced issue."""
    return {
        TRACED_ISSUE: _issue(TRACED_ISSUE, labels=["area:board"]),
        subject["number"]: subject,
    }


def _records(origin_ref: str) -> List[Dict[str, Any]]:
    """A complete closed incident, traced to ``origin_ref``."""
    origin = {"kind": "issue", "ref": origin_ref}
    return [
        {
            "id": "INC-0001",
            "kind": "incident",
            "date": "2026-09-01",
            "summary": "a failure with a cause",
            "severity": "high",
            "class": "false-green",
            "origin": origin,
            "status": "closed",
        },
        {
            "id": "RCA-0001",
            "kind": "rca",
            "date": "2026-09-02",
            "incident": "INC-0001",
            "origin": origin,
            "artifact": ARTIFACT_RELPATH,
            "corrective_actions": ["CA-0001"],
            "status": "closed",
            "reviewed_at": "2026-09-10",
        },
        {
            "id": "CA-0001",
            "kind": "corrective-action",
            "date": "2026-09-03",
            "rca": "RCA-0001",
            "action": "fix the mechanism",
            "status": "closed",
            "evidence": [{"kind": "commit", "ref": EVIDENCE_SHA}],
        },
        {
            "id": "LESSON-0001",
            "kind": "lesson",
            "title": "a durable rule",
            "rca": "RCA-0001",
            "date": "2026-09-04",
            "class": "enterprise",
            "status": "closed",
            "evidence": [{"kind": "commit", "ref": EVIDENCE_SHA}],
        },
    ]


#: The issue that tracks the probe's corrective action (issue #1028). Its state
#: on the probe board is what the rule under test reads, and it is deliberately a
#: SECOND issue: the action's remediation ref must resolve to a live board row,
#: which is exactly the situation the real ledger was in while eight of its
#: actions still read ``open`` after the issue tracking them had landed.
REMEDIATION_ISSUE = 950


def _open_action_records() -> List[Dict[str, Any]]:
    """The probe ledger: complete and closed, with its ONE action still open.

    ``_records()`` returns a ledger every other rule passes, so the only finding
    a probe can observe is the one it planted.
    """
    records = _records("#%d" % TRACED_ISSUE)
    records[2].update(
        {
            "status": "open",
            "evidence": [],
            "remediation_issue": "#%d" % REMEDIATION_ISSUE,
        }
    )
    return records


def _probe_board(remediation_state: Optional[str]) -> Dict[int, Dict[str, Any]]:
    """The probe board: the traced issue, plus the remediation issue in a state.

    ``remediation_state=None`` leaves the remediation issue OFF the board, which
    is the absent case the rule must not fire on.
    """
    snapshot = _snapshot(_issue(900, labels=["area:board"]))
    if remediation_state is not None:
        snapshot[REMEDIATION_ISSUE] = _issue(REMEDIATION_ISSUE, state=remediation_state)
    return snapshot


def _board_findings(report) -> List[Any]:
    return [f for f in report.findings if str(f.code).startswith("board-incident")]


def _errors(report) -> List[Any]:
    return [f for f in report.findings if f.severity == "error"]


def _error_codes(report) -> List[str]:
    return sorted(str(f.code) for f in _errors(report))


def _refused(case: Case, path: Path) -> Tuple[bool, str]:
    """Load ``path`` and report the refusal, or the fact that there was none."""
    try:
        case.c.load_policy(path)
    except case.c.PolicyUnavailable as exc:
        return True, str(exc)
    return False, "the policy was accepted"


# --- the probes -------------------------------------------------------------


def probe_area_label_is_not_an_incident(case: Case) -> Tuple[bool, str]:
    """#494's shape: work in the incident-response AREA is not an incident record."""
    snapshot = _snapshot(_issue(900, labels=[AREA_LABEL]))
    report = case.report(_records("#%d" % TRACED_ISSUE), snapshot)
    board = _board_findings(report)
    scanned = report.counts["board_incidents_scanned"]
    held = not board and not _errors(report) and scanned == 0
    return held, (
        "a CLOSED issue labelled %r produced %d board finding(s), scanned=%d, "
        "errors=%s" % (AREA_LABEL, len(board), scanned, _error_codes(report))
    )


def probe_record_label_without_a_record_is_refused(case: Case) -> Tuple[bool, str]:
    """A genuine incident record with no ledger record is still an error."""
    code = case.c.CODE_BOARD_INCIDENT_WITHOUT_RCA
    snapshot = _snapshot(_issue(900, labels=[RECORD_LABEL]))
    report = case.report(_records("#%d" % TRACED_ISSUE), snapshot)
    found = [f for f in report.findings if f.code == code]
    held = (
        len(found) == 1
        and found[0].subject == "#900"
        and _error_codes(report) == [code]
        and RECORD_LABEL in found[0].message
        and "ledger" in found[0].message
    )
    return held, "code=%s subject=%s errors=%s" % (
        code,
        found[0].subject if found else "(none)",
        _error_codes(report),
    )


def probe_ledger_incident_without_rca_is_refused(case: Case) -> Tuple[bool, str]:
    """A genuine incident RECORD with no RCA still fails the gate (issue #766).

    The board rule was re-keyed and the exemptions removed. The ledger's own
    rule — an ``INC-*`` line with no analysis — must be untouched, or this
    change traded one inert check for another. The RCA is dropped from an
    otherwise complete ledger; the finding must still be an error.
    """
    code = case.c.CODE_INCIDENT_WITHOUT_RCA
    snapshot = _snapshot(_issue(900, labels=["area:board"]))
    records = [
        record for record in _records("#%d" % TRACED_ISSUE)
        if record["kind"] != "rca"
    ]
    report = case.report(records, snapshot)
    found = [f for f in report.findings if f.code == code]
    held = len(found) == 1 and code in _error_codes(report)
    return held, "code=%s count=%d errors=%s" % (
        code,
        len(found),
        _error_codes(report),
    )


def probe_record_label_with_a_record_is_accepted(case: Case) -> Tuple[bool, str]:
    """The detector reads the RECORD: the same issue, backed by ``INC-*``, passes."""
    snapshot = _snapshot(_issue(900, labels=[RECORD_LABEL]))
    report = case.report(_records("#900"), snapshot)
    board = _board_findings(report)
    scanned = report.counts["board_incidents_scanned"]
    held = not board and not _errors(report) and scanned == 1
    return held, "scanned=%d board finding(s)=%d errors=%s (the ledger traces #900)" % (
        scanned,
        len(board),
        _error_codes(report),
    )


def probe_open_record_label_is_a_deviation(case: Case) -> Tuple[bool, str]:
    """An open record-labelled issue is a deviation, reported and not an error."""
    code = case.c.CODE_BOARD_INCIDENT_PENDING
    snapshot = _snapshot(_issue(900, state="OPEN", labels=[RECORD_LABEL]))
    report = case.report(_records("#%d" % TRACED_ISSUE), snapshot)
    found = [f for f in report.findings if f.code == code]
    held = len(found) == 1 and not _errors(report)
    return held, "code=%s count=%d errors=%s" % (
        code,
        len(found),
        _error_codes(report),
    )


def probe_exemptions_cannot_be_declared(case: Case) -> Tuple[bool, str]:
    """The four by-issue exemptions cannot come back as a policy edit."""
    path = case.scratch / "policy-exemptions.yaml"
    lines = [
        "schema: cmr.lessons/policy-v1",
        "",
        "board:",
        "  incident_label: %s" % RECORD_LABEL,
        "  exemptions:",
    ]
    for ref in RETIRED_EXEMPTIONS:
        lines.append("    - ref: \"%s\"" % ref)
        lines.append("      reason: the retired hand-written exemption")
    lines.append("")
    lines.append("review_cadence_days: 180")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    refused, detail = _refused(case, path)
    held = refused and "exemptions" in detail
    return held, "%s; the retired refs %s cannot be declared" % (
        detail if refused else "NOT REFUSED — " + detail,
        "/".join(RETIRED_EXEMPTIONS),
    )


def probe_area_label_cannot_be_the_record_label(case: Case) -> Tuple[bool, str]:
    """The defect cannot be reinstated: an area label in that position is refused."""
    path = case.scratch / "policy-area-label.yaml"
    path.write_text(
        "schema: cmr.lessons/policy-v1\n\nboard:\n  incident_label: %s\n\n"
        "review_cadence_days: 180\n" % AREA_LABEL,
        encoding="utf-8",
    )
    refused, detail = _refused(case, path)
    held = refused and "AREA label" in detail and AREA_LABEL in detail
    return held, detail if refused else "NOT REFUSED — " + detail


def probe_shipped_policy_declares_the_record_label(case: Case) -> Tuple[bool, str]:
    """The policy in this repository names the record label and no exemption path."""
    policy = case.c.load_policy(case.repo / case.c.POLICY_RELPATH)
    has_exemptions = hasattr(policy, "exemptions")
    held = policy.incident_label == RECORD_LABEL and not has_exemptions
    return held, "incident_label=%r exemptions attribute=%s cadence=%d" % (
        policy.incident_label,
        has_exemptions,
        policy.review_cadence_days,
    )


def probe_real_board_has_no_unrecorded_record_label(case: Case) -> Tuple[bool, str]:
    """The REAL ledger, policy and committed snapshot: green with no exemption.

    Measured, not assumed: the count of record-labelled issues the checker
    scanned is compared against the labels in the snapshot, and the four retired
    area-label holders are named so "no finding" cannot be an empty scope.
    """
    c = case.c
    root = case.repo
    ledger = c.load_ledger(root / c.LEDGER_RELPATH)
    snapshot = c.load_snapshot(root / c.SNAPSHOT_RELPATH)
    policy = c.load_policy(root / c.POLICY_RELPATH)
    report = c.check_ledger(
        ledger,
        root=root,
        snapshot=snapshot,
        policy=policy,
        today=TODAY,
        git=c.GitProbe(root),
        generated_at="2026-09-15T00:00:00Z",
    )
    labelled = sorted(
        number
        for number, issue in snapshot.items()
        if policy.incident_label in (issue.get("labels") or [])
    )
    area_labelled = sorted(
        number
        for number, issue in snapshot.items()
        if AREA_LABEL in (issue.get("labels") or [])
    )
    board = _board_findings(report)
    scanned = report.counts["board_incidents_scanned"]
    subjects = {f.subject for f in report.findings}
    retired = [ref for ref in RETIRED_EXEMPTIONS if ref in subjects]
    held = (
        not board
        and not _errors(report)
        and scanned == len(labelled)
        and not retired
        and not hasattr(policy, "exemptions")
    )
    return held, (
        "snapshot: %d issue(s) carry %r (scanned=%d), %d carry %r and 0 of them "
        "is treated as an incident; board findings=%d errors=%s retired refs "
        "reported=%s" % (
            len(labelled),
            policy.incident_label,
            scanned,
            len(area_labelled),
            AREA_LABEL,
            len(board),
            _error_codes(report),
            retired or "(none)",
        )
    )


def _fresh_fixture(case: Case, tag: str) -> Path:
    """A fresh, isolated fixture root (issue #1052 probes never share state).

    The board-rule probes above all reuse ``case.fixture_root``; the docs/rca
    and README probes write files a `docs/rca/*.md` scan or a README scan
    would pick up, so each one gets its own root under scratch instead, to
    guarantee no probe's planted file leaks into another probe's verdict.
    """
    root = case.scratch / ("fixture-%s" % tag)
    artifact = root / ARTIFACT_RELPATH
    artifact.parent.mkdir(parents=True, exist_ok=True)
    blocks = ["# RCA-0001 — probe"] + [
        "%s\n\nText." % section for section in case.c.RCA_REQUIRED_SECTIONS
    ]
    artifact.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return root


def _fixture_report(case: Case, root: Path, records: List[Dict[str, Any]], snapshot):
    text = "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n"
    ledger = case.c.parse_ledger_text(text, root / "probe-ledger.jsonl")
    return case.c.check_ledger(
        ledger,
        root=root,
        snapshot=snapshot,
        policy=None,
        today=TODAY,
        git=_StubProbe(),
        generated_at="2026-09-15T00:00:00Z",
    )


def _doc_codes(report) -> List[Any]:
    return [f for f in report.findings if str(f.code).startswith("doc-rca")]


# --- issue #1052: one RCA id authority ---------------------------------


def probe_doc_rca_unknown_id_is_refused(case: Case) -> Tuple[bool, str]:
    """A ``docs/rca/*.md`` writeup citing an id the ledger never recorded."""
    code = case.c.CODE_DOC_RCA_ID_UNKNOWN
    root = _fresh_fixture(case, "doc-unknown")
    docs_dir = root / "docs" / "rca"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "sample.md").write_text(
        "# A narrative writeup\n\nSee RCA-9999 for background.\n", encoding="utf-8"
    )
    snapshot = _snapshot(_issue(900, labels=["area:board"]))
    report = _fixture_report(case, root, _records("#%d" % TRACED_ISSUE), snapshot)
    found = [f for f in report.findings if f.code == code]
    held = (
        len(found) == 1
        and found[0].subject == "docs/rca/sample.md"
        and code in _error_codes(report)
    )
    return held, "code=%s count=%d errors=%s" % (code, len(found), _error_codes(report))


def probe_doc_rca_citation_is_accepted(case: Case) -> Tuple[bool, str]:
    """Citing a KNOWN ledger id in prose (not minting it) is accepted."""
    root = _fresh_fixture(case, "doc-cite")
    docs_dir = root / "docs" / "rca"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "sample.md").write_text(
        "# A narrative writeup\n\nSee RCA-0001 for the full analysis.\n",
        encoding="utf-8",
    )
    snapshot = _snapshot(_issue(900, labels=["area:board"]))
    report = _fixture_report(case, root, _records("#%d" % TRACED_ISSUE), snapshot)
    doc_findings = _doc_codes(report)
    held = not doc_findings
    return held, "doc-rca finding(s)=%d errors=%s" % (
        len(doc_findings),
        _error_codes(report),
    )


def probe_doc_rca_mint_mismatch_is_refused(case: Case) -> Tuple[bool, str]:
    """A heading that MINTS a ledger id whose artifact is a different file.

    Issue #1052's exact defect shape: ``docs/rca/2026-09-16-pr-queue-clearing
    .md`` titled itself ``RCA-0007 / RCA-0008``, ids the ledger already held
    for a different artifact.
    """
    code = case.c.CODE_DOC_RCA_ARTIFACT_MISMATCH
    root = _fresh_fixture(case, "doc-mint")
    docs_dir = root / "docs" / "rca"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "sample.md").write_text(
        "# RCA-0001 — a doc that mints an id the ledger already owns\n\nBody.\n",
        encoding="utf-8",
    )
    snapshot = _snapshot(_issue(900, labels=["area:board"]))
    report = _fixture_report(case, root, _records("#%d" % TRACED_ISSUE), snapshot)
    found = [f for f in report.findings if f.code == code]
    held = (
        len(found) == 1
        and found[0].subject == "RCA-0001"
        and code in _error_codes(report)
    )
    return held, "code=%s count=%d errors=%s" % (code, len(found), _error_codes(report))


def probe_readme_incident_count_mismatch_is_refused(case: Case) -> Tuple[bool, str]:
    """A README incident count that does not match the ledger is refused."""
    code = case.c.CODE_README_INCIDENT_COUNT_MISMATCH
    root = _fresh_fixture(case, "readme-bad")
    readme_dir = root / "governance" / "lessons"
    readme_dir.mkdir(parents=True, exist_ok=True)
    (readme_dir / "README.md").write_text(
        "Two real incidents recorded so far.\n", encoding="utf-8"
    )
    snapshot = _snapshot(_issue(900, labels=["area:board"]))
    report = _fixture_report(case, root, _records("#%d" % TRACED_ISSUE), snapshot)
    found = [f for f in report.findings if f.code == code]
    held = len(found) == 1 and code in _error_codes(report)
    return held, "code=%s count=%d errors=%s" % (code, len(found), _error_codes(report))


def probe_readme_incident_count_match_is_accepted(case: Case) -> Tuple[bool, str]:
    """The same sentence, with the ledger's real count, is accepted."""
    root = _fresh_fixture(case, "readme-good")
    readme_dir = root / "governance" / "lessons"
    readme_dir.mkdir(parents=True, exist_ok=True)
    (readme_dir / "README.md").write_text(
        "One real incidents recorded so far.\n", encoding="utf-8"
    )
    snapshot = _snapshot(_issue(900, labels=["area:board"]))
    report = _fixture_report(case, root, _records("#%d" % TRACED_ISSUE), snapshot)
    found = [
        f for f in report.findings
        if f.code == case.c.CODE_README_INCIDENT_COUNT_MISMATCH
    ]
    held = not found
    return held, "finding(s)=%d errors=%s" % (len(found), _error_codes(report))


def probe_duplicate_id_is_refused(case: Case) -> Tuple[bool, str]:
    """Refusal (a): one id must have exactly one authoritative line.

    Pre-existing behaviour (``Ledger.__init__``) — this is its plant, so the
    "13+ probes" of issue #1052 covers all three refusals named in its
    acceptance criteria, not just the two new ones.
    """
    code = case.c.CODE_DUPLICATE_ID
    root = _fresh_fixture(case, "dup-id")
    records = _records("#%d" % TRACED_ISSUE)
    duplicate = dict(records[0])  # a second INC-0001 line, byte-different
    duplicate["summary"] = "a second, conflicting line for the same id"
    records = records + [duplicate]
    snapshot = _snapshot(_issue(900, labels=["area:board"]))
    report = _fixture_report(case, root, records, snapshot)
    found = [f for f in report.findings if f.code == code]
    held = len(found) == 1 and found[0].subject == "INC-0001" and code in _error_codes(report)
    return held, "code=%s count=%d errors=%s" % (code, len(found), _error_codes(report))


def probe_mutant_drops_duplicate_id_refusal(case: Case) -> Tuple[bool, str]:
    """The duplicate-id refusal (``Ledger.__init__``) must be provably able to fail."""
    anchor = "            if record_id in self.records:\n"
    replacement = "            if False:  # mutated out: MUTANT-DROPS-DUPLICATE-ID-REFUSAL\n"
    return _run_generic_mutant(
        case,
        tag="dup-id",
        anchor=anchor,
        replacement=replacement,
        probe_name="DUPLICATE-ID-IS-REFUSED",
        expect_code=case.c.CODE_DUPLICATE_ID,
    )


#: The wiring the corrective-action rule depends on: the board snapshot reaches
#: ``_check_actions``. A mutation that removes it must stop the refusal, or the
#: probe above proves nothing.
MUTANT_ANCHOR_CA_REMEDIATION = (
    "        _check_actions(actions, rcas, root=root, probe=probe, snapshot=snapshot)\n"
)
MUTANT_REPLACEMENT_CA_REMEDIATION = (
    "        _check_actions(actions, rcas, root=root, probe=probe)  # mutated out\n"
)


def probe_ca_remediation_landed_is_refused(case: Case) -> Tuple[bool, str]:
    """An open action whose tracked issue the board reports CLOSED is an error.

    This is the #1028 gap in the RCA surface: the record states its own closure
    condition ("close it when #<n> lands") and nothing could observe that the
    condition had been met, so a stale record was indistinguishable from work in
    flight.
    """
    code = case.c.CODE_CORRECTIVE_ACTION_REMEDIATION_LANDED
    report = case.report(_open_action_records(), _probe_board("CLOSED"))
    found = [f for f in report.findings if f.code == code]
    also_open = [
        f for f in report.findings if f.code == case.c.CODE_CORRECTIVE_ACTION_OPEN
    ]
    held = (
        len(found) == 1
        and found[0].subject == "CA-0001"
        and _error_codes(report) == [code]
        and ("#%d" % REMEDIATION_ISSUE) in found[0].message
        and "CLOSED" in found[0].message
        and not also_open
    )
    return held, (
        "code=%s subject=%s errors=%s (the benign deviation is not also emitted)" % (
            code,
            found[0].subject if found else "(none)",
            _error_codes(report),
        )
    )


def probe_ca_remediation_open_is_a_deviation(case: Case) -> Tuple[bool, str]:
    """The accepted half: while the tracked issue is open, in flight is a deviation.

    Without this half the rule would be "any open action", which refuses work
    that is genuinely in progress.
    """
    landed = case.c.CODE_CORRECTIVE_ACTION_REMEDIATION_LANDED
    open_code = case.c.CODE_CORRECTIVE_ACTION_OPEN
    report = case.report(_open_action_records(), _probe_board("OPEN"))
    found = [f for f in report.findings if f.code == open_code]
    held = (
        len(found) == 1
        and not [f for f in report.findings if f.code == landed]
        and not _errors(report)
    )
    return held, "code=%s count=%d errors=%s" % (
        open_code,
        len(found),
        _error_codes(report),
    )


def probe_ca_remediation_absent_is_not_evidence(case: Case) -> Tuple[bool, str]:
    """An issue the snapshot does not carry is not evidence that it landed.

    The snapshot is point-in-time, so its silence about an issue means nothing:
    firing there would make the verdict a function of how old a committed
    artifact is — "every action recorded since the last refresh is stale" —
    which is the very class of defect this rule exists to catch.
    """
    landed = case.c.CODE_CORRECTIVE_ACTION_REMEDIATION_LANDED
    board = _probe_board(None)
    report = case.report(_open_action_records(), board)
    absent = [f for f in report.findings if f.code == landed]
    held = (
        not absent
        and not _errors(report)
        and REMEDIATION_ISSUE not in board
        and case.c.CODE_CORRECTIVE_ACTION_OPEN
        in [f.code for f in report.findings]
    )
    return held, "the issue is off the snapshot; landed finding(s)=%d errors=%s" % (
        len(absent),
        _error_codes(report),
    )


def probe_mutant_drops_ca_remediation_refusal(case: Case) -> Tuple[bool, str]:
    """The corrective-action-remediation-landed refusal must be able to fail."""
    return _run_generic_mutant(
        case,
        tag="ca-remediation",
        anchor=MUTANT_ANCHOR_CA_REMEDIATION,
        replacement=MUTANT_REPLACEMENT_CA_REMEDIATION,
        probe_name="CA-REMEDIATION-LANDED-IS-REFUSED",
        expect_code=case.c.CODE_CORRECTIVE_ACTION_REMEDIATION_LANDED,
    )


def probe_real_docs_rca_have_no_unknown_ids(case: Case) -> Tuple[bool, str]:
    """The REAL ``docs/rca/`` tree cites only ids the ledger actually holds."""
    c = case.c
    root = case.repo
    ledger = c.load_ledger(root / c.LEDGER_RELPATH)
    snapshot = c.load_snapshot(root / c.SNAPSHOT_RELPATH)
    policy = c.load_policy(root / c.POLICY_RELPATH)
    report = c.check_ledger(
        ledger,
        root=root,
        snapshot=snapshot,
        policy=policy,
        today=TODAY,
        git=c.GitProbe(root),
        generated_at="2026-09-15T00:00:00Z",
    )
    doc_findings = _doc_codes(report)
    held = not doc_findings
    return held, "doc-rca finding(s)=%d" % len(doc_findings)


def probe_real_readme_incident_count_matches_ledger(case: Case) -> Tuple[bool, str]:
    """The REAL README's incident count matches the REAL ledger's count."""
    c = case.c
    root = case.repo
    ledger = c.load_ledger(root / c.LEDGER_RELPATH)
    snapshot = c.load_snapshot(root / c.SNAPSHOT_RELPATH)
    policy = c.load_policy(root / c.POLICY_RELPATH)
    report = c.check_ledger(
        ledger,
        root=root,
        snapshot=snapshot,
        policy=policy,
        today=TODAY,
        git=c.GitProbe(root),
        generated_at="2026-09-15T00:00:00Z",
    )
    found = [f for f in report.findings if f.code == c.CODE_README_INCIDENT_COUNT_MISMATCH]
    held = not found
    return held, "finding(s)=%d" % len(found)


#: Generic mutant driver (issue #1052): parameterised on the probe to re-run
#: against the mutated checker, so a single driver serves every rule's own
#: mutant, not just the board rule's.
GENERIC_DRIVER_SOURCE = '''"""Run ONE named probe against a mutated copy of the checker (derived control).

The mutant directory goes FIRST on sys.path, so the probe module's own
``import checker`` resolves to the mutated copy — while the probe module and
its helper functions are imported from the real tree.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
probe_dir = Path(sys.argv[1]).resolve()
mutant_dir = Path(sys.argv[2]).resolve()
repo = Path(sys.argv[3]).resolve()
scratch = Path(sys.argv[4]).resolve()
probe_name = sys.argv[5]
sys.path.insert(0, str(probe_dir))
sys.path.insert(0, str(mutant_dir))

import checker  # noqa: E402

resolved = Path(checker.__file__).resolve()
if not str(resolved).startswith(str(mutant_dir)):
    print("MUTANT-VERDICT=UNPROVEN checker resolved to %s" % resolved)
    raise SystemExit(3)

import negative_control  # noqa: E402

case = negative_control.Case(
    checker=checker, repo=repo, scratch=scratch, module_dir=mutant_dir
)
held, detail = negative_control.PROBES[probe_name](case)
print("MUTANT-DETAIL=%s" % detail)
if held:
    print("MUTANT-VERDICT=STILL-HELD")
    raise SystemExit(1)
print("MUTANT-VERDICT=NOT-HELD")
'''


def _run_generic_mutant(
    case: Case, *, tag: str, anchor: str, replacement: str, probe_name: str, expect_code: str
) -> Tuple[bool, str]:
    """Build a scratch copy of the checker with ``anchor`` replaced, and rerun
    ``probe_name`` against it in a fresh interpreter — the refusal must be
    OBSERVED to disappear, or the probe proves nothing."""
    mutant = case.scratch / ("mutant-%s" % tag)
    mutant_dir = mutant / "governance" / "lessons"
    mutant_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ("checker.py", "model.py", "edges.py"):
        source = case.module_dir / name
        if source.is_file():
            shutil.copy2(source, mutant_dir / name)
            copied.append(name)
    if "checker.py" not in copied:
        return False, "checker.py is not in %s" % case.module_dir
    text = (mutant_dir / "checker.py").read_text(encoding="utf-8")
    matches = text.count(anchor)
    if matches != 1:
        return False, "the mutation anchor matched %d time(s), not once" % matches
    mutated = text.replace(anchor, replacement)
    if mutated == text:
        return False, "the mutation was a no-op"
    (mutant_dir / "checker.py").write_text(mutated, encoding="utf-8")

    driver = mutant / "driver.py"
    driver.write_text(GENERIC_DRIVER_SOURCE, encoding="utf-8")
    done = subprocess.run(
        [
            sys.executable,
            str(driver),
            str(case.module_dir),
            str(mutant_dir),
            str(case.repo),
            str(mutant),
            probe_name,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    output = (done.stdout + done.stderr).strip()
    detail_line = ""
    for line in output.splitlines():
        if line.startswith("MUTANT-DETAIL="):
            detail_line = line.split("=", 1)[1]
    held = done.returncode == 0 and "MUTANT-VERDICT=NOT-HELD" in output
    return held, "%s; the mutant: %s" % (
        "NOT-HELD %s (the probe is proven able to fail)" % expect_code
        if held
        else "the mutation proved nothing (rc=%d): %s" % (done.returncode, output),
        detail_line or "(no detail)",
    )


#: The two call sites in ``check_ledger`` that wire the issue #1052 rules in —
#: each is a unique, single-occurrence anchor a mutation can remove cleanly.
MUTANT_ANCHOR_DOC_RCA = "    findings.extend(_check_doc_rca_ids(ledger, root=root))\n"
MUTANT_REPLACEMENT_DOC_RCA = "    pass  # mutated out: MUTANT-DROPS-DOC-RCA-REFUSAL\n"
MUTANT_ANCHOR_README = (
    "    findings.extend(_check_readme_incident_count(incidents, root=root))\n"
)
MUTANT_REPLACEMENT_README = "    pass  # mutated out: MUTANT-DROPS-README-REFUSAL\n"


def probe_mutant_drops_doc_rca_refusal(case: Case) -> Tuple[bool, str]:
    """The doc-rca-id-unknown refusal must be provably able to fail."""
    return _run_generic_mutant(
        case,
        tag="doc-rca",
        anchor=MUTANT_ANCHOR_DOC_RCA,
        replacement=MUTANT_REPLACEMENT_DOC_RCA,
        probe_name="DOC-RCA-UNKNOWN-ID-IS-REFUSED",
        expect_code=case.c.CODE_DOC_RCA_ID_UNKNOWN,
    )


def probe_mutant_drops_readme_refusal(case: Case) -> Tuple[bool, str]:
    """The readme-incident-count-mismatch refusal must be provably able to fail."""
    return _run_generic_mutant(
        case,
        tag="readme",
        anchor=MUTANT_ANCHOR_README,
        replacement=MUTANT_REPLACEMENT_README,
        probe_name="README-INCIDENT-COUNT-MISMATCH-IS-REFUSED",
        expect_code=case.c.CODE_README_INCIDENT_COUNT_MISMATCH,
    )


def probe_mutant_drops_the_refusal(case: Case) -> Tuple[bool, str]:
    """The control's own control: remove the selector, and the refusal must go.

    A mutated copy of the checker is built in scratch with exactly one edit, the
    probe that must fire is run against it in a fresh interpreter, and the
    harness requires the mutation to be OBSERVED — the unrecorded issue is no
    longer refused. A probe that still fires under this mutation proves nothing.
    """
    mutant = case.scratch / "mutant"
    mutant_dir = mutant / "governance" / "lessons"
    mutant_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ("checker.py", "model.py", "edges.py"):
        source = case.module_dir / name
        if source.is_file():
            shutil.copy2(source, mutant_dir / name)
            copied.append(name)
    if "checker.py" not in copied:
        return False, "checker.py is not in %s" % case.module_dir
    text = (mutant_dir / "checker.py").read_text(encoding="utf-8")
    matches = text.count(MUTANT_ANCHOR)
    if matches != 1:
        return False, "the mutation anchor matched %d time(s), not once" % matches
    mutated = text.replace(MUTANT_ANCHOR, MUTANT_REPLACEMENT)
    if mutated == text:
        return False, "the mutation was a no-op"
    (mutant_dir / "checker.py").write_text(mutated, encoding="utf-8")

    driver = mutant / "driver.py"
    driver.write_text(DRIVER_SOURCE, encoding="utf-8")
    done = subprocess.run(
        [
            sys.executable,
            str(driver),
            str(case.module_dir),
            str(mutant_dir),
            str(case.repo),
            str(mutant),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    output = (done.stdout + done.stderr).strip()
    detail_line = ""
    for line in output.splitlines():
        if line.startswith("MUTANT-DETAIL="):
            detail_line = line.split("=", 1)[1]
    held = (
        done.returncode == 0
        and "MUTANT-VERDICT=NOT-REFUSED board-incident-without-rca" in output
    )
    return held, "%s; the mutant: %s" % (
        "NOT-REFUSED board-incident-without-rca (the probe is proven able to fail)"
        if held
        else "the mutation proved nothing (rc=%d): %s" % (done.returncode, output),
        detail_line or "(no detail)",
    )


PROBES: Dict[str, Probe] = {
    "AREA-LABEL-IS-NOT-AN-INCIDENT": probe_area_label_is_not_an_incident,
    "RECORD-LABEL-WITHOUT-A-RECORD-IS-REFUSED": probe_record_label_without_a_record_is_refused,
    "LEDGER-INCIDENT-WITHOUT-RCA-IS-REFUSED": probe_ledger_incident_without_rca_is_refused,
    "RECORD-LABEL-WITH-A-RECORD-IS-ACCEPTED": probe_record_label_with_a_record_is_accepted,
    "OPEN-RECORD-LABEL-IS-A-DEVIATION": probe_open_record_label_is_a_deviation,
    "EXEMPTIONS-CANNOT-BE-DECLARED": probe_exemptions_cannot_be_declared,
    "AREA-LABEL-CANNOT-BE-THE-RECORD-LABEL": probe_area_label_cannot_be_the_record_label,
    "SHIPPED-POLICY-DECLARES-THE-RECORD-LABEL": probe_shipped_policy_declares_the_record_label,
    "REAL-BOARD-HAS-NO-UNRECORDED-RECORD-LABEL": probe_real_board_has_no_unrecorded_record_label,
    "MUTANT-DROPS-THE-REFUSAL": probe_mutant_drops_the_refusal,
    "DOC-RCA-UNKNOWN-ID-IS-REFUSED": probe_doc_rca_unknown_id_is_refused,
    "DOC-RCA-CITATION-IS-ACCEPTED": probe_doc_rca_citation_is_accepted,
    "DOC-RCA-MINT-MISMATCH-IS-REFUSED": probe_doc_rca_mint_mismatch_is_refused,
    "README-INCIDENT-COUNT-MISMATCH-IS-REFUSED": probe_readme_incident_count_mismatch_is_refused,
    "README-INCIDENT-COUNT-MATCH-IS-ACCEPTED": probe_readme_incident_count_match_is_accepted,
    "REAL-DOCS-RCA-HAVE-NO-UNKNOWN-IDS": probe_real_docs_rca_have_no_unknown_ids,
    "REAL-README-INCIDENT-COUNT-MATCHES-LEDGER": probe_real_readme_incident_count_matches_ledger,
    "MUTANT-DROPS-DOC-RCA-REFUSAL": probe_mutant_drops_doc_rca_refusal,
    "MUTANT-DROPS-README-REFUSAL": probe_mutant_drops_readme_refusal,
    "DUPLICATE-ID-IS-REFUSED": probe_duplicate_id_is_refused,
    "MUTANT-DROPS-DUPLICATE-ID-REFUSAL": probe_mutant_drops_duplicate_id_refusal,
    "CA-REMEDIATION-LANDED-IS-REFUSED": probe_ca_remediation_landed_is_refused,
    "CA-REMEDIATION-OPEN-IS-A-DEVIATION": probe_ca_remediation_open_is_a_deviation,
    "CA-REMEDIATION-ABSENT-IS-NOT-EVIDENCE": probe_ca_remediation_absent_is_not_evidence,
    "MUTANT-DROPS-CA-REMEDIATION-REFUSAL": probe_mutant_drops_ca_remediation_refusal,
}


def load_checker(module_dir: Path) -> Any:
    """Import the checker under test from ``module_dir``, never a cached copy."""
    module_dir = Path(module_dir).resolve()
    if not (module_dir / "checker.py").is_file():
        raise FileNotFoundError("no checker.py under %s" % module_dir)
    sys.dont_write_bytecode = True
    for cache in module_dir.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
    module = importlib.import_module("checker")
    resolved = Path(module.__file__).resolve()
    if not str(resolved).startswith(str(module_dir)):
        raise FileNotFoundError(
            "checker resolved to %s, which is not under %s" % (resolved, module_dir)
        )
    return module


def scratch_dir() -> Path:
    """A per-run scratch directory, refused loudly rather than reused.

    The name is explicit rather than a ``mktemp`` template: a template whose
    placeholder is a run of one letter trips this repository's own
    unfinished-marker scan. ``mkdir`` without ``exist_ok`` refuses a collision.
    """
    path = Path("/tmp") / ("ao766-negative-control.%d" % int(time.time() * 1000))
    path.mkdir(parents=True, exist_ok=False)
    return path


def run(repo: Path, module_dir: Path, out=sys.stdout) -> int:
    try:
        checker = load_checker(module_dir)
    except FileNotFoundError as exc:
        print("negative-control: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    except ImportError as exc:
        print("negative-control: CANNOT-ASSESS — cannot import the checker (%s)" % exc,
              file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    scratch = scratch_dir()
    failed: List[str] = []
    try:
        case = Case(
            checker=checker, repo=repo, scratch=scratch, module_dir=module_dir
        )
        for name, probe in PROBES.items():
            try:
                held, detail = probe(case)
            except Exception as exc:  # a probe that cannot run has not passed
                held, detail = False, "raised %s: %s" % (type(exc).__name__, exc)
            if not held:
                failed.append(name)
            print("  probe %s: %s — %s" % (name, "PASS" if held else "FAIL", detail))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if failed:
        print(
            "  PROBES: FAIL (%d of %d) — %s" % (len(failed), len(PROBES), ", ".join(failed))
        )
        print(
            "negative-control: NOT-OK — the board/incident rule is not proven "
            "(issue #766)",
            file=sys.stderr,
        )
        return EXIT_NOT_OK
    print("  PROBES: PASS (%d of %d)" % (len(PROBES), len(PROBES)))
    print(
        "negative-control: OK — an area label cannot manufacture an incident, a "
        "record-labelled issue with no ledger record is still refused, no "
        "exemption can be declared, an action left open past the landing of the "
        "issue that tracks it is refused rather than reported as in flight, and "
        "each refusal is proven able to fail"
    )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="governance/lessons/negative_control.py",
        description="Provoke the board/incident rule of the lessons gate (#766).",
    )
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--module-dir", type=Path, default=None)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).resolve()
    module_dir = (
        Path(args.module_dir).resolve()
        if args.module_dir
        else repo / "governance" / "lessons"
    )
    if not repo.is_dir():
        print("negative-control: CANNOT-ASSESS — no repository at %s" % repo,
              file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    return run(repo, module_dir)


if __name__ == "__main__":
    sys.exit(main())
