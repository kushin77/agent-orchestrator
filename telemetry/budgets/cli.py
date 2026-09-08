"""telemetry/budgets — offline operator CLI (issue #34).

Runs the per-tenant budgets + quotas + global kill-switch + SLO-export rail
fully offline from the repo root (PEP-420 namespace):

.. code-block:: bash

    python3 -m telemetry.budgets.cli policies
    python3 -m telemetry.budgets.cli quotas
    python3 -m telemetry.budgets.cli killswitch status
    python3 -m telemetry.budgets.cli killswitch pause --reason "incident #9" --by oncall
    python3 -m telemetry.budgets.cli killswitch resume
    python3 -m telemetry.budgets.cli check --tenant acme --vendor anthropic \\
        --cost 3.0 --tokens 5000 [--store usage.jsonl] [--audit audit.jsonl]
    python3 -m telemetry.budgets.cli budget --tenant acme --cost 3.0 --tokens 5000 [--store ..]
    python3 -m telemetry.budgets.cli quota --tenant acme --resource requests [--store ..]
    python3 -m telemetry.budgets.cli export --out state.json [--store ..] [--audit ..] [--slo-json ..]
    python3 -m telemetry.budgets.cli chargeback --store usage.jsonl --month 2026-09 --out report.json
    python3 -m telemetry.budgets.cli audit --audit audit.jsonl
    python3 -m telemetry.budgets.cli demo

Exit-code contract: ``0`` allow/all-OK, ``1`` any block/refuse/over-budget
call, ``2`` usage/config error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from telemetry.budgets.audit import JsonlAuditStore, MemoryAuditStore
from telemetry.budgets.budget import (
    BudgetEnforcer,
    load_budget_policies,
)
from telemetry.budgets.chargeback import ChargebackReportGenerator
from telemetry.budgets.exporter import BudgetStateExporter
from telemetry.budgets.killswitch import (
    JsonKillSwitchStore,
    KillSwitchController,
    KillSwitchState,
    load_killswitch_state,
)
from telemetry.budgets.ledger import MeteringReporterLedger, StaticLedger
from telemetry.budgets.model import DECISION_BLOCK, DECISION_REFUSE
from telemetry.budgets.preflight import first_blocking_rail, preflight
from telemetry.budgets.quota import QuotaEnforcer, StaticProbe, load_quota_policies


# --------------------------------------------------------------------------- #
# Context wiring (default configs -> enforcers/ledger/audit)
# --------------------------------------------------------------------------- #
class Context:
    """The wired offline context the commands act on."""

    def __init__(
        self,
        *,
        store_path: Optional[str] = None,
        audit_path: Optional[str] = None,
        killswitch_path: Optional[str] = None,
        inflight: int = 0,
        storage_bytes: int = 0,
    ) -> None:
        self.store_path = store_path
        self.ledger = self._build_ledger(store_path)
        self.budget = BudgetEnforcer(self.ledger, load_budget_policies())
        self.quota = QuotaEnforcer(
            self.ledger,
            load_quota_policies(),
            probe=StaticProbe(
                default_inflight=inflight,
                default_storage=storage_bytes,
            ),
        )
        self.audit = (
            JsonlAuditStore(Path(audit_path)) if audit_path else MemoryAuditStore()
        )
        self.killswitch_path = killswitch_path
        if killswitch_path:
            state_path = Path(killswitch_path)
            # A fresh state file (or a file that has not been written yet)
            # starts from the shipped OFF-by-default config; an existing
            # state file is the source of truth.
            if state_path.is_file():
                state = load_killswitch_state(state_path, env=os.environ)
            else:
                state = load_killswitch_state(env=os.environ)
            self.killswitch = KillSwitchController(
                JsonKillSwitchStore(state_path),
                audit=self.audit,
                initial=state,
            )
        else:
            state = load_killswitch_state(env=os.environ)
            self.killswitch = KillSwitchController(audit=self.audit, initial=state)
        self.exporter = BudgetStateExporter(
            self.ledger,
            killswitch=self.killswitch,
            budget=self.budget,
            quota=self.quota,
            audit=self.audit,
        )
        self.inflight = inflight
        self.storage_bytes = storage_bytes

    @staticmethod
    def _build_ledger(store_path: Optional[str]):
        if not store_path:
            return StaticLedger()
        # Consume the issue-#33 metering store (durable usage feed).
        from telemetry.metering.report import UsageReporter
        from telemetry.metering.store import JsonlUsageStore

        store = JsonlUsageStore(Path(store_path))
        return MeteringReporterLedger(UsageReporter(store))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telemetry.budgets.cli",
        description="Per-tenant budgets + quotas + global kill switch + SLO export (#34)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", default=None, help="metering usage JSONL store path")
    common.add_argument("--audit", default=None, help="audit JSONL store path")
    common.add_argument("--state", default=None, help="kill-switch state file path")
    common.add_argument("--inflight", type=int, default=0,
                        help="live in-flight concurrency to check against")
    common.add_argument("--storage", type=int, default=0,
                        help="live stored bytes to check against")

    p_policies = sub.add_parser("policies", parents=[common], help="dump budget policies")
    p_policies.set_defaults(func=_cmd_policies)

    p_quotas = sub.add_parser("quotas", parents=[common], help="dump effective quotas")
    p_quotas.set_defaults(func=_cmd_quotas)

    p_ks = sub.add_parser("killswitch", parents=[common], help="kill-switch commands")
    ks_sub = p_ks.add_subparsers(dest="killswitch_command", required=True)
    ks_sub.add_parser("status", parents=[common],
                      help="current kill-switch state").set_defaults(
        func=_cmd_killswitch_status
    )
    p_pause = ks_sub.add_parser("pause", parents=[common],
                                help="engage the global kill switch")
    p_pause.add_argument("--reason", required=True)
    p_pause.add_argument("--by", default="operator")
    p_pause.set_defaults(func=_cmd_killswitch_pause)
    ks_sub.add_parser("resume", parents=[common],
                      help="clear the global kill switch").set_defaults(
        func=_cmd_killswitch_resume
    )

    p_check = sub.add_parser("check", parents=[common],
                             help="composed pre-dispatch check (kill switch -> quota -> budget)")
    p_check.add_argument("--tenant", required=True)
    p_check.add_argument("--agent", default=None)
    p_check.add_argument("--vendor", default=None)
    p_check.add_argument("--model", default=None)
    p_check.add_argument("--service", default=None)
    p_check.add_argument("--critical", action="store_true",
                         help="critical call (allowed through a global pause)")
    p_check.add_argument("--cost", type=float, default=0.0)
    p_check.add_argument("--tokens", type=int, default=0)
    p_check.set_defaults(func=_cmd_check)

    p_budget = sub.add_parser("budget", parents=[common],
                              help="budget rail only")
    p_budget.add_argument("--tenant", required=True)
    p_budget.add_argument("--agent", default=None)
    p_budget.add_argument("--vendor", default=None)
    p_budget.add_argument("--model", default=None)
    p_budget.add_argument("--cost", type=float, default=0.0)
    p_budget.add_argument("--tokens", type=int, default=0)
    p_budget.set_defaults(func=_cmd_budget)

    p_quota = sub.add_parser("quota", parents=[common],
                             help="quota rail only")
    p_quota.add_argument("--tenant", required=True)
    p_quota.add_argument("--agent", default=None)
    p_quota.add_argument("--resource", default=None,
                         choices=["requests", "tokens", "concurrency", "storage"])
    p_quota.add_argument("--requested", type=float, default=1.0,
                         help="projected increment (tokens/bytes/calls)")
    p_quota.set_defaults(func=_cmd_quota)

    p_export = sub.add_parser("export", parents=[common],
                              help="machine-readable state export (dashboards/alerts)")
    p_export.add_argument("--out", default="budgets-state.json")
    p_export.add_argument("--slo-json", default=None,
                          help="JSONL of serialized SLO results (issue #32 vocabulary)")
    p_export.set_defaults(func=_cmd_export)

    p_cb = sub.add_parser("chargeback", parents=[common],
                          help="per-tenant chargeback report for billing")
    p_cb.add_argument("--month", default=None, help="month bucket YYYY-MM")
    p_cb.add_argument("--tenant", default=None)
    p_cb.add_argument("--out", default=None, help="output path (.json or .csv)")
    p_cb.set_defaults(func=_cmd_chargeback)

    p_audit = sub.add_parser("audit", parents=[common],
                             help="print the audit feed")
    p_audit.set_defaults(func=_cmd_audit)

    sub.add_parser("demo", parents=[common], help="end-to-end offline walk-through").set_defaults(
        func=_cmd_demo
    )
    return parser


# --------------------------------------------------------------------------- #
# Command implementations
# --------------------------------------------------------------------------- #
def _cmd_policies(args: argparse.Namespace) -> int:
    ctx = Context(store_path=args.store)
    for policy in sorted(ctx.budget.policies.values(),
                         key=lambda p: p.tenant_id):
        print(json.dumps(policy.to_dict(), indent=2, sort_keys=True))
    return 0


def _cmd_quotas(args: argparse.Namespace) -> int:
    ctx = Context(store_path=args.store)
    for policy in sorted(ctx.quota.policies.values(),
                         key=lambda p: p.tenant_id):
        print(json.dumps(policy.to_dict(), indent=2, sort_keys=True))
    return 0


def _cmd_killswitch_status(args: argparse.Namespace) -> int:
    ctx = Context(store_path=args.store, audit_path=args.audit,
                  killswitch_path=args.state)
    print(json.dumps(ctx.killswitch.state.to_dict(), indent=2, sort_keys=True))
    return 0


def _cmd_killswitch_pause(args: argparse.Namespace) -> int:
    ctx = Context(store_path=args.store, audit_path=args.audit,
                  killswitch_path=args.state)
    state = ctx.killswitch.pause(reason=args.reason, paused_by=args.by)
    print(f"kill switch ENGAGED: reason={args.reason!r} by={args.by!r}")
    print(json.dumps(state.to_dict(), indent=2, sort_keys=True))
    return 0


def _cmd_killswitch_resume(args: argparse.Namespace) -> int:
    ctx = Context(store_path=args.store, audit_path=args.audit,
                  killswitch_path=args.state)
    state = ctx.killswitch.clear()
    print("kill switch cleared")
    print(json.dumps(state.to_dict(), indent=2, sort_keys=True))
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    ctx = Context(store_path=args.store, audit_path=args.audit,
                  killswitch_path=args.state,
                  inflight=args.inflight, storage_bytes=args.storage)
    result = preflight(
        args.tenant,
        agent_id=args.agent,
        vendor=args.vendor,
        model=args.model,
        service=args.service,
        critical=args.critical,
        requested_cost_usd=args.cost,
        requested_tokens=args.tokens,
        killswitch=ctx.killswitch,
        quota=ctx.quota,
        budget=ctx.budget,
    )
    blocking = first_blocking_rail(result)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    if blocking is not None:
        print(f"\nREFUSED: {blocking.code} — {blocking.reason}")
        return 1
    print(f"\nallowed: {result.decision}")
    return 0


def _cmd_budget(args: argparse.Namespace) -> int:
    ctx = Context(store_path=args.store, audit_path=args.audit)
    decision = ctx.budget.check(
        args.tenant,
        agent_id=args.agent,
        vendor=args.vendor,
        model=args.model,
        requested_cost_usd=args.cost,
        requested_tokens=args.tokens,
    )
    print(json.dumps(decision.to_dict(), indent=2, sort_keys=True))
    return 1 if decision.decision in (DECISION_BLOCK, DECISION_REFUSE) else 0


def _cmd_quota(args: argparse.Namespace) -> int:
    ctx = Context(store_path=args.store, inflight=args.inflight,
                  storage_bytes=args.storage)
    decision = ctx.quota.check(
        args.tenant,
        agent_id=args.agent,
        resource=args.resource,
        requested=args.requested,
    )
    print(json.dumps(decision.to_dict(), indent=2, sort_keys=True))
    return 1 if decision.decision in (DECISION_BLOCK, DECISION_REFUSE) else 0


def _cmd_export(args: argparse.Namespace) -> int:
    ctx = Context(store_path=args.store, audit_path=args.audit,
                  killswitch_path=args.state)
    slo_payloads = _read_slo_json(args.slo_json) if args.slo_json else None
    path = ctx.exporter.write_json(Path(args.out), slo_results=slo_payloads)
    print(f"wrote state export: {path}")
    return 0


def _cmd_chargeback(args: argparse.Namespace) -> int:
    ctx = Context(store_path=args.store)
    if not args.store:
        print("error: chargeback needs --store (a metering usage JSONL store)",
              file=sys.stderr)
        return 2
    reporter = ctx.ledger.reporter
    generator = ChargebackReportGenerator(reporter)
    rows = generator.report(month=args.month, tenant_id=args.tenant)
    if args.out:
        out = Path(args.out)
        if out.suffix.lower() == ".csv":
            generator.write_csv(out, month=args.month, tenant_id=args.tenant)
        else:
            generator.write_json(out, month=args.month, tenant_id=args.tenant)
        print(f"wrote chargeback report: {out}")
        return 0
    for row in rows:
        print(json.dumps(row.to_dict(), sort_keys=True))
    print(json.dumps({"totals": generator.totals()}, sort_keys=True))
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    ctx = Context(store_path=args.store, audit_path=args.audit)
    events = ctx.audit.read()
    for event in events[-50:]:
        print(json.dumps(event.to_dict(), sort_keys=True))
    print(f"total audit events: {len(events)}")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    """End-to-end offline walk-through: allow, warn, block + kill switch."""
    print("== telemetry/budgets demo (issue #34) ==")

    # A static ledger with realistic current spend for acme.
    ledger = StaticLedger()
    ledger.seed_cost("acme", "2026-09", 110.0)     # near the 120 monthly cap
    ledger.seed_tokens("acme", "2026-09-08", 1_950_000)
    ledger.seed_calls("acme", "2026-09-08", 30)
    ledger.seed_vendor_cost("acme", "anthropic", "2026-09", 45.0)
    policies = load_budget_policies()
    quota_policies = load_quota_policies()
    audit = MemoryAuditStore()

    budget = BudgetEnforcer(ledger, policies)
    quota = QuotaEnforcer(ledger, quota_policies)
    killswitch = KillSwitchController(audit=audit)

    def _show(label: str, d: Any) -> None:
        print(f"\n[{label}] {d.decision} ({d.code})")
        print(f"  {d.reason}")

    # 1. acme in enforce: a small call is allowed.
    _show("acme small call", budget.check(
        "acme", vendor="anthropic", model="claude-3-5-sonnet",
        requested_cost_usd=0.5, requested_tokens=5_000))
    # 2. a call that would blow the monthly cap is BLOCKED (never silent).
    _show("acme over-budget call", budget.check(
        "acme", vendor="deepseek", model="deepseek-reasoner",
        requested_cost_usd=50.0, requested_tokens=200_000))
    # 3. observe tenant reports would_block but never refuses.
    _show("tenant-omega observe over-limit", budget.check(
        "tenant-omega", vendor="anthropic",
        requested_cost_usd=900.0, requested_tokens=9_000_000))
    # 4. quota: acme concurrency override hard 60; at 60 + 1 -> blocked.
    quota.probe.seed_inflight("acme", 60)
    _show("acme concurrency at hard", quota.check("acme", resource="concurrency"))
    quota.probe.seed_inflight("acme", 0)
    # 5. global kill switch: engage, then a normally-allowed call is refused.
    killswitch.pause(reason="demo: incident rehearsal", paused_by="demo")
    _show("acme call while paused", killswitch.check_call("acme", service="model"))
    _show("acme critical call while paused", killswitch.check_call(
        "acme", service="model", critical=True))
    killswitch.clear()

    # 6. composed preflight: allow under normal ops.
    result = preflight(
        "acme", vendor="deepseek", model="deepseek-chat",
        requested_cost_usd=0.5, requested_tokens=5_000,
        killswitch=killswitch, quota=quota, budget=budget)
    print(f"\n[composed preflight] {result.decision} outcome={result.outcome}")
    # 7. composed preflight: refuse while paused.
    killswitch.pause(reason="demo: full stop", paused_by="demo")
    result = preflight(
        "acme", vendor="deepseek", model="deepseek-chat",
        requested_cost_usd=0.5, requested_tokens=5_000,
        killswitch=killswitch, quota=quota, budget=budget)
    blocking = first_blocking_rail(result)
    print(f"[composed preflight paused] {result.decision} "
          f"outcome={result.outcome} refused_by={blocking.code if blocking else None}")
    killswitch.clear()

    # 8. state export includes kill switch + budget + quota + audit.
    exporter = BudgetStateExporter(
        ledger, killswitch=killswitch, budget=budget, quota=quota, audit=audit)
    snap = exporter.snapshot()
    print(f"\n[export] killSwitch.globalPause={snap['killSwitch']['globalPause']} "
          f"audit.events={snap['audit']['totalEvents']}")
    return 0


def _read_slo_json(path: str) -> List[Dict[str, Any]]:
    payloads: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            payloads.append(json.loads(line))
    return payloads


# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (ValueError, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
