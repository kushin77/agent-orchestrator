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
        "exemption can be declared, and the refusal is proven able to fail"
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
