#!/usr/bin/env python3
"""fleet-parity — the fleet acceptance test as a RUNNABLE instrument (#799).

THE DEFECT THIS EXISTS FOR
The acceptance test was scored by an operator reading logs. Measured 2026-09-15:
the fleet was handed issue #796, and the verdict — "no work product, and an
unbounded loop" — came from a human who grepped `.fleet/sister.log` by hand and
counted `executing directive` 13 times. A verdict that only a human can produce is
not a repeatable instrument: it cannot be run on the next lane, it cannot be run
by the lane itself, and its dimensions are whatever the reader happened to think
of. This module is that instrument.

THE SHAPE: COLLECT, THEN JUDGE
The impure work (git, the GitHub API, the repo's own checkers) is confined to
:func:`collect`, which builds a **subject** — a plain dictionary. Everything that
decides anything is :func:`judge`, which is pure: subject in, one
:class:`Verdict` per dimension out. That split is what makes the instrument
provokable. A dimension that cannot be read from the subject reports
CANNOT-ASSESS, and the probe suite can therefore plant a subject that fails
*any* dimension — including the ones that normally need a network — and assert
that the judgement moves. Without the split, "every dimension is provoked" would
be a claim about a live fleet, i.e. exactly the kind of claim this repo refuses.

NO CLAIM WITHOUT ITS MEASUREMENT
A :class:`Verdict` with status OK and no evidence cannot be constructed; it raises
:class:`Unmeasured`. That is deliberate and structural: the meta-rule of the
acceptance test ("no claim stated without its measurement") is not a convention
this module follows, it is a shape it cannot express. "An unreadable input is not
a pass" is the same rule seen from the other side, so an unreadable field yields
CANNOT-ASSESS and the verdict must NAME what could not be read.

THE NEGATIVE CONTROL FOR THE PLANTS
A probe suite that plants only failing subjects is satisfied by a judge that
fails everything. So the plants come in pairs: `plants/healthy.json` must PASS
every dimension, and each plant must FAIL (or, for the deliberately unreadable
ones, CANNOT-ASSESS) exactly the dimension it names — asserted in both directions
by :func:`self_test`.

EXIT CODES (the repo tri-state)
  0  every dimension measured and holding
  1  a MEASURED violation — the acceptance test FAILS, and the dimension is named
  2  CANNOT-ASSESS — some dimension could not be read, so no PASS may be claimed

CLI
  judge.py                        the probe suite alone (what `make verify` runs)
  judge.py --issue <n>            assess a real lane and print the verdict grid
  judge.py --subject-file <path>  judge a recorded subject offline (no network)
  judge.py --collect-only         write the collected subject as JSON and stop
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
PLANTS_DIR = HERE / "plants"
DEFAULT_SLUG = "kushin77/agent-orchestrator"

OK = "OK"
FAIL = "FAIL"
CANNOT = "CANNOT-ASSESS"

#: Every dimension the acceptance test is scored on, in the order it is reported.
#: The codes are the vocabulary of the report and of a plant's `dimension` field.
DIMENSION_CODES = (
    "verify-output",
    "attestation-sha",
    "gate-wired",
    "provoked",
    "commit-trailer",
    "pr-contract",
    "isolation-audit",
    "branch-pushed-then-deleted",
    "issue-closed-with-evidence",
    "measurement",
)

#: The two dimensions judged by RUNNING one of the repo's own checkers. Their
#: block in the subject is `{"rc": <int|None>, "output": <str|None>}`, so a plant
#: can provoke the judgement without the network and a live run can record the
#: real verdict. `unreadable` is the rc the checker uses for CANNOT-ASSESS.
RC_TOOLS = {
    "pr-contract": ("bash scripts/check-pr-contract.sh", "scripts/check-pr-contract.sh"),
    "isolation-audit": (
        "python3 governance/isolation/cli.py audit",
        "governance/isolation/cli.py audit",
    ),
}
CANNOT_ASSESS_RC = 2


class Unmeasured(RuntimeError):
    """An OK verdict was about to be stated without a measurement behind it."""


@dataclass(frozen=True)
class Verdict:
    """One dimension's verdict. `evidence` is REQUIRED for OK, by construction."""

    code: str
    status: str
    evidence: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status == OK and not self.evidence.strip():
            raise Unmeasured(
                f"{self.code}: refusing to state OK with no measurement — an unreadable input is "
                "not a pass (#799); report CANNOT-ASSESS and name what could not be read"
            )
        if self.status == CANNOT and not self.detail.strip():
            raise Unmeasured(f"{self.code}: CANNOT-ASSESS must name the input that could not be read")

    @property
    def line(self) -> str:
        body = self.evidence if self.status == OK else (self.detail or self.evidence)
        return f"  {self.status:<14} {self.code:<28} {body}"


@dataclass
class Report:
    """The verdict grid, and the aggregate the exit code is derived from."""

    issue: int | None
    verdicts: list[Verdict] = field(default_factory=list)

    @property
    def failures(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.status == FAIL]

    @property
    def unassessed(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.status == CANNOT]

    @property
    def aggregate(self) -> str:
        if self.failures:
            return FAIL
        if self.unassessed:
            return CANNOT
        return OK

    @property
    def exit_code(self) -> int:
        return {OK: 0, FAIL: 1, CANNOT: 2}[self.aggregate]

    def render(self) -> str:
        head = f"fleet-parity: issue #{self.issue}" if self.issue is not None else "fleet-parity"
        lines = [f"== {head} — {len(self.verdicts)} dimension(s) =="]
        lines += [v.line for v in self.verdicts]
        lines.append("")
        if self.aggregate == OK:
            lines.append(f"fleet-parity: PASS — every dimension measured and holding ({len(self.verdicts)})")
        elif self.aggregate == FAIL:
            named = ", ".join(v.code for v in self.failures)
            lines.append(f"fleet-parity: FAIL — measured violation in: {named}")
        else:
            named = ", ".join(v.code for v in self.unassessed)
            lines.append(f"fleet-parity: CANNOT-ASSESS — no verdict may be claimed: {named}")
        return "\n".join(lines)


# --- subject accessors -------------------------------------------------------
#
# Every one of them answers None for "not readable", and NONE of them has a
# default that could be mistaken for a measurement. A missing key and an
# unreadable file are the same fact to the judge: the input is not there.


def _block(subject: dict, path: str) -> dict:
    """`_block(subject, "provocation.coverage")` — dotted, and {} when unreadable."""
    node: object = subject
    for part in path.split("."):
        if not isinstance(node, dict):
            return {}
        node = node.get(part)
    return node if isinstance(node, dict) else {}


def _text(subject: dict, path: str) -> str | None:
    """`_text(subject, "attestation.git_sha")`, None when anything is missing."""
    node: object = subject
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    if isinstance(node, str) and node.strip():
        return node.strip()
    return None


def _number(subject: dict, path: str) -> int | None:
    node: object = subject
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node if isinstance(node, int) and not isinstance(node, bool) else None


def _flag(subject: dict, path: str) -> bool | None:
    node: object = subject
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node if isinstance(node, bool) else None


def _rows(subject: dict, path: str) -> list | None:
    node: object = subject
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node if isinstance(node, list) else None


def check_name(script_path: str) -> str:
    """`scripts/check-fleet-parity.sh` -> `fleet-parity` (the discovery rule, #698)."""
    base = Path(script_path).name
    return base[len("check-"):-len(".sh")] if base.startswith("check-") and base.endswith(".sh") else base


# --- the judge: pure, one function per dimension -----------------------------


def judge_verify_output(subject: dict) -> Verdict:
    """The issue's `Verify:` output is quoted, and it carries a COMPOSITE verdict.

    Per-check lines are not a verdict: a log that ends mid-run, or a run whose
    composite line never landed, is not evidence that the gate passed — and
    quoting `shell-syntax: OK` as "the gate output" is a misquote this instrument
    made once before the rule was written down. An interrupted run is
    CANNOT-ASSESS rather than FAIL, because a run still in flight and a killed one
    are indistinguishable from here, and neither is a measured violation.
    """
    code = "verify-output"
    command = _text(subject, "verify_command")
    output = _text(subject, "verify_output")
    if command is None:
        return Verdict(code, CANNOT, detail="the issue's `Verify:` command could not be read")
    if output is None:
        return Verdict(code, CANNOT, detail=f"no recorded output of `{command}` to quote")
    line = _composite_verdict_line(output)
    if line is None:
        return Verdict(
            code,
            CANNOT,
            detail=(
                f"`{command}`: the recorded output has no COMPOSITE verdict line (only per-check lines), "
                "so the run was interrupted or is still in flight"
            ),
        )
    if re.search(r"(?:verify|GATE|result=):?\s*FAIL|\(result=FAIL\)", line):
        return Verdict(code, FAIL, evidence=f"`{command}` -> {line!r}")
    return Verdict(code, OK, evidence=f"`{command}` -> {line!r}")


def judge_attestation_sha(subject: dict) -> Verdict:
    """The gate evidence covers what MERGED — the same commit, or the same tree."""
    code = "attestation-sha"
    attested = _text(subject, "attestation.git_sha")
    merged = _text(subject, "merge.commit_sha")
    if attested is None:
        return Verdict(code, CANNOT, detail="the lane's attestation carries no readable git_sha")
    if merged is None:
        return Verdict(code, CANNOT, detail="the merged commit's sha could not be read")
    if attested == merged:
        return Verdict(code, OK, evidence=f"attestation git_sha {attested[:12]} IS the merged commit")
    match = _flag(subject, "merge.tree_match")
    if match is True:
        return Verdict(
            code,
            OK,
            evidence=(
                f"attested {attested[:12]} != merge {merged[:12]} but the TREES are identical "
                "(squash merge): the evidence covers what merged"
            ),
        )
    if match is False:
        return Verdict(
            code,
            FAIL,
            evidence=(
                f"attested {attested[:12]} is neither the merged commit {merged[:12]} nor the same "
                "tree — the gate evidence does not cover what merged"
            ),
        )
    return Verdict(
        code,
        CANNOT,
        detail=f"attested {attested[:12]} and merge {merged[:12]} differ and their trees could not be compared",
    )


def judge_gate_wired(subject: dict) -> Verdict:
    """A check script the lane DELIVERED must be discovered by the gate of record."""
    code = "gate-wired"
    added = _rows(subject, "gate.added_checks")
    discovered = _rows(subject, "gate.discovered")
    if added is None or discovered is None:
        return Verdict(code, CANNOT, detail="the delivered check set or the discovered set could not be read")
    if not added:
        return Verdict(
            code,
            CANNOT,
            detail=(
                "the subject delivered no `scripts/check-*.sh` of its own, so there is nothing to wire "
                "— this is not evidence that a delivered gate would be wired"
            ),
        )
    denylisted = set(_rows(subject, "gate.denylisted") or [])
    skipped = sorted(name for name in (check_name(p) for p in added) if name in denylisted)
    if skipped:
        return Verdict(code, FAIL, evidence=f"delivered but disabled BY NAME in the denylist: {skipped}")
    unwired = sorted(name for name in (check_name(p) for p in added) if name not in set(discovered))
    if unwired:
        return Verdict(
            code,
            FAIL,
            evidence=f"delivered but NOT discovered (present is not wired): {unwired}",
        )
    names = sorted(check_name(p) for p in added)
    return Verdict(code, OK, evidence=f"delivered and discovered: {names} ({len(discovered)} discovered in all)")


def judge_provoked(subject: dict) -> Verdict:
    """The instrument that produced this verdict is itself provoked, per dimension."""
    code = "provoked"
    coverage = _block(subject, "provocation.coverage")
    plants_run = _number(subject, "provocation.plants_run")
    if not coverage or plants_run is None:
        return Verdict(code, CANNOT, detail="the instrument's own probe coverage was not recorded")
    unprovoked = [d for d in DIMENSION_CODES if not _number(_block(subject, "provocation.coverage"), d)]
    if unprovoked:
        return Verdict(
            code,
            FAIL,
            evidence=f"no planted subject fails these dimensions, so the verdict is unfalsifiable: {unprovoked}",
        )
    return Verdict(code, OK, evidence=f"{plants_run} planted subject(s) exercised all {len(DIMENSION_CODES)} dimensions")


def judge_commit_trailer(subject: dict) -> Verdict:
    """`Refs <slug>#<n>` is the trailing paragraph — never in the subject line."""
    code = "commit-trailer"
    message = _text(subject, "commit.message")
    issue = _number(subject, "issue")
    slug = _text(subject, "repo_slug") or DEFAULT_SLUG
    if message is None:
        return Verdict(code, CANNOT, detail="the lane's commit message could not be read")
    if issue is None:
        return Verdict(code, CANNOT, detail="the subject names no issue, so no reference can be looked for")
    ref = f"Refs {slug}#{issue}"
    subject_line = message.splitlines()[0] if message.splitlines() else ""
    if ref in subject_line:
        return Verdict(code, FAIL, evidence=f"the reference is in the SUBJECT line: {subject_line[:90]!r}")
    trailing = _trailing_paragraph(message)
    if ref not in trailing:
        return Verdict(
            code,
            FAIL,
            evidence=(
                f"{ref!r} is not in the trailing paragraph — the block found there was "
                f"{_first_line(trailing)[:90]!r}"
            ),
        )
    sha = (_text(subject, "commit.sha") or "")[:12]
    return Verdict(code, OK, evidence=f"the trailing paragraph of {sha} carries {ref!r}")


def judge_rc_tool(subject: dict, code: str) -> Verdict:
    """A dimension judged by RUNNING one of the repo's own checkers.

    A non-zero exit is never read as a pass, and an exit code that is not one of
    the checker's own verdicts is CANNOT-ASSESS rather than a violation: "the tool
    could not answer" and "the tool answered no" are different facts, and
    conflating them either hides a violation or invents one.
    """
    command, label = RC_TOOLS[code]
    rc = _number(subject, f"{code}.rc")
    output = _text(subject, f"{code}.output")
    if rc is None:
        detail = f"`{label}` produced no exit code"
        if output is not None:
            detail = f"{detail}: {_first_line(output)[:150]}"
        return Verdict(code, CANNOT, detail=detail)
    if rc == 0:
        return Verdict(code, OK, evidence=f"`{label}` -> rc 0; {_first_line(output or '(no output)')[:80]!r}")
    if rc == CANNOT_ASSESS_RC:
        return Verdict(code, CANNOT, detail=f"`{label}` reported CANNOT-ASSESS: {_first_line(output or '')[:110]!r}")
    if rc == 1:
        return Verdict(code, FAIL, evidence=f"`{label}` -> rc 1: {_first_line(output or '(no output)')[:110]!r}")
    return Verdict(code, CANNOT, detail=f"`{label}` exited {rc}, which is not one of its verdicts")


def judge_pr_contract(subject: dict) -> Verdict:
    """The lane's PR body satisfies `scripts/check-pr-contract.sh`."""
    return judge_rc_tool(subject, "pr-contract")


def judge_isolation_audit(subject: dict) -> Verdict:
    """The session was isolated, per `governance/isolation/cli.py audit`."""
    return judge_rc_tool(subject, "isolation-audit")


def judge_branch(subject: dict) -> Verdict:
    """The lane's branch was pushed (AO-GR-23) and is gone once the work landed.

    The two halves are owed at different times, and conflating them makes an
    in-flight lane read as a violation: a lane whose branch is pushed and still on
    the remote is exactly right UNTIL its change lands, and the deletion is owed
    only after the merge. Never-pushed is a violation at every moment (AO-GR-23).
    """
    code = "branch-pushed-then-deleted"
    name = _text(subject, "branch.name") or "(unnamed)"
    pushed = _flag(subject, "branch.pushed")
    remote = _flag(subject, "branch.remote_exists")
    merged = _flag(subject, "merge.merged")
    if pushed is None or remote is None:
        return Verdict(code, CANNOT, detail=f"the push/deletion state of {name} could not be read")
    if pushed is not True:
        return Verdict(
            code,
            FAIL,
            evidence=f"{name} was never pushed — a commit that exists only locally is not work (AO-GR-23)",
        )
    if remote is True and merged is not True:
        return Verdict(
            code,
            CANNOT,
            detail=(
                f"{name} is pushed and still on the remote, which is correct until the change lands — "
                "the deletion is not yet owed, so this is not a verdict"
            ),
        )
    if remote is True:
        return Verdict(
            code,
            FAIL,
            evidence=f"{name} still exists on the remote — the source branch was not deleted at closure",
        )
    return Verdict(code, OK, evidence=f"{name} was pushed and is deleted on the remote (no ref remains)")


def judge_issue_closed(subject: dict) -> Verdict:
    """The issue is closed, and a closing comment carries evidence — not an assertion.

    Closure is owed only once the change has LANDED (the model's own
    `owes_closure`, #821): an issue still open on an unmerged lane is normal, not a
    violation, so it is CANNOT-ASSESS rather than FAIL. Once a pull request is
    merged, an issue that is still open — or closed with an assertion instead of
    evidence — is the violation.
    """
    code = "issue-closed-with-evidence"
    merged = _flag(subject, "merge.merged")
    state = _text(subject, "issue_state.state")
    comments = _rows(subject, "issue_state.comments")
    if merged is not True:
        return Verdict(
            code,
            CANNOT,
            detail="no merged pull request, so the change has not landed and closure is not yet owed",
        )
    if state is None:
        return Verdict(code, CANNOT, detail="the issue's state could not be read")
    if state.upper() != "CLOSED":
        return Verdict(code, FAIL, evidence=f"the change landed but the issue is {state.upper()}, not CLOSED")
    if comments is None:
        return Verdict(code, CANNOT, detail="the issue's comments could not be read, so 'closed with evidence' is unassessed")
    for comment in comments:
        body = comment.get("body") if isinstance(comment, dict) else None
        if isinstance(body, str) and _carries_evidence(body):
            return Verdict(code, OK, evidence=f"closed, and a closing comment carries evidence: {body.strip()[:80]!r}")
    return Verdict(
        code,
        FAIL,
        evidence=f"closed with {len(comments)} comment(s), none carrying a command and a verdict — an assertion is not evidence",
    )


#: The shapes a closing comment must carry to count as evidence: a command (the
#: backticked or fenced form the PR contract already requires) AND a verdict token.
_EVIDENCE_VERDICT = re.compile(r"verify:\s*(?:PASS|FAIL)|:\s*(?:OK|NOT-OK)\b|\bPASS\b|\bFAIL\b")


def _carries_evidence(body: str) -> bool:
    has_command = "`" in body or "```" in body
    return has_command and bool(_EVIDENCE_VERDICT.search(body))


def judge_measurement(others: list[Verdict]) -> Verdict:
    """The meta-rule: no claim without its measurement, and a grid that is complete.

    Judged over the OTHER dimensions, because that is what it is about: it is
    satisfied by the shape of the grid, not by a fact of its own.
    """
    code = "measurement"
    seen = [v.code for v in others]
    # Every dimension EXCEPT this one: the grid is complete when each of the other
    # nine is present exactly once, and this verdict is the tenth.
    missing = [d for d in DIMENSION_CODES if d != code and d not in seen]
    if missing:
        return Verdict(code, FAIL, evidence=f"the grid omits dimension(s) entirely: {missing}")
    unmeasured = [v.code for v in others if v.status == OK and not v.evidence.strip()]
    unnamed = [v.code for v in others if v.status == CANNOT and not v.detail.strip()]
    if unmeasured:
        return Verdict(code, FAIL, evidence=f"stated OK with no measurement: {unmeasured}")
    if unnamed:
        return Verdict(code, FAIL, evidence=f"reported CANNOT-ASSESS without naming the unreadable input: {unnamed}")
    holds = len([v for v in others if v.status == OK])
    unassessed = len([v for v in others if v.status == CANNOT])
    return Verdict(
        code,
        OK,
        evidence=(
            f"{holds} of {len(others)} dimension(s) measured and holding; {unassessed} CANNOT-ASSESS, "
            "each naming the input that could not be read"
        ),
    )


def judge(subject: dict) -> Report:
    """Judge a subject. Pure: no git, no network, no filesystem."""
    verdicts = [
        judge_verify_output(subject),
        judge_attestation_sha(subject),
        judge_gate_wired(subject),
        judge_provoked(subject),
        judge_commit_trailer(subject),
        judge_pr_contract(subject),
        judge_isolation_audit(subject),
        judge_branch(subject),
        judge_issue_closed(subject),
    ]
    verdicts.append(judge_measurement(verdicts))
    return Report(issue=_number(subject, "issue"), verdicts=verdicts)


# --- the probes: every dimension planted, and its twin ------------------------


def _set(node: dict, path: str, value: object) -> None:
    """Set a dotted path in a subject copy — how a plant mutates ONE field."""
    parts = path.split(".")
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _raw(node: object, path: str) -> object:
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def probe_suite() -> tuple[list[str], dict[str, int], int]:
    """Run every planted subject; return (problems, coverage, plants_run).

    A plant is a ONE-FIELD mutation of `plants/healthy.json` plus the dimension it
    must move and the verdict it must produce. Two rules keep the proof honest:

    * the mutation must be ASSERTED to have landed (the mutated value must differ
      from the base), because a mutation that never applied would let the caller
      report "the instrument caught it" when nothing was ever planted;
    * the healthy subject is the negative control for the whole suite — a judge
      that failed everything would satisfy every failing plant. So each plant is
      ALSO required to leave the healthy subject's own verdict for that dimension
      at OK.
    """
    problems: list[str] = []
    coverage: dict[str, int] = {d: 0 for d in DIMENSION_CODES}
    base_path = PLANTS_DIR / "healthy.json"
    plants_path = PLANTS_DIR / "plants.json"
    if not base_path.exists():
        return [f"{base_path} is missing — the plants have no negative control"], coverage, 0
    if not plants_path.exists():
        return [f"{plants_path} is missing — nothing is provoked"], coverage, 0
    plants = json.loads(plants_path.read_text(encoding="utf-8")).get("plants") or []
    if not plants:
        return [f"{plants_path} declares no planted subject — nothing is provoked"], coverage, 0

    healthy = json.loads(base_path.read_text(encoding="utf-8"))["subject"]
    base = judge(healthy)
    if base.aggregate != OK:
        bad = [f"{v.code}={v.status}" for v in base.verdicts if v.status != OK]
        problems.append(f"the healthy subject must PASS every dimension; it reported {bad}")

    # The meta-rule's own probe: the SHAPE must refuse an unmeasured OK.
    try:
        Verdict("measurement", OK, "   ")
        problems.append(
            "an OK verdict with no evidence was constructible — 'no claim without measurement' is a "
            "convention here, not a rule"
        )
    except Unmeasured:
        coverage["measurement"] += 1

    ran = 0
    for plant in plants:
        name = plant.get("name") or "(unnamed plant)"
        code = plant.get("dimension")
        expect = plant.get("expect")
        mutate = plant.get("mutate")
        if code not in DIMENSION_CODES:
            problems.append(f"{name}: names dimension {code!r}, which is not one of {DIMENSION_CODES}")
            continue
        if expect not in (FAIL, CANNOT):
            problems.append(f"{name}: expect must be {FAIL} or {CANNOT}, not {expect!r}")
            continue
        if not isinstance(mutate, dict) or not mutate:
            problems.append(f"{name}: carries no mutation, so it plants nothing")
            continue
        subject = json.loads(json.dumps(healthy))
        landed = True
        for pointer, value in mutate.items():
            if _raw(subject, pointer) == value:
                problems.append(f"{name}: the mutation of {pointer!r} did not land (the value was already {value!r})")
                landed = False
            _set(subject, pointer, value)
        if not landed:
            continue
        ran += 1
        observed = {v.code: v.status for v in judge(subject).verdicts}
        if observed.get(code) != expect:
            problems.append(
                f"{name}: {code} should be {expect} for this subject but the instrument said "
                f"{observed.get(code)} — {plant.get('why', '')}"
            )
            continue
        if {v.code: v.status for v in base.verdicts}.get(code) != OK:
            problems.append(f"{name}: the SAME dimension is not OK on the healthy subject — the plant proves nothing")
            continue
        coverage[code] += 1

    for code in DIMENSION_CODES:
        if coverage[code] == 0:
            problems.append(f"dimension {code} has no planted subject that moves it — it is unprovoked")
    return problems, coverage, ran


def self_test() -> int:
    """The probe suite alone — what `make verify` runs."""
    problems, coverage, ran = probe_suite()
    print("== fleet-parity: the instrument, provoked ==")
    for code in DIMENSION_CODES:
        mark = "OK  " if coverage[code] else "FAIL"
        print(f"  {mark}  {code:<28} {coverage[code]} planted subject(s)")
    print(f"  plants run: {ran}")
    if problems:
        print("")
        for problem in problems:
            print(f"  FAIL  {problem}", file=sys.stderr)
        print(f"fleet-parity: FAIL — the instrument is not provoked ({len(problems)} problem(s))", file=sys.stderr)
        return 1
    print("")
    print(f"fleet-parity: OK — every one of the {len(DIMENSION_CODES)} dimension(s) is provable, and the healthy subject clears them all")
    return 0


# --- collect: the impure half, confined here ---------------------------------


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 120) -> tuple[int | None, str]:
    try:
        done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def _gh(slug: str, path: str) -> object | None:
    rc, out = _run(["gh", "api", f"repos/{slug}/{path}"])
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _composite_verdict_line(output: str) -> str | None:
    """The GATE's own verdict line — never a per-check line.

    Two forms are accepted and both are real artifacts: `verify: PASS|FAIL…` / 
    `GATE: PASS|FAIL` from the gate's stdout, and the attestation projection
    `attestation(<path>): result=PASS checks=N skipped=K`, which the collector
    renders from the gate's own signed record (the rendering names its source, so
    the quote is traceable rather than synthesised).
    """
    for line in (line.strip() for line in output.splitlines()):
        if re.match(r"^(?:verify|GATE): (?:PASS|FAIL)\b", line):
            return line
        if re.match(r"^attestation\([^)]*\): result=(?:PASS|FAIL)\b", line):
            return line
    return None


def _verdict_line(output: str) -> str | None:
    """Any verdict-shaped line: the composite first, then a per-check line.

    Used when COLLECTING the recorded lines (a run's per-check lines are part of
    the record), never when JUDGING: see `_composite_verdict_line`.
    """
    composite = _composite_verdict_line(output)
    if composite is not None:
        return composite
    for line in (line.strip() for line in output.splitlines()):
        if re.match(r"^[a-z0-9-]+: (?:OK|FAIL|NOT-OK)\b", line):
            return line
    return None


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _trailing_paragraph(message: str) -> str:
    paragraphs = [p for p in message.rstrip("\n").split("\n\n")]
    return paragraphs[-1] if paragraphs else ""


def _tree_of(sha: str, cwd: Path) -> str | None:
    rc, out = _run(["git", "rev-parse", f"{sha}^{{tree}}"], cwd=cwd)
    return out.strip() if rc == 0 and out.strip() else None


def _verify_command(body: str | None) -> str | None:
    """The issue's `Verify:` commands, from its `## Verify` section.

    Two shapes are in use on this board and both are read: a fenced block
    (`bash scripts/check-X.sh && make verify`) and a bullet list of inline-code
    commands. An absent section yields None — never a default, because a
    defaulted command would make unrelated output look like the issue's own.
    """
    if not body:
        return None
    section = re.search(r"^#+\s*Verify\b[^\n]*\n(.*?)(?=^#+\s|\Z)", body, re.MULTILINE | re.DOTALL)
    if not section:
        return None
    fenced = re.findall(r"```[a-z]*\n(.*?)```", section.group(1), re.DOTALL)
    if fenced:
        return " && ".join(line.strip() for block in fenced for line in block.splitlines() if line.strip())
    inline = re.findall(r"`([^`\n]+)`", section.group(1))
    if inline:
        return " && ".join(part.strip() for part in inline)
    for line in section.group(1).splitlines():
        if line.strip():
            return line.strip()
    return None


def _pull_candidates(slug: str, issue: int) -> list[int]:
    """Every PR cross-referenced from the issue's timeline, oldest first."""
    numbers: list[int] = []
    for event in _gh(slug, f"issues/{issue}/timeline") or []:
        if not isinstance(event, dict) or event.get("event") != "cross-referenced":
            continue
        source = (event.get("source") or {}).get("issue") or {}
        if "pull_request" not in source:
            continue
        number = source.get("number")
        if isinstance(number, int) and number not in numbers:
            numbers.append(number)
    return numbers


def _pull_for_issue(slug: str, issue: int, pr: int | None = None) -> tuple[dict | None, str]:
    """The PR that CARRIED the issue's lane, and the BASIS it was chosen on.

    A timeline lists every PR that merely mentions the issue — measured on #649:
    eight of them, seven belonging to other lanes — so "the first cross-referenced
    PR" is the wrong answer, and it was this collector's first answer, which
    scored #649 against the branch of #651. The lane BRANCH is the discriminator:
    rule 15 mints `issue-<n>` for the lane that owns the issue, so the PR whose
    head ref names the issue is the one being scored. Failing that, the PR whose
    body says `Closes #<n>`. The basis is RETURNED, and printed with the verdict,
    so a reader can see the footing the judgement stands on instead of guessing.
    """
    if pr is not None:
        return _gh(slug, f"pulls/{pr}"), f"--pr {pr}"
    candidates = _pull_candidates(slug, issue)
    pulls: list[tuple[dict, int]] = []
    for number in candidates:
        pull = _gh(slug, f"pulls/{number}")
        if isinstance(pull, dict):
            pulls.append((pull, number))
    if not pulls:
        return None, "no cross-referenced pull request"
    for pull, number in pulls:
        head_ref = ((pull.get("head") or {}).get("ref")) or ""
        if head_ref == f"issue-{issue}" or head_ref.startswith(f"issue-{issue}-"):
            return pull, f"PR #{number}: head branch {head_ref} names the issue (rule 15)"
    closing = re.compile(rf"\b(?:closes|fixes|resolves)\s+#{issue}\b", re.IGNORECASE)
    for pull, number in pulls:
        blob = f"{pull.get('title') or ''}\n{pull.get('body') or ''}"
        if closing.search(blob):
            return pull, f"PR #{number}: its body says it closes #{issue}"
    return None, f"none of {len(pulls)} cross-referenced pull request(s) names or closes #{issue}"


def _main_worktree(repo_root: Path) -> Path:
    """The primary checkout, not a lane worktree — where the lane registry lives."""
    rc, out = _run(["git", "worktree", "list", "--porcelain"], cwd=repo_root)
    if rc == 0:
        for line in out.splitlines():
            if line.startswith("worktree "):
                return Path(line[len("worktree "):].strip())
    return repo_root


def _gh_list(slug: str, path: str) -> list | None:
    """A paginated GitHub list, read ONE OBJECT PER LINE.

    `gh api --paginate --jq '.[]'` is used deliberately: a bare `--paginate` with a
    `--jq` filter that builds an array applies the filter PER PAGE and concatenates
    the results into invalid JSON, and a bare `--paginate` without `--jq`
    concatenates raw arrays the same way. One object per line has neither problem,
    and it matters here — measured: PR #851 carries 42 files and a single
    unpaginated call returns 30, which silently hid the `scripts/check-*.sh` the
    lane delivered and would have made the wiring dimension unassessable.
    """
    rc, out = _run(["gh", "api", "--paginate", f"repos/{slug}/{path}", "--jq", ".[]"])
    if rc != 0:
        return None
    rows: list = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            return None
    return rows


def _lane_candidates(issue: int, worktrees_root: Path) -> list[tuple[Path, str, dict | None]]:
    """Every lane worktree for the issue, with its branch and its attestation."""
    found: list[tuple[Path, str, dict | None]] = []
    if not worktrees_root.exists():
        return found
    for candidate in sorted(worktrees_root.glob(f"ao-{issue}-*")):
        if not (candidate / ".git").exists():
            continue
        rc, branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=candidate)
        attestation = None
        if (candidate / ".verify" / "attestation.json").exists():
            try:
                attestation = json.loads((candidate / ".verify" / "attestation.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                attestation = None
        found.append((candidate, branch.strip() if rc == 0 else "", attestation))
    return found


def _select_lane(
    issue: int, worktrees_root: Path, merge_sha: str | None, repo_root: Path
) -> tuple[Path | None, dict | None, str]:
    """The lane whose EVIDENCE covers what merged, and the basis it was chosen on.

    A lane is a worktree, and an issue can have several of them (#649 has a
    pre-merge lane and a post-merge one) — so choosing "the first directory"
    decides the verdict by accident, and it did: it scored #649's pre-merge
    attestation against the squash-merged commit and reported a violation. The
    rule below prefers the evidence that COVERS THE MERGE, then the same tree,
    then the lane whose branch names the issue, and RETURNS the basis so the
    report prints how the subject was chosen instead of hiding it.
    """
    candidates = _lane_candidates(issue, worktrees_root)
    if not candidates:
        return None, None, f"no lane worktree under {worktrees_root} matches ao-{issue}-*"
    if merge_sha:
        for candidate, branch, attestation in candidates:
            if attestation and attestation.get("git_sha") == merge_sha:
                return candidate, attestation, f"its attestation IS the merged commit {merge_sha[:12]}"
        merge_tree = _tree_of(merge_sha, repo_root)
        for candidate, branch, attestation in candidates:
            if not attestation or not attestation.get("git_sha"):
                continue
            if merge_tree and _tree_of(str(attestation["git_sha"]), repo_root) == merge_tree:
                return candidate, attestation, f"its attestation shares the merged commit's tree ({merge_sha[:12]})"
    for candidate, branch, attestation in candidates:
        if branch == f"issue-{issue}" or branch.startswith(f"issue-{issue}-"):
            return candidate, attestation, f"its branch {branch} names the issue"
    candidate, branch, attestation = candidates[0]
    return candidate, attestation, f"the only candidate ({branch or 'detached'}), and no branch names the issue"


def _lane_session(main: Path, lane: Path | None, issue: int) -> tuple[str | None, str]:
    """The isolation session that owns a lane, from the registry — or why it is unknown."""
    rc, out = _run(["python3", "governance/isolation/cli.py", "list", "--main", str(main)], cwd=main)
    if rc != 0:
        return None, "`governance/isolation/cli.py list` could not be read"
    rows = []
    for line in out.splitlines():
        match = re.match(r"^\s*([0-9a-f]{6,})\s+(#\d+|-)\s+(\S+)\s+(\S+)\s+(/\S+)\s*$", line)
        if match:
            rows.append(match.groups())
    if not rows:
        return None, "the isolation registry lists no lane"
    if lane is not None:
        for session, _issue, _branch, _agent, worktree in rows:
            if Path(worktree) == lane:
                return session, f"registry maps {lane} to session {session}"
    for session, issue_field, _branch, _agent, _worktree in rows:
        if issue_field.lstrip("#") == str(issue):
            return session, f"registry maps #{issue} to session {session}"
    return None, (
        f"no lane record names #{issue} or its worktree, and the audit's other findings belong to "
        f"{len(rows)} unrelated lane(s)"
    )


def collect(repo_root: Path, issue: int, *, worktrees_root: Path | None = None, pr: int | None = None, slug: str = DEFAULT_SLUG) -> dict:
    """Build a subject from the real artifacts. Every failure yields None, never a default."""
    subject: dict = {"issue": issue, "repo_slug": slug}
    worktrees_root = worktrees_root or Path(os.environ.get("AO_WORKTREES_ROOT", Path.home() / "ao-worktrees"))
    main = _main_worktree(repo_root)

    issue_api = _gh(slug, f"issues/{issue}") or {}
    body = issue_api.get("body") if isinstance(issue_api, dict) else None
    subject["verify_command"] = _verify_command(body if isinstance(body, str) else None)

    pull, pr_basis = _pull_for_issue(slug, issue, pr)
    merge_sha = pull.get("merge_commit_sha") if isinstance(pull, dict) else None
    lane, attestation, lane_basis = _select_lane(issue, worktrees_root, merge_sha, repo_root)
    subject["lane_worktree"] = str(lane) if lane else None
    subject["attestation"] = attestation
    subject["subject_basis"] = {"pr": pr_basis, "lane": lane_basis}

    recorded = ""
    if lane is not None and (lane / ".verify" / "verify.log").exists():
        try:
            recorded = (lane / ".verify" / "verify.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            recorded = ""
    recorded_lines = [line for line in recorded.splitlines() if line.strip() and _verdict_line(line)]
    quoted = []
    if isinstance(attestation, dict) and attestation.get("result"):
        quoted.append(
            f"attestation({lane}/.verify/attestation.json): result={attestation['result']} "
            f"checks={attestation.get('check_count')} skipped={attestation.get('skipped')}"
        )
    quoted += recorded_lines
    subject["verify_output"] = "\n".join(quoted) if quoted else None

    subject["merge"] = None
    subject["commit"] = None
    subject["branch"] = None
    if isinstance(pull, dict):
        head = ((pull.get("head") or {}).get("sha")) or None
        subject["merge"] = {
            "pr": pull.get("number"),
            "merged": bool(pull.get("merged")),
            "commit_sha": merge_sha,
            "tree_match": None,
        }
        if merge_sha and attestation and attestation.get("git_sha"):
            attested = str(attestation["git_sha"])
            if attested == merge_sha:
                subject["merge"]["tree_match"] = True
            else:
                left, right = _tree_of(attested, repo_root), _tree_of(merge_sha, repo_root)
                if left and right:
                    subject["merge"]["tree_match"] = left == right
        head_ref = ((pull.get("head") or {}).get("ref")) or None
        remote_exists = None
        if head_ref:
            rc, _out = _run(["gh", "api", f"repos/{slug}/git/ref/heads/{head_ref}"])
            remote_exists = rc == 0
        # "pushed" is MEASURED, not assumed: a commit the GitHub API reads back is a
        # commit that reached the remote (AO-GR-23's whole point is that a commit
        # existing only locally is invisible to the board).
        pushed = _gh(slug, f"commits/{head}") is not None if head else None
        subject["branch"] = {"name": head_ref, "pushed": pushed, "remote_exists": remote_exists}
        if head:
            commit = _gh(slug, f"commits/{head}") or {}
            message = ((commit.get("commit") or {}).get("message")) if isinstance(commit, dict) else None
            subject["commit"] = {"sha": head, "message": message}

    files = _gh_list(slug, f"pulls/{pull['number']}/files") if isinstance(pull, dict) else None
    added = None
    if files is not None:
        added = [str(row.get("filename")) for row in files if isinstance(row, dict)
                 and str(row.get("filename", "")).startswith("scripts/check-")]
    discovered = _check_names(repo_root)
    denylist = _denylist(repo_root)
    subject["gate"] = {
        "added_checks": added,
        "discovered": sorted(discovered) if discovered is not None else None,
        "denylisted": sorted(denylist) if denylist is not None else None,
    }

    rc, out = _run(
        ["bash", "scripts/check-pr-contract.sh", "--pr", str(pull["number"])] if isinstance(pull, dict)
        else ["false"],
        cwd=repo_root,
    )
    if isinstance(pull, dict):
        subject["pr-contract"] = {"rc": rc, "output": out}
    else:
        # Checking a PR that does not exist would return the checker's own failure,
        # and reporting THAT as the subject's violation is a false positive (measured:
        # an in-flight lane scored FAIL here with an empty output).
        subject["pr-contract"] = {"rc": None, "output": f"no pull request to check: {pr_basis}"}

    session, session_basis = _lane_session(main, lane, issue)
    subject["isolation_session"] = session
    if session is None:
        # Scoping is what makes this dimension about the SUBJECT. An unscoped audit
        # reports findings that belong to other lanes, and charging them to this one
        # would be a false positive — so an unscopable audit is CANNOT-ASSESS.
        subject["isolation-audit"] = {"rc": None, "output": f"cannot scope the audit: {session_basis}"}
    else:
        rc, out = _run(
            ["python3", "governance/isolation/cli.py", "audit", "--main", str(main), "--session", session],
            cwd=repo_root,
        )
        subject["isolation-audit"] = {"rc": rc, "output": f"scoped to session {session}: {out.strip()[:400]}"}

    state = issue_api.get("state") if isinstance(issue_api, dict) else None
    comments = _gh_list(slug, f"issues/{issue}/comments")
    subject["issue_state"] = {
        "state": state,
        "comments": [{"body": c.get("body")} for c in comments if isinstance(c, dict)] if comments is not None else None,
    }

    _problems, coverage, ran = probe_suite()
    subject["provocation"] = {"coverage": coverage, "plants_run": ran}
    return subject


def _check_names(repo_root: Path) -> list[str] | None:
    """The discovered check names, from the SAME discovery layer `make verify` uses."""
    rc, out = _run(["bash", "-c", "source scripts/discover-checks.sh; discover_check_scripts"], cwd=repo_root)
    if rc != 0:
        return None
    return [line.split("|")[0] for line in out.splitlines() if "|" in line]


def _denylist(repo_root: Path) -> list[str] | None:
    path = repo_root / "scripts" / "check-denylist.txt"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


# --- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fleet-parity", description=__doc__.splitlines()[0])
    parser.add_argument("--issue", type=int, default=None, help="the issue whose lane is assessed")
    parser.add_argument("--pr", type=int, default=None, help="the pull request, when it is not discoverable")
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="the checkout to measure against")
    parser.add_argument("--worktrees-root", default=None, help="where the lane worktrees live")
    parser.add_argument("--slug", default=DEFAULT_SLUG)
    parser.add_argument("--subject-file", default=None, help="judge a recorded subject, offline")
    parser.add_argument("--collect-only", action="store_true", help="write the subject as JSON and stop")
    args = parser.parse_args(argv)

    if args.subject_file:
        report = judge(json.loads(Path(args.subject_file).read_text(encoding="utf-8")))
        print(report.render())
        return report.exit_code

    if args.issue is None:
        return self_test()

    subject = collect(
        Path(args.repo_root),
        args.issue,
        worktrees_root=Path(args.worktrees_root) if args.worktrees_root else None,
        pr=args.pr,
        slug=args.slug,
    )
    if args.collect_only:
        print(json.dumps(subject, indent=2, sort_keys=True))
        return 0
    print(subject_json_note(subject))
    report = judge(subject)
    print(report.render())
    return report.exit_code


def subject_json_note(subject: dict) -> str:
    """One line naming what was read and how the PR was chosen.

    A verdict is only as good as the subject behind it, so every CANNOT-ASSESS is
    diagnosable from the report alone: the lane it measured, whether the gate
    evidence was readable, and the BASIS on which the pull request was selected
    (rather than the reader having to guess which of the issue's many
    cross-referenced PRs was scored).
    """
    lane = subject.get("lane_worktree") or "(no lane worktree found)"
    attestation = "read" if subject.get("attestation") else "ABSENT"
    basis = subject.get("subject_basis") or {}
    chosen = basis.get("pr") or "(unresolved)"
    why_lane = basis.get("lane") or "(unresolved)"
    session = subject.get("isolation_session") or "not in the registry"
    return (
        f"  (subject: lane={lane} [{why_lane}] attestation={attestation}\n"
        f"            pr: {chosen}\n"
        f"            isolation session: {session})"
    )


if __name__ == "__main__":
    raise SystemExit(main())
