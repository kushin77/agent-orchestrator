"""The negative controls for every refusal this package can raise (issue #650).

Issue #650's acceptance criteria require "at least one NEGATIVE control per
validator", and this repository's doctrine says why: **a validator that is never
shown to refuse is a formality.** A schema, a state machine and an SLA clock all
have a happy path, and a suite that only walks the happy path passes just as
green when the validator has been replaced by `return True`.

So every refusal in :data:`~.model.REFUSALS` — all 27 of them — is **provoked
here**, and the driver fails if any provocation is not refused, is refused with
the *wrong* code, or is refused without naming the offender. Three properties
make that worth more than a list of assertions:

* **coverage is computed, not claimed.** :func:`run` compares the provoked code
  set against ``REFUSALS`` and fails when they diverge. A new refusal added
  without a control therefore fails the driver (and the suite) instead of
  quietly shipping an unproven refusal.
* **the driver can fail.** ``tests/test_negative_control.py`` neuters a real
  validator with ``monkeypatch`` and requires ``run()`` to turn non-zero — a
  driver that cannot fail proves nothing about the controls it reports.
* **the offender must be named.** Each provocation declares the substring its
  refusal has to contain, so "something was refused" is not accepted in place of
  "the lead was refused, by its id, for not being qualified".

Run it directly with ``python3 integrations/erp/crm/negative_control.py``; it
exits 0 only when every provocation is refused by name.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, TextIO, Tuple

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from integrations.erp.crm import audit, documents, flows, provenance, schema, sla, timesheet  # noqa: E402
from integrations.erp.crm.definitions import DefinitionSet  # noqa: E402
from integrations.erp.crm.definitions import load as load_definitions  # noqa: E402
from integrations.erp.crm.model import (  # noqa: E402
    KIND_LEAD,
    REFUSALS,
    SCHEMA_VERSION,
    Document,
    Refused,
)

TENANT = "negative-control"


@dataclass(frozen=True)
class Provocation:
    """One refusal, the input that provokes it, and the offender it must name."""

    name: str
    code: str
    needle: str
    check: Callable[[], None]


def _refuse_with_finding(code: str, findings: Sequence[object]) -> None:
    """Turn an observed whole-set finding into the refusal the driver checks.

    A chain check reports rather than raises (it inspects a set), so a
    provocation that tampers with the chain has to fail *here* when nothing was
    reported — otherwise a neutered chain check would look like a provocation
    that "passed" because it raised nothing.
    """
    for finding in findings:
        if getattr(finding, "code", None) == code:
            raise Refused(code, getattr(finding, "detail", ""))
    raise AssertionError(f"no {code} finding was reported")


def provocations(base: flows.GoldenPath) -> Tuple[Provocation, ...]:
    """Every provocation, built against one pristine golden-path workspace."""
    space = base.workspace
    definitions = space.definitions

    def unsupported_keyword() -> None:
        schema.assert_supported({"type": "object", "$ref": "#/$defs/thing"}, where="scratch.schema.json")

    def broken_definitions() -> None:
        broken = definitions.to_dict()
        broken["kinds"]["lead"]["transitions"]["qualified"] = ["sprocketed"]
        load_definitions(broken)

    def broken_envelope() -> None:
        documents.parse(
            {"schemaVersion": SCHEMA_VERSION, "kind": "lead", "tenant": TENANT, "state": "new", "fields": {}},
            definitions,
            where="ENV-0001",
        )

    def undeclared_kind() -> None:
        definitions.kind("sprocket")

    def undeclared_state() -> None:
        documents.parse(
            {
                "schemaVersion": SCHEMA_VERSION,
                "kind": "lead",
                "id": "LEAD-9000",
                "tenant": TENANT,
                "state": "delivered",
                "fields": {"company": "X", "stage": "new"},
            },
            definitions,
            where="LEAD-9000",
        )

    def undeclared_field() -> None:
        flows.create_document(
            space,
            KIND_LEAD,
            "LEAD-9001",
            {"company": "X", "stage": "new", "planet": "Mars"},
            actor="nc",
            at=flows.T["opened"],
        )

    def missing_required_field() -> None:
        flows.create_document(
            space,
            KIND_LEAD,
            "LEAD-9002",
            {"stage": "new"},
            actor="nc",
            at=flows.T["opened"],
        )

    def declared_type_violation() -> None:
        flows.create_document(
            space,
            KIND_LEAD,
            "LEAD-9003",
            {"company": "X", "stage": "new", "value": "a lot"},
            actor="nc",
            at=flows.T["opened"],
        )

    def declared_range_violation() -> None:
        zero_minutes = Document(
            kind="timesheet",
            id="TS-0004",
            tenant=TENANT,
            state="submitted",
            fields={
                "project": "PROJ-0001",
                "task": "TASK-0001",
                "minutes": 0,
                "work_date": "2026-09-08",
                "rate_minor": 9000,
                "currency": "EUR",
            },
        )
        timesheet.accumulate(
            list(space.all_documents()) + [zero_minutes],
            project="PROJ-0001",
            task="TASK-0001",
            definitions=definitions,
        )

    def undeclared_vocabulary_term() -> None:
        flows.create_document(
            space,
            KIND_LEAD,
            "LEAD-9004",
            {"company": "X", "stage": "epic"},
            actor="nc",
            at=flows.T["opened"],
        )

    def undeclared_vocabulary() -> None:
        definitions.vocabulary("sprocket-stages")

    def undeclared_policy() -> None:
        stray = Document(
            kind="support-issue",
            id="ISS-9001",
            tenant=TENANT,
            state="open",
            fields={
                "priority": "p9",
                "channel": "email",
                "opened_at": flows.T["issue_open"],
            },
        )
        sla.age(stray, flows.T["issue_open"], definitions=definitions)

    def illegal_transition() -> None:
        flows.advance(space, "LEAD-0001", "qualified", actor="nc", at=flows.T["contacted"])

    def undeclared_action() -> None:
        audit.Rail().append(
            at=flows.T["opened"],
            actor="nc",
            action="launch",
            kind=KIND_LEAD,
            ref="LEAD-0001",
        )

    def duplicate_document() -> None:
        flows.create_document(
            space,
            KIND_LEAD,
            "LEAD-0001",
            {"company": "Northwind Traders", "stage": "new"},
            actor="nc",
            at=flows.T["opened"],
        )

    def missing_document() -> None:
        space.get("LEAD-4242")

    def unqualified_lead() -> None:
        # A private workspace: the golden lead has already converted, so it would
        # provoke `already-converted` and prove the wrong refusal.
        fresh = flows.workspace(TENANT, definitions)
        fresh = flows.create_document(
            fresh,
            KIND_LEAD,
            "LEAD-0001",
            {"company": "Northwind Traders", "stage": "new"},
            actor="nc",
            at=flows.T["contacted"],
        )
        fresh = flows.advance(
            fresh, "LEAD-0001", "contacted", actor="nc", at=flows.T["contacted"]
        )
        flows.convert_lead(fresh, "LEAD-0001", "OPP-9001", actor="nc", at=flows.T["contacted"])

    def converted_twice() -> None:
        flows.convert_lead(space, "LEAD-0001", "OPP-9002", actor="nc", at=flows.T["converted"])

    def closed_task() -> None:
        closed = flows.advance(
            space, "TASK-0001", "in-progress", actor="nc", at=flows.T["ts3"]
        )
        closed = flows.advance(
            closed, "TASK-0001", "done", actor="nc", at=flows.T["ts3"]
        )
        timesheet.accumulate(
            closed.all_documents(),
            project="PROJ-0001",
            task="TASK-0001",
            definitions=definitions,
        )

    def wrong_project() -> None:
        other = flows.start_project(
            space,
            "PROJ-9001",
            {"project_type": "internal", "currency": "EUR"},
            actor="nc",
            at=flows.T["project"],
        )
        other = flows.start_task(
            other,
            "TASK-9001",
            {"project": "PROJ-9001", "task_type": "review", "rate_minor": 1000},
            actor="nc",
            at=flows.T["task"],
        )
        timesheet.accumulate(
            other.all_documents(),
            project="PROJ-0001",
            task="TASK-9001",
            definitions=definitions,
        )

    def missing_task() -> None:
        timesheet.accumulate(
            space.all_documents(),
            project="PROJ-0001",
            task="TASK-9999",
            definitions=definitions,
        )

    def duplicate_entry() -> None:
        doubled = list(space.all_documents()) + [space.get("TS-0002")]
        timesheet.accumulate(
            doubled, project="PROJ-0001", task="TASK-0001", definitions=definitions
        )

    def currency_disagreement() -> None:
        stray = Document(
            kind="timesheet",
            id="TS-9001",
            tenant=TENANT,
            state="submitted",
            fields={
                "project": "PROJ-0001",
                "task": "TASK-0001",
                "minutes": 60,
                "work_date": "2026-09-08",
                "rate_minor": 9000,
                "currency": "USD",
            },
        )
        timesheet.accumulate(
            list(space.all_documents()) + [stray],
            project="PROJ-0001",
            task="TASK-0001",
            definitions=definitions,
        )

    def clock_before_open() -> None:
        sla.age(space.get("ISS-0002"), "2026-09-10T07:00:00Z", definitions=definitions)

    def unreadable_clock() -> None:
        sla.age(space.get("ISS-0002"), "yesterday", definitions=definitions)

    def tampered_chain() -> None:
        entries = [dict(entry) for entry in space.rail.to_list()]
        entries[2]["to_state"] = "sprocketed"
        rebuilt = audit.Rail.from_list(entries)
        _refuse_with_finding("audit-broken", rebuilt.verify())

    def unreadable_provenance() -> None:
        record = provenance.load().to_dict()
        record["harvests"] = []
        provenance.load(record)

    def copied_upstream_code() -> None:
        record = provenance.load().to_dict()
        record["harvests"][0]["codeCopied"] = True
        provenance.load(record)

    checks = (
        Provocation("schema keyword freeze", "unsupported-schema-keyword", "$ref", unsupported_keyword),
        Provocation("definitions graph", "definitions-invalid", "sprocketed", broken_definitions),
        Provocation("document envelope", "schema-violation", "id", broken_envelope),
        Provocation("kind lookup", "unknown-kind", "sprocket", undeclared_kind),
        Provocation("state membership", "unknown-state", "delivered", undeclared_state),
        Provocation("field membership", "unknown-field", "planet", undeclared_field),
        Provocation("required fields", "missing-field", "company", missing_required_field),
        Provocation(
            "declared field type", "invalid-value", "LEAD-9003.value", declared_type_violation
        ),
        Provocation("field range", "invalid-value", "outside 1..1440", declared_range_violation),
        Provocation(
            "vocabulary term",
            "unknown-vocabulary-term",
            "lead-stages",
            undeclared_vocabulary_term,
        ),
        Provocation(
            "vocabulary lookup", "unknown-vocabulary", "sprocket-stages", undeclared_vocabulary
        ),
        Provocation("SLA policy lookup", "unknown-policy", "p9", undeclared_policy),
        Provocation("state machine", "illegal-transition", "qualified", illegal_transition),
        Provocation("audit action vocabulary", "unknown-action", "launch", undeclared_action),
        Provocation("document identity", "duplicate-id", "LEAD-0001", duplicate_document),
        Provocation("document lookup", "unknown-document", "LEAD-4242", missing_document),
        Provocation("conversion precondition", "not-qualified", "LEAD-0001", unqualified_lead),
        Provocation("conversion once-only", "already-converted", "LEAD-0001", converted_twice),
        Provocation("closed parent", "inactive-parent", "TASK-0001", closed_task),
        Provocation("task/project membership", "wrong-project", "TASK-9001", wrong_project),
        Provocation("task lookup", "unknown-task", "TASK-9999", missing_task),
        Provocation("entry identity", "duplicate-entry", "TS-0002", duplicate_entry),
        Provocation("rollup currency", "currency-mismatch", "USD", currency_disagreement),
        Provocation("SLA clock regression", "clock-regression", "ISS-0002", clock_before_open),
        Provocation("SLA timestamp form", "invalid-timestamp", "yesterday", unreadable_clock),
        Provocation("audit chain", "audit-broken", "does not reproduce", tampered_chain),
        Provocation(
            "provenance record", "provenance-invalid", "at least one harvest", unreadable_provenance
        ),
        Provocation("GR-10 no vendoring", "code-copied", "crm-funnel", copied_upstream_code),
    )
    return checks


def covered_codes(base: Optional[flows.GoldenPath] = None) -> frozenset:
    """The refusal codes the provocations cover."""
    path = base if base is not None else flows.golden_path(TENANT)
    return frozenset(check.code for check in provocations(path))


def run(sink: Optional[TextIO] = None) -> int:
    """Provoke every refusal; return 0 only when each one is refused by name."""
    out = sink if sink is not None else sys.stdout
    failures: List[str] = []
    try:
        base = flows.golden_path(TENANT)
    except Exception as exc:  # the driver's own inability to start is NOT-OK, not a pass
        print(f"negative-control: FAIL — the golden path could not be built", file=out)
        print(f"  FAIL  {type(exc).__name__}: {exc}", file=out)
        return 1
    checks = provocations(base)

    if base.findings:
        failures.append(
            "the golden-path workspace does not satisfy its own invariants: "
            + "; ".join(f"{finding.code}: {finding.detail}" for finding in base.findings)
        )

    uncovered = sorted(REFUSALS - covered_codes(base))
    if uncovered:
        failures.append("no provocation covers: " + ", ".join(uncovered))

    for check in checks:
        try:
            check.check()
        except Refused as refusal:
            if refusal.reason != check.code:
                failures.append(
                    f"{check.name}: refused as {refusal.reason!r}, expected {check.code!r} "
                    f"({refusal.detail})"
                )
            elif check.needle and check.needle not in refusal.detail:
                failures.append(
                    f"{check.name}: a {refusal.reason} refusal was raised but does not name "
                    f"{check.needle!r}: {refusal.detail}"
                )
            else:
                print(f"  OK    {check.code} refused by name: {refusal.detail}", file=out)
        except Exception as exc:  # any other exception means the control did not hold
            failures.append(
                f"{check.name}: raised {type(exc).__name__} instead of a refusal: {exc}"
            )
        else:
            failures.append(
                f"{check.name}: NOT refused — {check.code} is a formality, not a control"
            )

    print("", file=out)
    if failures:
        print(f"negative-control: FAIL — {len(failures)} problem(s)", file=out)
        for failure in failures:
            print(f"  FAIL  {failure}", file=out)
        return 1
    print(
        f"negative-control: OK — {len(checks)} provocation(s) covering "
        f"{len(REFUSALS)} refusal code(s), each refused by name",
        file=out,
    )
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
