#!/usr/bin/env python3
"""Per-task budget scope adapter for the paperclip.ing seam (issue #415).

The budget seam mismatches of `docs/PAPERCLIP-ING-INTEGRATION.md` §5 items 7–10
are closed here, by *deriving* the upstream cost shape from the fleet's own
budget rail and metering store — the adapter maps the rail, it never rewrites it:

* **#7 scope** — the rail caps per *tenant + vendor*, not per *agent / team /
  project*. This module derives ``scope.level`` from the producer that actually
  backs the charge: the metering store's per-agent attribution (``agent``), a
  tenant policy in the budget rail (``team``), or a board milestone (``project``).
  A level with **no producer** fails closed instead of silently reporting
  ``team``.
* **#8 currency** — the rail encodes USD by convention with no ``currency``
  field. The adapter resolves the currency by an explicit, reported precedence
  (charge row → rail declaration → the fleet default) and **states** it; a row
  whose currency is present but not an ISO-4217 code is refused rather than
  assumed.
* **#9 hard stop** — the rail expresses the stop as ``hard_cap_pct`` (default
  100). The adapter derives the boolean ``hard_stop`` and publishes the
  **hand-off**: at the cap the work stops and asks, it never silently continues.
* **#10 receipt** — ``receipt_ref`` ties the spend to the ticket that funded it:
  the receipt the charge carries must be a receipt the ticket's ``evidence[]``
  also carries (the `governance/ticket` join, #400/#401). A spend that resolves
  to **no ticket** is a FAIL, not an orphan row.

The module is stdlib-only and offline by construction (it reuses the mapping
module's YAML-subset loader), so the gate can wire it into ``make verify``
without a network or a third-party dependency. Exit contract: 0 OK / 1 NOT-OK /
2 CANNOT-ASSESS — CANNOT-ASSESS is never reported as a pass.

Usage: ``python3 integrations/paperclip/budget.py check --root .``

---knowledge---
module_id: integrations.paperclip.budget
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [RailPolicy, Rail, Metering, Charge, load_rail, load_metering, load_project_ids, load_tickets, (+12 more)]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from integrations.paperclip import mapping as mapping_mod  # noqa: E402

#: The scoping levels the seam admits (mirrors `budget.schema.json`).
SCOPE_LEVELS: Tuple[str, ...] = ("agent", "team", "project")

#: The currency the fleet's USD convention resolves to when nothing states one.
#: It is *stated* in every report (see ``Report.currency_source``), never silent.
DEFAULT_CURRENCY = "USD"

#: The rail's default hard-cap percentage — reaching it always stops.
DEFAULT_HARD_CAP_PCT = 100.0

#: The default burn-rate alert threshold (as a fraction of cap) when the rail
#: declares none (mirrors ``telemetry/budgets`` ``DEFAULT_WARN_AT_PCT``).
DEFAULT_WARN_AT_PCT = 0.8

#: The cost window the vendor/cost caps use (``telemetry/budgets``: month).
DEFAULT_PERIOD = "month"

#: The rail files the adapter derives from (read-only; never written).
RAIL_RELPATHS: Tuple[str, ...] = (
    "telemetry/budgets/config/policies.yaml",
    "gateway/finops/budgets.yaml",
)

#: The per-ticket charge ledger the ticket projection also reads (#401).
CHARGE_LEDGER_RELPATH = "telemetry/budgets/ledger.jsonl"

#: The durable metering store (issue #33) the spend is summed from.
METERING_RELPATH = "telemetry/metering/usage.jsonl"

#: The projected ticket store (issue #401) that carries the ``evidence[]`` receipts.
TICKETS_RELPATH = ".verify/ticket/tickets.json"

#: The committed board snapshot (the project producer: its milestone grouping).
BOARD_RELPATH = ".board/snapshot.json"

#: The hand-off verbs ``decide`` can return.
ACTION_OK = "ok"
ACTION_WARN = "warn"
ACTION_STOP_HANDOFF = "stop-handoff"

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_TICKET_TAIL_RE = re.compile(r"#(\d+)\s*$")


# ==========================================================================
# Source shapes
# ==========================================================================


@dataclass(frozen=True)
class RailPolicy:
    """One rail-derived cap for a scope id (a team is a tenant in the rail)."""

    scope_id: str
    producer: str
    cap: float
    warn_at_pct: Optional[float] = None
    hard_cap_pct: Optional[float] = None
    soft: bool = False
    currency: Optional[str] = None


@dataclass(frozen=True)
class Rail:
    """The fleet budget rail, read-only (the source the adapter maps)."""

    policies: Mapping[str, RailPolicy] = field(default_factory=dict)
    currency: Optional[str] = None
    sources: Tuple[str, ...] = ()
    present: bool = False


@dataclass(frozen=True)
class Metering:
    """The durable metering store's attribution (issue #33), read-only."""

    agent_ids: FrozenSet[str] = frozenset()
    spend_by_scope: Mapping[Tuple[str, str], float] = field(default_factory=dict)
    present: bool = False


@dataclass(frozen=True)
class Charge:
    """One per-task budget charge (a row of ``telemetry/budgets/ledger.jsonl``)."""

    ticket: str
    receipt: Optional[str]
    scope_level: Optional[str]
    scope_id: Optional[str]
    cap: Optional[float]
    spent: Optional[float]
    currency: Optional[str]
    hard_cap_pct: Optional[float]
    warn_at_pct: Optional[float]
    where: str = ""

    @classmethod
    def from_dict(cls, record: Mapping[str, Any], where: str) -> "Charge":
        scope = record.get("scope") if isinstance(record.get("scope"), dict) else {}

        def _num(value: Any) -> Optional[float]:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            return None

        def _text(value: Any) -> Optional[str]:
            return value if isinstance(value, str) else None

        return cls(
            ticket=str(record.get("ticket") or ""),
            receipt=_text(record.get("receipt")),
            scope_level=_text(scope.get("level")),
            scope_id=_text(scope.get("id")),
            cap=_num(record.get("cap")),
            spent=_num(record.get("spent")),
            currency=_text(record.get("currency")),
            hard_cap_pct=_num(record.get("hard_cap_pct")),
            warn_at_pct=_num(record.get("warn_at_pct")),
            where=where,
        )


# ==========================================================================
# Source readers (offline, read-only)
# ==========================================================================


def load_rail(root: Path) -> Rail:
    """Read the budget rail (per-tenant caps) from the committed YAML."""
    policies: Dict[str, RailPolicy] = {}
    sources: List[str] = []
    currency: Optional[str] = None

    telemetry = root / RAIL_RELPATHS[0]
    if telemetry.is_file():
        sources.append(RAIL_RELPATHS[0])
        data = mapping_mod.load_yaml_file(telemetry) or {}
        declared = data.get("currency")
        if isinstance(declared, str) and _CURRENCY_RE.match(declared):
            currency = declared
        for entry in data.get("policies") or []:
            if not isinstance(entry, dict):
                continue
            tenant = str(entry.get("tenantId") or "")
            cost = entry.get("cost") if isinstance(entry.get("cost"), dict) else {}
            cap = cost.get("limitUsd")
            if not tenant or not isinstance(cap, (int, float)) or isinstance(cap, bool):
                continue
            policies[tenant] = RailPolicy(
                scope_id=tenant,
                producer=RAIL_RELPATHS[0],
                cap=float(cap),
                warn_at_pct=cost.get("warnAtPct"),
                hard_cap_pct=None,
                soft=str(cost.get("cap") or entry.get("cap") or "hard") == "soft",
            )

    gateway = root / RAIL_RELPATHS[1]
    if gateway.is_file():
        sources.append(RAIL_RELPATHS[1])
        data = mapping_mod.load_yaml_file(gateway) or {}
        for tenant in sorted(data.get("budgets") or {}):
            entry = data["budgets"][tenant]
            if not isinstance(entry, dict):
                continue
            cap = entry.get("monthlyBudgetUsd")
            if not isinstance(cap, (int, float)) or isinstance(cap, bool):
                continue
            policies[str(tenant)] = RailPolicy(
                scope_id=str(tenant),
                producer=RAIL_RELPATHS[1],
                cap=float(cap),
                warn_at_pct=entry.get("warnAtPct"),
                hard_cap_pct=entry.get("hardCapPct"),
                soft=False,
            )

    return Rail(
        policies=policies,
        currency=currency,
        sources=tuple(sources),
        present=bool(sources),
    )


def load_metering(path: Path) -> Metering:
    """Read the metering store: per-agent attribution + per-scope spend (USD)."""
    if not path.is_file():
        return Metering(present=False)
    agent_ids: Set[str] = set()
    spend: Dict[Tuple[str, str], float] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("kind") != "usage":
            continue
        if not record.get("billable", False):
            continue
        cost = record.get("costUsd")
        cost = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0
        agent = record.get("agentId")
        if isinstance(agent, str) and agent:
            agent_ids.add(agent)
            spend[("agent", agent)] = spend.get(("agent", agent), 0.0) + cost
        tenant = record.get("tenantId")
        if isinstance(tenant, str) and tenant:
            spend[("team", tenant)] = spend.get(("team", tenant), 0.0) + cost
    return Metering(agent_ids=frozenset(agent_ids), spend_by_scope=spend, present=True)


def load_project_ids(root: Path) -> FrozenSet[str]:
    """The project producer: the board snapshot's milestone grouping."""
    board = mapping_mod.load_board(root)
    milestones = {
        str(item.get("milestone"))
        for item in board.get("issues", [])
        if isinstance(item, dict) and item.get("milestone")
    }
    return frozenset(milestones)


def _ticket_key(value: str) -> str:
    """Normalize a ticket reference to its bare ``#n`` tail when it has one."""
    match = _TICKET_TAIL_RE.search(value.strip())
    return f"#{match.group(1)}" if match else value.strip().lower()


def load_tickets(path: Path) -> Dict[str, Set[str]]:
    """Map each ticket to the set of receipt refs its ``evidence[]`` carries."""
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    records: Iterable[Any]
    if isinstance(payload, dict):
        records = payload.get("tickets") or []
    else:
        records = payload or []
    tickets: Dict[str, Set[str]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        key = _ticket_key(str(record.get("id") or ""))
        if not key:
            continue
        refs: Set[str] = set()
        for item in record.get("evidence") or []:
            if isinstance(item, str) and item:
                refs.add(item)
            elif isinstance(item, dict) and item.get("ref"):
                refs.add(str(item["ref"]))
        tickets.setdefault(key, set()).update(refs)
    return tickets


def load_charges(path: Path) -> List[Charge]:
    """Read the per-ticket charge ledger (absent ledger yields no charges)."""
    if not path.is_file():
        return []
    charges: List[Charge] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        where = f"{path.name}:{lineno}"
        try:
            record = json.loads(line)
        except ValueError:
            charges.append(
                Charge("", None, None, None, None, None, None, None, None, where=where)
            )
            continue
        if not isinstance(record, dict):
            charges.append(
                Charge("", None, None, None, None, None, None, None, None, where=where)
            )
            continue
        charges.append(Charge.from_dict(record, where))
    return charges


# ==========================================================================
# Derivation (the seam mapping)
# ==========================================================================


def build_producers(
    *,
    rail: Rail,
    metering: Metering,
    project_ids: FrozenSet[str],
) -> Dict[str, Set[str]]:
    """Which scope ids each level has a real producer for (fail closed otherwise).

    ``agent`` is produced by the metering store's per-agent attribution, ``team``
    by a tenant policy in the budget rail, ``project`` by a board milestone. A
    level absent here has no producer and a charge scoped to it is refused.
    """
    return {
        "agent": set(metering.agent_ids),
        "team": set(rail.policies),
        "project": set(project_ids),
    }


def _fraction(value: Optional[float], default: float) -> float:
    """Normalize a rail percentage to a fraction (``80`` and ``0.8`` both → .8)."""
    if value is None:
        return default
    return float(value) / 100.0 if float(value) > 1.0 else float(value)


def resolve_currency(charge: Charge, rail: Rail) -> Tuple[Optional[str], Optional[str], List[str]]:
    """Resolve the currency by an explicit precedence; refuse a malformed one.

    Returns ``(currency, source, findings)``. A currency present on the row but
    not an ISO-4217 code is **refused** — the adapter states a currency, it does
    not assume one.
    """
    findings: List[str] = []
    if charge.currency is not None:
        if _CURRENCY_RE.match(charge.currency):
            return charge.currency, "charge", findings
        findings.append(
            f"{charge.where}: currency-less row — currency {charge.currency!r} is not an "
            "ISO-4217 code; a currency is stated, never assumed"
        )
        return None, None, findings
    if rail.currency is not None:
        return rail.currency, f"rail:{RAIL_RELPATHS[0]}", findings
    return DEFAULT_CURRENCY, "fleet-default", findings


def derive_hard_stop(hard_cap_pct: float, *, soft: bool = False) -> bool:
    """``hard_stop`` is the boolean form of the percentage rail (mismatch #9).

    A cap at or below 100% with hard semantics is absolute: spend at it always
    stops, whatever the policy. A ``soft`` advisory cap never stops.
    """
    return (hard_cap_pct <= DEFAULT_HARD_CAP_PCT) and not soft


def hand_off(record: Mapping[str, Any], *, spend: Optional[float] = None) -> Dict[str, Any]:
    """The described hand-off: at the cap the work stops and asks (mismatch #9)."""
    spent = float(spend if spend is not None else record.get("spent") or 0.0)
    cap = float(record.get("cap") or 0.0)
    currency = record.get("currency") or DEFAULT_CURRENCY
    alert_pct = float(record.get("burn_rate_alert_pct") or 0.0)
    if cap <= 0:
        return {
            "action": ACTION_STOP_HANDOFF,
            "reason": "no declared cap — a spend with no cap is refused, not unlimited",
        }
    if spent >= cap:
        return {
            "action": ACTION_STOP_HANDOFF,
            "reason": (
                f"spend {spent:g} {currency} is at/above the cap {cap:g} {currency} — work "
                "stops and asks for a top-up (hand-off to the governance/ticket top-up "
                "approval); it does not silently continue"
            ),
        }
    if (spent / cap) * 100.0 >= alert_pct:
        return {
            "action": ACTION_WARN,
            "reason": (
                f"spend {spent:g} {currency} is at {spent / cap * 100.0:g}% of the cap "
                f"{cap:g} {currency}, at/above the {alert_pct:g}% burn-rate alert"
            ),
        }
    return {"action": ACTION_OK, "reason": f"spend {spent:g} {currency} is below the alert"}


def derive_charge(
    charge: Charge,
    *,
    rail: Rail,
    producers: Mapping[str, Set[str]],
    tickets: Mapping[str, Set[str]],
    metering: Metering,
) -> Tuple[Optional[Dict[str, Any]], List[str], Dict[str, str]]:
    """Derive one upstream cost record + the ticket facet from a charge row.

    Returns ``(record, findings, currency_sources)``. ``record`` is ``None`` when
    any rule refuses the row — the adapter fails closed, it never emits a
    degraded record.
    """
    findings: List[str] = []
    where = charge.where or "charge"

    level = (charge.scope_level or "").strip()
    if not level:
        if charge.cap is not None:
            findings.append(
                f"{where}: cap {charge.cap:g} declared with no scope level — a cap must name "
                "the level it applies to (agent|team|project); refusing to report 'team'"
            )
        else:
            findings.append(f"{where}: no scope level declared")
    elif level not in SCOPE_LEVELS:
        findings.append(
            f"{where}: scope level {level!r} is outside the seam vocabulary {list(SCOPE_LEVELS)}"
        )
    elif not producers.get(level):
        findings.append(
            f"{where}: scope level {level!r} has no producer in the rail — failing closed "
            "rather than silently reporting 'team'"
        )
    elif not charge.scope_id:
        findings.append(f"{where}: scope level {level!r} declared with no scope id")
    elif charge.scope_id not in producers[level]:
        findings.append(
            f"{where}: scope id {charge.scope_id!r} is not produced by any {level} source"
        )

    policy = rail.policies.get(charge.scope_id or "")
    cap = charge.cap if charge.cap is not None else (policy.cap if policy else None)
    if cap is None or cap <= 0:
        findings.append(
            f"{where}: no declared cap for scope {charge.scope_id or level!r} — a spend with "
            "no cap is refused, never treated as unlimited"
        )

    currency, currency_source, currency_findings = resolve_currency(charge, rail)
    findings.extend(currency_findings)

    metered = metering.spend_by_scope.get((level, charge.scope_id or "")) if level else None
    spent = charge.spent if charge.spent is not None else metered
    if spent is None:
        spent = 0.0

    hard_cap_pct = (
        charge.hard_cap_pct
        if charge.hard_cap_pct is not None
        else (policy.hard_cap_pct if policy and policy.hard_cap_pct else DEFAULT_HARD_CAP_PCT)
    )
    warn = charge.warn_at_pct if charge.warn_at_pct is not None else (
        policy.warn_at_pct if policy else None
    )
    warn_frac = _fraction(warn, DEFAULT_WARN_AT_PCT)
    hard_frac = _fraction(hard_cap_pct, 1.0)
    burn_rate_alert_pct = min(100.0, max(0.0, round(100.0 * warn_frac / hard_frac, 4)))
    hard_stop = derive_hard_stop(hard_cap_pct, soft=bool(policy.soft) if policy else False)

    if not charge.receipt:
        findings.append(
            f"{where}: no receipt — a spend with no per-task receipt cannot be tied to a ticket"
        )
    else:
        key = _ticket_key(charge.ticket)
        if not key or key not in tickets:
            findings.append(
                f"{where}: receipt {charge.receipt!r} resolves to no ticket "
                f"(ticket {charge.ticket!r} carries no evidence) — a spend with no ticket is a FAIL"
            )
        elif charge.receipt not in tickets[key]:
            findings.append(
                f"{where}: receipt {charge.receipt!r} backs no evidence receipt on ticket "
                f"{charge.ticket!r} — the charge and the delivery receipt must be one object"
            )

    if findings:
        return None, findings, {}

    record: Dict[str, Any] = {
        "scope": {"level": level, "id": charge.scope_id},
        "period": DEFAULT_PERIOD,
        "cap": float(cap),
        "spent": float(spent),
        "currency": currency,
        "hard_stop": hard_stop,
        "burn_rate_alert_pct": burn_rate_alert_pct,
        "receipt_ref": charge.receipt,
    }
    sources = {"currency": currency_source or "fleet-default"}
    return record, findings, sources


def to_facet(record: Mapping[str, Any], *, ticket: str) -> Dict[str, Any]:
    """Project a cost record onto the ticket's closed ``facets.budget`` shape."""
    return {
        "scope": dict(record["scope"]),
        "spent": record["spent"],
        "cap": record["cap"],
        "receipt": record["receipt_ref"],
    }


# ==========================================================================
# Report + tri-state
# ==========================================================================


@dataclass(frozen=True)
class Report:
    """The adapter's result: records, refusals, and cannot-assess reasons."""

    records: Tuple[Dict[str, Any], ...] = ()
    findings: Tuple[str, ...] = ()
    cannot_assess: Tuple[str, ...] = ()
    currency_source: Mapping[str, str] = field(default_factory=dict)

    def exit_code(self) -> int:
        """The tri-state contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS."""
        if self.cannot_assess:
            return 2
        if self.findings:
            return 1
        return 0


def build(
    root: Path,
    *,
    charges_path: Optional[Path] = None,
    tickets_path: Optional[Path] = None,
    metering_path: Optional[Path] = None,
) -> Report:
    """Derive every per-task cost record from the rail, offline and deterministically.

    A missing rail/metering/ticket source is CANNOT-ASSESS — never a pass. An
    absent *charge ledger* is not a failure: there is simply nothing to map.
    """
    root = Path(root)
    charges_file = charges_path or (root / CHARGE_LEDGER_RELPATH)
    tickets_file = tickets_path or (root / TICKETS_RELPATH)
    metering_file = metering_path or (root / METERING_RELPATH)

    charges = load_charges(charges_file)
    if not charges:
        return Report(cannot_assess=())

    cannot: List[str] = []

    rail = load_rail(root)
    if not rail.present:
        cannot.append(
            "budget rail absent (%s) — cannot derive cap/scope/currency" % ", ".join(RAIL_RELPATHS)
        )

    metering = load_metering(metering_file)
    if not metering.present and any(c.spent is None for c in charges):
        cannot.append(f"metering store absent ({METERING_RELPATH}) — cannot derive spend")

    tickets = load_tickets(tickets_file)
    if not tickets:
        cannot.append(
            f"ticket store absent ({TICKETS_RELPATH}) — cannot tie a receipt to a ticket"
        )

    if cannot:
        return Report(cannot_assess=tuple(cannot))

    producers = build_producers(
        rail=rail, metering=metering, project_ids=load_project_ids(root)
    )
    records: List[Dict[str, Any]] = []
    findings: List[str] = []
    currency_sources: Dict[str, str] = {}
    for charge in charges:
        record, charge_findings, sources = derive_charge(
            charge, rail=rail, producers=producers, tickets=tickets, metering=metering
        )
        if record is not None:
            record["_ticket"] = charge.ticket
            records.append(record)
        findings.extend(charge_findings)
        currency_sources.update(sources)

    records.sort(key=lambda r: (r["receipt_ref"], r["scope"]["level"], r["scope"]["id"]))
    return Report(
        records=tuple(records),
        findings=tuple(findings),
        cannot_assess=(),
        currency_source=currency_sources,
    )


# ==========================================================================
# CLI
# ==========================================================================


def cmd_check(args: argparse.Namespace) -> int:
    """Run the adapter and print a tri-state report; return the real exit code."""
    root = Path(args.root).resolve()
    report = build(
        root,
        charges_path=Path(args.charges) if args.charges else None,
        tickets_path=Path(args.tickets) if args.tickets else None,
        metering_path=Path(args.metering) if args.metering else None,
    )
    if report.exit_code() == 2:
        for reason in report.cannot_assess:
            print(f"check: CANNOT-ASSESS — {reason}", file=sys.stderr)
        return 2
    if report.findings:
        print(
            f"check: NOT-OK — {len(report.findings)} finding(s) over "
            f"{len(report.records)} derived record(s)",
            file=sys.stderr,
        )
        for finding in report.findings:
            print(f"  FAIL  {finding}", file=sys.stderr)
        return 1
    if not report.records:
        print("check: OK — no per-task charge ledger; nothing to map")
        return 0
    print(
        f"check: OK — {len(report.records)} per-task cost record(s) derived; "
        f"scope levels, stated currency, hard-stop and ticket receipt all held"
    )
    for source in sorted(set(report.currency_source.values())):
        print(f"  note  currency stated by: {source}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paperclip-budget",
        description="per-task budget scope adapter for the paperclip.ing seam (issue #415)",
    )
    sub = parser.add_subparsers(dest="verb", required=True)
    check = sub.add_parser("check", help="derive records and report tri-state")
    check.add_argument("--root", default=".", help="repo root (default: cwd)")
    check.add_argument("--charges", default=None, help="override the charge ledger path")
    check.add_argument("--tickets", default=None, help="override the ticket store path")
    check.add_argument("--metering", default=None, help="override the metering store path")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
