"""The deterministic mapper: fleet state -> upstream paperclip shapes (issue #428).

The mapper is a pure function of the tree it is given. It reads the fleet's own
stores — ``registry/profiles/seeds/*.yaml``, ``registry/personas/cards/*.yaml``,
``.board/snapshot.json`` + ``.board/claims.jsonl`` and the budget rail
(``gateway/finops/budgets.yaml``, ``telemetry/budgets/config/policies.yaml``) —
and emits records that validate against the three frozen seam schemas in
``docs/contracts/paperclip/``. Same input, byte-identical output.

The two stdlib-only tools the adapter may not take a third-party dependency for
— a **YAML subset loader** (``load_yaml``: mappings, sequences, scalars, quoted
strings, block scalars and flow collections) and a **JSON-Schema subset
validator** (``validate``: ``type``, ``required``, ``properties``,
``additionalProperties``, ``enum``, ``items``, ``minLength``, ``minimum``,
``maximum``, ``pattern`` and ``format: date-time``) — live in the seam this
adapter shares with ``integrations/hermes/`` (``integrations/_seam/``, issue
#1208) and are re-exported here. The validator is the **superset** of the two
copies that used to exist, so every keyword this adapter enforced is still
enforced, by both adapters.

The gate drives both: a required field or a closed-vocabulary value that drifts
is refused, by name, and the negative control proves the refusal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .._seam.schema import validate  # noqa: F401 - re-exported at this adapter's seam
from .._seam.yaml_subset import load_yaml, load_yaml_file  # noqa: F401
from .model import Activity, Budget, Cost, Issue, Persona, Profile

#: The three frozen seam contracts and their schemas.
SCHEMA_KINDS: Tuple[str, ...] = ("heartbeat", "ticket", "budget")
SCHEMA_DIR = Path("docs/contracts/paperclip")

#: fleet/monitor.py POLL_SECONDS — the scheduled cadence the seam derives.
CADENCE_SECONDS = 20

#: The standing owner of an unclaimed work item (the platform orchestrator).
DEFAULT_OWNER = "orchestrator"

#: The upstream status enum for a ticket, in the schema's closed vocabulary.
TICKET_STATUSES = ("in-progress", "blocked", "in-review", "done")

#: The reporting line every mapped agent hangs from (the platform root).
PLATFORM_ROOT = "platform/purebliss"


# ==========================================================================
# The shared seam: the YAML subset loader and the schema-subset validator
# ==========================================================================
# Both tools used to be defined here. They were also defined, body for body, in
# ``integrations/hermes/mapping.py`` — and the copies had already drifted (this
# adapter enforced ``format: date-time``/``minimum``/``maximum`` and the other
# did not). They live in ``integrations/_seam/`` now (issue #1208) and are
# re-exported under their original names, so every existing caller keeps
# importing them from ``integrations.paperclip.mapping``.


def load_schema(root: Path, kind: str) -> Dict[str, Any]:
    """Load one of the three frozen seam schemas."""
    return json.loads((root / SCHEMA_DIR / f"{kind}.schema.json").read_text(encoding="utf-8"))


def load_schemas(root: Path) -> Dict[str, Dict[str, Any]]:
    return {kind: load_schema(root, kind) for kind in SCHEMA_KINDS}


# ==========================================================================
# Source readers
# ==========================================================================

_SEED_GLOB = "registry/profiles/seeds/*.yaml"
_CARD_GLOB = "registry/personas/cards/*.yaml"


def iter_seed_profiles(root: Path) -> List[Profile]:
    profiles: List[Profile] = []
    for path in sorted(root.glob(_SEED_GLOB)):
        data = load_yaml_file(path) or {}
        profiles.append(
            Profile(
                id=str(data.get("id") or path.stem.split(".")[0]),
                version=str(data.get("version") or ""),
                owner=str(data.get("owner") or ""),
                default_model_tier=str(data.get("defaultModelTier") or ""),
                capabilities=tuple(data.get("capabilitySet") or ()),
                tools=tuple(data.get("toolAllowlist") or ()),
                constraints=tuple(data.get("constraintSet") or ()),
            )
        )
    return sorted(profiles, key=lambda p: p.id)


def iter_persona_cards(root: Path) -> List[Persona]:
    personas: List[Persona] = []
    for path in sorted(root.glob(_CARD_GLOB)):
        data = load_yaml_file(path) or {}
        personas.append(
            Persona(
                id=str(data.get("id") or path.stem),
                name=str(data.get("name") or data.get("id") or path.stem),
                summary=str(data.get("summary") or ""),
                posture=str(data.get("posture") or "executor"),
                tier=str(data.get("defaultModelTier") or ""),
                owned_lanes=tuple(data.get("ownedLanes") or ()),
                expertise=tuple(data.get("expertise") or ()),
            )
        )
    return sorted(personas, key=lambda p: p.id)


def load_board(root: Path) -> Dict[str, Any]:
    path = root / ".board" / "snapshot.json"
    if not path.exists():
        return {"issues": []}
    return json.loads(path.read_text(encoding="utf-8"))


def load_claims(root: Path) -> List[Dict[str, Any]]:
    path = root / ".board" / "claims.jsonl"
    if not path.exists():
        return []
    claims: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        claims.append(json.loads(line))
    return claims


# ==========================================================================
# Mappers
# ==========================================================================


def map_agents(root: Path) -> List[Dict[str, Any]]:
    """Seeds + persona cards -> the upstream org chart.

    "If it can receive a heartbeat, it is hired": every seed and every card is
    hired; ``reports_to`` carries the reporting line. A persona card wins over a
    seed of the same id (it is the richer, operator-facing record).
    """
    profiles = {p.id: p for p in iter_seed_profiles(root)}
    personas = {p.id: p for p in iter_persona_cards(root)}
    agents: List[Dict[str, Any]] = []
    for agent_id in sorted(set(profiles) | set(personas)):
        profile = profiles.get(agent_id)
        persona = personas.get(agent_id)
        capabilities = set(profile.capabilities if profile else ())
        if persona:
            capabilities |= set(persona.owned_lanes) | set(persona.expertise)
        role = persona.posture if persona else "agent-profile"
        tier = (persona.tier if persona and persona.tier else "") or (
            profile.default_model_tier if profile else ""
        )
        if agent_id == DEFAULT_OWNER:
            reports_to = PLATFORM_ROOT
        else:
            reports_to = DEFAULT_OWNER
        agents.append(
            {
                "agent_id": agent_id,
                "name": persona.name if persona else agent_id,
                "role": role,
                "reports_to": reports_to,
                "hired": True,
                "tier": tier,
                "capabilities": sorted(capabilities),
                "kind": "persona" if persona else "seed",
            }
        )
    return agents


def _latest_claims(claims: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    latest: Dict[int, Dict[str, Any]] = {}
    for claim in claims:
        try:
            issue = int(claim.get("issue"))
        except (TypeError, ValueError):
            continue
        latest[issue] = claim
    return latest


def _ticket_status(
    *, closed: bool, blocked: bool, claimed: bool, has_commit: bool
) -> str:
    if closed:
        return "done"
    if blocked:
        return "blocked"
    if claimed and has_commit:
        return "in-review"
    return "in-progress"


def map_tickets(root: Path) -> List[Dict[str, Any]]:
    """Claims + board snapshot -> upstream issues (the ticket seam records).

    The fleet records claim *events* and lock files, not a status enum and not a
    ``blocked_by`` field (seam doc mismatch #4/#5), so both are derived here: the
    status from the claim state plus the dependency chain, the goal joined from
    the snapshot's milestone/parent.
    """
    board = load_board(root)
    issues = [it for it in board.get("issues", []) if isinstance(it, dict)]
    by_number = {int(it["number"]): it for it in issues if "number" in it}
    latest = _latest_claims(load_claims(root))

    tickets: List[Dict[str, Any]] = []
    for number in sorted(by_number):
        issue = by_number[number]
        closed = str(issue.get("state", "")).upper() == "CLOSED"
        claim = latest.get(number)
        claimed = bool(claim) and claim.get("event") == "claim"
        has_commit = bool(claim and claim.get("base_commit"))
        owner = str(claim.get("agent")) if claimed else DEFAULT_OWNER

        blocked_by = [
            f"kushin77/agent-orchestrator#{int(b)}"
            for b in (issue.get("blocked_by") or [])
            if int(b) in by_number and not (str(by_number[int(b)].get("state", "")).upper() == "CLOSED")
        ]
        blocked = bool(blocked_by)

        milestone = str(issue.get("milestone") or "")
        parent = issue.get("parent")
        if milestone:
            goal = milestone
        elif parent is not None:
            goal = f"kushin77/agent-orchestrator#{int(parent)}"
        else:
            goal = "EPIC-00"

        if closed:
            evidence = [f"closed:{issue.get('closed_at') or 'unknown'}"]
        elif claimed and has_commit:
            evidence = [f"kushin77/agent-orchestrator#{number}", f"commit:{claim.get('base_commit')}"]
        else:
            evidence = [f"kushin77/agent-orchestrator#{number}"]

        tickets.append(
            Issue(
                id=f"kushin77/agent-orchestrator#{number}",
                owner=owner,
                status=_ticket_status(
                    closed=closed, blocked=blocked, claimed=claimed, has_commit=has_commit
                ),
                blocked_by=tuple(blocked_by),
                goal=goal,
                evidence=tuple(evidence),
            ).to_dict()
        )
    return tickets


def _budget_from_gateway(tenant: str, entry: Dict[str, Any]) -> Cost:
    policy = str(entry.get("policy") or "warn")
    hard_cap_pct = float(entry.get("hardCapPct", 100))
    warn_pct = float(entry.get("warnAtPct", 80))
    return Cost(
        scope_level="team",
        scope_id=tenant,
        period="month",
        cap=float(entry.get("monthlyBudgetUsd", 0.0)),
        spent=0.0,
        currency="USD",
        hard_stop=hard_cap_pct <= 100 or policy == "stop",
        burn_rate_alert_pct=warn_pct,
        receipt_ref=f"gateway/finops/budgets.yaml#{tenant}",
    )


def _budget_from_telemetry(tenant: str, entry: Dict[str, Any]) -> Cost:
    mode = str(entry.get("mode") or "observe")
    cost = entry.get("cost") or {}
    warn = cost.get("warnAtPct")
    return Cost(
        scope_level="team",
        scope_id=tenant,
        period=str(cost.get("window") or "month"),
        cap=float(cost.get("limitUsd", 0.0)),
        spent=0.0,
        currency="USD",
        hard_stop=mode == "enforce",
        burn_rate_alert_pct=float(warn) * 100 if warn is not None else 100.0,
        receipt_ref=f"telemetry/budgets/config/policies.yaml#{tenant}",
    )


def map_budgets(root: Path) -> List[Dict[str, Any]]:
    """The budget rail -> upstream costs (the budget seam records).

    The fleet caps per **tenant + vendor**, not per agent/team/project (seam doc
    mismatch #7), so a tenant budget maps to scope level ``team``; ``currency``
    (``USD`` by convention, mismatch #8), ``hard_stop`` (from the percentage
    rail, mismatch #9), and ``receipt_ref`` (pointing at the config row, mismatch
    #10) are all derived.
    """
    costs: List[Cost] = []

    gateway = load_yaml_file(root / "gateway" / "finops" / "budgets.yaml") or {}
    for tenant in sorted(gateway.get("budgets") or {}):
        costs.append(_budget_from_gateway(tenant, gateway["budgets"][tenant] or {}))

    telemetry = load_yaml_file(root / "telemetry" / "budgets" / "config" / "policies.yaml") or {}
    for entry in telemetry.get("policies") or []:
        if not isinstance(entry, dict):
            continue
        tenant = str(entry.get("tenantId") or "")
        if not tenant:
            continue
        costs.append(_budget_from_telemetry(tenant, entry))

    return [c.to_dict() for c in sorted(costs, key=lambda c: c.receipt_ref)]


#: The rungs that publish a heartbeat, and their runtime beat paths.
HEARTBEAT_RUNGS: Tuple[Tuple[str, str], ...] = (
    ("monitor", ".fleet/monitor.heartbeat.json"),
    ("brain", ".fleet/brain.heartbeat.json"),
    ("sister", ".fleet/sister.heartbeat.json"),
)

#: Fallback beat timestamp when a rung has published no beat (offline, fixed).
NO_BEAT_TS = "1970-01-01T00:00:00Z"

#: A state that maps to a non-scheduled wake cause (fleet state -> seam cause).
_WAKE_CAUSE = {"dispatching": "assigned", "reviewing": "review-requested"}

#: States that count as durable progress rather than a named blocker.
_PROGRESS_STATES = ("healthy", "idle", "dispatching", "reviewing")


def map_heartbeats(
    root: Path, *, session_id: str = "unbound", now: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Rung beats -> seam heartbeat records.

    The fleet beat is a flat liveness record with no ``wake``, no ``outcome`` and
    no ``tick`` (seam doc mismatches #1/#2/#3): the mapper *synthesizes* the wake
    from the rung's actual state, derives the cadence from
    ``fleet/monitor.py`` ``POLL_SECONDS``, and carries a fixed tick. A rung with
    no beat on disk is reported as a named blocker, never silently dropped.
    """
    beats: List[Dict[str, Any]] = []
    for rung, rel in HEARTBEAT_RUNGS:
        path = root / rel
        beat: Dict[str, Any] = {}
        if path.exists():
            try:
                beat = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                beat = {}
            if not isinstance(beat, dict):
                beat = {}
        state = str(beat.get("state") or ("no-heartbeat" if not beat else "unknown"))
        ts = str(beat.get("ts") or (now or NO_BEAT_TS))
        delta: Dict[str, Any] = {}
        if beat:
            if "state" in beat:
                delta["state"] = state
            if "commit" in beat:
                delta["commit"] = beat.get("commit")
        progress = state in _PROGRESS_STATES
        beats.append(
            {
                "agent_id": rung,
                "session_id": session_id,
                "tick": 0,
                "cadence_seconds": CADENCE_SECONDS,
                "wake": {
                    "cause": _WAKE_CAUSE.get(state, "scheduled"),
                    "delta": delta,
                },
                "outcome": {
                    "status": "progress" if progress else "blocked",
                    "detail": f"rung {rung} state={state}",
                    "owner": "fleet/platform",
                },
                "ts": ts,
            }
        )
    return beats


# ==========================================================================
# Plan + validation
# ==========================================================================


def build_plan(root: Path, *, company_id: str, session_id: str = "unbound") -> Dict[str, Any]:
    """Build the full dry-run sync plan (offline, deterministic)."""
    return {
        "paperclip": {
            "mode": "cli-over-http",
            "adr": "ADR-0013",
            "api_prefix": "/api",
            "company_id": company_id,
        },
        "agents": map_agents(root),
        "tickets": map_tickets(root),
        "budgets": map_budgets(root),
        "heartbeats": map_heartbeats(root, session_id=session_id),
    }


#: plan section -> the seam schema its records must satisfy.
SECTION_SCHEMA = {"heartbeats": "heartbeat", "tickets": "ticket", "budgets": "budget"}


def validate_plan(plan: Dict[str, Any], schemas: Dict[str, Dict[str, Any]]) -> List[str]:
    """Validate every schema-bearing record in a plan; return all findings."""
    findings: List[str] = []
    for section, kind in SECTION_SCHEMA.items():
        schema = schemas[kind]
        records = plan.get(section) or []
        if not records:
            findings.append(f"{section}: no records emitted for the '{kind}' contract")
            continue
        for i, record in enumerate(records):
            findings.extend(validate(record, schema, f"{section}[{i}]"))
    return findings


# ==========================================================================
# Audit -> Activity projection (issue #347)
# ==========================================================================


def activity_from_audit(record: Dict[str, Any]) -> Activity:
    """Map one audit record onto the upstream ``Activity`` shape (issue #347).

    The tamper-evident audit trail (``telemetry/ledger``) is served read-only as
    the shell's Audit view; this pure projection makes each stored audit record
    an upstream activity entry. ``id`` is ``<tenantId>:<seq>`` (stable and unique
    within a tenant chain), ``actor`` is the canonical ``kind:id`` principal,
    ``verb`` is the action, ``object_ref`` is the audited resource and ``ts`` is
    the record's own RFC 3339 stamp. No I/O and no mutation: the input record is
    only read. The record is expected to come from the read model's
    ``filter``/``records`` (see ``telemetry/audit/README.md``).
    """
    return Activity(
        id=f"{record.get('tenantId', '')}:{record.get('seq', '')}",
        actor=str(record.get("actor", "")),
        verb=str(record.get("action", "")),
        object_ref=str(record.get("resource") or ""),
        ts=str(record.get("ts", "")),
    )


def map_audit_activities(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Project audit records into the ``Activity`` records the /api surface carries."""
    return [activity_from_audit(record).to_dict() for record in records]
