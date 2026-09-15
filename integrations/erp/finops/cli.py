"""The lane's tri-state check, and its human-facing transcript (issue #654).

``python3 -m integrations.erp.finops.cli check`` is what ``scripts/check-erp-finops.sh``
runs, and it is deliberately *not* a second test suite. It measures the things a
suite of unit tests cannot:

* **the declaration covers the surface** — every document kind the live core
  model declares has a create rate, and every kind with a lifecycle has a
  transition rate. A unit test proves the code handles the kinds it was told
  about; this measures that the *list* is complete, which is the failure that
  actually ships (a new document kind added upstream is silently unbilled).
* **every operation reached the chain** — a create for every kind and a
  transition for every declared move is metered, and the ledger's own action
  sequence is compared against the plan. The comparison is against the ledger,
  not against an in-memory list, so a meter that wrote only one of the two
  record types fails here.
* **the cost is the declared cost** — the roll-up's figure is compared against
  the rate card's own numbers, so a roll-up that mis-sums is caught.
* **the hard stop is real and deterministic** — a tenant is metered to its limit
  and the crossing operation is refused, twice, with the same index both times;
  the refused operation is shown to have left **nothing** on either sink.
* **no telemetry file was edited** — the content digest of every tracked file
  under ``telemetry/`` is taken before and after the lane does its whole job and
  must be unchanged. This is acceptance criterion 3, measured rather than
  asserted in prose.
* **the refusals are provoked** — the negative control runs here, so a refusal
  that stopped refusing fails this check rather than surviving to a review.

Exit-code contract (repository convention): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
CANNOT-ASSESS is never reported as a pass: a lane that cannot read its own
declarations cannot report OK.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from integrations.erp.core.validators import load_model

from . import negative_control, provenance, rates
from .budget import ErpBudgetGuard, load_policies, spend_ledger
from .harness import (
    DEFAULT_TENANT,
    Workspace,
    build_workspace,
    golden_path,
    matrix_plan,
    policies_from,
    run_matrix,
    stamp,
)
from .ledger import AuditSink
from .meter import ErpMeter
from .model import EVENT_KIND_FOR_OPERATION, OP_CREATE, OP_TRANSITION, REFUSALS, MeteredEvent, Refused
from .rollup import ErpRollup
from .schema import load as load_schema
from .schema import validate as validate_schema
from .usage import UsageSink
from telemetry.metering.report import UsageReporter
from telemetry.metering.store import MemoryUsageStore

REPO_ROOT = Path(__file__).resolve().parents[3]
USAGE_EVENT_SCHEMA = Path(__file__).resolve().parent / "schema" / "usage-event.schema.json"

OK = "  OK    "
FAIL = "  FAIL  "


class Report:
    """Accumulates what was measured, so the verdict is one line at the end."""

    def __init__(self) -> None:
        self.failures: List[str] = []
        self.cannot: List[str] = []
        self.checked = 0

    def ok(self, message: str) -> None:
        self.checked += 1
        print(f"{OK}{message}")

    def fail(self, message: str) -> None:
        self.checked += 1
        self.failures.append(message)
        print(f"{FAIL}{message}")

    def cannot_assess(self, message: str) -> None:
        self.checked += 1
        self.cannot.append(message)
        print(f"  CANNOT-ASSESS  {message}")

    def verdict(self) -> int:
        if self.failures:
            return 1
        if self.cannot:
            return 2
        return 0


# --------------------------------------------------------------------------- #
# acceptance criterion 3: no telemetry file is edited
# --------------------------------------------------------------------------- #
def telemetry_digest(root: Path = REPO_ROOT) -> Optional[str]:
    """Content digest of every tracked file under ``telemetry/``.

    ``git ls-files`` (not a walk) so the measurement covers the files the
    repository actually owns: an ignored ``__pycache__`` appearing beside them
    is not an edit, and treating it as one would make this check fail for a
    reason no reader believes. ``None`` means the digest could not be taken at
    all, which the caller reports as CANNOT-ASSESS.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "telemetry"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    digest = hashlib.sha256()
    for relative in sorted(line for line in result.stdout.split("\n") if line.strip()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update((root / relative).read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# the checks
# --------------------------------------------------------------------------- #
def check_declarations(report: Report) -> Optional[Workspace]:
    """The declarations load, validate, and cover the live core model."""
    try:
        model = load_model()
    except Exception as exc:  # noqa: BLE001 - any failure here is CANNOT-ASSESS
        report.cannot_assess(f"the core document model cannot be loaded: {exc}")
        return None

    try:
        card = rates.load()
    except Refused as exc:
        report.fail(f"the rate card is refused: {exc}")
        return None
    except OSError as exc:
        report.fail(f"the rate card cannot be read: {exc}")
        return None

    findings = card.coverage(model.document_kinds(), model.lifecycle_kinds())
    if findings:
        for finding in findings:
            report.fail(f"rate coverage: {finding.code}: {finding.detail}")
    else:
        report.ok(
            f"rate coverage covers the live core model: {len(card.kinds())} kind(s), "
            f"{len(card.rates)} rate(s) for {len(model.document_kinds())} declared "
            f"kind(s) and {len(model.lifecycle_kinds())} lifecycle kind(s)"
        )

    try:
        policies = load_policies()
    except Refused as exc:
        report.fail(f"the budget declaration is refused: {exc}")
        return None
    report.ok(f"budget declaration: {len(policies)} tenant policy/policies")

    try:
        harvest = provenance.load()
    except Refused as exc:
        report.fail(f"the harvest record is refused: {exc}")
        return None
    report.ok(
        f"harvest record: {len(harvest.harvests)} harvest(s) from "
        f"{len(harvest.upstreams())} source(s), policy {harvest.policy}"
    )
    return None


def check_matrix(report: Report, workspace: Workspace) -> Optional[Tuple[MeteredEvent, ...]]:
    """Every kind is created and every declared move is transitioned and chained."""
    tenant = DEFAULT_TENANT
    plan = matrix_plan(workspace.model)
    kinds = sorted(workspace.meter.kinds())
    expected = (
        tuple(EVENT_KIND_FOR_OPERATION[OP_CREATE] for _ in kinds)
        + tuple(EVENT_KIND_FOR_OPERATION[OP_TRANSITION] for _ in plan)
    )
    try:
        events = run_matrix(workspace, tenant)
    except Refused as exc:
        report.fail(f"metering the declared surface was refused: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001 - the core model's own refusal
        report.fail(f"metering the declared surface failed: {type(exc).__name__}: {exc}")
        return None

    if len(events) != len(expected):
        report.fail(
            f"the matrix metered {len(events)} operation(s); the plan declares "
            f"{len(expected)}"
        )
        return None
    report.ok(
        f"the matrix metered {len(events)} operation(s): {len(kinds)} create(s) and "
        f"{len(plan)} declared transition(s)"
    )

    chained = workspace.audit.actions(tenant)
    if chained != expected:
        report.fail(
            f"the ledger holds {len(chained)} record(s) with action(s) "
            f"{chained[:4]}; the plan declares {len(expected)} starting "
            f"{expected[:4]}"
        )
        return None
    report.ok(
        f"every metered operation reached the audit chain: {len(chained)} record(s), "
        f"one per operation, in order"
    )

    if workspace.usage.count() != len(events):
        report.fail(
            f"the usage feed holds {workspace.usage.count()} record(s) for "
            f"{len(events)} metered operation(s)"
        )
        return None
    report.ok(f"every metered operation reached the usage feed: {workspace.usage.count()} record(s)")

    verdict = workspace.audit.verify(tenant)
    if verdict.status != "OK":
        report.fail(f"the audit chain for {tenant} is {verdict.status}: {verdict.detail}")
        return None
    report.ok(f"the audit chain verifies: {tenant} tail {verdict.tail}")
    return events


def check_event_shape(report: Report, events: Sequence[MeteredEvent]) -> None:
    """Every emitted event matches the published shape."""
    try:
        schema = load_schema(USAGE_EVENT_SCHEMA)
    except (OSError, ValueError) as exc:
        report.cannot_assess(f"the metered-event schema cannot be read: {exc}")
        return
    bad = 0
    for event in events:
        violations = validate_schema(event.to_dict(), schema, where=event.source_key())
        if violations:
            bad += 1
            report.fail(f"event {event.source_key()}: {'; '.join(violations)}")
    if not bad:
        report.ok(f"every metered event matches {USAGE_EVENT_SCHEMA.name}: {len(events)} checked")


def check_rollup(report: Report, workspace: Workspace, events: Sequence[MeteredEvent]) -> None:
    """The roll-up's per-tenant cost is the rate card's own arithmetic."""
    rollup = ErpRollup(workspace.reporter)
    row = rollup.for_tenant(DEFAULT_TENANT, month="2026-09")
    if row is None:
        report.fail(f"the roll-up holds no row for {DEFAULT_TENANT} after metering")
        return
    expected_cost = 0.0
    for event in events:
        price = workspace.rates.price_for(event.kind, event.operation)
        if price is None:
            report.fail(f"{event.kind}/{event.operation} is unpriced; the catalog must price it")
            return
        expected_cost += price
    if row.operations != len(events):
        report.fail(f"the roll-up counts {row.operations} operation(s), not {len(events)}")
        return
    if abs(row.cost_usd - round(expected_cost, 8)) > 1e-9:
        report.fail(
            f"the roll-up reports {row.cost_usd} for {DEFAULT_TENANT}, but the rate "
            f"card's prices sum to {round(expected_cost, 8)}"
        )
        return
    report.ok(
        f"roll-up: {row.operations} ERP operation(s) for {DEFAULT_TENANT} in "
        f"{row.month} cost {row.cost_usd} {workspace.rates.currency}, which is the "
        f"rate card's own sum; {row.unmetered_operations} unmetered"
    )

    if rollup.for_tenant("no-such-tenant") is not None:
        report.fail("the roll-up returned a row for a tenant with no ERP usage")
        return
    try:
        rollup.bill("no-such-tenant")
    except Refused as exc:
        if exc.code != "unmetered-usage":
            report.fail(f"billing an unmetered tenant refused {exc.code}, not unmetered-usage")
            return
        report.ok("the roll-up answers NO-DATA for an unmetered tenant and refuses to bill it")
    else:
        report.fail("the roll-up billed a tenant with no ERP usage at all")
        return

    try:
        rollup.certify(workspace.audit, DEFAULT_TENANT)
    except Refused as exc:
        report.fail(f"certifying an intact chain was refused: {exc}")
        return
    report.ok(f"the roll-up certifies {DEFAULT_TENANT}'s cost against its audit chain")


def _stop_run(limit_usd: float) -> Tuple[int, str, int, int]:
    """Meter one tenant up to its limit; return (allowed, code, ledger, usage)."""
    workspace = build_workspace(policies=policies_from({"stopper": limit_usd}))
    allowed = 0
    code = ""
    index = 0
    while index < 64:
        try:
            workspace.meter.create(
                "sales-order",
                tenant="stopper",
                document_id=f"SO-STOP-{index:04d}",
                actor="agent:erp-robot",
                at=stamp(index),
            )
        except Refused as exc:
            code = exc.code
            break
        allowed += 1
        index += 1
    return allowed, code, workspace.audit.count("stopper"), workspace.usage.count()


def check_budget_stop(report: Report) -> None:
    """The hard stop is deterministic, happens at the cap, and writes nothing."""
    limit = 0.05
    price = rates.load().price_for("sales-order", OP_CREATE)
    if price is None:
        report.fail("sales-order/create is unpriced, so the budget check cannot run")
        return

    allowed, code, ledger, usage = _stop_run(limit)
    second_allowed, second_code, second_ledger, second_usage = _stop_run(limit)

    if code != "budget-exhausted":
        report.fail(f"the tenant at its budget was refused {code!r}, not budget-exhausted")
        return
    if (allowed, ledger, usage) != (second_allowed, second_ledger, second_usage) or code != second_code:
        report.fail(
            f"the stop is not deterministic: {allowed}/{ledger}/{usage} then "
            f"{second_allowed}/{second_ledger}/{second_usage}"
        )
        return
    spent = allowed * price
    if spent >= limit:
        report.fail(
            f"the stop came too late: {allowed} operation(s) cost {spent} against a "
            f"{limit} cap"
        )
        return
    if spent + price < limit:
        report.fail(
            f"the stop came too early: {allowed} operation(s) cost {spent}, and one more "
            f"would still be within the {limit} cap"
        )
        return
    if ledger != allowed or usage != allowed:
        report.fail(
            f"the refused operation left a trace: {ledger} ledger record(s) and "
            f"{usage} usage record(s) for {allowed} allowed operation(s)"
        )
        return
    report.ok(
        f"the budget stop is deterministic and leaves nothing behind: {allowed} "
        f"operation(s) cost {round(spent, 8)} of a {limit} cap, the next was refused "
        f"budget-exhausted, and both sinks still hold exactly {allowed} record(s)"
    )


def check_determinism(report: Report) -> None:
    """Two independent runs of the same path agree, on the chain and the roll-up."""
    first = build_workspace()
    second = build_workspace()
    golden_path(first, DEFAULT_TENANT)
    golden_path(second, DEFAULT_TENANT)
    first_tail = first.audit.tail(DEFAULT_TENANT)
    second_tail = second.audit.tail(DEFAULT_TENANT)
    first_rows = [row.to_dict() for row in ErpRollup(first.reporter).usage()]
    second_rows = [row.to_dict() for row in ErpRollup(second.reporter).usage()]
    if first_tail != second_tail:
        report.fail(f"two runs produced different chain tails: {first_tail} vs {second_tail}")
        return
    if first_rows != second_rows:
        report.fail("two runs produced different roll-up rows")
        return
    report.ok(
        f"two independent runs agree exactly: chain tail {first_tail[0]}@"
        f"{first_tail[1][:12]}… and one identical roll-up row"
    )


def check_telemetry_untouched(report: Report, workspace: Workspace) -> None:
    """Acceptance criterion 3: the tracked telemetry tree is byte-identical."""
    before = telemetry_digest()
    if before is None:
        report.cannot_assess("the tracked telemetry digest cannot be taken (git unavailable)")
        return
    run_matrix(workspace, DEFAULT_TENANT)
    after = telemetry_digest()
    if after is None:
        report.cannot_assess("the tracked telemetry digest cannot be taken after metering")
        return
    if before != after:
        report.fail(
            f"the tracked telemetry tree changed while metering: {before[:12]} -> {after[:12]}"
        )
        return
    report.ok(
        f"no tracked file under telemetry/ was edited by a full metered run "
        f"(digest {before[:12]}…, unchanged)"
    )


def cmd_check(argv: Optional[Sequence[str]] = None) -> int:
    """Measure everything above; exit 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS."""
    print("== declarations ==")
    report = Report()
    check_declarations(report)

    print("== the declared surface, metered end to end ==")
    workspace = build_workspace()
    events = check_matrix(report, workspace)
    if events is not None:
        check_event_shape(report, events)
        check_rollup(report, workspace, events)

    print("== the deterministic hard stop ==")
    check_budget_stop(report)

    print("== determinism ==")
    check_determinism(report)

    print("== acceptance criterion 3: the telemetry pillar is consumed, not edited ==")
    check_telemetry_untouched(report, build_workspace())

    print("== the refusals, provoked ==")
    control = negative_control.run(capture=True)
    if control.uncovered:
        report.fail(
            f"the refusal vocabulary is not fully provoked: {', '.join(control.uncovered)}"
        )
    for failure in control.failures:
        report.fail(f"negative control: {failure}")
    if not control.uncovered and not control.failures:
        report.ok(
            f"all {len(REFUSALS)} refusal(s) provoked and refused by name "
            f"({control.provoked} provocation(s))"
        )

    print("")
    if report.failures:
        print(f"erp-finops check: NOT-OK — {len(report.failures)} problem(s)")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1
    if report.cannot:
        print(f"erp-finops check: CANNOT-ASSESS — {len(report.cannot)} unanswered question(s)")
        for item in report.cannot:
            print(f"  - {item}")
        return 2
    print(f"erp-finops check: OK — {report.checked} measurement(s), all honest")
    return 0


# --------------------------------------------------------------------------- #
# transcripts
# --------------------------------------------------------------------------- #
def cmd_demo(argv: Optional[Sequence[str]] = None) -> int:
    """The golden-path transcript, as JSON."""
    workspace = build_workspace()
    events = golden_path(workspace, DEFAULT_TENANT)
    payload: Dict[str, Any] = {
        "tenant": DEFAULT_TENANT,
        "events": [event.to_dict() for event in events],
        "chain": {
            "records": workspace.audit.count(DEFAULT_TENANT),
            "verified": workspace.audit.verify(DEFAULT_TENANT).status,
            "tail": list(workspace.audit.tail(DEFAULT_TENANT)),
        },
        "usage": [row.to_dict() for row in ErpRollup(workspace.reporter).usage()],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_rates(argv: Optional[Sequence[str]] = None) -> int:
    """The validated rate card."""
    try:
        card = rates.load()
    except Refused as exc:
        print(f"erp-finops rates: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(card.to_dict(), indent=2, sort_keys=True))
    return 0


def cmd_budgets(argv: Optional[Sequence[str]] = None) -> int:
    """The validated budget declaration, rendered back to its own shape."""
    try:
        policies = load_policies()
    except Refused as exc:
        print(f"erp-finops budgets: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"policies": [policy.to_dict() for policy in policies.values()]},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def cmd_provenance(argv: Optional[Sequence[str]] = None) -> int:
    """The validated harvest record."""
    try:
        harvest = provenance.load()
    except Refused as exc:
        print(f"erp-finops provenance: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(harvest.to_dict(), indent=2, sort_keys=True))
    return 0


def cmd_rollup(argv: Optional[Sequence[str]] = None) -> int:
    """The per-tenant ERP usage/cost roll-up over a freshly metered run."""
    workspace = build_workspace()
    run_matrix(workspace, DEFAULT_TENANT)
    rollup = ErpRollup(workspace.reporter)
    print(
        json.dumps(
            {
                "totals": rollup.totals(),
                "rows": [row.to_dict() for row in rollup.usage()],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m integrations.erp.finops.cli",
        description="ERP FinOps metering: the lane's check and its transcripts.",
    )
    parser.add_argument(
        "command",
        choices=("check", "demo", "rates", "budgets", "provenance", "rollup"),
        help="what to do",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return {
        "check": cmd_check,
        "demo": cmd_demo,
        "rates": cmd_rates,
        "budgets": cmd_budgets,
        "provenance": cmd_provenance,
        "rollup": cmd_rollup,
    }[args.command]()


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
