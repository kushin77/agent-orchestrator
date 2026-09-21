"""Artifact round trips — the frozen shapes, the ledger and the live projection.

---knowledge---
module_id: governance.tagging.artifacts
system: governance
app: tagging
solution_class: enterprise
patterns: [provoked-negative-control, append-only-ledger, honesty-tri-state]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [main]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

The fourth check in `scripts/check-tagging.sh` provokes the taxonomy's refusals.
This module provokes the *artifacts*: the frozen shape file, the append-only
ledger and the live projection. The distinction matters, because these three can
each pass while doing nothing at all —

* a **schema** nothing validates against is a decoration;
* a **ledger** that accepts any row, or cannot be read past its first bad line,
  is not an audit trail;
* a **projection** that reports zero refusals because it read zero issues is
  indistinguishable from a clean board.

So each is driven with its clean twin and its provoked half: a malformed ledger
row must be REFUSED before the write, a hand-written garbage line must be
REPORTED rather than raised, and a projection over a fixture must name the exact
item it refuses while the all-clean fixture is refused nothing.

    python3 governance/tagging/artifacts.py

Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS — the repository's tri-state.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger as L  # noqa: E402
import live as LV  # noqa: E402
import model as M  # noqa: E402
import policy as P  # noqa: E402
import schema as S  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TAXONOMY = ROOT / "governance" / "tagging" / "taxonomy.yaml"
RULES = ROOT / "governance" / "tagging" / "rules.yaml"
CONTROLS = ROOT / "governance" / "tagging" / "controls.yaml"

OK = 0
NOT_OK = 1
CANNOT_ASSESS = 2

WELL_TAGGED = {
    "number": 1,
    "state": "open",
    "title": "a well-tagged item",
    "labels": [
        {"name": "class:enterprise"},
        {"name": "type:feature"},
        {"name": "priority:P1"},
        {"name": "area:standards"},
        {"name": "posture:iac"},
        {"name": "lifecycle:build"},
    ],
}
MIS_TAGGED = {
    "number": 2,
    "state": "open",
    "title": "an item carrying a contradiction",
    "labels": [
        {"name": "class:elite"},
        {"name": "type:bug"},
        {"name": "priority:P0"},
        {"name": "area:fleet"},
        {"name": "posture:no-human-needed"},
        {"name": "posture:human-gated"},
    ],
}
UNTAGGED = {"number": 3, "state": "open", "title": "no tags at all", "labels": []}
CLOSED = {
    "number": 4,
    "state": "closed",
    "title": "closed, so out of scope",
    "labels": [{"name": "posture:magic"}],
}
UNDECLARED = {
    "number": 5,
    "state": "open",
    "title": "a dimension the taxonomy does not declare",
    "labels": [
        {"name": "class:enterprise"},
        {"name": "type:feature"},
        {"name": "priority:P2"},
        {"name": "area:standards"},
        {"name": "vibe:elite"},
    ],
}


def _snapshot(path: Path, issues: Sequence[Dict]) -> Path:
    path.write_text(
        json.dumps(
            {"generated_at": "2099-01-01T00:00:00Z", "issues": list(issues)},
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def main(argv: Sequence[str] | None = None) -> int:
    checks: List[Tuple[str, Callable[[], str]]] = []

    def _shapes() -> str:
        declared = set(S.shapes())
        expected = {"taxonomy", "rules", "controls", "ledger_row"}
        if declared != expected:
            raise AssertionError(
                "the schema declares %s, expected %s"
                % (sorted(declared), sorted(expected))
            )
        return "%d shape(s): %s" % (len(declared), ", ".join(sorted(declared)))

    checks.append(("schema-shapes", _shapes))

    scratch = Path(tempfile.mkdtemp(prefix="ao1175-artifacts."))
    ledger_file = scratch / "ledger.jsonl"
    try:

        def _ledger_round_trip() -> str:
            for index, kind in enumerate(("plan", "check", "board"), start=1):
                L.append(
                    L.row(
                        kind,
                        "subject-%d" % index,
                        "ok",
                        exit_code=0,
                        tags=["class:elite"],
                        gates=index,
                    ),
                    ledger_file,
                )
            rows, malformed = L.read(ledger_file)
            if malformed:
                raise AssertionError("a well-formed ledger reported %s" % (malformed,))
            if len(rows) != 3:
                raise AssertionError("wrote 3 rows, read back %d" % len(rows))
            summary = L.summarise(ledger_file)
            if summary["by_kind"] != {"board": 1, "check": 1, "plan": 1}:
                raise AssertionError("by_kind is %s" % summary["by_kind"])
            return "3 rows written, 3 read back, 0 malformed"

        checks.append(("ledger-round-trip", _ledger_round_trip))

        def _ledger_refuses_a_bad_row() -> str:
            refused = 0
            for bad in (
                {"at": "x", "kind": "plan", "subject": "s", "decision": "maybe"},
                {"at": "x", "kind": "nope", "subject": "s", "decision": "ok"},
                {"at": "x", "kind": "plan", "subject": "", "decision": "ok"},
                {"at": "x", "kind": "plan", "subject": "s", "decision": "ok", "extra": 1},
            ):
                try:
                    L.append(bad, scratch / "refused.jsonl")
                except L.LedgerRefused:
                    refused += 1
            if refused != 4:
                raise AssertionError(
                    "only %d of 4 malformed rows were refused (the shape is not frozen)"
                    % refused
                )
            if (scratch / "refused.jsonl").exists():
                raise AssertionError("a refused row was written anyway")
            # The clean twin: a legal row is accepted by the same code path.
            L.append(
                L.row("plan", "clean-twin", "ok", exit_code=0), scratch / "clean.jsonl"
            )
            return "4 malformed rows refused, clean twin accepted"

        checks.append(("ledger-refuses-bad-rows", _ledger_refuses_a_bad_row))

        def _ledger_reads_past_garbage() -> str:
            messy = scratch / "messy.jsonl"
            messy.write_text(
                "not json at all\n"
                + json.dumps(L.row("plan", "good", "ok", exit_code=0))
                + "\n"
                + json.dumps({"at": "x", "kind": "plan", "subject": "s"})  # missing decision
                + "\n",
                encoding="utf-8",
            )
            rows, malformed = L.read(messy)
            if len(rows) != 1:
                raise AssertionError("expected 1 readable row, got %d" % len(rows))
            if len(malformed) != 2:
                raise AssertionError("expected 2 malformed lines, got %s" % (malformed,))
            return "1 row read, 2 malformed lines reported by line number"

        checks.append(("ledger-reads-past-garbage", _ledger_reads_past_garbage))

        taxonomy = M.load_taxonomy(TAXONOMY)

        def _live_refuses_the_fixture() -> str:
            snapshot = _snapshot(scratch / "fixture.json", [WELL_TAGGED, MIS_TAGGED, UNTAGGED, CLOSED, UNDECLARED])
            projection = LV.project(
                ROOT, taxonomy, snapshot_path=snapshot, ledger_file=ledger_file
            )
            if projection["open_issues"] != 4:
                raise AssertionError(
                    "expected 4 open issues (the closed one is out of scope), got %d"
                    % projection["open_issues"]
                )
            if projection["tagged"] != 3 or projection["untagged"] != 1:
                raise AssertionError(
                    "coverage is tagged=%d untagged=%d, expected 3/1"
                    % (projection["tagged"], projection["untagged"])
                )
            codes = set(projection["refusals"])
            for expected in ("posture-contradiction", "unknown-dimension"):
                if expected not in codes:
                    raise AssertionError(
                        "%s was not refused — the projection read the board but judged nothing (%s)"
                        % (expected, sorted(codes))
                    )
            if "vibe" not in projection["dimensions_undeclared_but_used"]:
                raise AssertionError(
                    "an undeclared dimension in use was not reported: %s"
                    % projection["dimensions_undeclared_but_used"]
                )
            messages = " ".join(projection["refusals"]["posture-contradiction"])
            if "issue-2" not in messages:
                raise AssertionError(
                    "the refusal does not NAME the item it refused: %s" % messages
                )
            return (
                "4 open / 3 tagged / 1 untagged; refused %d item(s) by code and name"
                % projection["refusal_count"]
            )

        checks.append(("live-projects-the-fixture", _live_refuses_the_fixture))

        def _live_accepts_a_clean_board() -> str:
            snapshot = _snapshot(scratch / "clean.json", [WELL_TAGGED])
            projection = LV.project(ROOT, taxonomy, snapshot_path=snapshot)
            if projection["refusal_count"] != 0:
                raise AssertionError(
                    "a clean board was refused %d time(s): %s"
                    % (projection["refusal_count"], projection["refusals"])
                )
            if projection["untagged"] != 0:
                raise AssertionError("a fully tagged board reported untagged items")
            return "1 open / 1 tagged, refused nothing"

        checks.append(("live-accepts-a-clean-board", _live_accepts_a_clean_board))

        def _live_is_unavailable_without_a_snapshot() -> str:
            try:
                LV.project(ROOT, taxonomy, snapshot_path=scratch / "absent.json")
            except LV.LiveUnavailable:
                # The clean twin: a readable snapshot projects.
                LV.project(
                    ROOT,
                    taxonomy,
                    snapshot_path=_snapshot(scratch / "present.json", [WELL_TAGGED]),
                )
                return "absent snapshot is CANNOT-ASSESS, present one projects"
            raise AssertionError("a missing snapshot did not raise LiveUnavailable")

        checks.append(("live-unavailable-without-snapshot", _live_is_unavailable_without_a_snapshot))

        def _controls_are_read() -> str:
            taxonomy_obj = M.load_taxonomy(TAXONOMY)
            rules_obj = M.load_rules(RULES)
            document = P.load(CONTROLS)
            findings = P.check(taxonomy_obj, rules_obj, document, rules_path=RULES)
            if M.errors(findings):
                raise AssertionError(
                    "the declared controls disagree with the authority: %s"
                    % [f.message for f in findings]
                )
            # The provoked half: relax the taxonomy's required set behind the
            # control's back and require the mismatch BY NAME.
            mutant = scratch / "controls.yaml"
            text = CONTROLS.read_text(encoding="utf-8")
            mutated = text.replace("issue: [class, type, priority, area]", "issue: [class]")
            if mutated == text:
                raise AssertionError("the controls mutation did not land")
            mutant.write_text(mutated, encoding="utf-8")
            broken = P.check(taxonomy_obj, rules_obj, P.load(mutant), rules_path=RULES)
            if P.CODE_REQUIRED_MISMATCH not in {f.code for f in broken}:
                raise AssertionError(
                    "a relaxed required set was not refused by name (%s)"
                    % sorted({f.code for f in broken})
                )
            return "controls agree with the authority; a relaxed set is refused by name"

        checks.append(("controls-are-read", _controls_are_read))

        failures: List[str] = []
        for name, fn in checks:
            try:
                detail = fn()
            except AssertionError as exc:
                print("  FAIL  %-32s %s" % (name, exc))
                failures.append(name)
            except Exception as exc:  # noqa: BLE001 - a driver reports, never crashes
                print("  FAIL  %-32s unexpected %s: %s" % (name, type(exc).__name__, exc))
                failures.append(name)
            else:
                print("  PASS  %-32s %s" % (name, detail))
    except M.TaggingUnavailable as exc:
        print("tagging-artifacts: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return CANNOT_ASSESS
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if failures:
        print("tagging-artifacts: FAIL — %d artifact check(s) did not hold" % len(failures))
        return NOT_OK
    print("tagging-artifacts: PASS — %d artifact(s) driven with their clean twins" % len(checks))
    return OK


if __name__ == "__main__":
    raise SystemExit(main())
