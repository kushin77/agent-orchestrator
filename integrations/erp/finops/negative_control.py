"""One provocation per refusal, and the coverage that keeps it complete (#654).

The repository's doctrine is blunt about why this file exists: **a validator that
is never shown to refuse is a formality.** This lane can refuse 23 different
ways, and a suite that only walks the happy path passes just as green when the
budget guard has been replaced by ``return None``.

Three properties make the driver worth more than a list of assertions:

* **coverage is computed, not claimed.** :func:`run` compares the set of
  provoked codes against :data:`~.model.REFUSALS` and fails when they diverge,
  so a refusal added without a control fails the driver — and, through
  ``cli.py check``, the lane's gate — instead of shipping unproven.
* **the offender must be named.** Every provocation declares the substring its
  refusal has to contain, so "something was refused" is not accepted in place of
  "the tenant was refused by name for being out of budget".
* **the driver can fail.** ``tests/test_negative_control.py`` neuters the budget
  guard with ``monkeypatch`` and requires :func:`run` to go non-zero, and
  ``scripts/check-erp-finops.sh`` does the same against a *copied tree* — a
  driver that cannot fail proves nothing about the controls it reports.

Run it directly with ``python3 -m integrations.erp.finops.negative_control``; it
exits 0 only when every refusal is provoked, named, and covered.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Mapping, Optional, Sequence, Tuple

from . import provenance, rates, schema as schemas
from .budget import load_policies
from .harness import build_workspace, policies_from, stamp
from .ledger import open_sink
from .model import (
    REFUSALS,
    MeteredEvent,
    Refused,
)
from .rollup import ErpRollup
from .usage import assert_event_shape_dict
from telemetry.metering.report import UsageReporter

TENANT = "acme"

#: A card that declares one operation and prices it. Everything else is absent,
#: which is what ``rate-missing`` is for.
THIN_CARD: Mapping[str, object] = {
    "schemaVersion": "ao.erp.finops/v1",
    "currency": "USD",
    "supportedCurrencies": ["USD"],
    "rates": [{"kind": "sales-order", "operation": "create", "price": 0.015}],
}

#: A card that declares the operation and deliberately publishes no price.
UNPRICED_CARD: Mapping[str, object] = {
    "schemaVersion": "ao.erp.finops/v1",
    "currency": "USD",
    "supportedCurrencies": ["USD"],
    "rates": [{"kind": "sales-order", "operation": "create", "priced": False}],
}


@dataclass(frozen=True)
class Provocation:
    """One refusal, the input that provokes it, and the offender it must name."""

    name: str
    code: str
    needle: str
    check: Callable[[], None]
    why: str = ""


@dataclass(frozen=True)
class ControlResult:
    """What the driver measured: provoked codes, failures, and the gap."""

    provoked: int
    codes: Tuple[str, ...]
    failures: Tuple[str, ...]
    uncovered: Tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failures and not self.uncovered


# --------------------------------------------------------------------------- #
# the provocations
# --------------------------------------------------------------------------- #
def _meter(**kwargs: object) -> MeteredEvent:
    workspace = kwargs.pop("workspace", None) or build_workspace()
    return workspace.meter.create(  # type: ignore[union-attr]
        str(kwargs.pop("kind", "sales-order")),
        tenant=str(kwargs.pop("tenant", TENANT)),
        document_id=str(kwargs.pop("document_id", "SO-0001")),
        actor=str(kwargs.pop("actor", "agent:erp-robot")),
        at=str(kwargs.pop("at", stamp(0))),
        **kwargs,  # type: ignore[arg-type]
    )


def provocations() -> Tuple[Provocation, ...]:
    """Every provocation, built fresh so no two of them share state."""

    def unknown_operation() -> None:
        rates.Rate(kind="sales-order", operation="delete", priced=True, price_usd=0.01)

    def unknown_document_kind() -> None:
        _meter(kind="sprocket")

    def missing_tenant() -> None:
        _meter(tenant="")

    def tenant_mismatch() -> None:
        _meter(document={"doctype": "sales-order", "tenant": "globex", "state": "draft"})

    def missing_document_id() -> None:
        _meter(document_id="")

    def missing_actor() -> None:
        _meter(actor="   ")

    def invalid_timestamp() -> None:
        _meter(at="half past three")

    def clock_regression() -> None:
        workspace = build_workspace()
        _meter(workspace=workspace, at=stamp(10), document_id="SO-LATE")
        _meter(workspace=workspace, at=stamp(5), document_id="SO-EARLY")

    def duplicate_event() -> None:
        workspace = build_workspace()
        _meter(workspace=workspace, at=stamp(1), document_id="SO-DUP")
        _meter(workspace=workspace, at=stamp(1), document_id="SO-DUP")

    def rate_missing() -> None:
        workspace = build_workspace(
            rate_card=THIN_CARD, policies=policies_from({TENANT: 100.0})
        )
        _meter(workspace=workspace, kind="item", document_id="ITEM-0001")

    def rate_invalid() -> None:
        rates.load(
            {
                "schemaVersion": "ao.erp.finops/v1",
                "currency": "USD",
                "supportedCurrencies": ["USD"],
                "rates": [
                    {"kind": "sales-order", "operation": "create", "price": 0.0}
                ],
            }
        )

    def unknown_currency() -> None:
        rates.load(
            {
                "schemaVersion": "ao.erp.finops/v1",
                "currency": "USD",
                "supportedCurrencies": ["USD"],
                "rates": [
                    {
                        "kind": "sales-order",
                        "operation": "create",
                        "price": 0.01,
                        "currency": "XYZ",
                    }
                ],
            }
        )

    def rate_card_invalid() -> None:
        rates.load(
            {
                "schemaVersion": "ao.erp.finops/v1",
                "supportedCurrencies": ["USD"],
                "rates": [],
            }
        )

    def unsupported_schema_keyword() -> None:
        schemas.assert_supported(
            {"type": "object", "oneOf": [{"type": "object"}]}, where="scratch.schema.json"
        )

    def budget_policy_invalid() -> None:
        load_policies(
            {
                "schemaVersion": "ao.erp.finops/v1",
                "policies": [{"tenantId": TENANT, "limitUsd": 0}],
            }
        )

    def budget_unknown_tenant() -> None:
        workspace = build_workspace(policies=policies_from({TENANT: 100.0}))
        _meter(workspace=workspace, tenant="globex")

    def budget_exhausted() -> None:
        workspace = build_workspace(policies=policies_from({"stopper": 0.01}))
        for index in range(8):
            _meter(
                workspace=workspace,
                tenant="stopper",
                at=stamp(index),
                document_id=f"SO-STOP-{index:04d}",
            )

    def ledger_unverified() -> None:
        with tempfile.TemporaryDirectory(prefix="ao-erp-finops-nc-") as directory:
            sink = open_sink(directory)
            workspace = build_workspace(policies=policies_from({TENANT: 100.0}))
            workspace.meter.audit = sink
            _meter(workspace=workspace, at=stamp(0), document_id="SO-TAMPER")
            path = Path(directory) / f"{TENANT}.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines()
            # The chain file is prefixed by ``#`` comment lines, so the first
            # line that is a record is the one to tamper with.
            index = next(
                position
                for position, line in enumerate(lines)
                if line.strip() and not line.strip().startswith("#")
            )
            record = json.loads(lines[index])
            record["action"] = "erp.document.tampered"
            lines[index] = json.dumps(record, sort_keys=True)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            ErpRollup(UsageReporter(workspace.usage.store)).certify(sink, TENANT)

    def unmetered_usage() -> None:
        workspace = build_workspace(
            rate_card=UNPRICED_CARD, policies=policies_from({TENANT: 100.0})
        )
        _meter(workspace=workspace, at=stamp(0), document_id="SO-UNPRICED")
        ErpRollup(UsageReporter(workspace.usage.store)).bill(TENANT)

    def usage_event_invalid() -> None:
        assert_event_shape_dict(
            {"schemaVersion": "ao.erp.finops/v1", "tenant": "", "kind": "sales-order"},
            where="scratch-event",
        )

    def provenance_code_copied() -> None:
        provenance.load(
            {
                "schemaVersion": "ao.erp.finops/v1",
                "module": "integrations/erp/finops",
                "policy": "patterns-only-no-upstream-code",
                "harvests": [
                    {
                        "shape": "vendored-file",
                        "upstream": "frappe/erpnext",
                        "upstreamVersion": "v16",
                        "license": "GPL-3.0",
                        "codeCopied": True,
                        "url": "https://example.invalid/x",
                        "harvestedAt": "2026-09-15",
                    }
                ],
            }
        )

    def provenance_empty() -> None:
        provenance.load(
            {
                "schemaVersion": "ao.erp.finops/v1",
                "module": "integrations/erp/finops",
                "policy": "patterns-only-no-upstream-code",
                "harvests": [],
            }
        )

    def provenance_invalid() -> None:
        provenance.load(
            {
                "schemaVersion": "ao.erp.finops/v1",
                "policy": "patterns-only-no-upstream-code",
                "harvests": [],
            }
        )

    return (
        Provocation(
            "an operation outside the closed vocabulary",
            "unknown-operation",
            "delete",
            unknown_operation,
        ),
        Provocation(
            "a document kind the core model does not declare",
            "unknown-document-kind",
            "sprocket",
            unknown_document_kind,
        ),
        Provocation(
            "an event with no tenant", "missing-tenant", "tenant", missing_tenant
        ),
        Provocation(
            "a document belonging to another tenant",
            "tenant-mismatch",
            "globex",
            tenant_mismatch,
        ),
        Provocation(
            "an event that cannot name its document",
            "missing-document-id",
            "document",
            missing_document_id,
        ),
        Provocation(
            "an event with no actor", "missing-actor", "actor", missing_actor
        ),
        Provocation(
            "an unparseable event timestamp",
            "invalid-timestamp",
            "half past three",
            invalid_timestamp,
        ),
        Provocation(
            "an event older than the tenant's last",
            "clock-regression",
            "earlier",
            clock_regression,
        ),
        Provocation(
            "the same event emitted twice",
            "duplicate-event",
            "already been metered",
            duplicate_event,
        ),
        Provocation(
            "an operation with no declared rate",
            "rate-missing",
            "item/create",
            rate_missing,
        ),
        Provocation(
            "a declared rate that is not positive",
            "rate-invalid",
            "positive",
            rate_invalid,
        ),
        Provocation(
            "a rate in a currency the card does not support",
            "unknown-currency",
            "XYZ",
            unknown_currency,
        ),
        Provocation(
            "a rate card that does not satisfy its schema",
            "rate-card-invalid",
            "currency",
            rate_card_invalid,
        ),
        Provocation(
            "a schema using a keyword the validator cannot enforce",
            "unsupported-schema-keyword",
            "oneOf",
            unsupported_schema_keyword,
        ),
        Provocation(
            "a budget limit the platform cannot enforce",
            "budget-policy-invalid",
            "limit",
            budget_policy_invalid,
        ),
        Provocation(
            "a tenant with no declared budget",
            "budget-unknown-tenant",
            "globex",
            budget_unknown_tenant,
        ),
        Provocation(
            "a tenant that has reached its budget",
            "budget-exhausted",
            "stopped at",
            budget_exhausted,
        ),
        Provocation(
            "a cost published over a tampered audit chain",
            "ledger-unverified",
            "NOT-OK",
            ledger_unverified,
        ),
        Provocation(
            "a bill demanded for an unpriced operation",
            "unmetered-usage",
            "no published price",
            unmetered_usage,
        ),
        Provocation(
            "an emitted event that does not match its published shape",
            "usage-event-invalid",
            "tenant",
            usage_event_invalid,
        ),
        Provocation(
            "a harvest record claiming copied upstream code",
            "provenance-code-copied",
            "copied",
            provenance_code_copied,
        ),
        Provocation(
            "a harvest record with no harvests",
            "provenance-empty",
            "no harvests",
            provenance_empty,
        ),
        Provocation(
            "a harvest record that does not satisfy its schema",
            "provenance-invalid",
            "module",
            provenance_invalid,
        ),
    )


# --------------------------------------------------------------------------- #
# the driver
# --------------------------------------------------------------------------- #
def run(
    out: Optional[io.TextIOBase] = None,
    *,
    capture: bool = False,
) -> ControlResult:
    """Provoke every refusal; return what was provoked, and what was not.

    ``capture=True`` collects the transcript instead of printing it, which is how
    the lane's check folds this driver into its own report without the two
    outputs interleaving.
    """
    stream: io.TextIOBase = io.StringIO() if capture else (out or sys.stdout)
    failures: List[str] = []
    provoked: List[str] = []

    for provocation in provocations():
        try:
            provocation.check()
        except Refused as exc:
            if exc.code != provocation.code:
                failures.append(
                    f"{provocation.name}: refused {exc.code!r}, expected "
                    f"{provocation.code!r}"
                )
                continue
            named = f"{exc.detail} {exc.where or ''}"
            if provocation.needle and provocation.needle not in named:
                failures.append(
                    f"{provocation.name}: refused {exc.code!r} without naming "
                    f"{provocation.needle!r} ({exc.detail})"
                )
                continue
            provoked.append(exc.code)
            print(f"  ok    {provocation.name} -> {exc.code}", file=stream)
        except Exception as exc:  # noqa: BLE001 - any other failure is a finding
            failures.append(
                f"{provocation.name}: raised {type(exc).__name__}: {exc} instead of "
                f"refusing"
            )
        else:
            failures.append(
                f"{provocation.name}: was NOT refused — {provocation.code} is unproven"
            )

    uncovered = tuple(code for code in REFUSALS if code not in provoked)
    return ControlResult(
        provoked=len(provoked),
        codes=tuple(provoked),
        failures=tuple(failures),
        uncovered=uncovered,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run every provocation; exit 0 only when all of them bit and are covered."""
    print(f"== provoking all {len(REFUSALS)} refusal(s) ==")
    result = run()
    if result.uncovered:
        print(
            f"erp-finops negative control: NOT-OK — {len(result.uncovered)} refusal(s) "
            f"were never provoked: {', '.join(result.uncovered)}",
            file=sys.stderr,
        )
        return 1
    if result.failures:
        for failure in result.failures:
            print(f"  FAIL  {failure}", file=sys.stderr)
        print(
            f"erp-finops negative control: NOT-OK — {len(result.failures)} problem(s)",
            file=sys.stderr,
        )
        return 1
    print(
        f"erp-finops negative control: OK — {result.provoked} provocation(s), every "
        f"declared refusal refused by name"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
